"""Run the bounded Stage 05 route-4 skeleton with existing local artifacts."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from graphrisk.agent.components import build_components
from graphrisk.agent.config import Stage05Config, load_stage05_config
from graphrisk.agent.workflow import Stage05GraphWorkflow


def build_workflow(config: Stage05Config) -> Stage05GraphWorkflow:
    investigation, retriever = build_components(config)
    return Stage05GraphWorkflow(
        investigation, retriever, dataset_source=config.dataset_source,
        limits=config.limits, top_k=config.retrieval.top_k,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run bounded Stage 05 graph (fake draft only)")
    parser.add_argument("transaction_id")
    parser.add_argument("--policy-as-of", required=True)
    parser.add_argument("--relation-limit", type=int, default=3)
    parser.add_argument("--config", default="configs/stage05.yaml")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = load_stage05_config(args.config)
    workflow = build_workflow(config)
    result = workflow.run({
        "transaction_id": args.transaction_id, "dataset_source": config.dataset_source,
        "policy_as_of": args.policy_as_of, "relation_limit": args.relation_limit,
    })
    safe_id = hashlib.sha256(args.transaction_id.encode("utf-8")).hexdigest()[:16]
    output = args.output or config.resolve_path(config.paths.run_dir) / f"route4-{safe_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"status={result.status} steps={result.steps} policy_queries={result.policy_queries} "
          f"model_calls={result.model_calls} output={output}")


if __name__ == "__main__":
    main()
