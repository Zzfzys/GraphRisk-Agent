"""Apply the reentrant Stage 07 PostgreSQL schema and LangGraph checkpoint migrations."""

from __future__ import annotations

import os

from graphrisk.serving.postgres_store import PostgresStore


def main() -> None:
    dsn = os.getenv("GRAPHRISK_STAGE07_MIGRATION_DSN") or os.getenv("GRAPHRISK_STAGE07_DSN")
    if not dsn:
        raise SystemExit("GRAPHRISK_STAGE07_DSN is required")
    store = PostgresStore(dsn)
    store.migrate()
    if not store.check():
        raise SystemExit("Stage 07 persistence schema is not ready")
    print("Stage 07 PostgreSQL schema ready (version 1)")


if __name__ == "__main__":
    main()

