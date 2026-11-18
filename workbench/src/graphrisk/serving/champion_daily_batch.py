"""Score a closed historical day using the bound 216+49 candidate artifacts.

The only supported source in this first adapter is the audited historical
parquet pair. New source/arrival-time semantics need their own validation.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import duckdb
import psutil
import xgboost as xgb

from graphrisk.models.champion_features import source_columns, transform_batch
from graphrisk.serving.day_batch_bands import assign_daily_bands

MODEL_SHA256 = '39df475311dc34ecd83de66372cc1bc7aae4505517f6d878a4e1c4b11d47f2ba'
POLICY_SHA256 = 'b0373c18c1fbe443f6ee5c9fd545d7353fc13e918724ecdf60bc1ce257f60f92'
FEATURE_AUDIT_SHA256 = 'd2234a15ac05d7af51692e85e21303bd781993e296ea2b0a8a0ce67f7cddc1a7'


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class ChampionDailyBatch:
    """Bound, reproducible day-end scoring; paths are server-owned inputs."""

    def __init__(self, *, model: Path, protocol: Path, maps: Path, audit: Path,
                 policy: Path, source: Path, derived: Path):
        self.paths = {key: Path(value) for key, value in locals().items()
                      if key not in ('self',)}
        self.audit = json.loads(self.paths['audit'].read_text(encoding='utf-8'))
        self.policy = json.loads(self.paths['policy'].read_text(encoding='utf-8'))
        expected = {
            'model': self.policy['model_sha256'],
            'protocol': self.audit['study_protocol_sha256'],
            'maps': self.audit['category_maps_sha256'],
            'source': self.audit['raw_source_sha256'],
            'derived': self.audit['derived_source_sha256'],
        }
        if (self.policy['model_sha256'] != MODEL_SHA256 or
                not self.audit.get('exact_float32_and_nan_match') or
                self.audit.get('columns_verified') != 265 or
                self.policy['review_numerator'] != 14571 or
                self.policy['review_denominator'] != 88531):
            raise ValueError('feature audit or daily review policy is not compatible')
        threshold = self.policy['high_threshold']
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError('invalid model-specific high threshold')
        self.hashes = {name: file_sha256(path) for name, path in self.paths.items()}
        if (self.hashes['policy'] != POLICY_SHA256 or self.hashes['audit'] != FEATURE_AUDIT_SHA256 or
                any(self.hashes[name] != digest for name, digest in expected.items())):
            raise ValueError('candidate model, feature or category binding mismatch')
        self._stats = {name: (path.stat().st_size, path.stat().st_mtime_ns)
                       for name, path in self.paths.items()}
        self.contract = json.loads(self.paths['protocol'].read_text(encoding='utf-8'))
        self.maps = json.loads(self.paths['maps'].read_text(encoding='utf-8'))
        self.model = xgb.Booster(params={'nthread': 2})
        self.model.load_model(str(self.paths['model']))

    def _check_files(self):
        for name, path in self.paths.items():
            try:
                state = path.stat()
            except OSError as exc:
                raise ValueError('bound daily batch input unavailable') from exc
            if (state.st_size, state.st_mtime_ns) != self._stats[name]:
                raise ValueError('bound daily batch input changed; reload and validate')

    def score_day(self, relative_day: int, *, available_through_second: int) -> dict:
        """Score all saved rows for a closed relative day, not an online event."""
        if isinstance(relative_day, bool) or not isinstance(relative_day, int) or relative_day < 0:
            raise ValueError('relative day must be a nonnegative integer')
        end = (relative_day + 1) * 86400
        if (isinstance(available_through_second, bool) or not isinstance(available_through_second, int)
                or available_through_second < end):
            raise ValueError('daily batch is not closed')
        if relative_day * 86400 <= self.policy['selection_cutoff']:
            raise ValueError('daily batch must follow the model selection cutoff')
        if psutil.virtual_memory().available < 2 * 2**30:
            raise MemoryError('available RAM below 2 GiB')
        self._check_files()
        mapping = self.contract['base_mapping']['day']
        extra = self.contract['uid_extra']['day']
        derived_columns = list(dict.fromkeys([*mapping.values(), *extra]))
        if any(not name.replace('_', '').isalnum() for name in derived_columns):
            raise ValueError('invalid derived feature name')
        with duckdb.connect() as db:
            db.execute("SET memory_limit='256MB'")
            db.execute('SET threads=1')
            source = db.execute(
                'SELECT s.transaction_id,s.source_transaction_dt_seconds,' + source_columns(self.contract['raw']) +
                ' FROM read_parquet(?) s WHERE s.source_transaction_dt_seconds>=? AND s.source_transaction_dt_seconds<?'
                ' ORDER BY s.source_transaction_dt_seconds,s.transaction_id',
                [str(self.paths['source']), relative_day*86400, end]).fetchdf()
            derived = db.execute(
                'SELECT transaction_id,source_transaction_dt_seconds,' +
                ','.join(f'"{name}"' for name in derived_columns) +
                ' FROM read_parquet(?) WHERE source_transaction_dt_seconds>=? AND source_transaction_dt_seconds<?'
                ' ORDER BY source_transaction_dt_seconds,transaction_id',
                [str(self.paths['derived']), relative_day*86400, end]).fetchdf()
        if source.empty:
            raise ValueError('no saved transactions for this relative day')
        if psutil.virtual_memory().available < 2 * 2**30:
            raise MemoryError('available RAM below 2 GiB')
        matrix = transform_batch(source, derived, self.contract, self.maps, mode='day')
        scores = self.model.inplace_predict(matrix, validate_features=True)
        ids = source.transaction_id.astype(str).tolist()
        seconds = source.source_transaction_dt_seconds.astype(int).tolist()
        levels = assign_daily_bands(ids, seconds, scores.tolist(), high_threshold=self.policy['high_threshold'])
        self._check_files()
        return {
            'model_version': 'champion-xgb-day-216-plus49-fold3-v1',
            'temporal_scope': 'relative_day_end', 'relative_day': relative_day,
            'batch_end_second': end, 'available_through_second': available_through_second,
            'model_sha256': self.hashes['model'], 'feature_protocol_sha256': self.hashes['protocol'],
            'category_maps_sha256': self.hashes['maps'], 'policy_sha256': self.hashes['policy'],
            'source_sha256': self.hashes['source'], 'derived_sha256': self.hashes['derived'],
            'calibrated_probability': False,
            'results': [{'transaction_id': key, 'event_second': second, 'score': float(score), 'risk_level': level}
                        for key, second, score, level in zip(ids, seconds, scores, levels)],
        }


class ClosedHistoricalBatches:
    """Server-owned closure watermark for the audited historical source only."""

    def __init__(self, model: ChampionDailyBatch, *, available_through_second: int,
                 x3_tool=None, x3_guard=None):
        if isinstance(available_through_second, bool) or not isinstance(available_through_second, int) or available_through_second < 0:
            raise ValueError('the server closure watermark must be a nonnegative second')
        self.model = model
        self.available_through_second = available_through_second
        self.x3_tool, self.x3_guard = x3_tool, x3_guard

    def get_day(self, relative_day: int) -> dict:
        return self.model.score_day(relative_day, available_through_second=self.available_through_second)

    def get_transaction(self, relative_day: int, transaction_id: str, mode: str) -> dict:
        """Explicitly select the candidate or bound x3 for a registered historical case."""
        if self.x3_tool is None or self.x3_guard is None:
            raise ValueError('historical model selector is not configured')
        from graphrisk.serving.explicit_model_selection import select_score
        from graphrisk.serving.model_risk_band import PROFILE_SHA
        self.x3_guard()
        row = self.x3_tool._predictor.service.lookup(transaction_id)
        second = int(row['event_second'])
        if second // 86400 != relative_day:
            raise ValueError('transaction does not belong to the requested day')
        scope = SimpleNamespace(dataset_source='ieee_cis', target_event_second=second,
            transaction_id=transaction_id, primary_model_version=row['primary_model_version'],
            model_revision=PROFILE_SHA, case_alias='0' * 32)
        return select_score(mode, scope=scope, candidate=self.model,
            watermark=self.available_through_second, x3_tool=self.x3_tool, x3_guard=self.x3_guard)
