"""Audit Stage09 graph correctness without any full-dataset training or LLM call."""
from argparse import ArgumentParser
from pathlib import Path
from graphrisk.graph.stage09_audit import audit
def main():
    root=Path(__file__).resolve().parents[1]
    parser=ArgumentParser()
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--baseline-dir",type=Path,
        default=Path("reports/generated/stage09/route1/20261007-baseline-v1"))
    parser.add_argument("--before-source",type=Path,
        default=Path("reports/generated/stage09/route2/20261007-audit-v1/model-before-route2.py"))
    args=parser.parse_args()
    audit(root,args.output_dir.resolve(),args.baseline_dir.resolve(),args.before_source.resolve())
if __name__=="__main__":
    main()

