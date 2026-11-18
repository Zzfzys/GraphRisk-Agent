"""Prepare/run/check current GraphSAGE scale and finite-search experiments."""
import argparse
import json
from pathlib import Path

from graphrisk.graph.stage09_expansion_execution import prepare,run,check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-root",type=Path,default=Path("E:/GraphRisk-Agent"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare")
    mode.add_argument("--run",type=Path)
    mode.add_argument("--check",type=Path)
    parser.add_argument("--registration",type=Path)
    parser.add_argument("--tabular",type=Path)
    parser.add_argument("--recovery-registration",type=Path,help="new frozen runtime-only 150k memory recovery; uses failure quota")
    parser.add_argument("--resource-recovery-registration",type=Path,help="new frozen same-input g2 RAM-stop recovery; uses failure quota")
    args = parser.parse_args()
    if args.prepare and (args.registration is None or args.tabular is None):
        parser.error("prepare requires frozen registration and completed tabular directory")
    try:
        result = prepare(Path(__file__).resolve().parents[1],args.execution_root,args.registration,args.tabular,args.prepare) if args.prepare else run(args.execution_root,args.run,args.recovery_registration,args.resource_recovery_registration) if args.run else check(args.execution_root,args.check)
    except Exception as error:
        print(json.dumps({"status":"failed","error_type":type(error).__name__,"reason":str(error)},ensure_ascii=False))
        raise SystemExit(1) from None
    print(json.dumps(result,ensure_ascii=False))


if __name__ == "__main__":
    main()
