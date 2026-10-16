"""Freeze or check Stage09 inputs without model generation or training."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from graphrisk.agent.stage09_preflight import build_freeze, check_freeze


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default="configs/stage09_experiment.json", type=Path)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--output", type=Path, help="new ignored Stage09 JSON; never overwrite")
    choice.add_argument("--check", type=Path, help="check an existing freeze against current files")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.check:
        result = check_freeze(root, json.loads(args.check.read_text(encoding="utf-8")))
        print(json.dumps(result, ensure_ascii=False))
        if result["status"] != "passed":
            raise SystemExit(1)
        return
    output = args.output.resolve()
    allowed = (root / "reports/generated/stage09").resolve()
    if not output.is_relative_to(allowed) or output.suffix != ".json":
        parser.error("--output must be a JSON file below reports/generated/stage09")
    frozen = build_freeze(root, args.contract)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(frozen, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({"status": "frozen", "content_sha256": frozen["content_sha256"],
                      "checked_files": len(frozen["artifacts"]),
                      "generation_calls": 0, "training_runs": 0, "paid_calls": 0}))


if __name__ == "__main__":
    main()
