from __future__ import annotations

from datetime import datetime
from math import ceil
from typing import Mapping

from pydantic import ValidationError

from graphrisk.graph.service import GraphRiskService, GraphTargetNotCoveredError
from graphrisk.investigation.contracts import (
    EntityRef,
    EvidenceRef,
    FindRiskyNeighborsRequest,
    FindRiskyNeighborsResult,
    GetRelatedEntitiesRequest,
    GetRelatedEntitiesResult,
    RelatedEntitiesData,
    RelatedTransaction,
    RiskyNeighbor,
    RiskyNeighborsData,
    TransactionData,
    validate_tool_request,
)
from graphrisk.investigation.repository import (
    ArtifactUnavailableError,
    DuplicateTransactionError,
    InvestigationRepository,
    RepositoryQueryError,
)


ENTITY_TYPES = (
    ("card_id", "card"),
    ("device_id", "device"),
    ("email_domain", "email_domain"),
    ("address_id", "address"),
)


def get_related_entities(
    raw_request: Mapping[str, object], repository: InvestigationRepository
) -> GetRelatedEntitiesResult:
    request, failure = validate_tool_request(
        GetRelatedEntitiesRequest, GetRelatedEntitiesResult, raw_request
    )
    if failure is not None:
        return failure
    assert request is not None
    if request.dataset_source != repository.dataset_source:
        return GetRelatedEntitiesResult.failure(
            code="data_source_mismatch",
            transaction_id=request.transaction_id,
            dataset_source=request.dataset_source,
        )

    try:
        target_row = repository.transaction_with_time(request.transaction_id)
        if target_row is None:
            return GetRelatedEntitiesResult.failure(
                code="transaction_not_found",
                transaction_id=request.transaction_id,
                dataset_source=request.dataset_source,
            )
        target_time = target_row.pop("transaction_time")
        if not isinstance(target_time, datetime):
            raise ValueError("target transaction time is unavailable")
        target = TransactionData.model_validate(target_row)
        active_entities = {
            field: getattr(target, field)
            for field, _ in ENTITY_TYPES
            if not getattr(target, field).lower().startswith("unknown-")
        }
        total, rows = repository.related_transactions(
            transaction_id=request.transaction_id,
            target_time=target_time,
            active_entities=active_entities,
            limit=request.limit,
        )
        matches = [
            _related_transaction(row, target_time, active_entities)
            for row in rows
        ]
        data = RelatedEntitiesData(
            transaction_id=request.transaction_id,
            matches=matches,
            total_matches=total,
            truncated=total > len(matches),
        )
    except ArtifactUnavailableError:
        return GetRelatedEntitiesResult.failure(
            code="transaction_artifact_unavailable",
            transaction_id=request.transaction_id,
            dataset_source=request.dataset_source,
        )
    except DuplicateTransactionError:
        return GetRelatedEntitiesResult.failure(
            code="duplicate_transaction",
            transaction_id=request.transaction_id,
            dataset_source=request.dataset_source,
        )
    except (RepositoryQueryError, ValidationError, TypeError, ValueError):
        return GetRelatedEntitiesResult.failure(
            code="query_failed",
            transaction_id=request.transaction_id,
            dataset_source=request.dataset_source,
        )
    return GetRelatedEntitiesResult(
        status="ok",
        transaction_id=request.transaction_id,
        dataset_source=request.dataset_source,
        data=data,
        evidence=[
            EvidenceRef(
                source_kind="entity_link",
                record_id=match.transaction_id,
                dataset_source=request.dataset_source,
            )
            for match in matches
        ],
    )


def _related_transaction(
    row: dict[str, object],
    target_time: datetime,
    active_entities: dict[str, str],
) -> RelatedTransaction:
    candidate_time = row["transaction_time"]
    if not isinstance(candidate_time, datetime):
        raise ValueError("related transaction time is unavailable")
    elapsed_seconds = (target_time - candidate_time).total_seconds()
    if elapsed_seconds <= 0:
        raise ValueError("related transaction must strictly precede the target")
    shared_entities = [
        EntityRef(entity_type=entity_type, entity_id=entity_id)
        for field, entity_type in ENTITY_TYPES
        if (entity_id := active_entities.get(field)) is not None
        and row[field] == entity_id
    ]
    if not shared_entities:
        raise ValueError("related transaction has no eligible shared entity")
    return RelatedTransaction(
        transaction_id=row["transaction_id"],
        seconds_before_target=ceil(elapsed_seconds),
        shared_entities=shared_entities,
    )


