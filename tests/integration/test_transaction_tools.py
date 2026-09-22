from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from graphrisk.config import load_config
from graphrisk.features.transaction_features import build_historical_features
from graphrisk.investigation.repository import InvestigationRepository
from graphrisk.investigation.transaction_tools import (
    get_transaction,
    get_transaction_features,
)


@pytest.fixture
def repository(tmp_path: Path) -> InvestigationRepository:
    transactions = tmp_path / "transactions.parquet"
    features = tmp_path / "features.parquet"
    with duckdb.connect(":memory:") as connection:
        connection.execute(
            """
            CREATE TABLE transactions AS SELECT
                transaction_id, CAST(event_time AS TIMESTAMP) AS transaction_time,
                transaction_amount, card_id, device_id, email_domain,
                address_id, product_code, is_fraud, identity_key
            FROM (VALUES
                ('t0', '2025-01-01 00:00:00', 10.0, 'card-a', 'device-a',
                 'a.test', 'address-a', 'W', 1, 'identity-a'),
                ('t1', '2025-01-01 00:00:00', 30.0, 'card-a', 'device-a',
                 'a.test', 'address-a', 'W', 0, 'identity-a'),
                ('t2', '2025-01-01 00:01:00', 50.0, 'card-a', 'device-a',
                 'a.test', 'address-a', 'C', 0, 'identity-a')
            ) AS rows(transaction_id, event_time, transaction_amount,
                      card_id, device_id, email_domain, address_id,
                      product_code, is_fraud, identity_key)
            """
        )
        connection.execute(f"COPY transactions TO '{transactions.as_posix()}' (FORMAT PARQUET)")
    build_historical_features(transactions, features)
    return InvestigationRepository("synthetic", transactions, features)


def _request(transaction_id: str = "t0") -> dict[str, object]:
    return {"transaction_id": transaction_id, "dataset_source": "synthetic"}


def _copy_without_id(source: Path, destination: Path, excluded_id: str) -> None:
    with duckdb.connect(":memory:") as connection:
        connection.execute(
            f"COPY (SELECT * FROM read_parquet(?) WHERE transaction_id <> ?) "
            f"TO '{destination.as_posix()}' (FORMAT PARQUET)",
            [str(source), excluded_id],
        )


def test_transaction_and_features_return_only_approved_fields(repository) -> None:
    transaction = get_transaction(_request("t2"), repository)
    features = get_transaction_features(_request("t2"), repository)

    assert transaction.status == features.status == "ok"
    assert transaction.data is not None
    assert transaction.data.transaction_amount == 50.0
    assert transaction.data.source_transaction_dt_seconds is None
    assert transaction.evidence[0].source_kind == "canonical_transaction"
    assert features.data is not None
    assert features.data.tx_count_last_1h == 2
    assert features.data.tx_count_last_24h == 2
    assert features.data.tx_count_last_7d == 2
    assert features.data.amount_mean_last_24h == 20.0
    assert features.data.amount_std_last_24h == 10.0
    assert features.data.amount_deviation_last_24h == 30.0
    assert features.evidence[0].source_kind == "historical_features"
    for result in (transaction, features):
        serialized = result.model_dump_json()
        assert "is_fraud" not in serialized
        assert "identity_key" not in serialized
        assert "transaction_time" not in serialized


def test_same_time_events_are_not_in_history_and_null_is_preserved(repository) -> None:
    for transaction_id in ("t0", "t1"):
        result = get_transaction_features(_request(transaction_id), repository)
        assert result.status == "ok"
        assert result.data is not None
        assert result.data.tx_count_last_1h == 0
        assert result.data.amount_mean_last_24h is None
        assert result.data.amount_std_last_24h is None
        assert result.data.amount_deviation_last_24h is None


@pytest.mark.parametrize("tool", [get_transaction, get_transaction_features])
def test_no_transaction_is_not_found_and_sql_input_is_literal(repository, tool) -> None:
    for transaction_id in ("missing", "t0' OR 1=1 --"):
        result = tool(_request(transaction_id), repository)
        assert result.status == "not_found"
        assert result.error is not None
        assert result.error.code == "transaction_not_found"
        assert result.evidence == []


@pytest.mark.parametrize("tool", [get_transaction, get_transaction_features])
def test_invalid_request_stops_before_query(repository, tool) -> None:
    result = tool(_request(" "), repository)
    assert result.status == "invalid_input"
    assert result.error is not None
    assert result.error.code == "invalid_transaction_id"
    assert result.transaction_id is None


