"""PostgreSQL facts and reentrant Stage 07 schema migration."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from typing import Iterator

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from graphrisk.serving.investigation_service import ServiceError


SCHEMA_VERSION = 1
MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS investigation_runs (
    run_id text PRIMARY KEY,
    storage_schema_version integer NOT NULL DEFAULT 1,
    mode text NOT NULL DEFAULT 'postgres' CHECK (mode = 'postgres'),
    storage_state text NOT NULL CHECK (storage_state IN
        ('preparing', 'published', 'decision_applying', 'recovery_required', 'expired')),
    request_fingerprint text NOT NULL,
    created_request_id text,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    session_id text UNIQUE,
    report_sha256 text,
    approval_expires_at timestamptz,
    decision_fingerprint text,
    decision_request_id text,
    decision_claimed_at timestamptz,
    record_json jsonb,
    public_view_json jsonb
);
CREATE TABLE IF NOT EXISTS create_idempotency (
    key_sha256 text PRIMARY KEY,
    request_fingerprint text NOT NULL,
    run_id text NOT NULL UNIQUE REFERENCES investigation_runs(run_id),
    state text NOT NULL CHECK (state IN ('preparing', 'published', 'recovery_required')),
    expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS approval_events (
    run_id text NOT NULL REFERENCES investigation_runs(run_id),
    sequence integer NOT NULL CHECK (sequence >= 1),
    kind text NOT NULL CHECK (kind IN ('created', 'approved', 'rejected', 'expired')),
    request_id text,
    event_json jsonb NOT NULL,
    PRIMARY KEY (run_id, sequence)
);
CREATE TABLE IF NOT EXISTS cost_budget (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    spent_cny numeric(18, 6) NOT NULL CHECK (spent_cny >= 0),
    stop_cny numeric(18, 6) NOT NULL CHECK (stop_cny >= 0),
    budget_cny numeric(18, 6) NOT NULL CHECK (budget_cny >= 0)
);
CREATE TABLE IF NOT EXISTS cost_attempts (
    run_id text NOT NULL REFERENCES investigation_runs(run_id),
    attempt_no integer NOT NULL CHECK (attempt_no >= 1),
    reserved_cny numeric(18, 6) NOT NULL CHECK (reserved_cny >= 0),
    actual_cny numeric(18, 6),
    state text NOT NULL CHECK (state IN ('reserved', 'settled', 'uncertain')),
    PRIMARY KEY (run_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS investigation_runs_expiry_idx ON investigation_runs(expires_at);
"""
MIGRATION_SHA256 = hashlib.sha256(MIGRATION_SQL.encode("utf-8")).hexdigest()


