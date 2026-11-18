"""Local arrival journal and immutable, server-sealed champion daily batches."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

import duckdb
import pandas as pd
import psutil

from graphrisk.graph.coverage_data import NewTransaction
from graphrisk.models.champion_daily_features import (
    TEXT_FIELDS, build_daily_features, normalized_raw, raw_fields,
)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _second(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(name + ' must be a nonnegative whole second')


def _memory_guard():
    if psutil.virtual_memory().available < 2 * 2**30:
        raise MemoryError('available RAM below 2 GiB; daily batch stopped')


@contextmanager
def _monitor(db):
    stop, failure = threading.Event(), []
    def check():
        while not stop.wait(.5):
            try:
                _memory_guard()
            except MemoryError as exc:
                failure.append(exc)
                db.interrupt()
                return
    worker = threading.Thread(target=check, daemon=True)
    _memory_guard()
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join()
        if failure:
            raise failure[0]


def _frame_sha256(frame):
    digest = hashlib.sha256(_json(list(frame.columns)).encode())
    for row in frame.itertuples(index=False, name=None):
        digest.update(_json([None if pd.isna(value) else value.item() if hasattr(value, 'item')
                             else value for value in row]).encode())
    return digest.hexdigest()


class DailyBatchStore:
    """SQLite serializes ingestion and seal commits; a failed seal stays open.

    Events earlier than the latest sealed day are quarantined even if their own
    day was empty: accepting them would silently alter later historical inputs.
    Reopening and rescoring requires a future explicit revision workflow.
    """

    def __init__(self, db_path: Path, candidate, *, history: Path | None = None,
                 history_cutoff_second: int, scratch: Path):
        _second(history_cutoff_second, 'history cutoff')
        self.path, self.candidate = Path(db_path), candidate
        self.history = Path(history) if history is not None else None
        self.history_cutoff_second = history_cutoff_second
        self.scratch = Path(scratch)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.scratch.mkdir(parents=True, exist_ok=True)
        # ponytail: one local seal writer; use a job queue if batch throughput grows.
        self._lock = threading.RLock()
        self.fields = raw_fields(candidate.contract)
        if any(not name.replace('_', '').isalnum() for name in self.fields):
            raise ValueError('invalid bound daily source field name')
        if self.history is not None:
            if self.history.resolve() != candidate.paths['source'].resolve():
                raise ValueError('history must be the candidate-bound source')
            history_sha = candidate.hashes['source']
        else:
            history_sha = None
        self.binding = {'history_sha256': history_sha, 'history_cutoff_second': history_cutoff_second,
            **{key + '_sha256': candidate.hashes[key] for key in ('model', 'protocol', 'maps', 'policy')},
            'raw_fields': self.fields, 'version': 'daily-arrival-journal-v1'}
        with self._connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    transaction_id TEXT PRIMARY KEY, event_second INTEGER NOT NULL,
                    received_second INTEGER NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_time ON events(status,event_second);
                CREATE TABLE IF NOT EXISTS batches (
                    relative_day INTEGER PRIMARY KEY, batch_id TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL, sealed_content_sha256 TEXT NOT NULL,
                    summary TEXT);
            ''')
            if 'summary' not in {row[1] for row in conn.execute('PRAGMA table_info(batches)')}:
                conn.execute('ALTER TABLE batches ADD COLUMN summary TEXT')
            prior = conn.execute('SELECT payload FROM binding WHERE id=1').fetchone()
            if prior is None:
                conn.execute('INSERT INTO binding VALUES (1,?)', [_json(self.binding)])
            elif prior[0] != _json(self.binding):
                raise ValueError('daily journal model, history or mapping binding changed; use a new journal')

    @contextmanager
    def _connect(self):
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=30)
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

    def ingest(self, event: NewTransaction, *, received_second: int) -> dict:
        """The caller supplies a server-owned arrival clock, never a request value."""
        _second(received_second, 'received second')
        _memory_guard()
        event = NewTransaction.model_validate(event.model_dump())
        raw = normalized_raw(event, self.candidate.contract)
        stamp = event.source_transaction_dt_seconds
        if stamp > received_second:
            raise ValueError('event time cannot follow the server arrival second')
        if stamp // 86400 <= self.history_cutoff_second // 86400:
            raise ValueError('new event day must follow the bound historical cutoff day')
        # Retain the complete accepted source declaration as well as only the
        # fields consumed by the model. Neither labels nor predictions are accepted.
        payload = event.model_dump()
        payload['raw_features'] = {**payload['raw_features'], **raw}
        digest = _sha(payload)
        self.candidate._check_files()
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            previous = conn.execute('SELECT status,payload_sha256 FROM events WHERE transaction_id=?',
                                    [event.transaction_id]).fetchone()
            if previous:
                if previous[1] != digest:
                    raise ValueError('transaction ID conflicts with a different received payload')
                return {'status': 'duplicate', 'original_status': previous[0],
                        'transaction_id': event.transaction_id, 'relative_day': stamp // 86400}
            if self.history is not None:
                with duckdb.connect() as db:
                    db.execute("SET memory_limit='128MB'")
                    db.execute('SET threads=1')
                    duplicate = db.execute('SELECT count(*) FROM read_parquet(?) '
                        'WHERE transaction_id=? AND source_transaction_dt_seconds<=?',
                        [str(self.history), event.transaction_id, self.history_cutoff_second]).fetchone()[0]
                if duplicate:
                    raise ValueError('transaction ID already exists in the bound history')
            sealed = conn.execute('SELECT max(relative_day) FROM batches').fetchone()[0]
            state = 'quarantined' if sealed is not None and stamp // 86400 <= sealed else 'accepted'
            conn.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',
                [event.transaction_id, stamp, received_second, state, _json(payload), digest])
        return {'status': state, 'transaction_id': event.transaction_id, 'relative_day': stamp // 86400,
                'reason': 'sealed_history_late_arrival' if state == 'quarantined' else None}

    def _prepare(self, conn, db, relative_day):
        text = set(self.candidate.contract['categorical']) | TEXT_FIELDS
        definitions = ['transaction_id VARCHAR', 'source_transaction_dt_seconds BIGINT',
                       *(f'"{name}" ' + ('VARCHAR' if name in text else 'DOUBLE') for name in self.fields)]
        db.execute('CREATE TEMP TABLE daily_new (' + ','.join(definitions) + ')')
        end = (relative_day + 1) * 86400
        cursor = conn.execute('SELECT payload,received_second FROM events WHERE status=\'accepted\' AND event_second<? '
                              'ORDER BY event_second,transaction_id', [end])
        placeholders = ','.join('?' for _ in definitions)
        snapshot = hashlib.sha256()
        snapshot.update(_json(self.binding).encode())
        while rows := cursor.fetchmany(128):
            _memory_guard()
            values = []
            for payload, received_second in rows:
                event = json.loads(payload)
                snapshot.update(_json([event, received_second]).encode())
                raw = event['raw_features']
                values.append([event['transaction_id'], event['source_transaction_dt_seconds'],
                               *[raw[name] for name in self.fields]])
            db.executemany('INSERT INTO daily_new VALUES (' + placeholders + ')', values)
        columns = ['transaction_id', 'source_transaction_dt_seconds', *self.fields]
        projection = ','.join('"'+name+'"' for name in columns)
        if self.history is None:
            db.execute('CREATE TEMP VIEW daily_source AS SELECT * FROM daily_new')
        else:
            db.execute('CREATE TEMP VIEW daily_history AS SELECT * FROM read_parquet(' +
                       "'" + str(self.history).replace("'", "''") + "')")
            db.execute('CREATE TEMP VIEW daily_source AS SELECT ' + projection +
                ' FROM daily_history WHERE source_transaction_dt_seconds<=' + str(self.history_cutoff_second) +
                ' UNION ALL SELECT ' + projection + ' FROM daily_new')
        return snapshot.hexdigest()

    def seal(self, relative_day: int, *, available_through_second: int) -> dict:
        """Close once after day end; accepted arrivals are frozen atomically."""
        _second(relative_day, 'relative day')
        if relative_day > 2147483647 // 86400:
            raise ValueError('relative day exceeds supported transaction time range')
        _second(available_through_second, 'server closure watermark')
        end = (relative_day + 1) * 86400
        if available_through_second < end:
            raise ValueError('daily batch is not closed')
        if relative_day * 86400 <= max(self.history_cutoff_second, self.candidate.policy['selection_cutoff']):
            raise ValueError('daily batch must follow bound history and model selection cutoff')
        _memory_guard()
        self.candidate._check_files()
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute('SELECT payload FROM batches WHERE relative_day=?', [relative_day]).fetchone()
            if existing:
                return self._read_sealed(existing[0])
            sealed = conn.execute('SELECT max(relative_day) FROM batches').fetchone()[0]
            if sealed is not None and relative_day <= sealed:
                raise ValueError('cannot insert a batch before already sealed history')
            earlier = conn.execute('SELECT min(event_second/86400) FROM events WHERE status=\'accepted\' '
                'AND event_second<? AND event_second>=?', [relative_day * 86400,
                    (sealed + 1) * 86400 if sealed is not None else 0]).fetchone()[0]
            if earlier is not None:
                raise ValueError('seal the earlier nonempty relative day first')
            future_arrival = conn.execute('SELECT count(*) FROM events WHERE status=\'accepted\' '
                'AND event_second<? AND received_second>?', [end, available_through_second]).fetchone()[0]
            if future_arrival:
                raise ValueError('closure watermark precedes an accepted arrival')
            with duckdb.connect() as db:
                db.execute("SET memory_limit='256MB'")
                db.execute('SET threads=1')
                db.execute('SET temp_directory=?', [str(self.scratch)])
                with _monitor(db):
                    snapshot_sha = self._prepare(conn, db, relative_day)
                    source, derived = build_daily_features(db, self.candidate.contract, relative_day, _memory_guard)
            # Hash ordered semantic values, independently of Arrow/Parquet encoding.
            from graphrisk.serving.champion_daily_batch import file_sha256
            implementation_sha = file_sha256(Path(__file__).parents[1]/'models'/'champion_daily_features.py')
            derived_sha = _frame_sha256(derived)
            result = self.candidate.score_frames(relative_day, source, derived,
                available_through_second=available_through_second,
                source_sha256=snapshot_sha, derived_sha256=derived_sha)
            result.update(batch_id=f'day-{relative_day}-{snapshot_sha[:16]}', revision=1,
                source_kind='arrival_journal', source_snapshot_sha256=snapshot_sha,
                feature_implementation_sha256=implementation_sha,
                late_arrival_policy='quarantine_after_seal',
                batch_population='all_accepted_arrivals_for_relative_day',
                anonymous_field_availability='upstream_declared_at_arrival_not_independently_verified')
            for row in result['results']:
                payload = conn.execute('SELECT payload,received_second FROM events WHERE transaction_id=?',
                                       [row['transaction_id']]).fetchone()
                event = json.loads(payload[0])
                row['received_second'] = payload[1]
                row.update({name: event[name] for name in ('transaction_amount', 'card_id', 'device_id',
                    'email_domain', 'address_id', 'product_code', 'has_identity')})
            result['sealed_content_sha256'] = _sha(result)
            self.candidate._check_files()
            summary = {key: value for key, value in result.items() if key != 'results'}
            summary['transaction_count'] = len(result['results'])
            conn.execute('INSERT INTO batches VALUES (?,?,?,?,?)', [relative_day,
                result['batch_id'], _json(result), result['sealed_content_sha256'], _json(summary)])
        return result

    @staticmethod
    def _read_sealed(payload):
        value = json.loads(payload)
        digest = value.pop('sealed_content_sha256')
        if _sha(value) != digest:
            raise ValueError('sealed daily batch content hash mismatch')
        value['sealed_content_sha256'] = digest
        return value

    def get_day(self, relative_day: int) -> dict:
        _second(relative_day, 'relative day')
        self.candidate._check_files()
        with self._connect() as conn:
            row = conn.execute('SELECT payload FROM batches WHERE relative_day=?', [relative_day]).fetchone()
        if row is None:
            raise ValueError('daily batch is not sealed')
        return self._read_sealed(row[0])

    @contextmanager
    def investigation_snapshot(self, relative_day: int):
        """Yield the exact label-free source population bound at seal time.

        The SQLite read transaction freezes arrivals while the snapshot digest is
        recomputed. Later days and quarantined events cannot enter this view.
        Callers must still impose strictly-before-target investigation cutoffs.
        """
        _second(relative_day, 'relative day')
        _memory_guard()
        self.candidate._check_files()
        with self._connect() as conn:
            conn.execute('BEGIN')
            row = conn.execute('SELECT payload FROM batches WHERE relative_day=?',
                               [relative_day]).fetchone()
            if row is None:
                raise ValueError('daily batch is not sealed')
            batch = self._read_sealed(row[0])
            with duckdb.connect() as db:
                db.execute("SET memory_limit='128MB'")
                db.execute('SET threads=1')
                db.execute('SET temp_directory=?', [str(self.scratch)])
                with _monitor(db):
                    snapshot = self._prepare(conn, db, relative_day)
                    if snapshot != batch['source_snapshot_sha256']:
                        raise ValueError('sealed daily investigation source snapshot changed')
                    yield batch, db
        self.candidate._check_files()

    def list_batches(self) -> list[dict]:
        self.candidate._check_files()
        with self._connect() as conn:
            rows = conn.execute('SELECT relative_day,summary FROM batches ORDER BY relative_day DESC').fetchall()
            result = []
            for day, summary in rows:
                if summary is None:
                    payload = conn.execute('SELECT payload FROM batches WHERE relative_day=?', [day]).fetchone()[0]
                    batch = self._read_sealed(payload)
                    batch['transaction_count'] = len(batch.pop('results'))
                    summary = _json(batch)
                    conn.execute('UPDATE batches SET summary=? WHERE relative_day=?', [summary, day])
                result.append(json.loads(summary))
        return result

    def get_transaction(self, relative_day: int, transaction_id: str, mode: str = 'daily') -> dict:
        if mode != 'daily':
            raise ValueError('arrival journal serves daily model only; choose x3 explicitly in its own route')
        batch = self.get_day(relative_day)
        rows = batch.pop('results')
        row = next((value for value in rows if value['transaction_id'] == transaction_id), None)
        if row is None:
            raise ValueError('transaction is not present in the sealed daily batch')
        return {**batch, **row}

    def resolve_transaction(self, transaction_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute('SELECT event_second,status FROM events WHERE transaction_id=?',
                               [transaction_id]).fetchone()
        if row is None or row[1] != 'accepted':
            raise ValueError('transaction is unavailable or quarantined')
        return self.get_transaction(row[0] // 86400, transaction_id)

    def quarantined(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT transaction_id,event_second,received_second FROM events "
                                "WHERE status='quarantined' ORDER BY received_second,transaction_id").fetchall()
        return [dict(zip(('transaction_id', 'event_second', 'received_second'), row)) for row in rows]
