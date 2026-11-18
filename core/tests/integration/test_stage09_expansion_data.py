"""Independent native graph reference, tie boundaries and label isolation."""
from datetime import UTC,datetime,timedelta
from dataclasses import asdict

import duckdb
import numpy as np
import pandas as pd
import pytest
import torch
from torch_geometric.data import Batch

from graphrisk.graph.build_artifacts import GRAPH_COLUMNS
from graphrisk.graph.schema import GraphTransaction,GraphFeatureEncoder,NUMERIC_FEATURES
from graphrisk.graph.temporal_index import TemporalEntityIndex
from graphrisk.graph.stage09_expansion_data import project_source,fit_encoders,build_scale_caches,encode_graph,prefix_count,BoundedPackedGraphs
from graphrisk.graph.stage09_packed import PackedGraphs,verify_cache


def fixture(root):
    rows = []
    for i in range(16):
        seconds = i if i < 12 else 12+(i-12)//2
        row = {"transaction_id":f"t{i:02d}","transaction_time":datetime(2000,1,1,tzinfo=UTC)+timedelta(seconds=seconds),
               "card_id":f"c{i%2}","device_id":"unknown-device-"+str(i) if i==11 else "d",
               "email_domain":"email","address_id":"a","product_code":["C","H","R","S","W"][i%5]}
        row.update(dict(zip(NUMERIC_FEATURES,(float(i+1),0.,1.,2.,None,None,None,0.,1.))))
        row["is_fraud"] = 987 if i>=14 else i%2
        rows.append(row)
    folder = root / "data/interim/ieee-cis"
    folder.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(folder / "features_all.parquet",index=False)
    target = pd.DataFrame({"transaction_id":[r["transaction_id"] for r in rows],"source_transaction_dt_seconds":[int((r["transaction_time"]-datetime(2000,1,1,tzinfo=UTC)).total_seconds()) for r in rows],"is_fraud":[r["is_fraud"] for r in rows]})
    target.to_parquet(folder / "transactions.parquet",index=False)
    return rows,target


def test_fresh_scale_cache_matches_native_preserves_ties_and_excludes_future_labels(tmp_path):
    rows,targets = fixture(tmp_path)
    projected = tmp_path / "projected.parquet"
    with duckdb.connect() as db:
        project_source(db,tmp_path,projected,12)
        actual = db.execute("SELECT * FROM read_parquet(?)",[str(projected)]).fetchdf()
        assert list(actual.columns) == list(GRAPH_COLUMNS)
        assert not {"is_fraud","identity_key"} & set(actual.columns)
        assert actual.transaction_id.tolist() == targets.transaction_id.tolist()[:14]
        encoders,counts = fit_encoders(db,projected,targets.iloc[:10],[5,8,"full_allowed_train"])
    assert counts == {"5":5,"8":8,"full_allowed_train":10}
    ref_rows = [GraphTransaction.from_feature_row({k:v for k,v in r.items() if k != "is_fraud"}) for r in rows[:14]]
    reference = TemporalEntityIndex(ref_rows)
    for name,count in counts.items():
        expected = GraphFeatureEncoder.fit(ref_rows,train_transaction_ids=targets.transaction_id.tolist()[:count])
        assert asdict(encoders[name]) == asdict(expected)
    folder = tmp_path / "cache"
    build_scale_caches(projected,folder,targets.iloc[:10],targets.iloc[10:14],encoders,counts)
    for name,count in counts.items():
        manifest = verify_cache(folder / name)
        assert manifest["labels_in_cache"] is False
        assert manifest["statistics"]["cap5-full-train"]["time_violations"] == 0
        assert len(manifest["target_ids"]["train"]) == count
        packed = PackedGraphs(folder / name,manifest,5,"validation","full")
        actual = packed.batch([3,2,1,0])
        bounded = BoundedPackedGraphs(folder / name,manifest,5,"validation","full")
        owned = bounded.batch([3,2,1,0])
        assert bounded.packed.x._mmap.closed and bounded.packed.edges._mmap.closed
        # A second mapping/close cannot invalidate the first owned PyG tensors.
        bounded.batch([0])
        for field in ("x","edge_index","batch","root_index"):
            torch.testing.assert_close(getattr(owned,field),getattr(actual,field),rtol=0,atol=0)
        with pytest.raises(ValueError,match="positions"):
            bounded.batch([999])
        assert bounded.packed.x._mmap.closed and bounded.packed.edges._mmap.closed
        graphs = [reference.build(key,max_history_per_entity=5) for key in reversed(targets.transaction_id.tolist()[10:14])]
        native = Batch.from_data_list([g.to_pyg(encoders[name]) for g in graphs])
        for field in ("x","edge_index","batch","root_index"):
            torch.testing.assert_close(getattr(actual,field),getattr(native,field),rtol=0,atol=1e-6)
        for graph in graphs:
            expected = np.asarray([encoders[name].transform_node(n) for n in graph.nodes],dtype=np.float32)
            np.testing.assert_allclose(encode_graph(graph,encoders[name]),expected,rtol=0,atol=1e-6)
        assert actual.y is None
    # Future rows with invalid labels cannot change any projected graph bytes.
    rows[-1]["transaction_amount"] = 999999.
    pd.DataFrame(rows).to_parquet(tmp_path / "data/interim/ieee-cis/features_all.parquet",index=False)
    with duckdb.connect() as db:
        project_source(db,tmp_path,tmp_path / "projected-2.parquet",12)
    pd.testing.assert_frame_equal(pd.read_parquet(projected),pd.read_parquet(tmp_path / "projected-2.parquet"))
    bad = folder / "5/cap5-full-train/x.f32"
    bad.write_bytes(bad.read_bytes()+b"corrupt")
    with pytest.raises(ValueError,match="bytes changed"):
        verify_cache(folder / "5")


def test_scale_prefix_does_not_split_equal_time_or_accept_shuffle():
    frame = pd.DataFrame({"transaction_id":["a","b","c","d"],"source_transaction_dt_seconds":[0,1,1,2]})
    assert prefix_count(frame,2) == 1
    assert prefix_count(frame,999) == 4
    assert prefix_count(frame,"full_allowed_train") == 4
    with pytest.raises(ValueError,match="chronological"):
        prefix_count(frame.iloc[::-1],2)
    with pytest.raises(ValueError,match="positive"):
        prefix_count(frame,True)
