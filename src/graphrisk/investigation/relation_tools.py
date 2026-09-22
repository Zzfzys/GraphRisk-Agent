from __future__ import annotations

from datetime import datetime
from math import ceil
from typing import Mapping

from pydantic import ValidationError

from graphrisk.investigation.contracts import (
    EntityRef,
    EvidenceRef,
    FindRiskyNeighborsRequest,
    FindRiskyNeighborsResult,
    GetRelatedEntitiesRequest,
    GetRelatedEntitiesResult,
    RelatedEntitiesData,
    RelatedTransaction,
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
    raw_request: Mapping[str, object], repository: InvestigationRepository
) -> FindRiskyNeighborsResult:
    request, failure = validate_tool_request(
        FindRiskyNeighborsRequest, FindRiskyNeighborsResult, raw_request
    )
    if failure is not None:
        return failure
    assert request is not None
    code = (
        "data_source_mismatch"
        if request.dataset_source != repository.dataset_source
        else "capability_unavailable"
    )
    return FindRiskyNeighborsResult.failure(
        code=code,
        transaction_id=request.transaction_id,
        dataset_source=request.dataset_source,
    )
