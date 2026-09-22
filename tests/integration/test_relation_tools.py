from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from graphrisk.investigation.relation_tools import (
    find_risky_neighbors,
    get_related_entities,
)
from graphrisk.investigation.repository import InvestigationRepository


@pytest.fixture
def repository(tmp_path: Path) -> InvestigationRepository:
    def row(
        transaction_id: str,
        time: str,
        card: str,
        device: str,
        email: str,
        address: str,
    ) -> dict[str, object]:
        return {
            "transaction_id": transaction_id,
            "transaction_time": pd.Timestamp(time),
            "transaction_amount": 10.0,
            "card_id": card,
            "device_id": device,
            "email_domain": email,
            "address_id": address,
            "product_code": "W",
            "is_fraud": 0,
            "identity_key": "internal-only",
        }

    rows = [
        row("T", "2025-01-01 00:10:00", "card-A", "device-A", "mail-A", "address-A"),
        row("H_half", "2025-01-01 00:09:59.500000", "card-A", "d-1", "m-1", "a-1"),
        row("H_card", "2025-01-01 00:09:00", "card-A", "d-2", "m-2", "a-2"),
        row("H_device", "2025-01-01 00:09:00", "c-3", "device-A", "m-3", "a-3"),
        row("H_email", "2025-01-01 00:08:00", "c-4", "d-4", "mail-A", "a-4"),
        row("H_address", "2025-01-01 00:07:00", "c-5", "d-5", "m-5", "address-A"),
        row("H_multi", "2025-01-01 00:06:00", "card-A", "device-A", "mail-A", "address-A"),
        row("H_same", "2025-01-01 00:10:00", "card-A", "device-A", "mail-A", "address-A"),
        row("H_future", "2025-01-01 00:11:00", "card-A", "device-A", "mail-A", "address-A"),
        row(
            "U", "2025-01-01 00:10:00", "unknown-card-X", "unknown-device-X",
            "unknown-email-X", "unknown-address-X",
        ),
        row(
            "U_before", "2025-01-01 00:09:00", "unknown-card-X", "unknown-device-X",
            "unknown-email-X", "unknown-address-X",
        ),
    ]
    transactions = tmp_path / "transactions.parquet"
    with duckdb.connect(":memory:") as connection:
        connection.register("rows", pd.DataFrame(rows))
        connection.sql("SELECT * FROM rows").write_parquet(str(transactions))
    return InvestigationRepository("synthetic", transactions, tmp_path / "unused.parquet")


def _request(transaction_id: str = "T", limit: int = 20) -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        "dataset_source": "synthetic",
        "limit": limit,
    }


def test_four_entity_types_and_multi_hit_are_one_related_transaction(repository) -> None:
    result = get_related_entities(_request(), repository)

    assert result.status == "ok"
    assert result.data is not None
    assert result.data.total_matches == 6
    assert result.data.truncated is False
    by_id = {match.transaction_id: match for match in result.data.matches}
    assert len(result.data.matches) == len(by_id) == 6
    assert [entity.entity_type for entity in by_id["H_card"].shared_entities] == ["card"]
    assert [entity.entity_type for entity in by_id["H_device"].shared_entities] == ["device"]
    assert [entity.entity_type for entity in by_id["H_email"].shared_entities] == ["email_domain"]
    assert [entity.entity_type for entity in by_id["H_address"].shared_entities] == ["address"]
    assert [entity.entity_type for entity in by_id["H_multi"].shared_entities] == [
        "card", "device", "email_domain", "address"
    ]
    assert "H_same" not in by_id
    assert "H_future" not in by_id
    assert len(result.evidence) == 6
    assert {ref.record_id for ref in result.evidence} == set(by_id)
    assert all(ref.source_kind == "entity_link" for ref in result.evidence)
    assert "is_fraud" not in result.model_dump_json()
    assert "identity_key" not in result.model_dump_json()