class PostgresStore:
    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self.dsn = dsn

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        try:
            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                yield conn
        except psycopg.Error as error:
            raise ServiceError("persistence_unavailable") from error

    @contextmanager
    def saver(self) -> Iterator[PostgresSaver]:
        try:
            with PostgresSaver.from_conn_string(self.dsn) as saver:
                yield saver
        except psycopg.Error as error:
            raise ServiceError("persistence_unavailable") from error

    def migrate(self) -> None:
        with self.connect() as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(7017, 4)")
                conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                    version integer PRIMARY KEY,
                    checksum text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
                )""")
                rows = conn.execute("SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall()
                if any(row["version"] != SCHEMA_VERSION or row["checksum"] != MIGRATION_SHA256 for row in rows):
                    raise ServiceError("persistence_unavailable")
                if not rows:
                    conn.execute(MIGRATION_SQL)
                    conn.execute(
                        "INSERT INTO schema_migrations(version, checksum) VALUES (%s, %s)",
                        (SCHEMA_VERSION, MIGRATION_SHA256),
                    )
        with self.saver() as saver:
            saver.setup()

    def check(self) -> bool:
        try:
            with self.connect() as conn:
                rows = conn.execute(
                    "SELECT version, checksum FROM schema_migrations ORDER BY version"
                ).fetchall()
                checkpoint_table = conn.execute(
                    "SELECT to_regclass('public.checkpoints') AS checkpoint_table"
                ).fetchone()
                return bool(len(rows) == 1 and rows[0]["version"] == SCHEMA_VERSION
                            and rows[0]["checksum"] == MIGRATION_SHA256
                            and checkpoint_table["checkpoint_table"])
        except (ServiceError, psycopg.Error):
            return False

    def configure_budget(self, *, stop: Decimal, budget: Decimal) -> None:
        with self.connect() as conn:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO cost_budget(singleton, spent_cny, stop_cny, budget_cny)
                       VALUES (true, 0, %s, %s) ON CONFLICT (singleton) DO NOTHING""",
                    (stop, budget),
                )
                row = conn.execute(
                    "SELECT stop_cny, budget_cny FROM cost_budget WHERE singleton = true FOR UPDATE"
                ).fetchone()
                if row["stop_cny"] != stop or row["budget_cny"] != budget:
                    raise ServiceError("persistence_unavailable")

    def claim(self, *, run_id: str, fingerprint: str, key_hash: str | None,
              request_id: str | None, ttl: timedelta, max_runs: int) -> tuple[str, str]:
        """Return (run_id, state); a duplicate key never starts a second runner."""
        with self.connect() as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(7017, 5)")
                if key_hash is not None:
                    prior = conn.execute(
                        """SELECT request_fingerprint, run_id, state FROM create_idempotency
                           WHERE key_sha256 = %s FOR UPDATE""", (key_hash,)
                    ).fetchone()
                    if prior:
                        if prior["request_fingerprint"] != fingerprint:
                            raise ServiceError("idempotency_conflict")
                        return prior["run_id"], prior["state"]
                active = conn.execute(
                    """SELECT count(*) AS total FROM investigation_runs
                       WHERE storage_state NOT IN ('expired', 'recovery_required')
                         AND expires_at > clock_timestamp()"""
                ).fetchone()["total"]
                if active >= max_runs:
                    raise ServiceError("run_capacity_exceeded")
                row = conn.execute(
                    """INSERT INTO investigation_runs
                       (run_id, storage_state, request_fingerprint, created_request_id,
                        created_at, expires_at)
                       VALUES (%s, 'preparing', %s, %s, clock_timestamp(),
                               clock_timestamp() + %s)
                       RETURNING created_at, expires_at""",
                    (run_id, fingerprint, request_id, ttl),
                ).fetchone()
                if key_hash is not None:
                    conn.execute(
                        """INSERT INTO create_idempotency
                           (key_sha256, request_fingerprint, run_id, state, expires_at)
                           VALUES (%s, %s, %s, 'preparing', %s + interval '7 days')""",
                        (key_hash, fingerprint, run_id, row["expires_at"]),
                    )
        return run_id, "new"

    def save_prepared(self, run_id: str, record_data: dict, view_data: dict) -> None:
        with self.connect() as conn:
            with conn.transaction():
                updated = conn.execute(
                    """UPDATE investigation_runs SET record_json = %s, public_view_json = %s,
                       session_id = %s, report_sha256 = %s, approval_expires_at = %s
                       WHERE run_id = %s AND storage_state = 'preparing'""",
                    (Jsonb(record_data), Jsonb(view_data), record_data["session_id"],
                     record_data["report_sha256"],
                     record_data["approval"]["payload"]["expires_at"] if record_data["approval"] else None,
                     run_id),
                )
                if updated.rowcount != 1:
                    raise ServiceError("persistence_unavailable")

    def publish(self, run_id: str, record_data: dict, view_data: dict) -> None:
        with self.connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    """SELECT storage_state FROM investigation_runs WHERE run_id = %s FOR UPDATE""",
                    (run_id,),
                ).fetchone()
                if not row or row["storage_state"] != "preparing":
                    raise ServiceError("persistence_unavailable")
                conn.execute(
                    """UPDATE investigation_runs SET storage_state = 'published',
                       record_json = %s, public_view_json = %s, session_id = %s,
                       report_sha256 = %s, approval_expires_at = %s
                       WHERE run_id = %s""",
                    (Jsonb(record_data), Jsonb(view_data), record_data["session_id"],
                     record_data["report_sha256"],
                     record_data["approval"]["payload"]["expires_at"] if record_data["approval"] else None,
                     run_id),
                )
                if record_data["approval"]:
                    event = record_data["approval"]["audit"][0]
                    conn.execute(
                        """INSERT INTO approval_events(run_id, sequence, kind, request_id, event_json)
                           VALUES (%s, 1, 'created', %s, %s)""",
                        (run_id, record_data["approval_request_ids"].get("1"), Jsonb(event)),
                    )
                conn.execute(
                    """UPDATE create_idempotency SET state = 'published' WHERE run_id = %s""",
                    (run_id,),
                )

    def row(self, run_id: str) -> dict | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM investigation_runs WHERE run_id = %s", (run_id,)
            ).fetchone()

    def mark_recovery_required(self, run_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE investigation_runs SET storage_state = 'recovery_required'
                   WHERE run_id = %s AND storage_state = 'preparing'""", (run_id,)
            )
            conn.execute(
                """UPDATE create_idempotency SET state = 'recovery_required'
                   WHERE run_id = %s AND state = 'preparing'""", (run_id,)
            )

    def cleanup_expired(self, *, batch_size: int = 100) -> dict[str, int]:
        """Expire runs before deleting checkpoints; keep seven days of minimal audit."""
        if batch_size < 1 or batch_size > 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        with self.connect() as conn:
            with conn.transaction():
                rows = conn.execute(
                    """SELECT run_id, session_id FROM investigation_runs
                       WHERE expires_at <= clock_timestamp() AND record_json IS NOT NULL
                       ORDER BY expires_at LIMIT %s FOR UPDATE SKIP LOCKED""",
                    (batch_size,),
                ).fetchall()
                for row in rows:
                    conn.execute(
                        """UPDATE investigation_runs SET storage_state = 'expired'
                           WHERE run_id = %s""", (row["run_id"],)
                    )
        cleared = 0
        for row in rows:
            if row["session_id"]:
                with self.saver() as saver:
                    saver.delete_thread(row["session_id"])
            with self.connect() as conn:
                conn.execute(
                    """UPDATE investigation_runs
                       SET record_json = NULL, public_view_json = NULL
                       WHERE run_id = %s AND storage_state = 'expired'""",
                    (row["run_id"],),
                )
            cleared += 1
        with self.connect() as conn:
            with conn.transaction():
                old = conn.execute(
                    """SELECT run_id FROM investigation_runs
                       WHERE storage_state = 'expired'
                         AND expires_at + interval '7 days' <= clock_timestamp()
                       ORDER BY expires_at LIMIT %s FOR UPDATE SKIP LOCKED""",
                    (batch_size,),
                ).fetchall()
                ids = [row["run_id"] for row in old]
                for run_id in ids:
                    conn.execute("DELETE FROM approval_events WHERE run_id = %s", (run_id,))
                    conn.execute("DELETE FROM cost_attempts WHERE run_id = %s", (run_id,))
                    conn.execute("DELETE FROM create_idempotency WHERE run_id = %s", (run_id,))
                    conn.execute("DELETE FROM investigation_runs WHERE run_id = %s", (run_id,))
        return {"cleared_reports": cleared, "deleted_tombstones": len(ids)}

    def close(self) -> None:
        """Connections are short-lived; no persisted state is deleted."""