def find_risky_neighbors(
    raw_request: Mapping[str, object],
    repository: InvestigationRepository,
    graph_service: GraphRiskService | None = None,
) -> FindRiskyNeighborsResult:
    request, failure = validate_tool_request(
        FindRiskyNeighborsRequest, FindRiskyNeighborsResult, raw_request
    )
    if failure is not None:
        return failure
    assert request is not None
    def failed(code):
        return FindRiskyNeighborsResult.failure(
            code=code,
            transaction_id=request.transaction_id,
            dataset_source=request.dataset_source,
        )

    if request.dataset_source != repository.dataset_source:
        return failed("data_source_mismatch")
    if graph_service is None:
        return failed("capability_unavailable")
    if graph_service.dataset_source != request.dataset_source:
        return failed("data_source_mismatch")
    if graph_service.startup_error is not None:
        return failed(graph_service.startup_error)
    if graph_service.transaction_parquet is None or graph_service.feature_parquet is None:
        return failed("graph_artifact_unavailable")
    if (
        graph_service.transaction_parquet.resolve() != repository.transaction_parquet.resolve()
        or graph_service.feature_parquet.resolve() != repository.feature_parquet.resolve()
    ):
        return failed("data_source_mismatch")
    try:
        if repository.transaction_with_time(request.transaction_id) is None:
            return failed("transaction_not_found")
        target_prediction = graph_service.predict(request.transaction_id)
        matches = graph_service.find_neighbors(request.transaction_id, limit=request.limit)
        neighbors = [
            RiskyNeighbor(
                transaction_id=match.transaction_id,
                graph_risk_score=match.graph_risk_score,
                seconds_before_target=match.seconds_before_target,
                shared_entities=[
                    EntityRef(entity_type=entity_type, entity_id=entity_id)
                    for entity_type, entity_id in match.shared_entities
                ],
                evidence_role=getattr(match,"evidence_role","scored"),
                score_unavailable_reason=getattr(match,"score_unavailable_reason",None),
            )
            for match in matches
        ]
        data = RiskyNeighborsData(
            transaction_id=request.transaction_id,
            neighbors=neighbors,
            graph_model_version=target_prediction.graph_model_version,
            target_graph_risk_score=target_prediction.graph_risk_score,
            controlled_xgboost_score=target_prediction.controlled_xgboost_score,
            controlled_xgboost_model_version=target_prediction.controlled_xgboost_model_version,
            fusion_score=target_prediction.fusion_score,
            fusion_alpha=target_prediction.fusion_alpha,
            score_role=target_prediction.score_role,
        )
    except ArtifactUnavailableError:
        return failed("transaction_artifact_unavailable")
    except DuplicateTransactionError:
        return failed("duplicate_transaction")
    except GraphTargetNotCoveredError:
        return failed("graph_prediction_unavailable")
    except (RepositoryQueryError, ValidationError, TypeError, ValueError, RuntimeError):
        return failed("graph_artifact_unavailable")
    return FindRiskyNeighborsResult(
        status="ok",
        transaction_id=request.transaction_id,
        dataset_source=request.dataset_source,
        data=data,
        evidence=[
            EvidenceRef(
                source_kind="model_artifact",
                record_id=target_prediction.graph_model_version,
                dataset_source=request.dataset_source,
            ),
            *([EvidenceRef(
                source_kind="model_artifact",
                record_id=target_prediction.controlled_xgboost_model_version,
                dataset_source=request.dataset_source,
            )] if target_prediction.controlled_xgboost_model_version is not None else []),
            *[
                EvidenceRef(
                    source_kind="entity_link",
                    record_id=neighbor.transaction_id,
                    dataset_source=request.dataset_source,
                )
                for neighbor in neighbors
            ],
        ],
    )
