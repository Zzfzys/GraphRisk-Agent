"""Tiny causal examples and raw ID/schema failures; no model training."""
import csv
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from graphrisk.agent.stage09_preflight import _content_hash, _sha256
from graphrisk.features.wide_contract import CORE_TRANSACTION
from graphrisk.features.wide_pipeline import audit_history, build_side_table, fit_fold_profiles
from graphrisk.features.wide_preprocessing import WidePreprocessor


def fixture(root: Path, *, future=True, label="not_an_outcome", same_time_amount=20, line_ending="\r\n"):
    root.mkdir(parents=True, exist_ok=True)
    times = [10, 10, 20, 30, 40, 100000] if future else [10, 10, 20, 30]
    amounts = [10, same_time_amount, 30, 40, 50, 100000][:len(times)]
    columns = ["TransactionID", "TransactionDT", "isFraud", *sorted(CORE_TRANSACTION), "C1", "V1", "V2", "M1"]
    rows = []
    for index, (event_time, amount) in enumerate(zip(times, amounts)):
        row = {c: "" for c in columns}
        row.update(TransactionID=str(index+1), TransactionDT=str(event_time), isFraud=label,
            TransactionAmt=str(amount), ProductCD="W", card1="100", card4="visa" if index % 2 == 0 else "mastercard",
            addr1="300", P_emaildomain="example.com", C1="" if index == 1 else str(index),
            V1=str(index), V2=str(index) if index < 3 else "999", M1="T" if index % 2 else "F")
        rows.append(row)
    transaction = root / "transaction.csv"
    with transaction.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator=line_ending)
        writer.writeheader()
        writer.writerows(rows)
    identity = root / "identity.csv"
    with identity.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator=line_ending)
        writer.writerow(["TransactionID", "DeviceType", "DeviceInfo", "id_01"])
        writer.writerows([(str(i+1), "desktop" if i % 2 == 0 else "mobile", "old" if i < 3 else "new", str(i)) for i in range(len(times)) if i != 1])
    canonical = root / "canonical.parquet"
    with duckdb.connect() as db:
        db.execute("CREATE TABLE c(transaction_id VARCHAR, source_transaction_dt_seconds BIGINT, transaction_amount DOUBLE, card_id VARCHAR, device_id VARCHAR, email_domain VARCHAR, address_id VARCHAR, is_fraud VARCHAR)")
        db.executemany("INSERT INTO c VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [(str(i+1), t, a, "card-key", "unknown-device-2" if i == 1 else "device-key", "example.com", "address-key", label) for i, (t, a) in enumerate(zip(times, amounts))])
        db.execute("COPY c TO ? (FORMAT PARQUET)", [str(canonical)])
    return transaction, identity, canonical


def build(root, **kwargs):
    paths = fixture(root, **kwargs)
    db = duckdb.connect()
    db.execute("SET threads=1")
    summary = build_side_table(db, *paths, root / "side.parquet")
    return db, summary


def test_history_excludes_same_second_and_uses_previous_events(tmp_path):
    db, summary = build(tmp_path)
    values = db.execute("SELECT transaction_id, hist_card_count_24h, hist_card_amount_mean_24h, hist_card_seconds_since_previous, hist_card_age_seconds FROM side ORDER BY source_transaction_dt_seconds, transaction_id").fetchall()
    assert values[:2] == [("1", 0, None, None, None), ("2", 0, None, None, None)]
    assert values[2] == ("3", 2, 15, 10, 10)
    assert values[-1][1] == 0  # Past events fell out of the 24h window.
    assert summary["labels_in_side_table"] is False
    assert "is_fraud" not in [r[0] for r in db.execute("DESCRIBE side").fetchall()]
    assert db.execute("SELECT has_identity, hist_device_count_24h FROM side WHERE transaction_id='2'").fetchone() == (0, 0)
    db.close()


def test_future_and_label_changes_do_not_change_historical_features(tmp_path):
    first, _ = build(tmp_path / "first", future=False, label="one")
    second, _ = build(tmp_path / "second", future=True, label="different")
    assert first.execute("SELECT * FROM side ORDER BY transaction_id").fetchall() == second.execute("SELECT * FROM side WHERE transaction_id IN ('1','2','3','4') ORDER BY transaction_id").fetchall()
    first.close()
    second.close()


def test_same_time_peer_amount_does_not_change_peer_history(tmp_path):
    first, _ = build(tmp_path / "first", same_time_amount=20)
    second, _ = build(tmp_path / "second", same_time_amount=9999)
    query = "SELECT hist_card_count_24h, hist_card_amount_mean_24h FROM side WHERE transaction_id='1'"
    assert first.execute(query).fetchall() == second.execute(query).fetchall()
    first.close()
    second.close()


def test_train_only_fill_vocab_and_duplicate_selection(tmp_path):
    db, summary = build(tmp_path)
    train = {"min_relative_seconds": 10, "max_relative_seconds": 20, "count": 3, "ids_sha256": _content_hash({"ids": ["1", "2", "3"]})}
    states = fit_fold_profiles(db, summary["fields"], train, ["core", "source_as_received"], tmp_path)
    state = WidePreprocessor.load(tmp_path / states["source_as_received"]["path"])
    assert state.state["numeric"]["C1"]["median"] == 1
    assert state.state["categorical"]["DeviceInfo"] == ["old"]  # Missing also varies, so it is retained.
    assert state.state["dropped"]["V2"] == "exact_train_duplicate_of:V1"
    columns = state.state["input_columns"]
    frame = db.execute("SELECT " + ",".join('"'+n+'"' for n in columns) + " FROM side WHERE transaction_id IN ('2','4') ORDER BY transaction_id").fetchdf()
    original = frame.copy(deep=True)
    output = state.transform(frame)
    pd.testing.assert_frame_equal(frame, original)
    assert output["C1"].tolist() == [1, 3]
    assert output["missing:C1"].tolist() == [1, 0]
    assert output["DeviceInfo"].astype(str).tolist() == ["__missing__", "__unknown__"]
    assert not any(f["group"] == "source_as_received" and f["name"] in WidePreprocessor.load(tmp_path / states["core"]["path"]).state["input_columns"] for f in summary["fields"])
    db.close()


def test_side_table_rebuild_is_byte_reproducible(tmp_path):
    first, _ = build(tmp_path / "first")
    second, _ = build(tmp_path / "second")
    assert _sha256(tmp_path / "first/side.parquet") == _sha256(tmp_path / "second/side.parquet")
    first.close()
    second.close()


def test_direct_history_audit_detects_corrupted_window_values(tmp_path):
    db, _ = build(tmp_path)
    proof = audit_history(db, 6)
    assert proof["status"] == "passed" and proof["scalar_comparisons"] >= 100
    assert proof["simultaneous_targets"] == 2
    db.execute("UPDATE side SET hist_card_count_24h=99 WHERE transaction_id='1'")
    with pytest.raises(ValueError, match="audit mismatch"):
        audit_history(db, 6)
    db.close()


@pytest.mark.parametrize("mutation,reason", [
    ("duplicate_identity", "duplicate"), ("duplicate_transaction", "duplicate"),
    ("missing_canonical", "alignment"), ("wrong_time", "alignment"),
    ("orphan_identity", "orphan"), ("bad_numeric", "numeric"),
    ("nonfinite", "numeric"), ("fractional_time", "invalid time")])
@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_raw_failures_stop_before_writing_side_table(tmp_path, mutation, reason, line_ending):
    tx, identity, canonical = fixture(tmp_path, line_ending=line_ending)
    if mutation == "duplicate_identity":
        with identity.open("a", newline="", encoding="utf-8") as stream:
            csv.writer(stream, lineterminator=line_ending).writerow(["1", "desktop", "old", "0"])
    elif mutation == "duplicate_transaction":
        lines = tx.read_text().splitlines()
        tx.write_bytes((line_ending.join([*lines, lines[1]]) + line_ending).encode("utf-8"))
    elif mutation in {"missing_canonical", "wrong_time"}:
        with duckdb.connect() as db:
            db.from_parquet(str(canonical)).create("changed")
            if mutation == "missing_canonical":
                db.execute("DELETE FROM changed WHERE transaction_id='1'")
            else:
                db.execute("UPDATE changed SET source_transaction_dt_seconds=999 WHERE transaction_id='1'")
            db.execute("COPY changed TO ? (FORMAT PARQUET)", [str(canonical)])
    elif mutation == "orphan_identity":
        with identity.open("a", newline="", encoding="utf-8") as stream:
            csv.writer(stream, lineterminator=line_ending).writerow(["99", "desktop", "unknown", "0"])
    else:
        rows = list(csv.DictReader(tx.open()))
        rows[0]["C1" if mutation in {"bad_numeric", "nonfinite"} else "TransactionDT"] = {"bad_numeric": "oops", "nonfinite": "inf", "fractional_time": "10.5"}[mutation]
        with tx.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys(), lineterminator=line_ending)
            writer.writeheader()
            writer.writerows(rows)
    with duckdb.connect() as db, pytest.raises(ValueError, match=reason):
        build_side_table(db, tx, identity, canonical, tmp_path / "side.parquet")
    assert not (tmp_path / "side.parquet").exists()


def test_fit_id_mismatch_and_output_overwrite_are_rejected(tmp_path):
    db, summary = build(tmp_path)
    with pytest.raises(ValueError, match="fit IDs"):
        fit_fold_profiles(db, summary["fields"], {"max_relative_seconds": 20, "count": 99, "ids_sha256": "a" * 64}, ["core"], tmp_path)
    with pytest.raises(ValueError, match="already exists"):
        build_side_table(db, tmp_path / "transaction.csv", tmp_path / "identity.csv", tmp_path / "canonical.parquet", tmp_path / "side.parquet")
    db.close()
