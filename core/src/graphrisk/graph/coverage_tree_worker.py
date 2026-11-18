"""Isolated frozen tree inference. Deliberately imports no Torch/training module."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import xgboost as xgb

from graphrisk.features.wide_preprocessing import WidePreprocessor
from graphrisk.graph.coverage_resources import CoverageMonitor


def run(job):
    out = Path(job['output'])
    with CoverageMonitor(out.with_suffix('.resources.json'), maximum_seconds=300) as monitor:
        model = xgb.Booster()
        model.load_model(job['model'])
        model.set_param({'nthread':1})
        canonical = hashlib.sha256(model.save_raw(raw_format='json')).hexdigest()
        if canonical != job['source_sha256']:
            raise ValueError('native binary weights differ from sealed JSON weights')
        state = WidePreprocessor.load(Path(job['preprocessor']))
        columns = state.state['input_columns']
        if 'features' in job:
            frame = pd.DataFrame(job['features'], columns=columns)
            values = model.inplace_predict(state.transform(frame))
            monitor.require()
        else:
            target = pd.read_parquet(job['targets'], columns=['transaction_id','source_transaction_dt_seconds'])
            pieces, offset = [], 0
            begin,end=int(target.source_transaction_dt_seconds.iloc[0]),int(target.source_transaction_dt_seconds.iloc[-1])
            # The sealed side table is ordered by time/ID. Arrow batches avoid
            # materializing a 450-column external SQL sort under the 128MiB cap.
            for batch in pq.ParquetFile(job['side']).iter_batches(batch_size=1024,
                    columns=['transaction_id','source_transaction_dt_seconds',*columns], use_threads=False):
                raw=batch.to_pandas()
                raw=raw.loc[raw.source_transaction_dt_seconds.between(begin,end)]
                if len(raw):
                    monitor.require()
                    if raw.transaction_id.tolist() != target.transaction_id.iloc[offset:offset+len(raw)].tolist():
                        raise ValueError('tree prediction target order mismatch')
                    pieces.append(model.inplace_predict(state.transform(raw[columns])))
                    offset += len(raw)
                if offset == len(target):break
            if offset != len(target):
                raise ValueError('tree prediction incomplete')
            values = np.concatenate(pieces)
        if not np.isfinite(values).all() or ((values<0)|(values>1)).any():
            raise ValueError('invalid tree score')
        monitor.require()
        np.save(out, values, allow_pickle=False)
    out.with_suffix('.proof.json').write_text(json.dumps(dict(rows=len(values), canonical_json_sha256=canonical,
        torch_imported='torch' in sys.modules, new_fits=0, label_reads=0)), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('job', type=Path)
    run(json.loads(parser.parse_args().job.read_text(encoding='utf-8')))
