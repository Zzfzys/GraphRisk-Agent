"""Prepare, execute or check the preregistered five conditional experiments."""
import argparse
import json
from pathlib import Path
from graphrisk.graph.stage09_conditional_execution import prepare, run, check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-root", type=Path, default=Path("E:/GraphRisk-Agent"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare")
    group.add_argument("--run", type=Path)
    group.add_argument("--check", type=Path)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--recovery-registration", type=Path)
    args = parser.parse_args()
    if args.prepare:
        if not args.registration or not args.parent:
            parser.error("prepare requires frozen --registration and completed --parent")
        result = prepare(Path(__file__).resolve().parents[1], args.execution_root, args.registration, args.parent, args.prepare)
    else:
        result = run(args.execution_root, args.run, args.recovery_registration) if args.run else check(args.execution_root, args.check)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
