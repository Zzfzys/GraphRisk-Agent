from __future__ import annotations

from typing import Mapping

from pydantic import ValidationError

from graphrisk.investigation.contracts import (
    EvidenceRef,
    GetTransactionFeaturesRequest,
    GetTransactionFeaturesResult,
    GetTransactionRequest,
    GetTransactionResult,
    HistoricalFeaturesData,
    TransactionData,
    validate_tool_request,
)
from graphrisk.investigation.repository import (
    ArtifactUnavailableError,
    DuplicateTransactionError,
    InvestigationRepository,
    RepositoryQueryError,
)


def get_transaction(
    raw_request: Mapping[str, object], repository: InvestigationRepository
) -> GetTransactionResult:
    request, failure = validate_tool_request(
        GetTransactionRequest, GetTransactionResult, raw_request
    )
    if failure is not None:
        return failure
    assert request is not None
    if request.dataset_source != repository.dataset_source:
        return GetTransactionResult.failure(
            code="data_source_mismatch",
            transaction_id=request.transaction_id,
            dataset_source=request.dataset_source,
        )

    try:
        row = repository.transaction(request.transaction_id)
        if row is None:
            return GetTransactionResult.failure(
                code="transaction_not_found",
                transaction_id=request.transaction_id,
                dataset_source=request.dataset_source,
            )
        data = TransactionData.model_validate(row)
    except ArtifactUnavailableError:
        return GetTransactionResult.failure(
            code="transaction_artifact_unavailable",
            transaction_id=request.transaction_id,
            dataset_source=request.dataset_source,
        )
    except DuplicateTransactionError:
        return GetTransactionResult.failure(
            code="duplicate_transaction",
            transaction_id=request.transaction_id,
            dataset_source=request.dataset_source,
        )
    except (RepositoryQueryError, ValidationError):
        return GetTransactionResult.failure(
            code="query_failed",
            transaction_id=request.transaction_id,
            dataset_source=request.dataset_source,
        )
    return GetTransactionResult(
        status="ok",
        transaction_id=request.transaction_id,
        dataset_source=request.dataset_source,
        data=data,
        evidence=[
            EvidenceRef(
                source_kind="canonical_transaction",
                record_id=request.transaction_id,
                dataset_source=request.dataset_source,
            )
        ],
    )


def get_transaction_features(
    raw_request: Mapping[str, object], repository: InvestigationRepository
) -> GetTransactionFeaturesResult:
    request, failure = validate_tool_request(
        GetTransactionFeaturesRequest, GetTransactionFeaturesResult, raw_request
    )
    if failure is not None:
        return failure
    assert request is not None
    if request.dataset_source != repository.dataset_source:
        return GetTransactionFeaturesResult.failure(
            code="data_source_mismatch",
            transaction_id=request.transaction_id,
            dataset_source=request.dataset_source,
        )

    try:
        transaction = repository.transaction(request.transaction_id)
        if transaction is None:
            return GetTransactionFeaturesResult.failure(
                code="transaction_not_found",
                transaction_id=request.transaction_id,
                dataset_source=request.dataset_source,
            )
        row = repository.features(request.transaction_id)
        if row is None:
            return GetTransactionFeaturesResult.failure(
                code="feature_record_missing",
                transaction_id=request.transaction_id,
                dataset_source=request.dataset_source,
            )
        data = HistoricalFeaturesData.model_validate(row)
    except ArtifactUnavailableError as error:
        code = (
            "transaction_artifact_unavailable"
            if error.artifact == "transaction"
            else "feature_artifact_unavailable"
        )
        return GetTransactionFeaturesResult.failure(
            code=code,
            transaction_id=request.transaction_id,
            dataset_source=request.dataset_source,
        )
    except DuplicateTransactionError:
        return GetTransactionFeaturesResult.failure(
            code="duplicate_transaction",
            transaction_id=request.transaction_id,
            dataset_source=request.dataset_source,
        )
    except (RepositoryQueryError, ValidationError):
        return GetTransactionFeaturesResult.failure(
            code="query_failed",
            transaction_id=request.transaction_id,
            dataset_source=request.dataset_source,
        )
    return GetTransactionFeaturesResult(
        status="ok",
        transaction_id=request.transaction_id,
        dataset_source=request.dataset_source,
        data=data,
        evidence=[
            EvidenceRef(
                source_kind="historical_features",
                record_id=request.transaction_id,
                dataset_source=request.dataset_source,
            )
        ],
    )
