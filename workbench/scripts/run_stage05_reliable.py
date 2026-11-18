"""Run the bounded Stage 05 investigation and source-bound report pipeline."""

from __future__ import annotations

import argparse
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from graphrisk.agent.components import build_components
from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.reliability import Stage05ReliableRunner
from graphrisk.agent.reporting import FakeReportProvider, OllamaReportProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Stage 05 with retry, deadline and cost gates")
    parser.add_argument("transaction_id")
    parser.add_argument("--policy-as-of", required=True)
    parser.add_argument("--relation-limit", type=int, default=3)
    parser.add_argument("--config", default="configs/stage05.yaml")
    parser.add_argument("--provider", choices=("fake", "local_http"), default="local_http")
    parser.add_argument("--per-attempt-upper-cny", type=float, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = load_stage05_config(args.config)
    investigation, retriever = build_components(config)
    provider = FakeReportProvider() if args.provider == "fake" else OllamaReportProvider(config.generation)
    runner = Stage05ReliableRunner(
        investigation, retriever, provider, dataset_source=config.dataset_source,
        limits=config.limits, top_k=config.retrieval.top_k,
        per_attempt_upper_cny=args.per_attempt_upper_cny,
    )
    result = runner.run({
        "transaction_id": args.transaction_id, "dataset_source": config.dataset_source,
        "policy_as_of": args.policy_as_of, "relation_limit": args.relation_limit,
    })
    safe_id = hashlib.sha256(args.transaction_id.encode("utf-8")).hexdigest()[:16]
    run_time = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = args.output or config.resolve_path(config.paths.run_dir) / f"route7-{safe_id}-{run_time}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(result.model_dump_json(indent=2) + "\n")
    print(f"status={result.status} retrievals={result.audit.policy_attempts} "
          f"generations={result.audit.generation_attempts} retries={result.audit.retries} "
          f"elapsed_ms={result.audit.elapsed_ms} output={output}")


if __name__ == "__main__":
    main()
