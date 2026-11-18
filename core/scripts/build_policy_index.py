"""Build a reproducible Stage 05 policy index from validated source documents."""

from __future__ import annotations

import argparse

from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.policy_corpus import load_policy_corpus
from graphrisk.agent.policy_retrieval import OllamaEmbeddingProvider, build_policy_index, write_policy_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Stage 05 policy index")
    parser.add_argument("--config", default="configs/stage05.yaml")
    args = parser.parse_args()
    config = load_stage05_config(args.config)
    corpus = load_policy_corpus(config.resolve_path(config.paths.policy_source_dir))
    retrieval = config.retrieval
    provider = None
    if retrieval.backend in {"local_embedding", "pgvector"}:
        provider = OllamaEmbeddingProvider(
            retrieval.embedding_model_id, retrieval.embedding_model_revision,
            base_url=retrieval.embedding_endpoint,
        )
    index = build_policy_index(corpus, retrieval.index_backend, provider)
    output = config.resolve_path(config.paths.policy_artifact_dir) / f"policy-index-{retrieval.index_backend}.json"
    write_policy_index(index, output)
    print(f"backend={index.backend} chunks={len(index.chunks)} index_version={index.index_version} output={output}")


if __name__ == "__main__":
    main()
