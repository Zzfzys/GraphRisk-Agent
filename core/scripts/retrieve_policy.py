"""Query a local Stage 05 policy index with an explicit policy date."""

from __future__ import annotations

import argparse
import os

from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.policy_retrieval import OllamaEmbeddingProvider, PolicyRetriever
from graphrisk.agent.pgvector_retrieval import PgvectorPolicyRetriever


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieve policy evidence")
    parser.add_argument("query")
    parser.add_argument("--policy-as-of", required=True)
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--config", default="configs/stage05.yaml")
    args = parser.parse_args()
    config = load_stage05_config(args.config)
    retrieval = config.retrieval
    provider = None
    if retrieval.backend in {"local_embedding", "pgvector"}:
        provider = OllamaEmbeddingProvider(
            retrieval.embedding_model_id, retrieval.embedding_model_revision,
            base_url=retrieval.embedding_endpoint,
        )
    path = config.resolve_path(config.paths.policy_artifact_dir) / f"policy-index-{retrieval.index_backend}.json"
    top_k = retrieval.top_k if args.top_k is None else args.top_k
    retriever = (PgvectorPolicyRetriever(path, provider, os.getenv("GRAPHRISK_STAGE07_DSN", ""))
                 if retrieval.backend == "pgvector" else PolicyRetriever(path, provider))
    result = retriever.retrieve_policy(args.query, args.policy_as_of, top_k)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
