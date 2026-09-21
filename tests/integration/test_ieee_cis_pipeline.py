import csv
from pathlib import Path

from graphrisk.data.ieee_cis import IDENTITY_COLUMNS, TRANSACTION_COLUMNS
from graphrisk.pipeline import run_pipeline


def _write_csv(path: Path, fieldnames: set[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=sorted(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def test_ieee_cis_pipeline_builds_temporal_splits(tmp_path) -> None:
    transaction_path = tmp_path / "train_transaction.csv"
    identity_path = tmp_path / "train_identity.csv"
    transaction_rows = []
    for index in range(20):
        transaction_rows.append(
            {
                "TransactionID": str(index + 1),
                "TransactionDT": str(3600 * (index + 1)),
                "TransactionAmt": str(10 + index),
                "ProductCD": "W",
                "card1": str(1000 + index % 3),
                "card2": "200",
                "card3": "150",
                "card4": "visa",
                "card5": "226",
                "card6": "debit",
                "addr1": "315",
                "addr2": "87",
                "P_emaildomain": "example.com",
                "R_emaildomain": "",
                "isFraud": "1" if index in {5, 17} else "0",
            }
        )
    _write_csv(transaction_path, TRANSACTION_COLUMNS, transaction_rows)
    _write_csv(identity_path, IDENTITY_COLUMNS, [])

    config_path = tmp_path / "ieee_pipeline.yaml"
    config_path.write_text(
        f"""
project_name: test-graphrisk
paths:
  raw_transactions: {transaction_path.as_posix()}
  raw_identity: {identity_path.as_posix()}
  interim_transactions: {tmp_path.as_posix()}/interim/transactions.parquet
  interim_features: {tmp_path.as_posix()}/interim/features.parquet
  processed_dir: {tmp_path.as_posix()}/processed
  feature_report: {tmp_path.as_posix()}/reports/feature-report.md
data:
  source: ieee_cis
  relative_time_anchor: "2000-01-01T00:00:00+00:00"
split:
  train_ratio: 0.70
  validation_ratio: 0.15
  test_ratio: 0.15
logging:
  level: WARNING
""".strip(),
        encoding="utf-8",
    )

    summary = run_pipeline(config_path)

    assert summary.generation is None
    assert summary.ingestion.row_count == 20
    assert summary.ingestion.fraud_count == 2
    assert summary.splits.train_count == 14
    assert summary.splits.validation_count == 3
    assert summary.splits.test_count == 3
    assert "Competition-dataset results" in summary.report_path.read_text(encoding="utf-8")
