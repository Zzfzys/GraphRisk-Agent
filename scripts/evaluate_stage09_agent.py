"""Prepare frozen cases or run the fixed local Agent; never invokes hosted APIs."""
from argparse import ArgumentParser
from pathlib import Path
from graphrisk.demo.stage09_evaluation import prepare, require_frozen, run, safe_output, summarize, write_json

def main():
    parser = ArgumentParser()
    parser.add_argument("mode",choices=["prepare","run","check"])
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--route0-freeze",type=Path,
        default=Path("reports/generated/stage09/route0/20261007T093634Z/frozen-inputs.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = safe_output(root,args.output_dir)
    if args.mode == "prepare":
        prepare(root,output,args.route0_freeze.resolve())
    elif args.mode == "run":
        run(root,output)
    else:
        manifest = require_frozen(root,output)
        summary = summarize(output,manifest)
        write_json(output/"summary.json",summary)
        print(summary)

if __name__ == "__main__":
    main()

