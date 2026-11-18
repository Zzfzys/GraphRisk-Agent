"""Build/check independent 5B raw features. No training or model API calls."""
import argparse
import json
from pathlib import Path

from graphrisk.features.wide_execution import check_outputs, execute


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-root", type=Path, default=Path("E:/GraphRisk-Agent"))
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--name", help="new feature evidence name, never overwrite")
    choice.add_argument("--check", type=Path, help="read-only check of an existing evidence directory")
    parser.add_argument("--registration", type=Path, help="fresh source freeze required for build")
    args = parser.parse_args()
    if args.name and args.registration is None:
        parser.error("--registration required with --name")
    try:
        result = check_outputs(args.execution_root, args.check) if args.check else execute(
            Path(__file__).resolve().parents[1], args.execution_root, args.registration, args.name)
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, (ValueError, TimeoutError)) else "see preserved private failure evidence"}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
