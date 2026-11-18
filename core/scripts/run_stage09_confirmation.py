"""Freeze, prepare, train, calibrate and confirm; --check only validates artifacts."""
import argparse
import json
from pathlib import Path

from graphrisk.demo.stage09_evaluation import exclusive
from graphrisk.graph import stage09_confirmation_execution as workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-root", type=Path, default=Path("E:/GraphRisk-Agent"))
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--conditional", type=Path)
    parser.add_argument("--tabular", type=Path)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--freeze", metavar="UNIQUE_NAME")
    choice.add_argument("--prepare", type=Path)
    choice.add_argument("--train", type=Path)
    choice.add_argument("--calibrate", type=Path)
    choice.add_argument("--evaluate", type=Path)
    choice.add_argument("--historical-test", type=Path)
    choice.add_argument("--complete", type=Path)
    choice.add_argument("--check", type=Path)
    args = parser.parse_args()
    source, root = Path(__file__).resolve().parents[1], args.execution_root.resolve()
    if args.freeze:
        if not all((args.registration, args.conditional, args.tabular)):
            parser.error("freeze requires source registration and both parent experiments")
        result = workflow.freeze(source, root, args.registration, args.conditional, args.tabular, args.freeze)
        result = {"status": "frozen", "decision_sha256": result["content_sha256"]}
    else:
        for key, function in (("prepare", workflow.prepare), ("train", workflow.train), ("calibrate", workflow.calibrate),
            ("evaluate", workflow.evaluate), ("historical_test", workflow.historical_test), ("complete", workflow.complete), ("check", workflow.check)):
            report = getattr(args, key)
            if report is None:
                continue
            if key == "check":
                result = function(root, report.resolve())
            else:
                # Native exclusive create protects the one shared fit/label lane.
                with exclusive(report.resolve() / "execution.lock"):
                    result = function(root, report.resolve())
            break
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
