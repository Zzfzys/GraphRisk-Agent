"""Default-only read-only 5B acceptance check. No training/generation switches."""
import argparse
import json
from pathlib import Path

from graphrisk.graph.stage09_acceptance import check_final
from graphrisk.graph.stage09_confirmation_execution import check as confirmation_check
from check_stage09_coverage import verify as coverage_check


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('E:/GraphRisk-Agent'))
    parser.add_argument('--report',type=Path)
    args=parser.parse_args();root=args.root.resolve()
    report=args.report or root/'reports/generated/stage09/route5b/20261015-acceptance-v1'
    result=check_final(root,report)
    result['confirmation']=confirmation_check(root,root/'reports/generated/stage09/route5b/20261010-confirmation-v1')['status']
    result['coverage_rows']=coverage_check(root)['historical_rows']
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
