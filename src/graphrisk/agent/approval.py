"""In-process, checkpointed human approval for a frozen Stage 05 report."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Callable, Literal, Mapping, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import Field, ValidationError, field_validator, model_validator

from graphrisk.agent.reporting import ReportRun
from graphrisk.investigation.contracts import DatasetSource, StrictModel, TransactionId


ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "error"]


class ApprovalEvidence(StrictModel):
    transaction_record_id: str
    model_evidence_ids: list[str]
    graph_evidence_ids: list[str]
    policy_chunk_ids: list[str] = Field(min_length=1)
    investigation_status: Literal["completed", "partial"]


class ApprovalPayload(StrictModel):
    session_id: str = Field(min_length=1, max_length=128)
    approval_id: str
    transaction_id: TransactionId
    dataset_source: DatasetSource
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    investigation_id: str
    suggested_action: Literal["human_review"] = "human_review"
    evidence: ApprovalEvidence
    expires_at: datetime


class ApprovalDecision(StrictModel):
    session_id: str = Field(min_length=1, max_length=128)
    transaction_id: TransactionId
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["approve", "reject"]
    reviewer_id: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("session_id", "reviewer_id")
    @classmethod
    def safe_identifier(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
            raise ValueError("identifier must contain only letters, numbers, dot, underscore or dash")
        return value

    @field_validator("note", mode="before")
    @classmethod
    def trim_note(cls, value: object) -> object:
        return value.strip() or None if isinstance(value, str) else value


class ApprovalEvent(StrictModel):
    sequence: int = Field(ge=1)
    kind: Literal["created", "approved", "rejected", "expired"]
    at: datetime
    approval_id: str
    report_sha256: str
    reviewer_id: str | None = None
    note: str | None = None
    reason: Literal["ttl", "report_changed"] | None = None


class ApprovalResult(StrictModel):
    status: ApprovalStatus
    payload: ApprovalPayload | None = None
    decision: ApprovalDecision | None = None
    audit: list[ApprovalEvent] = Field(default_factory=list)
    issue_code: str | None = None
    external_action_count: Literal[0] = 0

    @model_validator(mode="after")
    def valid_outcome(self) -> "ApprovalResult":
        if self.status == "error":
            if self.issue_code is None:
                raise ValueError("error needs an issue code")
        elif self.payload is None or self.issue_code is not None:
            raise ValueError("non-error result needs a payload and no issue")
        if self.status in ("approved", "rejected") and self.decision is None:
            raise ValueError("final human decision is required")
        if [event.sequence for event in self.audit] != list(range(1, len(self.audit) + 1)):
            raise ValueError("approval audit must be contiguous")
        return self


class _ApprovalState(TypedDict, total=False):
    payload: dict[str, object]
    status: ApprovalStatus
    decision: dict[str, object]
    audit: list[dict[str, object]]


def _report_hash(report_run: ReportRun) -> str | None:
    if report_run.report is None:
        return None
    return hashlib.sha256(report_run.report.model_dump_json().encode("utf-8")).hexdigest()


def _approvable(report_run: ReportRun) -> bool:
    report, audit = report_run.report, report_run.audit
    if report_run.status not in ("verified", "partial_evidence") or report is None:
        return False
    expected = "partial_evidence" if report.investigation_status == "partial" else "verified"
    return (
        report_run.status == expected and audit.status == expected
        and report.suggested_action == "human_review"
        and report.validation_status == "source_quote_verified"
        and bool(report.policy_conclusions)
        and _report_hash(report_run) == audit.report_sha256
        and audit.investigation_id == report.investigation_id
    )


def _await_approval(state: _ApprovalState) -> _ApprovalState:
    # The interrupt node deliberately has no side effects before interrupt():
    # LangGraph re-runs it from the beginning on resume.
    payload = ApprovalPayload.model_validate(state["payload"])
    raw = interrupt(payload.model_dump(mode="json"))
    if not isinstance(raw, dict):
        raise ValueError("approval resume must be a structured decision")
    kind = raw.get("decision")
    if kind not in ("approve", "reject", "expire"):
        raise ValueError("approval decision must be approve, reject or expire")
    if (raw.get("session_id") != payload.session_id
        or raw.get("transaction_id") != payload.transaction_id
        or raw.get("report_sha256") != payload.report_sha256):
        raise ValueError("approval resume does not match frozen report")
    decision = None
    reviewer = None
    if kind != "expire":
        decision = ApprovalDecision.model_validate({key: raw.get(key) for key in ApprovalDecision.model_fields})
        reviewer = decision.reviewer_id
    at = datetime.fromisoformat(raw["decided_at"])
    if at.tzinfo is None:
        raise ValueError("approval audit timestamp must be timezone aware")
    status: ApprovalStatus = {"approve": "approved", "reject": "rejected", "expire": "expired"}[kind]
    audit = list(state["audit"])
    audit.append(ApprovalEvent(
        sequence=len(audit) + 1, kind=status, at=at, approval_id=payload.approval_id,
        report_sha256=payload.report_sha256, reviewer_id=reviewer,
        note=decision.note if decision is not None else None,
        reason=raw.get("reason") if kind == "expire" else None,
    ).model_dump(mode="json"))
    update: _ApprovalState = {"status": status, "audit": audit}
    if decision is not None:
        update["decision"] = decision.model_dump(mode="json")
    return update


class Stage05ApprovalWorkflow:
    """One-process LangGraph pause/resume; approval never invokes an enforcement tool."""

    def __init__(
        self,
        *,
        checkpointer: InMemorySaver | None = None,
        clock: Callable[[], datetime] | None = None,
        ttl: timedelta = timedelta(hours=24),
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("approval TTL must be positive")
        self.clock = clock or (lambda: datetime.now(UTC))
        self.ttl = ttl
        self._lock = RLock()
        graph = StateGraph(_ApprovalState)
        graph.add_node("await_approval", _await_approval)
        graph.add_edge(START, "await_approval")
        graph.add_edge("await_approval", END)
        self._graph = graph.compile(checkpointer=checkpointer or InMemorySaver())

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("approval clock must be timezone aware")
        return now.astimezone(UTC)

    @staticmethod
    def _config(session_id: str) -> dict[str, object]:
        return {"configurable": {"thread_id": session_id}}

    @staticmethod
    def _error(code: str, state: Mapping[str, object] | None = None) -> ApprovalResult:
        if state and "payload" in state:
            return ApprovalResult(status="error", payload=ApprovalPayload.model_validate(state["payload"]),
                                  audit=[ApprovalEvent.model_validate(event) for event in state.get("audit", [])],
                                  issue_code=code)
        return ApprovalResult(status="error", issue_code=code)

    @staticmethod
    def _result(state: Mapping[str, object]) -> ApprovalResult:
        return ApprovalResult(
            status=state["status"], payload=ApprovalPayload.model_validate(state["payload"]),
            decision=ApprovalDecision.model_validate(state["decision"]) if state.get("decision") else None,
            audit=[ApprovalEvent.model_validate(event) for event in state.get("audit", [])],
        )

    def _snapshot(self, session_id: str):
        return self._graph.get_state(self._config(session_id))

    def start(self, report_run: ReportRun, session_id: str) -> ApprovalResult:
        with self._lock:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", session_id):
                return self._error("invalid_session_id")
            report = report_run.report
            report_hash = _report_hash(report_run)
            if not _approvable(report_run) or report is None or report_hash is None:
                return self._error("report_not_approvable")
            prior = self._snapshot(session_id)
            if prior.values:
                prior_payload = ApprovalPayload.model_validate(prior.values["payload"])
                if prior_payload.report_sha256 != report_hash:
                    if prior.next:
                        return self._expire(session_id, prior.values, self._now(), "report_changed")
                    return self._error("session_bound_to_other_report", prior.values)
                if prior.next and self._now() >= prior_payload.expires_at:
                    return self._expire(session_id, prior.values, self._now(), "ttl")
                return self._result(prior.values)
            now = self._now()
            approval_id = "approval-" + hashlib.sha256(f"{session_id}|{report_hash}".encode()).hexdigest()[:24]
            payload = ApprovalPayload(
                session_id=session_id, approval_id=approval_id, transaction_id=report.transaction_id,
                dataset_source=report.dataset_source, report_sha256=report_hash,
                investigation_id=report.investigation_id, expires_at=now + self.ttl,
                evidence=ApprovalEvidence(
                    transaction_record_id=report.transaction_record_id,
                    model_evidence_ids=report.model_evidence_ids, graph_evidence_ids=report.graph_evidence_ids,
                    policy_chunk_ids=[item.chunk_id for item in report.policy_conclusions],
                    investigation_status=report.investigation_status,
                ),
            )
            created = ApprovalEvent(sequence=1, kind="created", at=now, approval_id=approval_id,
                                    report_sha256=report_hash)
            result = self._graph.invoke({
                "payload": payload.model_dump(mode="json"), "status": "pending",
                "audit": [created.model_dump(mode="json")],
            }, config=self._config(session_id))
            if "__interrupt__" not in result or not self._snapshot(session_id).next:
                raise RuntimeError("approval graph did not pause at the human gate")
            return self._result(self._snapshot(session_id).values)

    def _expire(self, session_id: str, state: Mapping[str, object], now: datetime,
                reason: Literal["ttl", "report_changed"]) -> ApprovalResult:
        payload = ApprovalPayload.model_validate(state["payload"])
        self._graph.invoke(Command(resume={
            "session_id": session_id, "transaction_id": payload.transaction_id,
            "report_sha256": payload.report_sha256, "decision": "expire", "decided_at": now.isoformat(),
            "reason": reason,
        }), config=self._config(session_id))
        return self._result(self._snapshot(session_id).values)

    def resume(self, raw_decision: Mapping[str, object], current_report: ReportRun) -> ApprovalResult:
        with self._lock:
            try:
                decision = ApprovalDecision.model_validate(raw_decision)
            except (ValidationError, TypeError, ValueError):
                return self._error("invalid_decision")
            snapshot = self._snapshot(decision.session_id)
            if not snapshot.values:
                return self._error("unknown_session")
            state = snapshot.values
            payload = ApprovalPayload.model_validate(state["payload"])
            if decision.transaction_id != payload.transaction_id or decision.report_sha256 != payload.report_sha256:
                return self._error("decision_binding_mismatch", state)
            now = self._now()
            current_hash = _report_hash(current_report)
            if (not _approvable(current_report) or current_hash != payload.report_sha256
                or current_report.report is None
                or current_report.report.investigation_id != payload.investigation_id):
                if snapshot.next:
                    return self._expire(decision.session_id, state, now, "report_changed")
                return self._error("report_changed", state)
            if not snapshot.next:
                existing = self._result(state)
                if existing.decision == decision:
                    return existing
                return self._error("already_finalized", state)
            if now >= payload.expires_at:
                return self._expire(decision.session_id, state, now, "ttl")
            self._graph.invoke(Command(resume={**decision.model_dump(mode="json"),
                                               "decided_at": now.isoformat()}),
                               config=self._config(decision.session_id))
            return self._result(self._snapshot(decision.session_id).values)
