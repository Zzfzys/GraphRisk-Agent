"""Prepare immutable A/B reading artifacts locally; no live/model option exists."""
from __future__ import annotations

import argparse
from pathlib import Path

from graphrisk.agent.upgrade_contract import canonical
from graphrisk.serving.daily_reference_comparison import prepare_comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accepted-root', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--corpus-version', required=True)
    args = parser.parse_args()
    value = prepare_comparison(args.accepted_root, args.output_root, profile_path=args.profile,
        manifest_path=args.manifest, corpus_version=args.corpus_version)
    print(canonical({'status': 'prepared_not_sent', 'model_calls': 0,
        'paired_manifest_path': str(Path(value['output_root']) / 'paired-manifest.json'),
        'denominators': value['denominators']}))


if __name__ == '__main__':
    main()
