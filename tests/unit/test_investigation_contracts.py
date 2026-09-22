import pytest
from pydantic import ValidationError

from graphrisk.investigation.contracts import (
    EntityRef,
    EvidenceRef,
    FindRiskyNeighborsRequest,
    FindRiskyNeighborsResult,
    GetRelatedEntitiesRequest,
    GetRelatedEntitiesResult,
    GetRiskScoreRequest,
    GetRiskScoreResult,
    GetTransactionFeaturesRequest,
    GetTransactionFeaturesResult,
    GetTransactionRequest,
    GetTransactionResult,
    HistoricalFeaturesData,
    RelatedEntitiesData,
    RelatedTransaction,
    RiskScoreData,
    RiskyNeighbor,
    RiskyNeighborsData,
    ToolError,
    TransactionData,
    validate_tool_request,
)

REQUEST_RESULTS = (
    (GetTransactionRequest, GetTransactionResult),
    (GetTransactionFeaturesRequest, GetTransactionFeaturesResult),
    (GetRiskScoreRequest, GetRiskScoreResult),
    (GetRelatedEntitiesRequest, GetRelatedEntitiesResult),
    (FindRiskyNeighborsRequest, FindRiskyNeighborsResult),
)


@pytest.mark.parametrize("request_type,result_type", REQUEST_RESULTS)
def test_each_tool_request_validates_and_normalizes_id(request_type, result_type) -> None:
    request, failure = validate_tool_request(
        request_type,
        result_type,
        {"transaction_id": " 2987000 ", "dataset_source": "ieee_cis"},
    )

    assert failure is None
    assert request is not None
    assert request.transaction_id == "2987000"
    assert request.dataset_source == "ieee_cis"


@pytest.mark.parametrize("request_type,result_type", REQUEST_RESULTS)
@pytest.mark.parametrize("invalid_id", ["   ", "x" * 129])
def test_bad_transaction_id_returns_safe_error_envelope(
    request_type, result_type, invalid_id
) -> None:
    request, failure = validate_tool_request(
        request_type,
        result_type,
        {"transaction_id": invalid_id, "dataset_source": "ieee_cis"},
    )

    assert request is None
    assert failure is not None
    assert failure.status == "invalid_input"
    assert failure.error is not None
    assert failure.error.code == "invalid_transaction_id"
    assert failure.transaction_id is None
    assert failure.data is None
    assert failure.evidence == []
    assert invalid_id not in failure.model_dump_json()
    assert result_type.model_validate(failure.model_dump()) == failure


def test_invalid_limit_and_unknown_source_have_distinct_codes() -> None:
    _, invalid_limit = validate_tool_request(
        GetRelatedEntitiesRequest,
        GetRelatedEntitiesResult,
        {"transaction_id": "2987000", "dataset_source": "ieee_cis", "limit": 51},
    )
    _, invalid_source = validate_tool_request(
        GetRiskScoreRequest,
        GetRiskScoreResult,
        {"transaction_id": "2987000", "dataset_source": "other"},
    )

    assert invalid_limit is not None and invalid_limit.error is not None
    assert invalid_limit.error.code == "invalid_limit"
    assert invalid_source is not None and invalid_source.error is not None
    assert invalid_source.error.code == "invalid_dataset_source"


def test_unknown_fields_are_rejected_by_request_schema() -> None:
    _, failure = validate_tool_request(
        GetTransactionRequest,
        GetTransactionResult,
        {"transaction_id": "2987000", "dataset_source": "ieee_cis", "is_fraud": 1},
    )

    assert failure is not None and failure.error is not None
    assert failure.error.code == "invalid_request"


def test_success_payloads_are_typed_and_round_trip() -> None:
    results = (
        GetTransactionResult(
            status="ok",
            transaction_id="2987000",
            dataset_source="ieee_cis",
            data=TransactionData(
                transaction_id="2987000",
                transaction_amount=68.5,
                product_code="W",
                source_transaction_dt_seconds=86400,
                card_id="card-a",
                device_id="device-a",
                email_domain="example.com",
                address_id="address-a",
            ),
            evidence=[
                EvidenceRef(
                    source_kind="canonical_transaction",
                    record_id="2987000",
                    dataset_source="ieee_cis",
                )
            ],
        ),
        GetTransactionFeaturesResult(
            status="ok",
            transaction_id="2987000",
            dataset_source="ieee_cis",
            data=HistoricalFeaturesData(
                transaction_id="2987000",
                tx_count_last_1h=0,
                tx_count_last_24h=0,
                tx_count_last_7d=0,
                amount_mean_last_24h=None,
                amount_std_last_24h=None,
                amount_deviation_last_24h=None,
            ),
        ),
        GetRiskScoreResult(
            status="ok",
            transaction_id="2987000",
            dataset_source="ieee_cis",
            data=RiskScoreData(
                transaction_id="2987000",
                risk_score=0.3,
                risk_level="medium",
                model_name="xgboost",
                model_version="xgboost-v1",
            ),
        ),
        GetRelatedEntitiesResult(
            status="ok",
            transaction_id="2987000",
            dataset_source="ieee_cis",
            data=RelatedEntitiesData(
                transaction_id="2987000",
                matches=[
                    RelatedTransaction(
                        transaction_id="2986999",
                        seconds_before_target=60,
                        shared_entities=[EntityRef(entity_type="card", entity_id="card-a")],
                    )
                ],
                total_matches=1,
                truncated=False,
            ),
        ),
        FindRiskyNeighborsResult(
            status="ok",
            transaction_id="2987000",
            dataset_source="ieee_cis",
            data=RiskyNeighborsData(
                transaction_id="2987000",
                neighbors=[
                    RiskyNeighbor(
                        transaction_id="2986999",
                        graph_risk_score=0.8,
                        shared_entities=[EntityRef(entity_type="card", entity_id="card-a")],
                    )
                ],
                graph_model_version="graphsage-v1",
            ),
        ),
    )

    for result in results:
        assert type(result).model_validate(result.model_dump()) == result
        assert "is_fraud" not in result.model_dump_json()
        assert "identity_key" not in result.model_dump_json()


