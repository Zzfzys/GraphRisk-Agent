"""Run bounded Stage 07 PostgreSQL TTL cleanup; never prints the DSN."""

from __future__ import annotations

import os

from graphrisk.serving.postgres_store import PostgresStore


def main() -> None:
    dsn = os.getenv("GRAPHRISK_STAGE07_DSN")
    if not dsn:
        raise SystemExit("GRAPHRISK_STAGE07_DSN is required")
    result = PostgresStore(dsn).cleanup_expired()
    print(f"cleared_reports={result['cleared_reports']} "
          f"deleted_tombstones={result['deleted_tombstones']}")


if __name__ == "__main__":
    main()

