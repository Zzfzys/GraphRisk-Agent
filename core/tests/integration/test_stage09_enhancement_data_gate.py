"""Time/ID-only input gate using tiny CSV/parquet fixtures, invalid unused labels."""
import csv

import duckdb
import pytest

from graphrisk.graph.stage09_enhancement import load_contract
from graphrisk.graph.stage09_enhancement_preflight import inspect_data


def fixtures(root):
    raw = root / "data/raw/ieee-cis"
    interim = root / "data/interim/ieee-cis"
    processed = root / "data/processed/ieee-cis"
    for path in (raw, interim, processed):
        path.mkdir(parents=True)
    with (raw / "train_transaction.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["TransactionID", "TransactionDT", "isFraud"])
        writer.writerows([(str(i), i * 10, "not_a_label") for i in range(100)])
    (raw / "train_identity.csv").write_text("TransactionID,id_01\n0,unused\n", encoding="utf-8")
    with duckdb.connect() as db:
        db.execute("CREATE TABLE events AS SELECT CAST(range AS VARCHAR) transaction_id, range * 10 source_transaction_dt_seconds FROM range(100)")
        db.execute("COPY events TO ? (FORMAT PARQUET)", [str(interim / "transactions.parquet")])
        for name, lo, hi in (("train", 0, 70), ("validation", 70, 85), ("test", 85, 100)):
            db.execute("CREATE OR REPLACE TABLE split_events AS SELECT transaction_id, to_timestamp(946684800 + source_transaction_dt_seconds) transaction_time FROM events WHERE source_transaction_dt_seconds >= ? AND source_transaction_dt_seconds < ?", [lo * 10, hi * 10])
            db.execute("COPY split_events TO ? (FORMAT PARQUET)", [str(processed / f"{name}_features.parquet")])


@pytest.fixture
def prepared(tmp_path):
    from pathlib import Path
    fixtures(tmp_path)
    contract = load_contract(Path(__file__).resolve().parents[2] / "configs/stage09_model_enhancement.json")
    return tmp_path, contract


def test_full_gate_ignores_outcomes_and_preserves_files(prepared):
    root, contract = prepared
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = inspect_data(root, contract)
    assert result["canonical_count"] == 100
    assert result["development_count"] == 85
    assert result["outcome_columns_projected"] == []
    assert len(result["windows"]) == 3
    assert all(p.read_bytes() == content for p, content in before.items())


def test_raw_time_mismatch_stops_preflight(prepared):
    root, contract = prepared
    path = root / "data/raw/ieee-cis/train_transaction.csv"
    path.write_text(path.read_text().replace("1,10,", "1,9999,"))
    with pytest.raises(ValueError, match="raw/canonical"):
        inspect_data(root, contract)


def test_identity_duplicate_stops_preflight(prepared):
    root, contract = prepared
    (root / "data/raw/ieee-cis/train_identity.csv").write_text("TransactionID,id_01\n0,x\n0,y\n")
    with pytest.raises(ValueError, match="identity IDs"):
        inspect_data(root, contract)
