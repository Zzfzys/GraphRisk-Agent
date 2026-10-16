
"""Local-only registered replay; check/analyze never generates or trains."""
from argparse import ArgumentParser
from pathlib import Path
import json

from graphrisk.demo.stage09_replay import prepare,run,require_registered,analyze,rollback

def safe_output(root,path):
    value=(root/path).resolve()
    parent=(root/"reports/generated/stage09/route5").resolve()
    if not value.is_relative_to(parent) or value==parent:
        raise ValueError("outputs must remain in an ignored Stage09 route5 run directory")
    return value

def main():
    parser=ArgumentParser()
    parser.add_argument("mode",choices=["prepare","run","check","rollback"])
    parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    directory=safe_output(root,args.output_dir)
    if args.mode=="prepare":prepare(root,directory)
    elif args.mode=="run":run(root,directory)
    elif args.mode=="rollback":
        value=rollback(root,directory)
        print(json.dumps({"passed":value["passed"],"cases":len(value["cases"]),"generation_calls":0}))
    else:print(json.dumps(analyze(root,directory),ensure_ascii=False))
if __name__=="__main__":main()

