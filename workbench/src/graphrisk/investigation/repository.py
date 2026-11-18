from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
RELATION_ENTITY_COLUMNS = ("card_id", "device_id", "email_domain", "address_id")


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
        row = self.transaction_with_time(transaction_id)
        if row is not None:
            row.pop("transaction_time")
        return row

    def transaction_with_time(self, transaction_id: str) -> dict[str, object] | None:
        columns = list(TRANSACTION_COLUMNS)
        columns.append("transaction_time")
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

    def related_transactions(
        self,
        *,
        transaction_id: str,
        target_time: datetime,
        active_entities: dict[str, str],
        limit: int,
    ) -> tuple[int, list[dict[str, object]]]:
        """Return prior shared-entity rows; callers validate target and result schema."""
        if not self.transaction_parquet.is_file():
            raise ArtifactUnavailableError("transaction")
        if not active_entities:
            return 0, []

        comparisons = " OR ".join(f"{column} = ?" for column in active_entities)
        where_clause = (
            "transaction_time < ? AND transaction_id <> ? "
            f"AND ({comparisons})"
        )
        parameters = [str(self.transaction_parquet), target_time, transaction_id]
        parameters.extend(active_entities.values())
        projection = ", ".join(
            ("transaction_id", "transaction_time", *RELATION_ENTITY_COLUMNS)
        )
        try:
            with duckdb.connect(":memory:") as connection:
                count, distinct_count = connection.execute(
                    "SELECT COUNT(*), COUNT(DISTINCT transaction_id) "
                    f"FROM read_parquet(?) WHERE {where_clause}",
                    parameters,
                ).fetchone()
                if count != distinct_count:
                    raise DuplicateTransactionError(transaction_id)
                if count == 0:
                    return 0, []
                cursor = connection.execute(
                    f"SELECT {projection} FROM read_parquet(?) "
                    f"WHERE {where_clause} "
                    "ORDER BY transaction_time DESC, transaction_id ASC LIMIT ?",
                    [*parameters, limit],
                )
                names = [description[0] for description in cursor.description]
                rows = [
                    dict(zip(names, row, strict=True)) for row in cursor.fetchall()
                ]
        except (duckdb.Error, OSError) as error:
            raise RepositoryQueryError("transaction") from error
        return int(count), rows

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
