"""Create/reconfigure the limited Stage 07 application role after migrations."""

from __future__ import annotations

import os

from graphrisk.serving.postgres_roles import grant_stage07_app


def main() -> None:
    migration_dsn = os.getenv("GRAPHRISK_STAGE07_MIGRATION_DSN")
    app_dsn = os.getenv("GRAPHRISK_STAGE07_APP_DSN")
    if not migration_dsn or not app_dsn:
        raise SystemExit("Migration and application DSNs are required")
    grant_stage07_app(migration_dsn, app_dsn)
    print("Stage 07 application role grants ready")


if __name__ == "__main__":
    main()
