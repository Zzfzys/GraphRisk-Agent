"""Conditional inputs against independently built tiny temporal cache."""
from datetime import UTC, datetime, timedelta
import duckdb
import numpy as np
import pandas as pd
import pytest
import torch

from graphrisk.graph.schema import NUMERIC_FEATURES
from graphrisk.graph.stage09_expansion_data import project_source, fit_encoders, build_scale_caches
from graphrisk.graph.stage09_packed import verify_cache
from graphrisk.graph.stage09_conditional_data import ConditionalGraphs, prepare_rewired
from graphrisk.graph.model import GraphSAGERiskModel


def cache(root):
    rows = []
    for i in range(12):
        row = {"transaction_id": f"t{i:02}", "transaction_time": datetime(2000, 1, 1, tzinfo=UTC)+timedelta(seconds=i),
            "card_id": "c", "device_id": "d", "email_domain": "e", "address_id": "a", "product_code": ["C", "H", "R", "S", "W"][i % 5]}
        row.update(dict(zip(NUMERIC_FEATURES, [float(i+1), 0., 1., 2., None, None, None, 0., 1.])))
        rows.append(row)
    folder = root / "data/interim/ieee-cis"
    folder.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(folder / "features_all.parquet", index=False)
    targets = pd.DataFrame({"transaction_id": [r["transaction_id"] for r in rows], "source_transaction_dt_seconds": range(12), "is_fraud": [i % 2 for i in range(12)]})
    targets.to_parquet(folder / "transactions.parquet", index=False)
    projected = root / "projected.parquet"
    with duckdb.connect() as db:
        project_source(db, root, projected, 11)
        encoders, counts = fit_encoders(db, projected, targets.iloc[:8], ["full_allowed_train"])
    build_scale_caches(projected, root / "cache", targets.iloc[:8], targets.iloc[8:], encoders, counts)
    result = root / "cache/full_allowed_train"
    return result, verify_cache(result)


def test_controls_keep_root_features_and_owned_tensors_and_detect_noop_rewire(tmp_path):
    folder, manifest = cache(tmp_path)
    full = ConditionalGraphs(folder, manifest, "validation", "full").batch([3, 0])
    none = ConditionalGraphs(folder, manifest, "validation", "no_edges").batch([3, 0])
    numeric = ConditionalGraphs(folder, manifest, "validation", "no_history_numeric").batch([3, 0])
    torch.testing.assert_close(none.x, full.x, rtol=0, atol=0)
    assert none.edge_index.shape == (2, 0)
    torch.testing.assert_close(numeric.x[numeric.root_index], full.x[full.root_index], rtol=0, atol=0)
    torch.testing.assert_close(numeric.x[:, 18:], full.x[:, 18:], rtol=0, atol=0)
    history = numeric.x[:, 23] == 1
    history[numeric.root_index] = False
    assert numeric.x[history, :18].count_nonzero() == 0 and full.x[history, :18].count_nonzero() > 0
    model = GraphSAGERiskModel(28, 8, 0.).eval()
    root = none.x[none.root_index]
    hidden = (model.conv1.lin_r(root)+model.conv1.lin_l(torch.zeros_like(root))).relu()
    expected = model.classifier((model.conv2.lin_r(hidden)+model.conv2.lin_l(torch.zeros_like(hidden))).relu()).squeeze(-1)
    torch.testing.assert_close(model(none), expected, rtol=1e-6, atol=1e-7)
    # An isolated root's prediction cannot depend on other retained nodes.
    changed = none.clone()
    nonroots = torch.ones(len(changed.x), dtype=torch.bool)
    nonroots[changed.root_index] = False
    changed.x[nonroots] = 999
    torch.testing.assert_close(model(none), model(changed), rtol=0, atol=0)
    before = (folder / "manifest.json").read_bytes()
    audit = prepare_rewired(folder, manifest, tmp_path / "rewired", 17)
    assert audit["validation"]["changed_graphs"] == 0 and audit["validation"]["degree_type_root_violations"] == 0
    transformed = ConditionalGraphs(folder, manifest, "validation", "rewired", tmp_path / "rewired/validation-edges.i64").batch([3, 0])
    for name in ("x", "edge_index", "root_index"):
        torch.testing.assert_close(getattr(transformed, name), getattr(full, name), rtol=0, atol=0)
    assert (folder / "manifest.json").read_bytes() == before


def test_enhanced_root_features_align_after_reordering_and_do_not_leak_to_neighbors(tmp_path):
    folder, manifest = cache(tmp_path)
    values = np.arange(12, dtype=np.float32).reshape(4, 3)
    path = tmp_path / "core.npy"
    np.save(path, values)
    reader = ConditionalGraphs(folder, manifest, "validation", "full", core=path)
    batch = reader.batch([3, 1])
    np.testing.assert_array_equal(batch.x[batch.root_index, 28:].numpy(), values[[3, 1]])
    nonroot = torch.ones(len(batch.x), dtype=torch.bool)
    nonroot[batch.root_index] = False
    assert batch.x[nonroot, 28:].count_nonzero() == 0 and reader.dimension == 31 and batch.y is None
    reader.batch([0])
    np.testing.assert_array_equal(batch.x[batch.root_index, 28:].numpy(), values[[3, 1]])
    reader.close()
    with pytest.raises(ValueError, match="closed"):
        reader.batch([0])
    path.unlink()
    np.save(path, np.ones((3, 3), dtype=np.float32))
    with pytest.raises(ValueError, match="count"):
        ConditionalGraphs(folder, manifest, "validation", "full", core=path)
    np.save(path, np.full((4, 3), np.nan, dtype=np.float32))
    bad = ConditionalGraphs(folder, manifest, "validation", "full", core=path)
    with pytest.raises(ValueError, match="nonfinite"):
        bad.batch([0])
    bad.close()


def test_fast_no_edges_and_single_allocation_match_original_fields_exactly(tmp_path):
    from graphrisk.graph.stage09_expansion_data import BoundedPackedGraphs
    folder, manifest = cache(tmp_path)
    core = np.arange(4*163, dtype=np.float32).reshape(4, 163)/19
    path = tmp_path / "core.npy"
    np.save(path, core)
    original = BoundedPackedGraphs(folder, manifest, 5, "validation", "no_edges")
    reader = ConditionalGraphs(folder, manifest, "validation", "no_edges", core=path)
    for positions in ([3, 0, 1], [1, 1], [0]):
        expected = original.batch(positions)
        appended = torch.zeros((len(expected.x), 163))
        appended[expected.root_index] = torch.from_numpy(core[positions])
        expected.x = torch.cat([expected.x, appended], 1)
        actual = reader.batch(positions)
        for name in ("x", "edge_index", "batch", "root_index"):
            torch.testing.assert_close(getattr(actual, name), getattr(expected, name), rtol=0, atol=0)
        assert actual.num_graphs == expected.num_graphs and actual.x.is_contiguous()
    reader.close()
    path.unlink()
