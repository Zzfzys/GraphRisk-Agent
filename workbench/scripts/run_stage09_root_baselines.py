"""Prepare/freeze or execute once the validation-only Stage09 root controls."""
from argparse import ArgumentParser
from pathlib import Path
from graphrisk.graph.stage09_baselines import prepare, train_controls

def main():
    parser = ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--train", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    function = prepare if args.prepare_only else train_controls
    function(root, args.output_dir.resolve())

if __name__ == "__main__":
    main()