def test_stable_order_limit_total_and_positive_subsecond_difference(repository) -> None:
    result = get_related_entities(_request(limit=2), repository)

    assert result.data is not None
    assert [match.transaction_id for match in result.data.matches] == [
        "H_half", "H_card"
    ]
    assert result.data.matches[0].seconds_before_target == 1
    assert result.data.matches[1].seconds_before_target == 60
    assert result.data.total_matches == 6
    assert result.data.truncated is True
    assert get_related_entities(_request(limit=2), repository) == result


def test_unknown_placeholders_do_not_form_relation(repository) -> None:
    result = get_related_entities(_request("U"), repository)

    assert result.status == "ok"
    assert result.data is not None
    assert result.data.matches == []
    assert result.data.total_matches == 0
    assert result.data.truncated is False
    assert result.evidence == []


@pytest.mark.parametrize("transaction_id", ["missing", "T' OR 1=1 --"])
def test_missing_transaction_and_sql_text_are_not_found(repository, transaction_id) -> None:
    result = get_related_entities(_request(transaction_id), repository)
    assert result.status == "not_found"
    assert result.error is not None and result.error.code == "transaction_not_found"
    assert result.evidence == []


@pytest.mark.parametrize("limit", [0, 51])
def test_invalid_limit_is_rejected_before_query(repository, limit) -> None:
    result = get_related_entities(_request(limit=limit), repository)
    assert result.status == "invalid_input"
    assert result.error is not None and result.error.code == "invalid_limit"


def test_request_source_mismatch_is_dependency_error(repository) -> None:
    request = _request()
    request["dataset_source"] = "ieee_cis"
    result = get_related_entities(request, repository)
    assert result.error is not None and result.error.code == "data_source_mismatch"


def test_missing_or_corrupt_transaction_artifact_is_structured(repository, tmp_path) -> None:
    missing = InvestigationRepository(
        "synthetic", tmp_path / "missing.parquet", repository.feature_parquet
    )
    absent_result = get_related_entities(_request(), missing)
    assert absent_result.error is not None
    assert absent_result.error.code == "transaction_artifact_unavailable"

    corrupt = tmp_path / "corrupt.parquet"
    corrupt.write_text("broken", encoding="utf-8")
    damaged = InvestigationRepository("synthetic", corrupt, repository.feature_parquet)
    damaged_result = get_related_entities(_request(), damaged)
    assert damaged_result.error is not None
    assert damaged_result.error.code == "query_failed"
    assert str(corrupt) not in damaged_result.model_dump_json()


@pytest.mark.parametrize("duplicated_id", ["T", "H_card"])
def test_duplicate_target_or_related_transaction_is_not_silently_used(
    repository, tmp_path, duplicated_id
) -> None:
    duplicated = tmp_path / "duplicates.parquet"
    with duckdb.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE duplicated AS SELECT * FROM read_parquet(?)",
            [str(repository.transaction_parquet)],
        )
        connection.execute(
            "INSERT INTO duplicated SELECT * FROM duplicated WHERE transaction_id = ?",
            [duplicated_id],
        )
        connection.sql("SELECT * FROM duplicated").write_parquet(str(duplicated))
    result = get_related_entities(
        _request(), InvestigationRepository("synthetic", duplicated, repository.feature_parquet)
    )
    assert result.error is not None and result.error.code == "duplicate_transaction"


def test_graph_placeholder_is_explicitly_unavailable_without_evidence(repository) -> None:
    result = find_risky_neighbors(_request(), repository)
    assert result.status == "unavailable"
    assert result.error is not None and result.error.code == "capability_unavailable"
    assert result.data is None
    assert result.evidence == []


def test_graph_placeholder_still_validates_input_and_source(repository) -> None:
    invalid = find_risky_neighbors(_request(" "), repository)
    assert invalid.error is not None and invalid.error.code == "invalid_transaction_id"
    wrong_source = _request()
    wrong_source["dataset_source"] = "ieee_cis"
    mismatch = find_risky_neighbors(wrong_source, repository)
    assert mismatch.error is not None and mismatch.error.code == "data_source_mismatch"
