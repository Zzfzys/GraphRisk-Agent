"""Freeze/check 5B using E dependencies/data and the calling worktree source."""
import argparse
import json
from pathlib import Path

from graphrisk.graph.stage09_enhancement_preflight import check_registration, register


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-root", type=Path, default=Path("E:/GraphRisk-Agent"))
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--name", help="new unique registration; never overwrite")
    choice.add_argument("--check", type=Path, help="existing registration directory, read-only")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    try:
        result = (check_registration(source, args.execution_root.resolve(), args.check) if args.check
                  else register(source, args.execution_root, args.name))
    except (OSError, ValueError, AssertionError) as error:
        print(json.dumps({"status": "failed", "reason": str(error)}, ensure_ascii=False))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
