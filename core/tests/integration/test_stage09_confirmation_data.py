from datetime import UTC, datetime, timedelta

import duckdb
import numpy as np
import pandas as pd
import pytest
from dataclasses import asdict

from graphrisk.agent.stage09_preflight import _content_hash
from graphrisk.demo.stage09_evaluation import read_json
from graphrisk.graph.schema import NUMERIC_FEATURES
from graphrisk.graph.stage09_conditional_data import ConditionalGraphs
from graphrisk.graph.stage09_confirmation_data import identities, prepare_fold_data, prepare_prediction_data
from graphrisk.graph.stage09_packed import verify_cache
from graphrisk.models.enhanced_execution import open_db


def setup_data(root):
    rows, wide, canonical = [], [], []
    for i in range(26):
        seconds = i if i < 24 else 24  # final two events are simultaneous
        key = f"t{i:02}"
        row = dict(transaction_id=key, transaction_time=datetime(2000,1,1,tzinfo=UTC)+timedelta(seconds=seconds),
            card_id="c",device_id="d",email_domain="e",address_id="a",product_code=["C","H","R","S","W"][i%5])
        row.update(dict(zip(NUMERIC_FEATURES,[float(i+1),0.,1.,2.,None,None,None,0.,1.])))
        row["is_fraud"] = 987 if i >= 14 else i % 2
        rows.append(row)
        wide.append(dict(transaction_id=key,source_transaction_dt_seconds=seconds,
                         TransactionAmt=float(i+1),DeviceInfo="old" if i<11 else "new",V1=float(i*i)))
        canonical.append(dict(transaction_id=key,source_transaction_dt_seconds=seconds,is_fraud=row["is_fraud"]))
    path = root/"data/interim/ieee-cis"
    path.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(path/"features_all.parquet",index=False)
    pd.DataFrame(canonical).to_parquet(path/"transactions.parquet",index=False)
    side = root/"wide.parquet"
    pd.DataFrame(wide).to_parquet(side,index=False)
    fields = [{"name":"TransactionAmt","dtype":"numeric","group":"core","source":"transaction"},
              {"name":"DeviceInfo","dtype":"categorical","group":"core","source":"identity"},
              {"name":"V1","dtype":"numeric","group":"source_as_received","source":"transaction"}]
    return side, fields, pd.DataFrame(canonical)


def window(frame, name, end):
    def segment(begin, finish):
        chosen = frame.iloc[begin:finish]
        return {"count":len(chosen),"ids_sha256":_content_hash({"ids":chosen.transaction_id.tolist()}),
                "min_relative_seconds":int(chosen.source_transaction_dt_seconds.min()),
                "max_relative_seconds":int(chosen.source_transaction_dt_seconds.max())}
    return {"name":name,"train":segment(0,end),"early_stop":segment(end,14),
            "calibration":segment(14,18),"outer":segment(18,22)}


def test_fold_refit_and_unread_outer_labels_with_strict_temporal_cache(tmp_path):
    side, fields, canonical = setup_data(tmp_path)
    db = open_db(tmp_path,side)
    first, second = tmp_path/"fold-1",tmp_path/"fold-2"
    first.mkdir()
    second.mkdir()
    try:
        a = prepare_fold_data(db,tmp_path,side,first,window(canonical,"fold-1",10),fields)
        b = prepare_fold_data(db,tmp_path,side,second,window(canonical,"fold-2",12),fields)
    finally:
        db.close()
    # Invalid labels in calibration/outer do not even enter preprocessing/graphs.
    assert "is_fraud" not in pd.read_parquet(first/"targets-calibration.parquet")
    assert "is_fraud" not in pd.read_parquet(first/"targets-outer.parquet")
    assert not a["calibration_outer_labels_projected"] and not b["labels_used_for_features"]
    p1,p2 = [read_json(path/"fold-1-core.json") for path in (first,second)]
    assert p1["fit_scope"]["count"] == 10 and p2["fit_scope"]["count"] == 12
    assert "DeviceInfo" in p1["dropped"] and p2["categorical"]["DeviceInfo"] == ["new","old"]
    n1,n2 = [read_json(path/"core-normalizer.json") for path in (first,second)]
    assert n1["train_rows"] == 10 and n2["train_rows"] == 12
    assert all(field["fit_scope"] == "fold-2-train-only" for field in read_json(second/"field-catalog.json"))
    assert n1["mean"][0] != n2["mean"][0] and a["input_dimension"] != b["input_dimension"]
    cache = verify_cache(first/"cache")
    assert all(stat["time_violations"] == 0 and stat["train_context_from_inner"] == 0 for stat in cache["statistics"].values())
    assert cache["statistics"]["train"]["historical_transactions"] > 0 and not cache["labels_in_cache"]
    reader = ConditionalGraphs(first/"cache",cache,"outer","full",core=first/"core-outer.npy")
    graph = reader.batch([3,0])
    nonroots = np.ones(len(graph.x),dtype=bool)
    nonroots[graph.root_index.numpy()] = False
    assert graph.y is None and graph.x[nonroots,28:].count_nonzero() == 0
    reader.close()


def test_historical_prediction_uses_saved_fold_stats_without_labels_or_refit(tmp_path):
    side,fields,canonical = setup_data(tmp_path)
    db = open_db(tmp_path,side)
    trained = tmp_path/"trained"
    trained.mkdir()
    output = tmp_path/"test"
    output.mkdir()
    spec = {"count":2,"ids_sha256":_content_hash({"ids":["t24","t25"]}),"min_relative_seconds":24,"max_relative_seconds":24}
    try:
        prepare_fold_data(db,tmp_path,side,trained,window(canonical,"fold-1",10),fields)
        before = (trained/"core-normalizer.json").read_bytes()
        value = prepare_prediction_data(db,tmp_path,side,output,trained,spec)
    finally:
        db.close()
    assert (trained/"core-normalizer.json").read_bytes() == before
    assert not value["test_labels_projected"] and "is_fraud" not in pd.read_parquet(output/"targets-test.parquet")
    cache = verify_cache(output/"cache")
    # The two targets at t=24 cannot include each other as historical context.
    assert cache["statistics"]["test"]["historical_transactions"] == 10
    assert np.isfinite(np.load(output/"core-test.npy",allow_pickle=False)).all()


def test_identity_projection_checks_complete_ordered_window(tmp_path):
    side,fields,canonical = setup_data(tmp_path)
    with duckdb.connect() as db:
        spec = window(canonical,"fold-1",10)["outer"]
        frame = identities(db,tmp_path/"data/interim/ieee-cis/transactions.parquet",spec)
        assert list(frame.columns) == ["transaction_id","source_transaction_dt_seconds"]
        with pytest.raises(ValueError,match="window mismatch"):
            identities(db,tmp_path/"data/interim/ieee-cis/transactions.parquet",{**spec,"count":999})
