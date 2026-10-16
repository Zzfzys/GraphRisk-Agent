"""Explicit coverage build; --check never predicts, fits or calls an LLM."""
import argparse
from pathlib import Path
import json

from graphrisk.graph.coverage_build import execute,check


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path('E:/GraphRisk-Agent'))
    parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--parent',type=Path,default=Path('E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261010-confirmation-v1'))
    parser.add_argument('--reuse-prepared',type=Path,help='Reuse a hash-verified prediction-only cache from a preserved interrupted run')
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--build',metavar='UNIQUE_NAME')
    group.add_argument('--check',type=Path,metavar='REPORT')
    args=parser.parse_args()
    value=execute(args.source,args.root,args.parent,args.build,args.reuse_prepared) if args.build else check(args.root,args.check)
    print(json.dumps(value),flush=True)


if __name__=='__main__':main()
