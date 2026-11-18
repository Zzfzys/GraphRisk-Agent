from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from graphrisk.data.contracts import CANONICAL_TRANSACTION_COLUMNS


class DataValidationError(ValueError):
    """Raised when raw transaction data violates the canonical contract."""


@dataclass(frozen=True)
class IngestionSummary:
    output_path: Path
    row_count: int
    fraud_count: int


def ingest_canonical_transactions(input_csv: Path, output_parquet: Path) -> IngestionSummary:
    if not input_csv.is_file():
        raise FileNotFoundError(f"transaction CSV does not exist: {input_csv}")

    output_parquet.parent.mkdir(parents=True, exist_ok=True)

    with duckdb.connect() as connection:
        raw_relation = connection.read_csv(str(input_csv), header=True, all_varchar=True)
        actual_columns = set(raw_relation.columns)
        missing_columns = sorted(set(CANONICAL_TRANSACTION_COLUMNS) - actual_columns)
        if missing_columns:
            raise DataValidationError(
                f"transaction CSV is missing required columns: {', '.join(missing_columns)}"
            )
        raw_relation.create_view("raw_transactions")

        validation = connection.sql(
            """
            SELECT
                COUNT(*) AS row_count,
                SUM(CASE WHEN transaction_id IS NULL OR TRIM(transaction_id) = '' THEN 1 ELSE 0 END)
                    AS missing_transaction_id,
                SUM(CASE WHEN TRY_CAST(transaction_time AS TIMESTAMPTZ) IS NULL THEN 1 ELSE 0 END)
                    AS invalid_transaction_time,
                SUM(CASE WHEN TRY_CAST(transaction_amount AS DOUBLE) IS NULL
                              OR TRY_CAST(transaction_amount AS DOUBLE) < 0 THEN 1 ELSE 0 END)
                    AS invalid_transaction_amount,
                SUM(CASE WHEN TRY_CAST(is_fraud AS INTEGER) NOT IN (0, 1)
                              OR is_fraud IS NULL THEN 1 ELSE 0 END)
                    AS invalid_label,
                SUM(CASE WHEN card_id IS NULL OR TRIM(card_id) = ''
                              OR device_id IS NULL OR TRIM(device_id) = ''
                              OR email_domain IS NULL OR TRIM(email_domain) = ''
                              OR address_id IS NULL OR TRIM(address_id) = ''
                              OR product_code IS NULL OR TRIM(product_code) = ''
                         THEN 1 ELSE 0 END) AS missing_entity
            FROM raw_transactions
            """
        ).fetchone()
        if validation is None:
            raise DataValidationError("unable to validate transaction CSV")

        metric_names = (
            "row_count",
            "missing_transaction_id",
            "invalid_transaction_time",
            "invalid_transaction_amount",
            "invalid_label",
            "missing_entity",
        )
        metrics = dict(zip(metric_names, (int(value or 0) for value in validation), strict=True))
        if metrics["row_count"] == 0:
            raise DataValidationError("transaction CSV contains no rows")

        invalid_metrics = {name: value for name, value in metrics.items() if name != "row_count" and value}
        if invalid_metrics:
            details = ", ".join(f"{name}={value}" for name, value in invalid_metrics.items())
            raise DataValidationError(f"transaction CSV failed validation: {details}")

        duplicate_count = connection.sql(
            """
            SELECT COUNT(*)
            FROM (
                SELECT TRIM(transaction_id) AS transaction_id
                FROM raw_transactions
                GROUP BY 1
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        if duplicate_count:
            raise DataValidationError(
                f"transaction CSV contains {duplicate_count} duplicated transaction IDs"
            )

        normalized_relation = connection.sql(
            """
            SELECT
                TRIM(transaction_id) AS transaction_id,
                TRY_CAST(transaction_time AS TIMESTAMPTZ) AT TIME ZONE 'UTC' AS transaction_time,
                TRY_CAST(transaction_amount AS DOUBLE) AS transaction_amount,
                LOWER(TRIM(card_id)) AS card_id,
                LOWER(TRIM(device_id)) AS device_id,
                LOWER(TRIM(email_domain)) AS email_domain,
                LOWER(TRIM(address_id)) AS address_id,
                UPPER(TRIM(product_code)) AS product_code,
                TRY_CAST(is_fraud AS TINYINT) AS is_fraud,
                SHA256(
                    LOWER(TRIM(card_id)) || '|' ||
                    LOWER(TRIM(address_id)) || '|' ||
                    LOWER(TRIM(email_domain))
                ) AS identity_key
            FROM raw_transactions
            ORDER BY transaction_time, transaction_id
            """
        )
        normalized_relation.write_parquet(str(output_parquet), compression="zstd")

        row_count, fraud_count = connection.from_parquet(str(output_parquet)).aggregate(
            "COUNT(*) AS row_count, SUM(is_fraud) AS fraud_count"
        ).fetchone()

    return IngestionSummary(
        output_path=output_parquet,
        row_count=int(row_count),
        fraud_count=int(fraud_count or 0),
    )
