"""Native XGBoost serialization only; run without importing Torch or training code."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import xgboost as xgb

from graphrisk.graph.coverage_resources import CoverageMonitor


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(2**20), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    if args.destination.exists() or args.destination.suffix != '.ubj':
        raise ValueError('new binary destination required; original weights are never overwritten')
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    # One-time JSON parsing is isolated, with an explicit larger cap than serving.
    # No data rows, labels, GPU, model fitting, or generation are involved.
    with CoverageMonitor(args.destination.with_suffix('.resources.json'), maximum_rss_mib=2560, maximum_seconds=120) as monitor:
        source_sha = sha(args.source)
        model = xgb.Booster()
        model.load_model(args.source)
        monitor.require()
        model.save_model(args.destination)
        canonical_sha = hashlib.sha256(model.save_raw(raw_format='json')).hexdigest()
        monitor.require()
        proof = dict(scope='native_format_conversion_only_not_training', source=str(args.source),
            source_sha256=source_sha, destination=str(args.destination), destination_sha256=sha(args.destination),
            canonical_json_sha256=canonical_sha, seconds=time.perf_counter()-started,
            new_fits=0, label_reads=0, paid_calls=0)
    args.destination.with_suffix('.conversion.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
    print(json.dumps(proof), flush=True)


if __name__ == '__main__':
    main()
