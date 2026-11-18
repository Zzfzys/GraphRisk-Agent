from __future__ import annotations

from pathlib import Path

import duckdb

from graphrisk.data.splitting import SplitSummary


def write_feature_report(
    feature_parquet: Path,
    split_summary: SplitSummary,
    report_path: Path,
    *,
    source_name: str,
) -> Path:
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with duckdb.connect() as connection:
        connection.from_parquet(str(feature_parquet)).create_view("features")
        summary = connection.sql(
            """
            SELECT
                COUNT(*) AS row_count,
                MIN(transaction_time) AS min_time,
                MAX(transaction_time) AS max_time,
                SUM(is_fraud) AS fraud_count,
                AVG(is_fraud) AS fraud_rate,
                SUM(CASE WHEN transaction_amount IS NULL THEN 1 ELSE 0 END) AS missing_amount,
                SUM(CASE WHEN identity_key IS NULL THEN 1 ELSE 0 END) AS missing_identity,
                SUM(CASE WHEN amount_mean_last_24h IS NULL THEN 1 ELSE 0 END)
                    AS missing_historical_mean
            FROM features
            """
        ).fetchone()

    (
        row_count,
        min_time,
        max_time,
        fraud_count,
        fraud_rate,
        missing_amount,
        missing_identity,
        missing_historical_mean,
    ) = summary

    evidence_note = (
        "Synthetic results must not be presented as real-world model evidence."
        if source_name == "synthetic"
        else "Competition-dataset results must not be presented as production performance."
    )
    time_note = (
        ""
        if source_name != "ieee_cis"
        else (
            "\n> IEEE-CIS calendar timestamps use the configured artificial anchor. "
            "Only source ordering and elapsed intervals are meaningful."
        )
    )
    report = f"""# Feature build report

> Data source: `{source_name}`
> This report describes generated artifacts. {evidence_note}
{time_note}

## Dataset summary

| Metric | Value |
|---|---:|
| Rows | {int(row_count)} |
| Earliest transaction | {min_time} |
| Latest transaction | {max_time} |
| Fraud rows | {int(fraud_count or 0)} |
| Fraud rate | {float(fraud_rate or 0):.6f} |

## Missing values

| Field | Missing rows |
|---|---:|
| `transaction_amount` | {int(missing_amount or 0)} |
| `identity_key` | {int(missing_identity or 0)} |
| `amount_mean_last_24h` | {int(missing_historical_mean or 0)} |

The historical mean is expected to be missing for an identity's first transaction or when no transaction exists in the preceding 24-hour window.

## Temporal split

| Split | Rows | File |
|---|---:|---|
| Train | {split_summary.train_count} | `{split_summary.train_path}` |
| Validation | {split_summary.validation_count} | `{split_summary.validation_path}` |
| Test | {split_summary.test_count} | `{split_summary.test_path}` |

## Point-in-time guarantees

- Windows exclude the current transaction;
- Windows cannot observe later event time;
- Transactions sharing an identical timestamp are treated as simultaneous;
- Labels are not used to construct features;
- Split membership is assigned only after historical features are calculated.
"""
    report_path.write_text(report, encoding="utf-8")
    return report_path
