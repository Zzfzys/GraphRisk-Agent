"""Evaluate Stage 05 retrieval against pinned, source-grounded policy labels."""

from __future__ import annotations

import argparse
import json
import os

from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.policy_evaluation import evaluate_suite, load_evaluation_suite, write_evaluation_report
from graphrisk.agent.policy_retrieval import OllamaEmbeddingProvider, PolicyRetriever, load_policy_index
from graphrisk.agent.pgvector_retrieval import PgvectorPolicyRetriever


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Stage 05 policy retrieval")
    parser.add_argument("--config", default="configs/stage05.yaml")
    parser.add_argument("--suite", default="data/evaluation/stage05_policy_queries_v1.yaml")
    parser.add_argument("--split", required=True, choices=("development", "frozen"))
    args = parser.parse_args()

    config = load_stage05_config(args.config)
    retrieval = config.retrieval
    index_path = config.resolve_path(config.paths.policy_artifact_dir) / f"policy-index-{retrieval.index_backend}.json"
    index = load_policy_index(index_path)
    suite, suite_sha256 = load_evaluation_suite(args.suite, index)
    provider = None
    if retrieval.backend in {"local_embedding", "pgvector"}:
        provider = OllamaEmbeddingProvider(
            retrieval.embedding_model_id, retrieval.embedding_model_revision,
            base_url=retrieval.embedding_endpoint,
        )
    retriever = (PgvectorPolicyRetriever(index_path, provider, os.getenv("GRAPHRISK_STAGE07_DSN", ""))
                 if retrieval.backend == "pgvector" else PolicyRetriever(index_path, provider))
    report = evaluate_suite(suite, suite_sha256, index, retriever, args.split)
    report["backend"] = retrieval.backend
    target = config.resolve_path(config.paths.run_dir) / f"policy-eval-{retrieval.backend}-{args.split}.json"
    write_evaluation_report(report, target)
    print(json.dumps({"output": str(target), "suite_sha256": suite_sha256, "backend": retrieval.backend,
                      "split": args.split, "summary": report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
