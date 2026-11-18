"""Explicit, public-safe projection of a Stage 06 investigation record."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_serializer, model_validator
from graphrisk.agent.quality_contracts import QualityAnalysis

from graphrisk.agent.approval import ApprovalDecision
from graphrisk.agent.contracts import Stage05Request
from graphrisk.investigation.contracts import StrictModel
from graphrisk.serving.investigation_service import (
    EvidenceState,
    InvestigationRecord,
    RunStatus,
    RunVersions,
)


class InvestigationCreateRequest(Stage05Request):
    relation_limit: int = Field(default=3, strict=True, ge=1, le=50)


class TransactionView(StrictModel):
    transaction_id: str
    amount: float
    product_code: str | None = None
    record_id: str


class HistoryView(StrictModel):
    status: str
    tx_count_last_1h: int | None = None
    tx_count_last_24h: int | None = None
    tx_count_last_7d: int | None = None
    error_code: str | None = None


class ModelView(StrictModel):
    score_mode: Literal["legacy", "xgboost_only"] = "legacy"
    status: str
    risk_score: float | None = None
    model_version: str | None = None
    probability_calibrated: Literal[False] = False
    error_code: str | None = None


class NeighborView(StrictModel):
    transaction_id: str
    graph_risk_score: float | None
    seconds_before_target: int
    shared_entity_types: list[str]
    evidence_role: Literal["scored", "relation_only"] = "scored"
    score_unavailable_reason: str | None = None

    @model_validator(mode='after')
    def score_presence_matches_role(self):
        if self.evidence_role=='relation_only':
            if self.graph_risk_score is not None or self.score_unavailable_reason not in ('outside_independent_score_scope','different_model_scope'):
                raise ValueError('relation-only view requires a reason and null score')
        elif self.graph_risk_score is None or self.score_unavailable_reason is not None:
            raise ValueError('scored view requires a score and no missing-score reason')
        return self

    @model_serializer(mode="wrap")
    def legacy_scored_fields(self, handler):
        value=handler(self)
        if self.evidence_role=="scored":
            value.pop("evidence_role",None)
            value.pop("score_unavailable_reason",None)
        return value


class GraphView(StrictModel):
    score_role: Literal["legacy_fusion", "experimental"] = "legacy_fusion"
    status: str
    error_code: str | None = None
    graph_model_version: str | None = None
    target_graph_risk_score: float | None = None
    controlled_xgboost_score: float | None = None
    fusion_score: float | None = None
    fusion_alpha: float | None = None
    neighbors: list[NeighborView] = Field(default_factory=list)
    probability_calibrated: Literal[False] = False


class PolicyCitationView(StrictModel):
    chunk_id: str
    doc_id: str
    title: str | None = None
    version: int
    section: str
    quote: str
    source_ref: str
    source_type: str | None = None


class ReportView(StrictModel):
    analysis: QualityAnalysis | None = None

    @model_serializer(mode="wrap")
    def serialize_analysis(self, handler):
        value = handler(self)
        if self.analysis is None:
            value.pop("analysis", None)
        return value

    investigation_id: str
    investigation_status: Literal["completed", "partial"]
    transaction: TransactionView
    history: HistoryView | None = None
    model: ModelView
    graph: GraphView
    policy_as_of: date
    policy_retrieval_status: str | None = None
    policy_citations: list[PolicyCitationView]
    uncertainty: list[str]
    suggested_action: Literal["human_review", "insufficient_evidence"]
    validation_status: Literal["source_quote_verified", "insufficient_evidence"]


class ToolAuditView(StrictModel):
    tool: str
    status: str
    duration_ms: float
    error_code: str | None = None
    request_id: str | None = None
    phase: Literal["tool", "dependency_attempt"] = "tool"
    attempt: int | None = None


class AuditView(StrictModel):
    investigation_id: str | None = None
    creation_request_id: str | None = None
    elapsed_ms: float
    policy_attempts: int
    generation_attempts: int
    retries: int
    graph_steps: int
    graph_node_trace: list[str]
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    run_cost_cny: float | None = None
    tool_events: list[ToolAuditView]


class ApprovalView(StrictModel):
    status: Literal["pending", "approved", "rejected", "expired", "error"]
    approval_id: str
    session_id: str
    report_sha256: str
    expires_at: datetime
    suggested_action: Literal["human_review"] = "human_review"
    decision: Literal["approve", "reject"] | None = None
    reviewer_id: str | None = None
    note: str | None = None
    reviewer_authenticated: Literal[False] = False
    audit: list["ApprovalEventView"] = Field(default_factory=list)
    external_action_count: Literal[0] = 0


class ApprovalEventView(StrictModel):
    sequence: int
    request_id: str | None = None
    kind: Literal["created", "approved", "rejected", "expired"]
    at: datetime
    report_sha256: str
    reviewer_id: str | None = None
    note: str | None = None
    reason: Literal["ttl", "report_changed"] | None = None


class ApprovalSubmitRequest(ApprovalDecision):
    run_id: str = Field(min_length=1, max_length=128)


class InvestigationView(StrictModel):
    run_id: str
    request: Stage05Request
    status: RunStatus
    evidence_state: EvidenceState
    session_id: str | None = None
    report_sha256: str | None = None
    versions: RunVersions
    report: ReportView | None = None
    audit: AuditView
    approval: ApprovalView | None = None
    created_at: datetime
    expires_at: datetime
    simulated_policy: Literal[True] = True
    scores_calibrated: Literal[False] = False
    automatic_action_executed: Literal[False] = False


class ApiError(StrictModel):
    error_code: str
    message: str
    run_id: str | None = None
    status: RunStatus | None = None


def project_approval(record: InvestigationRecord) -> ApprovalView | None:
    result = record.approval
    if result is None or result.payload is None:
        return None
    payload = result.payload
    return ApprovalView(
        status=result.status, approval_id=payload.approval_id,
        session_id=payload.session_id, report_sha256=payload.report_sha256,
        expires_at=payload.expires_at,
        decision=result.decision.decision if result.decision else None,
        reviewer_id=result.decision.reviewer_id if result.decision else None,
        note=result.decision.note if result.decision else None,
        audit=[ApprovalEventView(
            sequence=event.sequence, request_id=record.approval_request_ids.get(event.sequence),
            kind=event.kind, at=event.at,
            report_sha256=event.report_sha256, reviewer_id=event.reviewer_id,
            note=event.note, reason=event.reason,
        ) for event in result.audit],
    )


def project_investigation(record: InvestigationRecord) -> InvestigationView:
    """Select display fields; never serialize the raw ReliableRun to HTTP."""
    if record.public_view is not None:
        view = InvestigationView.model_validate(record.public_view)
        return view.model_copy(update={"approval": project_approval(record)})
    reliable = record.result
    graph_run = reliable.graph_run
    investigation = graph_run.investigation_report if graph_run else None
    derived = reliable.report_run.report if reliable.report_run else None
    report_view = None
    if derived is not None:
        transaction_data = investigation.transaction.data if investigation and investigation.transaction.status == "ok" else None
        history_tool = investigation.historical_features if investigation else None
        history_data = history_tool.data if history_tool and history_tool.status == "ok" else None
        risk_tool = investigation.risk_score if investigation else None
        neighbor_tool = investigation.risky_neighbors if investigation else None
        neighbor_data = neighbor_tool.data if neighbor_tool and neighbor_tool.status == "ok" else None
        retrieval = graph_run.policy_retrieval if graph_run else None
        hits = {hit.chunk.chunk_id: hit.chunk for hit in retrieval.hits} if retrieval else {}
        citations = []
        for quote in derived.policy_conclusions:
            source = hits.get(quote.chunk_id)
            if retrieval is not None and (
                source is None or source.doc_id != quote.doc_id or source.version != quote.version
                or source.text != quote.quote or source.source_ref != quote.source_ref
                or not source.applies_on(derived.policy_as_of)
            ):
                raise ValueError("policy_citation_binding_mismatch")
            citations.append(PolicyCitationView(
                chunk_id=quote.chunk_id, doc_id=quote.doc_id,
                title=source.title if source else None, version=quote.version,
                section=quote.section, quote=quote.quote, source_ref=quote.source_ref,
                source_type=source.source_type if source else None,
            ))
        report_view = ReportView(
            analysis=derived.analysis,
            investigation_id=derived.investigation_id,
            investigation_status=derived.investigation_status,
            transaction=TransactionView(
                transaction_id=derived.transaction_id, amount=derived.transaction_amount,
                product_code=transaction_data.product_code if transaction_data else None,
                record_id=derived.transaction_record_id,
            ),
            history=HistoryView(
                status=history_tool.status,
                tx_count_last_1h=history_data.tx_count_last_1h if history_data else None,
                tx_count_last_24h=history_data.tx_count_last_24h if history_data else None,
                tx_count_last_7d=history_data.tx_count_last_7d if history_data else None,
                error_code=history_tool.error.code if history_tool and history_tool.error else None,
            ) if history_tool else None,
            model=ModelView(
                score_mode=record.versions.score_mode,
                status=risk_tool.status if risk_tool else "ok" if derived.model_score is not None else "not_available",
                risk_score=derived.model_score, model_version=derived.model_version,
                error_code=risk_tool.error.code if risk_tool and risk_tool.error else None,
            ),
            graph=GraphView(
                score_role="experimental" if record.versions.score_mode == "xgboost_only" else "legacy_fusion",
                status=neighbor_tool.status if neighbor_tool else derived.graph_status,
                error_code=neighbor_tool.error.code if neighbor_tool and neighbor_tool.error else None,
                graph_model_version=derived.graph_model_version,
                target_graph_risk_score=derived.target_graph_risk_score,
                controlled_xgboost_score=neighbor_data.controlled_xgboost_score if neighbor_data else None,
                fusion_score=neighbor_data.fusion_score if neighbor_data else None,
                fusion_alpha=neighbor_data.fusion_alpha if neighbor_data else None,
                neighbors=[NeighborView(
                    transaction_id=item.transaction_id, graph_risk_score=item.graph_risk_score,
                    seconds_before_target=item.seconds_before_target,
                    shared_entity_types=sorted({entity.entity_type for entity in item.shared_entities}),
                    evidence_role=item.evidence_role,
                    score_unavailable_reason=item.score_unavailable_reason,
                ) for item in neighbor_data.neighbors] if neighbor_data else [],
            ),
            policy_as_of=derived.policy_as_of,
            policy_retrieval_status=retrieval.status if retrieval else None,
            policy_citations=citations, uncertainty=list(derived.uncertainty),
            suggested_action=derived.suggested_action,
            validation_status=derived.validation_status,
        )
    tool_events = []
    if graph_run and graph_run.investigation_audit:
        tool_events = [ToolAuditView(
            tool=event.tool, status=event.status, duration_ms=event.duration_ms,
            error_code=event.error_code, request_id=record.created_request_id,
        ) for event in graph_run.investigation_audit.events]
    tool_events.extend(ToolAuditView(
        tool=attempt.kind, status=attempt.status, duration_ms=attempt.elapsed_ms,
        error_code=attempt.error_code, request_id=record.created_request_id,
        phase="dependency_attempt", attempt=attempt.attempt,
    ) for attempt in reliable.audit.attempts)
    audit = reliable.audit
    approval = project_approval(record)
    return InvestigationView(
        run_id=record.run_id, request=record.request, status=record.status,
        evidence_state=record.evidence_state,
        session_id=record.session_id if approval else None,
        report_sha256=record.report_sha256 if approval else None,
        versions=record.versions, report=report_view,
        audit=AuditView(
            investigation_id=report_view.investigation_id if report_view else None,
            creation_request_id=record.created_request_id,
            elapsed_ms=audit.elapsed_ms, policy_attempts=audit.policy_attempts,
            generation_attempts=audit.generation_attempts, retries=audit.retries,
            graph_steps=audit.graph_steps, graph_node_trace=list(audit.graph_node_trace),
            prompt_tokens=audit.prompt_tokens, output_tokens=audit.output_tokens,
            run_cost_cny=audit.run_cost_cny, tool_events=tool_events,
        ),
        approval=approval, created_at=record.created_at, expires_at=record.expires_at,
    )