@pytest.mark.parametrize("tool", [get_transaction, get_transaction_features])
def test_requested_source_must_match_repository(repository, tool) -> None:
    result = tool({"transaction_id": "t0", "dataset_source": "ieee_cis"}, repository)
    assert result.status == "dependency_error"
    assert result.error is not None
    assert result.error.code == "data_source_mismatch"
    assert result.evidence == []


@pytest.mark.parametrize("tool", [get_transaction, get_transaction_features])
def test_missing_transaction_artifact_is_dependency_error(repository, tool) -> None:
    unavailable = InvestigationRepository(
        "synthetic", repository.transaction_parquet.with_name("missing.parquet"),
        repository.feature_parquet,
    )
    result = tool(_request(), unavailable)
    assert result.status == "dependency_error"
    assert result.error is not None
    assert result.error.code == "transaction_artifact_unavailable"


def test_missing_feature_file_and_row_are_distinct(repository, tmp_path: Path) -> None:
    missing_file = InvestigationRepository(
        "synthetic", repository.transaction_parquet,
        tmp_path / "missing-features.parquet",
    )
    file_result = get_transaction_features(_request(), missing_file)
    assert file_result.error is not None
    assert file_result.error.code == "feature_artifact_unavailable"

    shortened = tmp_path / "shortened-features.parquet"
    _copy_without_id(repository.feature_parquet, shortened, "t0")
    missing_row = InvestigationRepository(
        "synthetic", repository.transaction_parquet, shortened
    )
    row_result = get_transaction_features(_request(), missing_row)
    assert row_result.error is not None
    assert row_result.error.code == "feature_record_missing"


@pytest.mark.parametrize("tool", [get_transaction, get_transaction_features])
def test_duplicate_transaction_does_not_silently_pick_a_row(
    repository, tmp_path: Path, tool
) -> None:
    duplicated = tmp_path / "duplicates.parquet"
    with duckdb.connect(":memory:") as connection:
        connection.execute("CREATE TABLE duplicated AS SELECT * FROM read_parquet(?)", [str(repository.transaction_parquet)])
        connection.execute("INSERT INTO duplicated SELECT * FROM duplicated WHERE transaction_id = 't0'")
        connection.execute(f"COPY duplicated TO '{duplicated.as_posix()}' (FORMAT PARQUET)")
    duplicate_repository = InvestigationRepository(
        "synthetic", duplicated, repository.feature_parquet
    )
    result = tool(_request(), duplicate_repository)
    assert result.status == "dependency_error"
    assert result.error is not None
    assert result.error.code == "duplicate_transaction"


def test_duplicate_feature_record_does_not_silently_pick_a_row(repository, tmp_path: Path) -> None:
    duplicated = tmp_path / "duplicate-features.parquet"
    with duckdb.connect(":memory:") as connection:
        connection.execute("CREATE TABLE duplicated AS SELECT * FROM read_parquet(?)", [str(repository.feature_parquet)])
        connection.execute("INSERT INTO duplicated SELECT * FROM duplicated WHERE transaction_id = 't0'")
        connection.execute(f"COPY duplicated TO '{duplicated.as_posix()}' (FORMAT PARQUET)")
    duplicate_repository = InvestigationRepository(
        "synthetic", repository.transaction_parquet, duplicated
    )
    result = get_transaction_features(_request(), duplicate_repository)
    assert result.error is not None
    assert result.error.code == "duplicate_transaction"


def test_corrupt_parquet_is_structured_query_failure(repository, tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.parquet"
    corrupt.write_text("not a Parquet file", encoding="utf-8")
    broken_repository = InvestigationRepository(
        "synthetic", corrupt, repository.feature_parquet
    )
    result = get_transaction(_request(), broken_repository)
    assert result.error is not None
    assert result.error.code == "query_failed"
    assert str(corrupt) not in result.model_dump_json()


def test_repository_uses_explicit_config_source_and_paths() -> None:
    config = load_config("configs/ieee_cis.yaml")
    repository = InvestigationRepository.from_config(config)
    assert repository.dataset_source == "ieee_cis"
    assert repository.transaction_parquet == config.resolve_path(config.paths.interim_transactions)
    assert repository.feature_parquet == config.resolve_path(config.paths.interim_features)
