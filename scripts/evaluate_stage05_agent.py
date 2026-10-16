"""Evaluate the frozen Stage 05 Agent case suite without network or model calls."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from graphrisk.agent.agent_evaluation import evaluate_cases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("reports/generated/stage05-agent-eval-v1.json"))
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="graphrisk-agent-eval-") as temp:
        result = evaluate_cases(repo, Path(temp) / "policy-index.json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"passed={result['passed']}/{result['case_count']} branch_accuracy={result['branch_accuracy']:.3f} output={args.output}")
    if result["passed"] != result["case_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
