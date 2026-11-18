from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from graphrisk.config import SplitConfig


@dataclass(frozen=True)
class SplitSummary:
    train_path: Path
    validation_path: Path
    test_path: Path
    train_count: int
    validation_count: int
    test_count: int


def write_temporal_splits(
    feature_parquet: Path,
    output_dir: Path,
    split_config: SplitConfig,
) -> SplitSummary:
    if not feature_parquet.is_file():
        raise FileNotFoundError(f"feature data does not exist: {feature_parquet}")

    output_dir.mkdir(parents=True, exist_ok=True)
    train_path = output_dir / "train_features.parquet"
    validation_path = output_dir / "validation_features.parquet"
    test_path = output_dir / "test_features.parquet"

    with duckdb.connect() as connection:
        connection.from_parquet(str(feature_parquet)).create_view("features")
        row_count = int(connection.sql("SELECT COUNT(*) FROM features").fetchone()[0])
        train_count = int(row_count * split_config.train_ratio)
        validation_count = int(row_count * split_config.validation_ratio)
        test_count = row_count - train_count - validation_count
        if min(train_count, validation_count, test_count) < 1:
            raise ValueError(
                "temporal split requires at least one row in train, validation, and test"
            )

        connection.sql(
            """
            CREATE TEMP VIEW ranked_features AS
            SELECT
                *,
                ROW_NUMBER() OVER (ORDER BY transaction_time, transaction_id) AS _row_number
            FROM features
            """
        )

        split_definitions = (
            ("train", 1, train_count, train_path),
            (
                "validation",
                train_count + 1,
                train_count + validation_count,
                validation_path,
            ),
            ("test", train_count + validation_count + 1, row_count, test_path),
        )
        for split_name, first_row, last_row, output_path in split_definitions:
            relation = connection.sql(
                f"""
                SELECT * EXCLUDE (_row_number), '{split_name}'::VARCHAR AS dataset_split
                FROM ranked_features
                WHERE _row_number BETWEEN {first_row} AND {last_row}
                ORDER BY transaction_time, transaction_id
                """
            )
            relation.write_parquet(str(output_path), compression="zstd")

        boundaries = connection.sql(
            f"""
            SELECT
                (SELECT MAX(transaction_time) FROM read_parquet('{_sql_path(train_path)}')),
                (SELECT MIN(transaction_time) FROM read_parquet('{_sql_path(validation_path)}')),
                (SELECT MAX(transaction_time) FROM read_parquet('{_sql_path(validation_path)}')),
                (SELECT MIN(transaction_time) FROM read_parquet('{_sql_path(test_path)}'))
            """
        ).fetchone()
        if boundaries[0] > boundaries[1] or boundaries[2] > boundaries[3]:
            raise RuntimeError("temporal split boundaries overlap or are out of order")

    return SplitSummary(
        train_path=train_path,
        validation_path=validation_path,
        test_path=test_path,
        train_count=train_count,
        validation_count=validation_count,
        test_count=test_count,
    )


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''").replace("\\", "/")
