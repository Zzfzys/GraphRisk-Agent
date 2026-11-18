from pathlib import Path

import duckdb

from graphrisk.pipeline import run_pipeline


def _write_config(path: Path, root: Path) -> None:
    path.write_text(
        f"""
project_name: test-graphrisk
paths:
  raw_transactions: {root.as_posix()}/data/raw/transactions.csv
  interim_transactions: {root.as_posix()}/data/interim/transactions.parquet
  interim_features: {root.as_posix()}/data/interim/features.parquet
  processed_dir: {root.as_posix()}/data/processed
  feature_report: {root.as_posix()}/reports/feature_report.md
data:
  source: synthetic
  regenerate_synthetic: true
split:
  train_ratio: 0.70
  validation_ratio: 0.15
  test_ratio: 0.15
synthetic:
  rows: 120
  seed: 7
  start_time: "2025-01-01T00:00:00+00:00"
logging:
  level: WARNING
""".strip(),
        encoding="utf-8",
    )


def _transaction_ids(path: Path) -> list[str]:
    with duckdb.connect() as connection:
        return [
            row[0]
            for row in connection.from_parquet(str(path)).order(
                "transaction_time, transaction_id"
            ).project("transaction_id").fetchall()
        ]


def test_pipeline_is_reproducible_and_temporally_ordered(tmp_path) -> None:
    config_path = tmp_path / "pipeline.yaml"
    _write_config(config_path, tmp_path)

    first = run_pipeline(config_path)
    first_ids = {
        "train": _transaction_ids(first.splits.train_path),
        "validation": _transaction_ids(first.splits.validation_path),
        "test": _transaction_ids(first.splits.test_path),
    }
    second = run_pipeline(config_path)
    second_ids = {
        "train": _transaction_ids(second.splits.train_path),
        "validation": _transaction_ids(second.splits.validation_path),
        "test": _transaction_ids(second.splits.test_path),
    }

    assert first_ids == second_ids
    assert first.splits.train_count == 84
    assert first.splits.validation_count == 18
    assert first.splits.test_count == 18
    assert set(first_ids["train"]).isdisjoint(first_ids["validation"])
    assert set(first_ids["train"]).isdisjoint(first_ids["test"])
    assert set(first_ids["validation"]).isdisjoint(first_ids["test"])
    assert first.report_path.is_file()
    assert "Synthetic results must not be presented" in first.report_path.read_text(
        encoding="utf-8"
    )
