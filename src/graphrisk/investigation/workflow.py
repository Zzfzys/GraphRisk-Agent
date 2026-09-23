from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Mapping

from pydantic import Field, model_validator

from graphrisk.investigation.contracts import (
    DatasetSource,
    ErrorCode,
    FindRiskyNeighborsResult,
    GetRelatedEntitiesResult,
    GetRiskScoreResult,
    GetTransactionFeaturesResult,
    GetTransactionResult,
    StrictModel,
    ToolError,
    ToolStatus,
    TransactionId,
)
from graphrisk.investigation.relation_tools import (
    find_risky_neighbors,
    get_related_entities,
)
from graphrisk.investigation.risk_tool import RiskModelTool, get_risk_score
from graphrisk.investigation.transaction_tools import (
    get_transaction,
    get_transaction_features,
)

ToolName = Literal[
    "get_transaction",
    "get_transaction_features",
    "get_risk_score",
    "get_related_entities",
    "find_risky_neighbors",
]
ReportStatus = Literal["completed", "partial", "not_found", "invalid_input", "failed"]


class InvestigationRequest(StrictModel):
    transaction_id: TransactionId
    dataset_source: DatasetSource
    relation_limit: int = Field(default=20, ge=1, le=50)


class EvidenceBinding(StrictModel):
    section: Literal[
        "transaction", "historical_features", "risk_score", "related_entities"
    ]
    source_tool: ToolName
    record_ids: list[str] = Field(min_length=1)


class InvestigationIssue(StrictModel):
    tool: ToolName
    status: Literal["not_found", "invalid_input", "dependency_error"]
    error: ToolError


class CapabilityGap(StrictModel):
    tool: Literal["find_risky_neighbors"]
    status: Literal["unavailable"] = "unavailable"
    error: ToolError
    planned_stage: Literal["Stage 04"] = "Stage 04"


class InvestigationReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    status: ReportStatus
    transaction_id: TransactionId | None = None
    dataset_source: DatasetSource | None = None
    transaction: GetTransactionResult
    historical_features: GetTransactionFeaturesResult | None = None
    risk_score: GetRiskScoreResult | None = None
    related_entities: GetRelatedEntitiesResult | None = None
    risky_neighbors: FindRiskyNeighborsResult | None = None
    evidence_bindings: list[EvidenceBinding] = Field(default_factory=list)
    issues: list[InvestigationIssue] = Field(default_factory=list)
    unavailable_capabilities: list[CapabilityGap] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def status_matches_sections(self) -> "InvestigationReport":
        if self.status in ("completed", "partial") and self.transaction.status != "ok":
            raise ValueError("completed or partial reports require transaction facts")
        if self.status == "completed" and self.issues:
            raise ValueError("completed reports cannot contain investigation issues")
        if self.status == "partial" and not self.issues:
            raise ValueError("partial reports require at least one investigation issue")
        return self


