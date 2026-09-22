from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from graphrisk.config import ProjectConfig
from graphrisk.investigation.contracts import DatasetSource


TRANSACTION_COLUMNS = (
    "transaction_id",
    "transaction_amount",
    "product_code",
    "card_id",
    "device_id",
    "email_domain",
    "address_id",
)
FEATURE_COLUMNS = (
    "transaction_id",
    "tx_count_last_1h",
    "tx_count_last_24h",
    "tx_count_last_7d",
    "amount_mean_last_24h",
    "amount_std_last_24h",
    "amount_deviation_last_24h",
)


class ArtifactUnavailableError(Exception):
    def __init__(self, artifact: str) -> None:
        self.artifact = artifact
        super().__init__(artifact)


class DuplicateTransactionError(Exception):
    pass


class RepositoryQueryError(Exception):
    pass


@dataclass(frozen=True)
class InvestigationRepository:
    """Read approved columns from the two Parquet artifacts bound by one config."""

    dataset_source: DatasetSource
    transaction_parquet: Path
    feature_parquet: Path

    @classmethod
    def from_config(cls, config: ProjectConfig) -> "InvestigationRepository":
        if config.data.source not in ("synthetic", "ieee_cis"):
            raise ValueError("investigation requires synthetic or ieee_cis source")
        return cls(
            dataset_source=config.data.source,
            transaction_parquet=config.resolve_path(config.paths.interim_transactions),
            feature_parquet=config.resolve_path(config.paths.interim_features),
        )

    def transaction(self, transaction_id: str) -> dict[str, object] | None:
        columns = list(TRANSACTION_COLUMNS)
        if self.dataset_source == "ieee_cis":
            columns.append("source_transaction_dt_seconds")
        return self._select_one(
            self.transaction_parquet,
            "transaction",
            transaction_id,
            columns,
        )

    def features(self, transaction_id: str) -> dict[str, object] | None:
        return self._select_one(
            self.feature_parquet,
            "feature",
            transaction_id,
            list(FEATURE_COLUMNS),
        )

    @staticmethod
    def _select_one(
        parquet: Path,
        artifact: str,
        transaction_id: str,
        columns: list[str],
    ) -> dict[str, object] | None:
        if not parquet.is_file():
            raise ArtifactUnavailableError(artifact)
        projection = ", ".join(columns)
        try:
            with duckdb.connect(":memory:") as connection:
                cursor = connection.execute(
                    f"SELECT {projection} FROM read_parquet(?) "
                    "WHERE transaction_id = ? LIMIT 2",
                    [str(parquet), transaction_id],
                )
                rows = cursor.fetchall()
                names = [description[0] for description in cursor.description]
        except (duckdb.Error, OSError) as error:
            raise RepositoryQueryError(artifact) from error
        if len(rows) > 1:
            raise DuplicateTransactionError(transaction_id)
        return dict(zip(names, rows[0], strict=True)) if rows else None
