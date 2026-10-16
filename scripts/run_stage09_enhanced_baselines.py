"""Prepare, run/resume or read-only check the frozen fold-1 tabular baselines."""
import argparse
import json
from pathlib import Path

from graphrisk.models.enhanced_execution import check, prepare, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-root", type=Path, default=Path("E:/GraphRisk-Agent"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", help="new unique directory name")
    mode.add_argument("--run", type=Path, help="resume unattempted tasks; failed fits stop")
    mode.add_argument("--check", type=Path, help="read-only completed-run verification")
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--features", type=Path)
    parser.add_argument("--recovery-registration", type=Path, help="new registered runtime-only repair for pre-model x1 memory failure")
    parser.add_argument("--interruption-registration", type=Path, help="new registered recovery for Windows sleep interrupted x3; consumes failure quota")
    args = parser.parse_args()
    if args.prepare and (args.registration is None or args.features is None):
        parser.error("prepare requires registration and features")
    try:
        result = prepare(Path(__file__).resolve().parents[1], args.execution_root, args.registration, args.features, args.prepare) if args.prepare else run(args.execution_root, args.run, args.recovery_registration, args.interruption_registration) if args.run else check(args.execution_root, args.check)
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__, "reason": str(error)}, ensure_ascii=False))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
