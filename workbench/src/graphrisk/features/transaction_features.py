from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class FeatureBuildSummary:
    output_path: Path
    row_count: int


def build_historical_features(
    input_parquet: Path,
    output_parquet: Path,
) -> FeatureBuildSummary:
    if not input_parquet.is_file():
        raise FileNotFoundError(f"normalized transaction data does not exist: {input_parquet}")

    output_parquet.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as connection:
        connection.from_parquet(str(input_parquet)).create_view("transactions")
        featured = connection.sql(
            """
            WITH historical AS (
                SELECT
                    *,
                    COUNT(*) OVER identity_last_1h AS tx_count_last_1h,
                    COUNT(*) OVER identity_last_24h AS tx_count_last_24h,
                    COUNT(*) OVER identity_last_7d AS tx_count_last_7d,
                    AVG(transaction_amount) OVER identity_last_24h AS amount_mean_last_24h,
                    STDDEV_POP(transaction_amount) OVER identity_last_24h AS amount_std_last_24h
                FROM transactions
                WINDOW
                    identity_last_1h AS (
                        PARTITION BY identity_key
                        ORDER BY transaction_time
                        RANGE BETWEEN INTERVAL '1 hour' PRECEDING
                                  AND INTERVAL '1 microsecond' PRECEDING
                    ),
                    identity_last_24h AS (
                        PARTITION BY identity_key
                        ORDER BY transaction_time
                        RANGE BETWEEN INTERVAL '24 hours' PRECEDING
                                  AND INTERVAL '1 microsecond' PRECEDING
                    ),
                    identity_last_7d AS (
                        PARTITION BY identity_key
                        ORDER BY transaction_time
                        RANGE BETWEEN INTERVAL '7 days' PRECEDING
                                  AND INTERVAL '1 microsecond' PRECEDING
                    )
            )
            SELECT
                *,
                transaction_amount - amount_mean_last_24h AS amount_deviation_last_24h,
                EXTRACT('hour' FROM transaction_time)::UTINYINT AS transaction_hour,
                EXTRACT('dow' FROM transaction_time)::UTINYINT AS transaction_day_of_week
            FROM historical
            ORDER BY transaction_time, transaction_id
            """
        )
        featured.write_parquet(str(output_parquet), compression="zstd")
        row_count = featured.count("*").fetchone()[0]

    return FeatureBuildSummary(output_path=output_parquet, row_count=int(row_count))
