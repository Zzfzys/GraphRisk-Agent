"""Import a pinned local dense policy index into the Stage 07 vector tables."""

from __future__ import annotations

import argparse
import os

from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.pgvector_retrieval import import_policy_vectors
from graphrisk.agent.policy_retrieval import load_policy_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Import validated policy embeddings into pgvector")
    parser.add_argument("--config", default="configs/stage07_pgvector.yaml")
    args = parser.parse_args()
    config = load_stage05_config(args.config)
    if config.retrieval.backend != "pgvector":
        raise ValueError("import requires retrieval.backend: pgvector")
    path = config.resolve_path(config.paths.policy_artifact_dir) / "policy-index-local_embedding.json"
    index = load_policy_index(path)
    if (index.model_id != config.retrieval.embedding_model_id
            or index.model_revision != config.retrieval.embedding_model_revision):
        raise ValueError("configured embedding model differs from dense index")
    dsn = os.getenv("GRAPHRISK_STAGE07_MIGRATION_DSN") or os.getenv("GRAPHRISK_STAGE07_DSN", "")
    count = import_policy_vectors(dsn, index)
    print(f"pgvector chunks={count} index_version={index.index_version}")


if __name__ == "__main__":
    main()
