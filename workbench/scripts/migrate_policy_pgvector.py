"""Create Stage 07 policy-vector tables using the migration role."""

from __future__ import annotations

import os

from graphrisk.agent.pgvector_retrieval import migrate_policy_vectors


def main() -> None:
    dsn = os.getenv("GRAPHRISK_STAGE07_MIGRATION_DSN") or os.getenv("GRAPHRISK_STAGE07_DSN", "")
    migrate_policy_vectors(dsn)
    print("Stage 07 pgvector policy schema ready")


if __name__ == "__main__":
    main()
