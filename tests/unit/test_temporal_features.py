import csv

import duckdb
import pytest

from graphrisk.data.contracts import CANONICAL_TRANSACTION_COLUMNS
from graphrisk.data.ingestion import ingest_canonical_transactions
from graphrisk.features.transaction_features import build_historical_features


def _write_transactions(path, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CANONICAL_TRANSACTION_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _row(transaction_id: str, time: str, amount: float, card_id: str = "card-1"):
    return {
        "transaction_id": transaction_id,
        "transaction_time": time,
        "transaction_amount": amount,
        "card_id": card_id,
        "device_id": "device-1",
        "email_domain": "example.com",
        "address_id": "address-1",
        "product_code": "A",
        "is_fraud": 0,
    }


def _build_features(tmp_path, rows, suffix: str):
    csv_path = tmp_path / f"{suffix}.csv"
    normalized_path = tmp_path / f"{suffix}-normalized.parquet"
    feature_path = tmp_path / f"{suffix}-features.parquet"
    _write_transactions(csv_path, rows)
    ingest_canonical_transactions(csv_path, normalized_path)
    build_historical_features(normalized_path, feature_path)
    return feature_path


def test_historical_windows_exclude_current_and_same_timestamp(tmp_path) -> None:
    rows = [
        _row("T1", "2025-01-01T00:00:00+00:00", 10.0),
        _row("T2", "2025-01-01T00:00:00+00:00", 99.0),
        _row("T3", "2025-01-01T00:30:00+00:00", 20.0),
        _row("T4", "2025-01-01T02:00:00+00:00", 40.0),
        _row("T5", "2025-01-01T02:30:00+00:00", 15.0, card_id="card-2"),
    ]
    feature_path = _build_features(tmp_path, rows, "base")

    with duckdb.connect() as connection:
        values = connection.from_parquet(str(feature_path)).project(
            "transaction_id, tx_count_last_1h, tx_count_last_24h, "
            "amount_mean_last_24h, amount_deviation_last_24h"
        ).order("transaction_id").fetchall()

    assert values[0] == ("T1", 0, 0, None, None)
    assert values[1] == ("T2", 0, 0, None, None)
    assert values[2][0:3] == ("T3", 2, 2)
    assert values[2][3] == pytest.approx(54.5)
    assert values[2][4] == pytest.approx(-34.5)
    assert values[3][0:3] == ("T4", 0, 3)
    assert values[3][3] == pytest.approx(43.0)
    assert values[3][4] == pytest.approx(-3.0)
    assert values[4] == ("T5", 0, 0, None, None)


def test_adding_future_transaction_does_not_change_historical_features(tmp_path) -> None:
    original_rows = [
        _row("T1", "2025-01-01T00:00:00+00:00", 10.0),
        _row("T2", "2025-01-01T01:00:00+00:00", 20.0),
        _row("T3", "2025-01-01T02:00:00+00:00", 30.0),
    ]
    original_path = _build_features(tmp_path, original_rows, "original")
    future_path = _build_features(
        tmp_path,
        original_rows + [_row("T4", "2025-01-08T00:00:00+00:00", 999999.0)],
        "with-future",
    )

    with duckdb.connect() as connection:
        original = connection.from_parquet(str(original_path)).filter(
            "transaction_id IN ('T1', 'T2', 'T3')"
        ).project(
            "transaction_id, tx_count_last_1h, tx_count_last_24h, "
            "tx_count_last_7d, amount_mean_last_24h"
        ).order("transaction_id").fetchall()
        with_future = connection.from_parquet(str(future_path)).filter(
            "transaction_id IN ('T1', 'T2', 'T3')"
        ).project(
            "transaction_id, tx_count_last_1h, tx_count_last_24h, "
            "tx_count_last_7d, amount_mean_last_24h"
        ).order("transaction_id").fetchall()

    assert original == with_future