class AuditEvent(StrictModel):
    investigation_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    tool: ToolName
    started_at: datetime
    completed_at: datetime
    duration_ms: float = Field(ge=0)
    status: ToolStatus
    error_code: ErrorCode | None = None

    @model_validator(mode="after")
    def timestamps_and_error_match(self) -> "AuditEvent":
        for value in (self.started_at, self.completed_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("audit timestamps must include a timezone")
        if self.completed_at < self.started_at:
            raise ValueError("audit completion cannot precede start")
        if (self.error_code is None) != (self.status == "ok"):
            raise ValueError("audit error code must match tool status")
        return self


class InvestigationAudit(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    investigation_id: str = Field(min_length=1, max_length=128)
    events: list[AuditEvent]

    @model_validator(mode="after")
    def events_are_ordered_and_bound(self) -> "InvestigationAudit":
        if [event.sequence for event in self.events] != list(
            range(1, len(self.events) + 1)
        ):
            raise ValueError("audit event sequence must be contiguous")
        if any(
            event.investigation_id != self.investigation_id for event in self.events
        ):
            raise ValueError("audit events must use the trace investigation ID")
        return self


class InvestigationRun(StrictModel):
    report: InvestigationReport
    audit: InvestigationAudit


class InvestigationWorkflow:
    def __init__(
        self,
        risk_tool: RiskModelTool,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
        investigation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.risk_tool = risk_tool
        self.repository = risk_tool.repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic_ns = monotonic_ns or time.perf_counter_ns
        self._investigation_id_factory = investigation_id_factory or (
            lambda: str(uuid.uuid4())
        )

    def run(self, raw_request: Mapping[str, object]) -> InvestigationRun:
        investigation_id = self._investigation_id_factory()
        events: list[AuditEvent] = []

        transaction_request = {
            key: value for key, value in raw_request.items() if key != "relation_limit"
        }
        transaction = self._invoke(
            investigation_id,
            events,
            "get_transaction",
            lambda: get_transaction(transaction_request, self.repository),
            GetTransactionResult,
            transaction_request,
            "query_failed",
        )
        if transaction.status != "ok":
            report = self._stopped_report(transaction)
            return InvestigationRun(
                report=report,
                audit=InvestigationAudit(
                    investigation_id=investigation_id, events=events
                ),
            )

        assert transaction.transaction_id is not None
        assert transaction.dataset_source is not None
        relation_limit = raw_request.get("relation_limit", 20)
        common_request = {
            "transaction_id": transaction.transaction_id,
            "dataset_source": transaction.dataset_source,
        }
        relation_request = {**common_request, "limit": relation_limit}

        features = self._invoke(
            investigation_id,
            events,
            "get_transaction_features",
            lambda: get_transaction_features(common_request, self.repository),
            GetTransactionFeaturesResult,
            common_request,
            "query_failed",
        )
        risk = self._invoke(
            investigation_id,
            events,
            "get_risk_score",
            lambda: get_risk_score(common_request, self.risk_tool),
            GetRiskScoreResult,
            common_request,
            "prediction_failed",
        )
        relations = self._invoke(
            investigation_id,
            events,
            "get_related_entities",
            lambda: get_related_entities(relation_request, self.repository),
            GetRelatedEntitiesResult,
            common_request,
            "query_failed",
        )
        graph = self._invoke(
            investigation_id,
            events,
            "find_risky_neighbors",
            lambda: find_risky_neighbors(relation_request, self.repository),
            FindRiskyNeighborsResult,
            common_request,
            "query_failed",
        )

        outputs = [features, risk, relations]
        issues = [self._issue(result) for result in outputs if result.status != "ok"]
        bindings = [
            EvidenceBinding(
                section="transaction",
                source_tool="get_transaction",
                record_ids=[transaction.transaction_id],
            )
        ]
        if features.status == "ok":
            bindings.append(
                EvidenceBinding(
                    section="historical_features",
                    source_tool="get_transaction_features",
                    record_ids=[transaction.transaction_id],
                )
            )
        if risk.status == "ok" and risk.data is not None:
            bindings.append(
                EvidenceBinding(
                    section="risk_score",
                    source_tool="get_risk_score",
                    record_ids=[transaction.transaction_id, risk.data.model_version],
                )
            )
        if relations.status == "ok" and relations.data is not None:
            bindings.append(
                EvidenceBinding(
                    section="related_entities",
                    source_tool="get_related_entities",
                    record_ids=[
                        transaction.transaction_id,
                        *[match.transaction_id for match in relations.data.matches],
                    ],
                )
            )
        unavailable = (
            [
                CapabilityGap(
                    tool="find_risky_neighbors",
                    error=graph.error,
                )
            ]
            if graph.status == "unavailable" and graph.error is not None
            else []
        )
        if graph.status not in ("ok", "unavailable"):
            issues.append(self._issue(graph))

        report = InvestigationReport(
            status="partial" if issues else "completed",
            transaction_id=transaction.transaction_id,
            dataset_source=transaction.dataset_source,
            transaction=transaction,
            historical_features=features,
            risk_score=risk,
            related_entities=relations,
            risky_neighbors=graph,
            evidence_bindings=bindings,
            issues=issues,
            unavailable_capabilities=unavailable,
            limitations=self._limitations(transaction.dataset_source),
        )
        return InvestigationRun(
            report=report,
            audit=InvestigationAudit(investigation_id=investigation_id, events=events),
        )

    def _invoke(
        self,
        investigation_id: str,
        events: list[AuditEvent],
        tool_name: ToolName,
        call: Callable[[], object],
        result_type: type,
        request: Mapping[str, object],
        unexpected_code: ErrorCode,
    ):
        started_at = self._clock()
        started_ns = self._monotonic_ns()
        try:
            result = call()
        except Exception:
            transaction_id = request.get("transaction_id")
            dataset_source = request.get("dataset_source")
            result = result_type.failure(
                code=unexpected_code,
                transaction_id=transaction_id if isinstance(transaction_id, str) else None,
                dataset_source=(
                    dataset_source
                    if dataset_source in ("synthetic", "ieee_cis")
                    else None
                ),
            )
        completed_ns = self._monotonic_ns()
        completed_at = self._clock()
        events.append(
            AuditEvent(
                investigation_id=investigation_id,
                sequence=len(events) + 1,
                tool=tool_name,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=max(0.0, (completed_ns - started_ns) / 1_000_000),
                status=result.status,
                error_code=result.error.code if result.error is not None else None,
            )
        )
        return result

    @staticmethod
    def _issue(result) -> InvestigationIssue:
        assert result.error is not None
        assert result.status in ("not_found", "invalid_input", "dependency_error")
        return InvestigationIssue(
            tool=result.tool,
            status=result.status,
            error=result.error,
        )

    @staticmethod
    def _stopped_report(transaction: GetTransactionResult) -> InvestigationReport:
        status_map: dict[str, ReportStatus] = {
            "invalid_input": "invalid_input",
            "not_found": "not_found",
            "dependency_error": "failed",
        }
        return InvestigationReport(
            status=status_map[transaction.status],
            transaction_id=transaction.transaction_id,
            dataset_source=transaction.dataset_source,
            transaction=transaction,
            issues=[InvestigationWorkflow._issue(transaction)],
        )

    @staticmethod
    def _limitations(dataset_source: DatasetSource) -> list[str]:
        limitations = [
            "The tabular risk score is not a calibrated fraud probability.",
            "Shared modeled entities do not establish a verified person or fraud conclusion.",
            "Graph risk neighbors are unavailable until Stage 04.",
            "This workflow is read-only and does not execute enforcement actions.",
        ]
        if dataset_source == "ieee_cis":
            limitations.append(
                "IEEE-CIS event time uses an artificial anchor and only supports relative ordering."
            )
        else:
            limitations.append("Synthetic records are demonstration data.")
        return limitations
