import numpy as np
import pandas as pd
import pytest
import duckdb

from graphrisk.models.champion_features import source_columns, training_category_maps, transform_batch


def test_maps_use_only_training_time_and_composite_keys(tmp_path):
    source = pd.DataFrame({
        "source_transaction_dt_seconds": [1, 2, 3],
        "card1": [" b ", "a", "future"], "addr1": ["x", "y", "z"],
        "P_emaildomain": ["e", "e", "future"], "ProductCD": ["W", "C", "future"],
    })
    path = tmp_path / "source.parquet"
    source.to_parquet(path)
    db = duckdb.connect()
    maps = training_category_maps(db, path, ["ProductCD"], 2)
    assert maps["ProductCD"] == {"C": 0, "W": 1}
    assert len(maps["card1_addr1"]) == 2
    assert "future" not in maps["ProductCD"]
    rows = db.execute("SELECT " + source_columns(["card1", "addr1", "P_emaildomain"]) + " FROM read_parquet(?) s", [str(path)]).df()
    assert rows.card1_addr1.iloc[0] in maps["card1_addr1"]
    assert rows.card1_addr1.iloc[2] not in maps["card1_addr1"]
    db.close()


def test_transform_order_unknown_category_and_identity_guards():
    # 216 baseline columns are deliberately simple: category + 214 raw values + cents.
    raw = ["ProductCD", "TransactionAmt", *[f"V{i}" for i in range(213)]]
    names = [*raw, "cents"]
    extra = [f"batch_uid_{i}" for i in range(49)]
    contract = {"columns": names, "raw": raw, "categorical": ["ProductCD"],
                "base_mapping": {"day": {}}, "uid_extra": {"day": extra}}
    source = pd.DataFrame({name: [1.0, 2.0] for name in raw if name != "ProductCD"})
    source["ProductCD"] = [" known ", "unseen"]
    source["transaction_id"] = [10, 11]
    source["source_transaction_dt_seconds"] = [100, 101]
    source["card1_addr1"] = ["a", "new"]
    source["card1_addr1_P_emaildomain"] = ["b", "new"]
    derived = pd.DataFrame({name: [1.0, 2.0] for name in extra})
    derived["transaction_id"] = [10, 11]
    derived["source_transaction_dt_seconds"] = [100, 101]
    maps = {"ProductCD": {"known": 4}, "card1_addr1": {"a": 0}, "card1_addr1_P_emaildomain": {"b": 0}}
    result = transform_batch(source, derived, contract, maps)
    assert list(result.columns) == names + extra
    assert result.ProductCD.iloc[0] == 4
    assert np.isnan(result.ProductCD.iloc[1])
    assert all(dtype == np.float32 for dtype in result.dtypes)
    with pytest.raises(ValueError, match="IDs"):
        transform_batch(source, derived.iloc[::-1].reset_index(drop=True), contract, maps)
    derived.loc[1, "source_transaction_dt_seconds"] = 102
    with pytest.raises(ValueError, match="times"):
        transform_batch(source, derived, contract, maps)
    with pytest.raises(ValueError, match="missing"):
        transform_batch(source, derived.drop(columns="source_transaction_dt_seconds"), contract, maps)
