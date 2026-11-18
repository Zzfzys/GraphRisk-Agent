"""Run Stage 05 evidence graph and source-bound route-5 report generation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.reporting import FakeReportProvider, OllamaReportProvider, Stage05ReportWorkflow
from run_stage05_graph import build_workflow


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a source-bound Stage 05 report")
    parser.add_argument("transaction_id")
    parser.add_argument("--policy-as-of", required=True)
    parser.add_argument("--relation-limit", type=int, default=3)
    parser.add_argument("--config", default="configs/stage05.yaml")
    parser.add_argument("--provider", choices=("fake", "local_http"), default="local_http")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = load_stage05_config(args.config)
    graph = build_workflow(config).run({
        "transaction_id": args.transaction_id, "dataset_source": config.dataset_source,
        "policy_as_of": args.policy_as_of, "relation_limit": args.relation_limit,
    })
    provider = FakeReportProvider() if args.provider == "fake" else OllamaReportProvider(config.generation)
    result = Stage05ReportWorkflow(provider).run(graph)
    safe_id = hashlib.sha256(args.transaction_id.encode("utf-8")).hexdigest()[:16]
    output = args.output or config.resolve_path(config.paths.run_dir) / f"route5-{safe_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"graph_run": graph.model_dump(mode="json"),
                                  "report_run": result.model_dump(mode="json")},
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"graph={graph.status} report={result.status} issue={result.issue_code} output={output}")


if __name__ == "__main__":
    main()