def test_not_found_unavailable_and_dependency_failure_are_distinct() -> None:
    missing = GetTransactionResult.failure(
        code="transaction_not_found",
        transaction_id="missing",
        dataset_source="ieee_cis",
    )
    unavailable = FindRiskyNeighborsResult.failure(
        code="capability_unavailable",
        transaction_id="2987000",
        dataset_source="ieee_cis",
    )
    model_missing = GetRiskScoreResult.failure(
        code="model_artifact_unavailable",
        transaction_id="2987000",
        dataset_source="ieee_cis",
    )
    timeout = GetRiskScoreResult.failure(
        code="dependency_timeout",
        transaction_id="2987000",
        dataset_source="ieee_cis",
    )

    assert (missing.status, missing.error.code) == (
        "not_found", "transaction_not_found"
    )
    assert (unavailable.status, unavailable.error.code) == (
        "unavailable", "capability_unavailable"
    )
    assert (model_missing.status, model_missing.error.code) == (
        "dependency_error", "model_artifact_unavailable"
    )
    assert model_missing.error.retryable is False
    assert timeout.error.retryable is True
    assert unavailable.data is None
    assert unavailable.evidence == []


def test_failure_cannot_contain_fabricated_evidence_or_mismatched_status() -> None:
    with pytest.raises(ValidationError, match="failed results require an error"):
        FindRiskyNeighborsResult(
            status="unavailable",
            transaction_id="2987000",
            dataset_source="ieee_cis",
            error=ToolError.from_code("capability_unavailable"),
            evidence=[
                EvidenceRef(
                    source_kind="entity_link",
                    record_id="2986999",
                    dataset_source="ieee_cis",
                )
            ],
        )

    with pytest.raises(ValidationError, match="error code does not match"):
        GetTransactionResult(
            status="not_found",
            transaction_id="2987000",
            dataset_source="ieee_cis",
            error=ToolError.from_code("model_artifact_unavailable"),
        )


def test_success_rejects_label_leak_and_cross_source_evidence() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TransactionData.model_validate(
            {
                "transaction_id": "2987000",
                "transaction_amount": 10,
                "product_code": "W",
                "card_id": "card-a",
                "device_id": "device-a",
                "email_domain": "example.com",
                "address_id": "address-a",
                "is_fraud": 1,
            }
        )

    with pytest.raises(ValidationError, match="evidence must match"):
        GetRiskScoreResult(
            status="ok",
            transaction_id="2987000",
            dataset_source="ieee_cis",
            data=RiskScoreData(
                transaction_id="2987000",
                risk_score=0.2,
                risk_level="low",
                model_name="xgboost",
                model_version="xgboost-v1",
            ),
            evidence=[
                EvidenceRef(
                    source_kind="model_artifact",
                    record_id="xgboost-v1",
                    dataset_source="synthetic",
                )
            ],
        )


def test_related_entities_empty_success_is_not_not_found() -> None:
    result = GetRelatedEntitiesResult(
        status="ok",
        transaction_id="2987000",
        dataset_source="ieee_cis",
        data=RelatedEntitiesData(
            transaction_id="2987000",
            matches=[],
            total_matches=0,
            truncated=False,
        ),
    )

    assert result.status == "ok"
    assert result.data.matches == []


def test_related_entity_count_and_payload_id_are_checked() -> None:
    with pytest.raises(ValidationError, match="total_matches cannot be less"):
        RelatedEntitiesData(
            transaction_id="2987000",
            matches=[
                RelatedTransaction(
                    transaction_id="2986999",
                    seconds_before_target=1,
                    shared_entities=[EntityRef(entity_type="card", entity_id="card-a")],
                )
            ],
            total_matches=0,
            truncated=False,
        )

    with pytest.raises(ValidationError, match="payload transaction ID must match"):
        GetRiskScoreResult(
            status="ok",
            transaction_id="2987000",
            dataset_source="ieee_cis",
            data=RiskScoreData(
                transaction_id="2987001",
                risk_score=0.2,
                risk_level="low",
                model_name="xgboost",
                model_version="xgboost-v1",
            ),
        )
