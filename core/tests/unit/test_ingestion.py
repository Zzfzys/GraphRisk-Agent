import csv

import duckdb
import pytest

from graphrisk.data.contracts import CANONICAL_TRANSACTION_COLUMNS, build_identity_key
from graphrisk.data.ingestion import DataValidationError, ingest_canonical_transactions


def _valid_row(transaction_id: str) -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        "transaction_time": "2025-01-01T00:00:00+00:00",
        "transaction_amount": 10.0,
        "card_id": "card-1",
        "device_id": "device-1",
        "email_domain": "example.com",
        "address_id": "address-1",
        "product_code": "A",
        "is_fraud": 0,
    }


def test_ingestion_rejects_duplicate_transaction_ids(tmp_path) -> None:
    input_path = tmp_path / "transactions.csv"
    with input_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CANONICAL_TRANSACTION_COLUMNS)
        writer.writeheader()
        writer.writerow(_valid_row("T1"))
        writer.writerow(_valid_row("T1"))

    with pytest.raises(DataValidationError, match="duplicated transaction IDs"):
        ingest_canonical_transactions(input_path, tmp_path / "transactions.parquet")


def test_ingestion_rejects_invalid_label(tmp_path) -> None:
    input_path = tmp_path / "transactions.csv"
    invalid_row = _valid_row("T1")
    invalid_row["is_fraud"] = 2
    with input_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CANONICAL_TRANSACTION_COLUMNS)
        writer.writeheader()
        writer.writerow(invalid_row)

    with pytest.raises(DataValidationError, match="invalid_label=1"):
        ingest_canonical_transactions(input_path, tmp_path / "transactions.parquet")


def test_ingestion_normalizes_and_derives_identity_key(tmp_path) -> None:
    input_path = tmp_path / "transactions.csv"
    output_path = tmp_path / "transactions.parquet"
    row = _valid_row(" T1 ")
    row["card_id"] = " Card-1 "
    row["address_id"] = " ADDRESS-1 "
    row["email_domain"] = " Example.COM "
    with input_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CANONICAL_TRANSACTION_COLUMNS)
        writer.writeheader()
        writer.writerow(row)

    ingest_canonical_transactions(input_path, output_path)

    with duckdb.connect() as connection:
        normalized = connection.from_parquet(str(output_path)).project(
            "transaction_id, card_id, address_id, email_domain, identity_key"
        ).fetchone()

    assert normalized == (
        "T1",
        "card-1",
        "address-1",
        "example.com",
        build_identity_key("card-1", "address-1", "example.com"),
    )
