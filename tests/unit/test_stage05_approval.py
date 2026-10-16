"""Route 6 checks the real LangGraph interrupt and in-process resume boundary."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from graphrisk.agent.approval import Stage05ApprovalWorkflow
from graphrisk.agent.reporting import DerivedReport, PolicyQuotation, ReportAudit, ReportRun


def _report(*, amount: float = 12.5, status: str = "verified") -> ReportRun:
    report = DerivedReport(
        transaction_id="T3", dataset_source="synthetic", policy_as_of="2026-07-01",
        investigation_id="investigation-1", investigation_status="completed",
        transaction_amount=amount, transaction_record_id="T3",
        model_score=0.42, model_version="model-v1", model_evidence_ids=["model-v1"],
        related_transaction_ids=["T2"], graph_status="ok", graph_model_version="graph-v1",
        target_graph_risk_score=0.5, graph_evidence_ids=["T2"], graph_neighbor_ids=["T2"],
        policy_conclusions=[PolicyQuotation(
            chunk_id="policy-1", doc_id="POLICY-V1", version=1, section="规则",
            quote="如需进一步调查，请先交人工复核。", source_ref="simulated-policy.md",
        )],
        uncertainty=["政策适用性需人工复核"], suggested_action="human_review",
        validation_status="source_quote_verified",
    )
    digest = hashlib.sha256(report.model_dump_json().encode()).hexdigest()
    audit = ReportAudit(
        investigation_id="investigation-1", status=status, prompt_sha256=None,
        model_id="fake", model_revision=None, index_version="index-v1", elapsed_ms=0,
        prompt_tokens=None, output_tokens=None, estimated_cost_cny=0, report_sha256=digest,
    )
    return ReportRun(status=status, report=report, audit=audit)


@pytest.fixture
def setup():
    current = [datetime(2026, 9, 25, 12, tzinfo=UTC)]
    saver = InMemorySaver()
    workflow = Stage05ApprovalWorkflow(checkpointer=saver, clock=lambda: current[0], ttl=timedelta(minutes=30))
    return workflow, current, saver


def _decision(pending, kind="approve", **changes):
    return {
        "session_id": pending.payload.session_id,
        "transaction_id": pending.payload.transaction_id,
        "report_sha256": pending.payload.report_sha256,
        "decision": kind, "reviewer_id": "analyst-1",
    } | changes


def test_approval_interrupt_freezes_minimal_evidence_and_resumes_without_action(setup):
    workflow, _, _ = setup
    report = _report()
    pending = workflow.start(report, "session-a")
    assert pending.status == "pending" and pending.external_action_count == 0
    assert pending.payload.transaction_id == "T3"
    assert pending.payload.report_sha256 == report.audit.report_sha256
    assert pending.payload.suggested_action == "human_review"
    assert pending.payload.evidence.policy_chunk_ids == ["policy-1"]
    assert pending.payload.investigation_id == "investigation-1"
    assert len(pending.audit) == 1 and pending.audit[0].kind == "created"
    snapshot = workflow._graph.get_state(workflow._config("session-a"))
    assert snapshot.next == ("await_approval",)
    assert snapshot.tasks[0].interrupts[0].value == pending.payload.model_dump(mode="json")

    approved = workflow.resume(_decision(pending), report)
    assert approved.status == "approved" and approved.external_action_count == 0
    assert approved.decision.reviewer_id == "analyst-1"
    assert [event.kind for event in approved.audit] == ["created", "approved"]
    assert workflow._graph.get_state(workflow._config("session-a")).next == ()
    assert report.report.approval_status == "not_requested"  # original evidence is immutable


def test_reject_is_a_distinct_terminal_state(setup):
    workflow, _, _ = setup
    report = _report()
    pending = workflow.start(report, "session-reject")
    rejected = workflow.resume(_decision(pending, "reject"), report)
    assert rejected.status == "rejected" and rejected.external_action_count == 0
    assert rejected.audit[-1].kind == "rejected"


def test_wrong_session_hash_transaction_and_invalid_decision_do_not_resume(setup):
    workflow, _, _ = setup
    report = _report()
    pending = workflow.start(report, "session-bound")
    cases = [
        (_decision(pending, session_id="unknown"), "unknown_session"),
        (_decision(pending, report_sha256="f" * 64), "decision_binding_mismatch"),
        (_decision(pending, transaction_id="T4"), "decision_binding_mismatch"),
        (_decision(pending, kind="allow"), "invalid_decision"),
    ]
    for raw, code in cases:
        result = workflow.resume(raw, report)
        assert result.status == "error" and result.issue_code == code
        assert result.external_action_count == 0
    assert workflow._graph.get_state(workflow._config("session-bound")).next == ("await_approval",)
    assert workflow.start(report, "session-bound").status == "pending"


def test_duplicate_is_idempotent_and_conflicting_replay_fails(setup):
    workflow, _, _ = setup
    report = _report()
    pending = workflow.start(report, "session-replay")
    decision = _decision(pending)
    first = workflow.resume(decision, report)
    again = workflow.resume(decision, report)
    assert again == first
    assert len(again.audit) == 2
    conflict = workflow.resume(_decision(pending, "reject"), report)
    assert conflict.status == "error" and conflict.issue_code == "already_finalized"
    assert workflow.start(report, "session-replay") == first


def test_expiry_blocks_late_approval_and_records_reason(setup):
    workflow, current, _ = setup
    report = _report()
    pending = workflow.start(report, "session-expiry")
    current[0] += timedelta(minutes=30)
    expired = workflow.resume(_decision(pending), report)
    assert expired.status == "expired" and expired.audit[-1].reason == "ttl"
    assert expired.external_action_count == 0
    assert workflow.resume(_decision(pending), report).issue_code == "already_finalized"


def test_modified_report_expires_old_request_and_needs_new_session(setup):
    workflow, _, _ = setup
    original = _report()
    pending = workflow.start(original, "session-old")
    changed = _report(amount=999)
    expired = workflow.resume(_decision(pending), changed)
    assert expired.status == "expired" and expired.audit[-1].reason == "report_changed"
    assert workflow.resume(_decision(pending), original).issue_code == "already_finalized"
    assert workflow.start(changed, "session-old").issue_code == "session_bound_to_other_report"
    assert workflow.start(changed, "session-new").status == "pending"


def test_starting_new_report_on_pending_session_expires_old_report(setup):
    workflow, _, _ = setup
    original = _report()
    workflow.start(original, "session-collision")
    expired = workflow.start(_report(amount=99), "session-collision")
    assert expired.status == "expired" and expired.audit[-1].reason == "report_changed"


def test_unverified_or_tampered_report_cannot_enter_approval(setup):
    workflow, _, _ = setup
    original = _report()
    tampered = original.model_copy(update={"report": original.report.model_copy(update={"transaction_amount": 100})})
    invalid_status = _report(status="provider_error")
    invalid_audit_status = original.model_copy(update={
        "audit": original.audit.model_copy(update={"status": "provider_error"}),
    })
    for candidate in (tampered, invalid_status, invalid_audit_status):
        result = workflow.start(candidate, "session-no")
        assert result.status == "error" and result.issue_code == "report_not_approvable"
    assert not workflow._graph.get_state(workflow._config("session-no")).values


def test_same_process_second_workflow_uses_shared_checkpointer(setup):
    first, current, saver = setup
    report = _report()
    pending = first.start(report, "session-shared")
    second = Stage05ApprovalWorkflow(checkpointer=saver, clock=lambda: current[0])
    approved = second.resume(_decision(pending), report)
    assert approved.status == "approved"
    assert first.resume(_decision(pending), report) == approved


def test_note_is_normalized_audited_and_part_of_exact_replay(setup):
    workflow, _, _ = setup
    report = _report()
    pending = workflow.start(report, "session-note")
    decision = _decision(pending, note="  reviewed source  ")
    first = workflow.resume(decision, report)
    assert first.decision.note == "reviewed source"
    assert first.audit[-1].note == "reviewed source"
    assert workflow.resume(_decision(pending, note="reviewed source"), report) == first
    assert workflow.resume(_decision(pending, note="different"), report).issue_code == "already_finalized"
    assert report.report.approval_status == "not_requested"
