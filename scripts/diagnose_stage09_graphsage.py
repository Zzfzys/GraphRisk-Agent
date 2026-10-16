"""Prepare, run or sealed final review for Stage09 GraphSAGE diagnosis."""
from argparse import ArgumentParser
from pathlib import Path
from graphrisk.demo.stage09_evaluation import exclusive
from graphrisk.graph.stage09_diagnosis import prepare, run, final_review

def main():
    parser = ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "run", "final-review"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reuse-cache", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = args.output_dir.resolve()
    if not directory.is_relative_to(root / "reports/generated/stage09/route4"):
        raise ValueError("outputs must remain inside Stage09 route4")
    with exclusive(root / "reports/generated/stage09/gnn-execution.lock"):
        if args.mode == "prepare":
            prepare(root, directory, args.reuse_cache)
        else:
            if args.reuse_cache:
                raise ValueError("--reuse-cache is registered during prepare only")
            {"run": run, "final-review": final_review}[args.mode](root, directory)

if __name__ == "__main__":
    main()

