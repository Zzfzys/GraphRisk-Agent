import csv
from datetime import UTC, datetime

import duckdb
import pytest

from graphrisk.data.ieee_cis import (
    IDENTITY_COLUMNS,
    TRANSACTION_COLUMNS,
    adapt_ieee_cis_transactions,
)
from graphrisk.data.ingestion import DataValidationError


def _transaction(transaction_id: str, **overrides: str) -> dict[str, str]:
    row = {
        "TransactionID": transaction_id,
        "TransactionDT": "3600",
        "TransactionAmt": "25.50",
        "ProductCD": "W",
        "card1": "1001",
        "card2": "200",
        "card3": "150",
        "card4": "visa",
        "card5": "226",
        "card6": "debit",
        "addr1": "315",
        "addr2": "87",
        "P_emaildomain": "Example.COM",
        "R_emaildomain": "",
        "isFraud": "0",
    }
    row.update(overrides)
    return row


def _write_csv(path, fieldnames, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=sorted(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def test_adapter_joins_identity_and_preserves_relative_order(tmp_path) -> None:
    transactions = tmp_path / "train_transaction.csv"
    identities = tmp_path / "train_identity.csv"
    output = tmp_path / "transactions.parquet"
    _write_csv(
        transactions,
        TRANSACTION_COLUMNS,
        [
            _transaction("2", TransactionDT="7200", isFraud="1"),
            _transaction("1", TransactionDT="3600"),
        ],
    )
    _write_csv(
        identities,
        IDENTITY_COLUMNS,
        [
            {
                "TransactionID": "1",
                "DeviceType": "desktop",
                "DeviceInfo": "Windows 10",
            }
        ],
    )

    summary = adapt_ieee_cis_transactions(
        transactions,
        identities,
        output,
        relative_time_anchor=datetime(2000, 1, 1, tzinfo=UTC),
    )

    with duckdb.connect() as connection:
        rows = connection.from_parquet(str(output)).project(
            "transaction_id, transaction_time, source_transaction_dt_seconds, "
            "device_id, email_domain, is_fraud, identity_key"
        ).order("transaction_time").fetchall()

    assert summary.row_count == 2
    assert summary.fraud_count == 1
    assert rows[0][0] == "1"
    assert rows[0][1].astimezone(UTC) == datetime(2000, 1, 1, 1, 0, tzinfo=UTC)
    assert rows[0][2] == 3600
    assert rows[0][3] == "device-windows 10"
    assert rows[0][4] == "example.com"
    assert rows[1][0] == "2"
    assert rows[1][3] == "unknown-device-2"
    assert all(row[6] for row in rows)


def test_adapter_uses_transaction_unique_fallbacks_for_missing_entities(tmp_path) -> None:
    transactions = tmp_path / "train_transaction.csv"
    identities = tmp_path / "train_identity.csv"
    output = tmp_path / "transactions.parquet"
    missing = {
        "card1": "",
        "addr1": "",
        "P_emaildomain": "",
        "R_emaildomain": "",
    }
    _write_csv(
        transactions,
        TRANSACTION_COLUMNS,
        [_transaction("1", **missing), _transaction("2", **missing)],
    )
    _write_csv(identities, IDENTITY_COLUMNS, [])

    adapt_ieee_cis_transactions(
        transactions,
        identities,
        output,
        relative_time_anchor=datetime(2000, 1, 1, tzinfo=UTC),
    )

    with duckdb.connect() as connection:
        entities = connection.from_parquet(str(output)).project(
            "card_id, device_id, email_domain, address_id, identity_key"
        ).fetchall()

    assert entities[0] != entities[1]
    assert entities[0][0] == "unknown-card-1"
    assert entities[1][3] == "unknown-address-2"


def test_adapter_rejects_invalid_labels(tmp_path) -> None:
    transactions = tmp_path / "train_transaction.csv"
    identities = tmp_path / "train_identity.csv"
    _write_csv(
        transactions,
        TRANSACTION_COLUMNS,
        [_transaction("1", isFraud="not-a-label")],
    )
    _write_csv(identities, IDENTITY_COLUMNS, [])

    with pytest.raises(DataValidationError, match="invalid_label=1"):
        adapt_ieee_cis_transactions(
            transactions,
            identities,
            tmp_path / "transactions.parquet",
            relative_time_anchor=datetime(2000, 1, 1, tzinfo=UTC),
        )


def test_adapter_rejects_missing_source_columns(tmp_path) -> None:
    transactions = tmp_path / "train_transaction.csv"
    identities = tmp_path / "train_identity.csv"
    incomplete_columns = TRANSACTION_COLUMNS - {"isFraud"}
    row = _transaction("1")
    _write_csv(
        transactions,
        incomplete_columns,
        [{name: row[name] for name in incomplete_columns}],
    )
    _write_csv(identities, IDENTITY_COLUMNS, [])

    with pytest.raises(DataValidationError, match="missing columns: isFraud"):
        adapt_ieee_cis_transactions(
            transactions,
            identities,
            tmp_path / "transactions.parquet",
            relative_time_anchor=datetime(2000, 1, 1, tzinfo=UTC),
        )
