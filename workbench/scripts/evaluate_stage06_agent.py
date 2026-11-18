"""Run the frozen 50-case Stage 06 fake suite and optional pinned local samples."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from graphrisk.demo.stage06_evaluation import evaluate_stage06


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", help="also run the three fixed local Ollama samples")
    parser.add_argument("--output", type=Path, help="output JSON; defaults to a timestamped ignored path")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="graphrisk-stage06-eval-") as directory:
        result = evaluate_stage06(repo, Path(directory) / "index.json", include_real=args.real)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = args.output or repo / "reports/generated/stage06" / f"agent-eval-v1-{timestamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    summary = result["fake"]["summary"]
    print(f"fake={summary['passed']}/{summary['count']} "
          f"real_samples={len(result['real_local']['samples'])} output={output}")
    if summary["passed"] != summary["count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
