"""Grant only the application privileges needed by Stage 07 persistence and retrieval."""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import sql


BUSINESS_TABLES = (
    "investigation_runs", "approval_events", "create_idempotency",
    "cost_budget", "cost_attempts",
)
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def _parts(dsn: str) -> tuple[str, str, str]:
    parsed = urlsplit(dsn)
    if parsed.scheme not in ("postgresql", "postgres") or not parsed.username:
        raise ValueError("A PostgreSQL URL with user and database is required")
    database = unquote(parsed.path.lstrip("/"))
    if not database:
        raise ValueError("Database name is required")
    return unquote(parsed.username), unquote(parsed.password or ""), database


def grant_stage07_app(migration_dsn: str, app_dsn: str) -> None:
    """Create/update a limited role after all business and vector migrations."""
    admin_user, _, admin_db = _parts(migration_dsn)
    app_user, app_password, app_db = _parts(app_dsn)
    if admin_user == app_user or admin_db != app_db or not app_password:
        raise ValueError("App role must have its own password in the same database")
    with psycopg.connect(migration_dsn) as conn:
        with conn.transaction():
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app_user,)).fetchone()
            role = sql.Identifier(app_user)
            password = sql.Literal(app_password)
            if not exists:
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER "
                                     "NOCREATEDB NOCREATEROLE NOREPLICATION").format(role, password))
            else:
                conn.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {} NOSUPERUSER "
                                     "NOCREATEDB NOCREATEROLE NOREPLICATION").format(role, password))
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(admin_db), role))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
            for table in BUSINESS_TABLES + CHECKPOINT_TABLES:
                conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {} TO {}").format(
                    sql.Identifier(table), role))
            for table in ("policy_vector_indexes", "policy_vector_chunks"):
                if conn.execute("SELECT to_regclass(%s)", ("public." + table,)).fetchone()[0] is not None:
                    conn.execute(sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                        sql.Identifier(table), role))
            for table in ("schema_migrations", "checkpoint_migrations"):
                conn.execute(sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                    sql.Identifier(table), role))
