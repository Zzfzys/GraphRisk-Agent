from __future__ import annotations

from typing import Annotated, Any, Generic, Literal, Mapping, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

DatasetSource = Literal["synthetic", "ieee_cis"]
TransactionId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
ToolStatus = Literal[
    "ok", "not_found", "invalid_input", "unavailable", "dependency_error"
]
FailureStatus = Literal[
    "not_found", "invalid_input", "unavailable", "dependency_error"
]
ErrorCode = Literal[
    "invalid_transaction_id",
    "invalid_dataset_source",
    "invalid_limit",
    "invalid_request",
    "transaction_not_found",
    "capability_unavailable",
    "transaction_artifact_unavailable",
    "feature_artifact_unavailable",
    "feature_record_missing",
    "model_artifact_unavailable",
    "graph_artifact_unavailable",
    "graph_prediction_unavailable",
    "data_source_mismatch",
    "duplicate_transaction",
    "query_failed",
    "prediction_failed",
    "dependency_timeout",
]

ERROR_STATUS: dict[ErrorCode, FailureStatus] = {
    "invalid_transaction_id": "invalid_input",
    "invalid_dataset_source": "invalid_input",
    "invalid_limit": "invalid_input",
    "invalid_request": "invalid_input",
    "transaction_not_found": "not_found",
    "capability_unavailable": "unavailable",
    "transaction_artifact_unavailable": "dependency_error",
    "feature_artifact_unavailable": "dependency_error",
    "feature_record_missing": "dependency_error",
    "model_artifact_unavailable": "dependency_error",
    "graph_artifact_unavailable": "dependency_error",
    "graph_prediction_unavailable": "unavailable",
    "data_source_mismatch": "dependency_error",
    "duplicate_transaction": "dependency_error",
    "query_failed": "dependency_error",
    "prediction_failed": "dependency_error",
    "dependency_timeout": "dependency_error",
}
ERROR_MESSAGES: dict[ErrorCode, str] = {
    "invalid_transaction_id": "Provide a non-empty transaction ID of at most 128 characters.",
    "invalid_dataset_source": "Choose a supported dataset source.",
    "invalid_limit": "Choose a result limit between 1 and 50.",
    "invalid_request": "The tool request does not match its input schema.",
    "transaction_not_found": "The transaction was not found in the selected dataset.",
    "capability_unavailable": "This investigation capability is not available yet.",
    "transaction_artifact_unavailable": "The transaction data is unavailable.",
    "feature_artifact_unavailable": "The historical feature data is unavailable.",
    "feature_record_missing": "The transaction has no historical feature record.",
    "model_artifact_unavailable": "The risk model is unavailable.",
    "graph_artifact_unavailable": "The graph model or graph data artifact is unavailable.",
    "graph_prediction_unavailable": "No eligible graph prediction is available for this transaction.",
    "data_source_mismatch": "The requested and configured data sources do not match.",
    "duplicate_transaction": "The transaction ID is not unique in the selected dataset.",
    "query_failed": "The investigation data query failed.",
    "prediction_failed": "The risk model could not produce a prediction.",
    "dependency_timeout": "A required investigation dependency timed out.",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TransactionToolRequest(StrictModel):
    transaction_id: TransactionId
    dataset_source: DatasetSource


class GetTransactionRequest(TransactionToolRequest):
    pass


class GetTransactionFeaturesRequest(TransactionToolRequest):
    pass


class GetRiskScoreRequest(TransactionToolRequest):
    pass


class GetRelatedEntitiesRequest(TransactionToolRequest):
    limit: int = Field(default=20, ge=1, le=50)


class FindRiskyNeighborsRequest(TransactionToolRequest):
    limit: int = Field(default=20, ge=1, le=50)


class EvidenceRef(StrictModel):
    source_kind: Literal[
        "canonical_transaction", "historical_features", "model_artifact", "entity_link"
    ]
    record_id: str = Field(min_length=1, max_length=128)
    dataset_source: DatasetSource


class ToolError(StrictModel):
    code: ErrorCode
    message: str
    retryable: bool

    @classmethod
    def from_code(cls, code: ErrorCode) -> "ToolError":
        return cls(
            code=code,
            message=ERROR_MESSAGES[code],
            retryable=code == "dependency_timeout",
        )

    @model_validator(mode="after")
    def message_and_retry_policy_are_standard(self) -> "ToolError":
        if self.message != ERROR_MESSAGES[self.code]:
            raise ValueError("tool error message must be the standard message for its code")
        if self.retryable != (self.code == "dependency_timeout"):
            raise ValueError("retryable must match the tool error code")
        return self


class TransactionData(StrictModel):
    transaction_id: TransactionId
    transaction_amount: float = Field(ge=0)
    product_code: str = Field(min_length=1)
    source_transaction_dt_seconds: int | None = Field(default=None, ge=0)
    card_id: str = Field(min_length=1)
    device_id: str = Field(min_length=1)
    email_domain: str = Field(min_length=1)
    address_id: str = Field(min_length=1)


class HistoricalFeaturesData(StrictModel):
    transaction_id: TransactionId
    tx_count_last_1h: int = Field(ge=0)
    tx_count_last_24h: int = Field(ge=0)
    tx_count_last_7d: int = Field(ge=0)
    amount_mean_last_24h: float | None
    amount_std_last_24h: float | None = Field(ge=0)
    amount_deviation_last_24h: float | None


class RiskScoreData(StrictModel):
    transaction_id: TransactionId
    risk_score: float = Field(ge=0, le=1)
    risk_level: Literal["low", "medium", "high"]
    model_name: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    probability_calibrated: Literal[False] = False


class EntityRef(StrictModel):
    entity_type: Literal["card", "device", "email_domain", "address"]
    entity_id: str = Field(min_length=1)


class RelatedTransaction(StrictModel):
    transaction_id: TransactionId
    seconds_before_target: int = Field(gt=0)
    shared_entities: list[EntityRef] = Field(min_length=1)


class RelatedEntitiesData(StrictModel):
    transaction_id: TransactionId
    matches: list[RelatedTransaction]
    total_matches: int = Field(ge=0)
    truncated: bool

    @model_validator(mode="after")
    def count_matches_truncation(self) -> "RelatedEntitiesData":
        if self.total_matches < len(self.matches):
            raise ValueError("total_matches cannot be less than returned matches")
        if self.truncated != (self.total_matches > len(self.matches)):
            raise ValueError("truncated must reflect whether matches were omitted")
        return self


class RiskyNeighbor(StrictModel):
    transaction_id: TransactionId
    graph_risk_score: float = Field(ge=0, le=1)
    shared_entities: list[EntityRef] = Field(min_length=1)


class RiskyNeighborsData(StrictModel):
    transaction_id: TransactionId
    neighbors: list[RiskyNeighbor]
    graph_model_version: str = Field(min_length=1)


PayloadT = TypeVar("PayloadT")


class ToolResult(StrictModel, Generic[PayloadT]):
    schema_version: Literal["1.0"] = "1.0"
    tool: str
    status: ToolStatus
    transaction_id: TransactionId | None = None
    dataset_source: DatasetSource | None = None
    data: PayloadT | None = None
    error: ToolError | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @classmethod
    def failure(
        cls,
        *,
        code: ErrorCode,
        transaction_id: str | None,
        dataset_source: DatasetSource | None,
    ) -> Self:
        return cls(
            status=ERROR_STATUS[code],
            transaction_id=transaction_id,
            dataset_source=dataset_source,
            error=ToolError.from_code(code),
        )

    @model_validator(mode="after")
    def result_shape_matches_status(self) -> "ToolResult[PayloadT]":
        if self.status == "ok":
            if self.data is None or self.error is not None:
                raise ValueError("ok results require data and cannot contain an error")
            if self.transaction_id is None or self.dataset_source is None:
                raise ValueError("ok results require transaction ID and dataset source")
            payload_transaction_id = getattr(self.data, "transaction_id", None)
            if payload_transaction_id != self.transaction_id:
                raise ValueError("payload transaction ID must match the result")
        else:
            if self.data is not None or self.error is None or self.evidence:
                raise ValueError("failed results require an error and no data or evidence")
            if ERROR_STATUS[self.error.code] != self.status:
                raise ValueError("error code does not match result status")
            if self.status != "invalid_input" and (
                self.transaction_id is None or self.dataset_source is None
            ):
                raise ValueError("non-input failures require transaction ID and dataset source")
        if any(ref.dataset_source != self.dataset_source for ref in self.evidence):
            raise ValueError("evidence must match the selected dataset source")
        return self


class GetTransactionResult(ToolResult[TransactionData]):
    tool: Literal["get_transaction"] = "get_transaction"


class GetTransactionFeaturesResult(ToolResult[HistoricalFeaturesData]):
    tool: Literal["get_transaction_features"] = "get_transaction_features"


class GetRiskScoreResult(ToolResult[RiskScoreData]):
    tool: Literal["get_risk_score"] = "get_risk_score"


class GetRelatedEntitiesResult(ToolResult[RelatedEntitiesData]):
    tool: Literal["get_related_entities"] = "get_related_entities"


class FindRiskyNeighborsResult(ToolResult[RiskyNeighborsData]):
    tool: Literal["find_risky_neighbors"] = "find_risky_neighbors"


RequestT = TypeVar("RequestT", bound=TransactionToolRequest)
ResultT = TypeVar("ResultT", bound=ToolResult[Any])


def validate_tool_request(
    request_type: type[RequestT],
    result_type: type[ResultT],
    raw_request: Mapping[str, object],
) -> tuple[RequestT | None, ResultT | None]:
    """Return a typed request or a safe, structured input failure."""
    try:
        return request_type.model_validate(raw_request), None
    except ValidationError as error:
        first_field = str(error.errors()[0]["loc"][0])
        if first_field == "transaction_id":
            code: ErrorCode = "invalid_transaction_id"
        elif first_field == "dataset_source":
            code = "invalid_dataset_source"
        elif first_field == "limit":
            code = "invalid_limit"
        else:
            code = "invalid_request"
        return None, result_type.failure(
            code=code,
            transaction_id=None,
            dataset_source=None,
        )
