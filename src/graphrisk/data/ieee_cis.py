from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb

from graphrisk.data.ingestion import DataValidationError, IngestionSummary

TRANSACTION_COLUMNS = {
    "TransactionID",
    "TransactionDT",
    "TransactionAmt",
    "ProductCD",
    "card1",
    "card2",
    "card3",
    "card4",
    "card5",
    "card6",
    "addr1",
    "addr2",
    "P_emaildomain",
    "R_emaildomain",
    "isFraud",
}
IDENTITY_COLUMNS = {"TransactionID", "DeviceType", "DeviceInfo"}


def adapt_ieee_cis_transactions(
    transaction_csv: Path,
    identity_csv: Path,
    output_parquet: Path,
    *,
    relative_time_anchor: datetime,
) -> IngestionSummary:
    """Map IEEE-CIS training rows into the project's canonical transaction contract.

    ``TransactionDT`` is a relative offset whose real calendar origin is not published.
    The configured anchor creates a stable UTC timestamp while preserving all source
    ordering and time differences. It must not be presented as the real event date.
    """
    if not transaction_csv.is_file():
        raise FileNotFoundError(f"IEEE-CIS transaction CSV does not exist: {transaction_csv}")
    if not identity_csv.is_file():
        raise FileNotFoundError(f"IEEE-CIS identity CSV does not exist: {identity_csv}")
    if relative_time_anchor.tzinfo is None or relative_time_anchor.utcoffset() is None:
        raise ValueError("relative_time_anchor must include a timezone")

    output_parquet.parent.mkdir(parents=True, exist_ok=True)
    output_path = _sql_path(output_parquet)
    anchor = relative_time_anchor.isoformat().replace("'", "''")

    with duckdb.connect() as connection:
        connection.execute("SET preserve_insertion_order = false")
        transactions = connection.read_csv(
            str(transaction_csv),
            header=True,
            all_varchar=True,
        )
        identities = connection.read_csv(
            str(identity_csv),
            header=True,
            all_varchar=True,
        )
        _require_columns(set(transactions.columns), TRANSACTION_COLUMNS, "transaction")
        _require_columns(set(identities.columns), IDENTITY_COLUMNS, "identity")
        transactions.create_view("ieee_transactions")
        identities.create_view("ieee_identities")

        duplicate_identity_count = int(
            connection.sql(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT TransactionID
                    FROM ieee_identities
                    GROUP BY TransactionID
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
        )
        if duplicate_identity_count:
            raise DataValidationError(
                "IEEE-CIS identity table contains "
                f"{duplicate_identity_count} duplicated TransactionID values"
            )

        validation = connection.sql(
            """
            SELECT
                COUNT(*) AS row_count,
                COUNT(DISTINCT TransactionID) AS unique_transaction_count,
                COUNT_IF(TRY_CAST(TransactionDT AS BIGINT) IS NULL) AS invalid_time,
                COUNT_IF(TRY_CAST(TransactionAmt AS DOUBLE) IS NULL
                         OR TRY_CAST(TransactionAmt AS DOUBLE) < 0) AS invalid_amount,
                COUNT_IF(TRY_CAST(isFraud AS TINYINT) IS NULL
                         OR TRY_CAST(isFraud AS TINYINT) NOT IN (0, 1)) AS invalid_label,
                COUNT_IF(NULLIF(TRIM(ProductCD), '') IS NULL) AS missing_product
            FROM ieee_transactions
            """
        ).fetchone()
        if validation is None:
            raise DataValidationError("unable to validate IEEE-CIS transaction data")
        (
            row_count,
            unique_transaction_count,
            invalid_time,
            invalid_amount,
            invalid_label,
            missing_product,
        ) = (int(value or 0) for value in validation)
        if row_count == 0:
            raise DataValidationError("IEEE-CIS transaction table contains no rows")
        if unique_transaction_count != row_count:
            raise DataValidationError(
                "IEEE-CIS transaction table contains duplicated or missing TransactionID values"
            )
        invalid_metrics = {
            "invalid_time": invalid_time,
            "invalid_amount": invalid_amount,
            "invalid_label": invalid_label,
            "missing_product": missing_product,
        }
        failures = {name: value for name, value in invalid_metrics.items() if value}
        if failures:
            details = ", ".join(f"{name}={value}" for name, value in failures.items())
            raise DataValidationError(f"IEEE-CIS transaction data failed validation: {details}")

        connection.execute(
            f"""
            COPY (
                WITH mapped AS (
                    SELECT
                        TRIM(t.TransactionID) AS transaction_id,
                        CAST('{anchor}' AS TIMESTAMPTZ)
                            + TRY_CAST(t.TransactionDT AS BIGINT) * INTERVAL '1 second'
                            AS transaction_time,
                        TRY_CAST(t.TransactionDT AS BIGINT) AS source_transaction_dt_seconds,
                        TRY_CAST(t.TransactionAmt AS DOUBLE) AS transaction_amount,
                        CASE
                            WHEN NULLIF(TRIM(t.card1), '') IS NULL
                                THEN 'unknown-card-' || TRIM(t.TransactionID)
                            ELSE 'card-' || LOWER(TRIM(t.card1)) || ':'
                                || COALESCE(LOWER(NULLIF(TRIM(t.card2), '')), 'na') || ':'
                                || COALESCE(LOWER(NULLIF(TRIM(t.card3), '')), 'na') || ':'
                                || COALESCE(LOWER(NULLIF(TRIM(t.card4), '')), 'na') || ':'
                                || COALESCE(LOWER(NULLIF(TRIM(t.card5), '')), 'na') || ':'
                                || COALESCE(LOWER(NULLIF(TRIM(t.card6), '')), 'na')
                        END AS card_id,
                        CASE
                            WHEN COALESCE(
                                NULLIF(LOWER(TRIM(i.DeviceInfo)), ''),
                                NULLIF(LOWER(TRIM(i.DeviceType)), '')
                            ) IS NULL THEN 'unknown-device-' || TRIM(t.TransactionID)
                            ELSE 'device-' || COALESCE(
                                NULLIF(LOWER(TRIM(i.DeviceInfo)), ''),
                                NULLIF(LOWER(TRIM(i.DeviceType)), '')
                            )
                        END AS device_id,
                        CASE
                            WHEN COALESCE(
                                NULLIF(LOWER(TRIM(t.P_emaildomain)), ''),
                                NULLIF(LOWER(TRIM(t.R_emaildomain)), '')
                            ) IS NULL THEN 'unknown-email-' || TRIM(t.TransactionID)
                            ELSE COALESCE(
                                NULLIF(LOWER(TRIM(t.P_emaildomain)), ''),
                                NULLIF(LOWER(TRIM(t.R_emaildomain)), '')
                            )
                        END AS email_domain,
                        CASE
                            WHEN NULLIF(TRIM(t.addr1), '') IS NULL
                                THEN 'unknown-address-' || TRIM(t.TransactionID)
                            ELSE 'address-' || LOWER(TRIM(t.addr1)) || ':'
                                || COALESCE(LOWER(NULLIF(TRIM(t.addr2), '')), 'na')
                        END AS address_id,
                        UPPER(TRIM(t.ProductCD)) AS product_code,
                        TRY_CAST(t.isFraud AS TINYINT) AS is_fraud
                    FROM ieee_transactions AS t
                    LEFT JOIN ieee_identities AS i USING (TransactionID)
                )
                SELECT
                    *,
                    SHA256(card_id || '|' || address_id || '|' || email_domain)
                        AS identity_key
                FROM mapped
                ORDER BY transaction_time, transaction_id
            ) TO '{output_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )

        output_summary = connection.sql(
            f"""
            SELECT
                COUNT(*) AS row_count,
                SUM(is_fraud) AS fraud_count,
                COUNT_IF(transaction_id IS NULL OR transaction_id = '') AS missing_id,
                COUNT_IF(transaction_time IS NULL) AS missing_time,
                COUNT_IF(card_id IS NULL OR device_id IS NULL OR email_domain IS NULL
                         OR address_id IS NULL OR product_code IS NULL
                         OR identity_key IS NULL) AS missing_entity,
                COUNT_IF(is_fraud IS NULL OR is_fraud NOT IN (0, 1)) AS invalid_output_label
            FROM read_parquet('{output_path}')
            """
        ).fetchone()
        if output_summary is None:
            raise DataValidationError("unable to validate adapted IEEE-CIS output")
        (
            output_rows,
            fraud_count,
            missing_id,
            missing_time,
            missing_entity,
            invalid_output_label,
        ) = (
            int(value or 0) for value in output_summary
        )
        if output_rows != row_count:
            raise DataValidationError(
                f"IEEE-CIS adapter changed row count from {row_count} to {output_rows}"
            )
        if missing_id or missing_time or missing_entity or invalid_output_label:
            raise DataValidationError(
                "adapted IEEE-CIS output violates canonical requirements: "
                f"missing_id={missing_id}, missing_time={missing_time}, "
                f"missing_entity={missing_entity}, "
                f"invalid_label={invalid_output_label}"
            )

    return IngestionSummary(
        output_path=output_parquet,
        row_count=output_rows,
        fraud_count=fraud_count,
    )


def _require_columns(actual: set[str], required: set[str], table_name: str) -> None:
    missing = sorted(required - actual)
    if missing:
        raise DataValidationError(
            f"IEEE-CIS {table_name} table is missing columns: {', '.join(missing)}"
        )


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''").replace("\\", "/")
