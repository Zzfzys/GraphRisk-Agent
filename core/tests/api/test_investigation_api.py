"""Route 2 HTTP contract with small injected Stage 05 results."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from graphrisk.agent.reliability import ReliabilityAudit, ReliableRun
from graphrisk.agent.reporting import DerivedReport, PolicyQuotation, ReportAudit, ReportRun
from graphrisk.serving.investigation_service import RunVersions, Stage06Service


REQUEST = {"transaction_id": "T3", "dataset_source": "synthetic",
           "policy_as_of": "2026-07-01", "relation_limit": 3}


def _run(status="verified") -> ReliableRun:
    report_run = None
    if status in {"verified", "partial_evidence", "insufficient_evidence"}:
        report = DerivedReport(
            transaction_id="T3", dataset_source="synthetic", policy_as_of="2026-07-01",
            investigation_id="audit-123", investigation_status="partial" if status == "partial_evidence" else "completed",
            transaction_amount=12.5, transaction_record_id="T3", model_score=0.42,
            model_version="model-v1", model_evidence_ids=["model-e1"],
            related_transaction_ids=[], graph_status="unavailable" if status == "partial_evidence" else "ok",
            graph_model_version="graph-v1", target_graph_risk_score=None if status == "partial_evidence" else 0.5,
            graph_evidence_ids=[], graph_neighbor_ids=[],
            policy_conclusions=[] if status == "insufficient_evidence" else [PolicyQuotation(
                chunk_id="policy-1", doc_id="POLICY-V1", version=1, section="rule",
                quote="Human review is required.", source_ref="simulated-policy.md")],
            uncertainty=["Human applicability check remains."],
            suggested_action="insufficient_evidence" if status == "insufficient_evidence" else "human_review",
            validation_status="insufficient_evidence" if status == "insufficient_evidence" else "source_quote_verified",
        )
        report_run = ReportRun(
            status=status, report=report,
            audit=ReportAudit(
                investigation_id="audit-123", status=status, prompt_sha256=None,
                model_id="fake", model_revision=None, index_version="index-v1",
                elapsed_ms=1, prompt_tokens=4, output_tokens=2,
                estimated_cost_cny=0,
                report_sha256=hashlib.sha256(report.model_dump_json().encode()).hexdigest(),
            ),
        )
    return ReliableRun(
        status=status, graph_run=None, report_run=report_run,
        audit=ReliabilityAudit(
            status=status, elapsed_ms=1, policy_attempts=0 if status == "not_found" else 1,
            generation_attempts=0 if status in {"not_found", "insufficient_evidence"} else 1,
            retries=0, attempts=[], cumulative_cost_cny=0, api_stop_cny=24,
            api_budget_cny=30, cost_remaining_cny=24, graph_steps=0,
            graph_node_trace=[], model_id="fake" if report_run else None,
        ),
    )


class FakeRunner:
    def __init__(self, result):
        self.result, self.calls = result, 0
        self.last_request = None

    def run(self, request):
        self.calls += 1
        self.last_request = request
        return self.result


def _app(status="verified", *, clock=None, ttl=timedelta(minutes=30), ready=True):
    runner = FakeRunner(_run(status))
    service = Stage06Service(
        dataset_source="synthetic", versions=RunVersions(
            graph_comparison_run="demo", policy_index_version="index-v1",
            generation_model_id="fake"), runner=runner, clock=clock, ttl=ttl,
        readiness_probe=lambda: {
            "data": True, "risk_model": True, "graph": True,
            "policy_index": True, "ollama": ready,
        },
    )
    return create_app(investigation_service_factory=lambda: service), runner


def test_create_get_and_openapi_expose_only_display_fields(caplog):
    app, runner = _app()
    with TestClient(app) as client:
        created = client.post("/api/v1/investigations", json=REQUEST)
        assert created.status_code == 201
        body = created.json()
        assert body["status"] == "verified" and body["evidence_state"] == "source_verified"
        assert body["report"]["model"]["risk_score"] == 0.42
        assert body["report"]["validation_status"] == "source_quote_verified"
        assert body["report"]["policy_citations"][0]["source_ref"] == "simulated-policy.md"
        assert body["audit"]["investigation_id"] == body["report"]["investigation_id"]
        assert body["approval"]["status"] == "pending"
        assert body["report_sha256"] == body["approval"]["report_sha256"]
        assert body["simulated_policy"] and not body["scores_calibrated"]
        serialized = created.text
        assert all(secret not in serialized for secret in ("is_fraud", "card_id", "device_id", "identity_key", "graph_run", "EVIDENCE_JSON"))
        loaded = client.get(f"/api/v1/investigations/{body['run_id']}")
        assert loaded.status_code == 200 and loaded.json() == body
        assert runner.calls == 1
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/v1/investigations" in paths
        assert "partial_evidence" in paths["/api/v1/investigations"]["post"]["responses"]["201"]["description"]
        assert "insufficient_evidence" in paths["/api/v1/investigations"]["post"]["responses"]["201"]["description"]
        assert "404" in paths["/api/v1/investigations"]["post"]["responses"]
        assert "503" in paths["/api/v1/investigations"]["post"]["responses"]
    assert "T3" not in caplog.text and "Human review is required." not in caplog.text


@pytest.mark.parametrize("status,expected_state,approval", [
    ("partial_evidence", "partial", "pending"),
    ("insufficient_evidence", "insufficient", None),
])
def test_partial_and_no_policy_are_successful_but_not_complete(status, expected_state, approval):
    app, _ = _app(status)
    with TestClient(app) as client:
        response = client.post("/api/v1/investigations", json=REQUEST)
    assert response.status_code == 201
    body = response.json()
    assert body["evidence_state"] == expected_state
    assert body["report"]["graph"]["neighbors"] == []
    assert body["report"]["graph"]["status"] == ("unavailable" if status == "partial_evidence" else "ok")
    assert (body["approval"]["status"] if body["approval"] else None) == approval
    if status == "insufficient_evidence":
        assert body["session_id"] is None and body["report_sha256"] is None
        assert body["report"]["policy_citations"] == []


@pytest.mark.parametrize("status,http_status,code", [
    ("not_found", 404, "transaction_not_found"),
    ("generation_error", 503, "generation_unavailable"),
    ("retrieval_error", 503, "policy_retrieval_unavailable"),
    ("deadline_exceeded", 504, "deadline_exceeded"),
    ("budget_exhausted", 429, "budget_exhausted"),
])
def test_failed_runs_are_structured_and_reread_without_rerun(status, http_status, code):
    app, runner = _app(status)
    with TestClient(app) as client:
        response = client.post("/api/v1/investigations", json=REQUEST)
        assert response.status_code == http_status
        assert response.json()["error_code"] == code
        run_id = response.json()["run_id"]
        again = client.get(f"/api/v1/investigations/{run_id}")
        assert again.status_code == http_status and again.json()["run_id"] == run_id
    assert runner.calls == 1


def test_invalid_inputs_and_wrong_source_never_run_model():
    app, runner = _app()
    with TestClient(app) as client:
        invalid = client.post("/api/v1/investigations", json={**REQUEST, "policy_as_of": "2026-07-01T12:00:00"})
        assert invalid.status_code == 422 and invalid.json()["error_code"] == "invalid_request"
        assert "2026-07-01T12:00:00" not in invalid.text
        invalid_limit = client.post("/api/v1/investigations", json={**REQUEST, "relation_limit": True})
        assert invalid_limit.status_code == 422
        wrong_source = client.post("/api/v1/investigations", json={**REQUEST, "dataset_source": "ieee_cis"})
        assert wrong_source.status_code == 422 and wrong_source.json()["error_code"] == "dataset_source_mismatch"
    assert runner.calls == 0


def test_default_relation_limit_is_three_and_unexpected_failure_is_redacted(caplog):
    app, runner = _app()
    with TestClient(app) as client:
        response = client.post("/api/v1/investigations", json={
            key: value for key, value in REQUEST.items() if key != "relation_limit"})
        assert response.status_code == 201
        assert response.json()["request"]["relation_limit"] == 3
        assert runner.last_request["relation_limit"] == 3
        def fail(_request):
            raise RuntimeError("private-provider-prompt")
        runner.run = fail
        failure = client.post("/api/v1/investigations", json=REQUEST)
        assert failure.status_code == 500
        assert failure.json()["error_code"] == "investigation_internal_error"
        assert "private-provider-prompt" not in failure.text
    assert "private-provider-prompt" not in caplog.text


def test_health_readiness_and_record_expiry_are_distinct():
    now = [datetime(2026, 9, 26, tzinfo=UTC)]
    app, _ = _app(clock=lambda: now[0], ttl=timedelta(minutes=5), ready=False)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").json() == {"status": "ok", "investigation_service_enabled": True}
        ready = client.get("/api/v1/ready")
        assert ready.status_code == 503 and not ready.json()["components"]["ollama"]
        created = client.post("/api/v1/investigations", json=REQUEST)
        now[0] += timedelta(minutes=5)
        expired = client.get(f"/api/v1/investigations/{created.json()['run_id']}")
        assert expired.status_code == 410 and expired.json()["error_code"] == "run_expired"


def test_unconfigured_stage06_is_not_ready_but_old_api_lives():
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/ready").status_code == 503
        response = client.post("/api/v1/investigations", json=REQUEST)
        assert response.status_code == 503 and response.json()["error_code"] == "service_unconfigured"
        assert client.get("/health").status_code == 200


def _approval_body(created, *, decision="approve", note="  Checked evidence.  "):
    return {
        "run_id": created["run_id"], "session_id": created["session_id"],
        "transaction_id": created["request"]["transaction_id"],
        "report_sha256": created["report_sha256"],
        "decision": decision, "reviewer_id": "analyst-1", "note": note,
    }


@pytest.mark.parametrize("decision,status", [("approve", "approved"), ("reject", "rejected")])
def test_approval_decision_note_audit_and_exact_retry(decision, status, caplog):
    app, runner = _app()
    with TestClient(app) as client:
        created = client.post("/api/v1/investigations", json=REQUEST).json()
        path = f"/api/v1/investigations/{created['run_id']}/approval"
        pending = client.get(path)
        assert pending.status_code == 200 and pending.json()["status"] == "pending"
        assert pending.json()["reviewer_authenticated"] is False
        body = _approval_body(created, decision=decision)
        first = client.post(path, json=body)
        assert first.status_code == 200
        approval = first.json()
        assert approval["status"] == status and approval["decision"] == decision
        assert approval["note"] == "Checked evidence."
        assert approval["audit"][-1]["note"] == "Checked evidence."
        assert [event["kind"] for event in approval["audit"]] == ["created", status]
        assert approval["external_action_count"] == 0
        assert client.post(path, json=body).json() == approval
        assert client.get(path).json() == approval
        reread = client.get(f"/api/v1/investigations/{created['run_id']}").json()
        assert reread["report"] == created["report"]
        assert reread["report_sha256"] == created["report_sha256"]
        assert reread["approval"] == approval
        assert runner.calls == 1
        schema = client.get("/openapi.json").json()
        assert path.replace(created["run_id"], "{run_id}") in schema["paths"]
    assert "Checked evidence." not in caplog.text


def test_approval_rejects_conflict_and_wrong_run_session_transaction_or_hash():
    app, _ = _app()
    with TestClient(app) as client:
        created = client.post("/api/v1/investigations", json=REQUEST).json()
        path = f"/api/v1/investigations/{created['run_id']}/approval"
        body = _approval_body(created)
        cases = [
            ({**body, "run_id": "run-other-123"}, "approval_run_mismatch"),
            ({**body, "session_id": "session-other"}, "approval_binding_mismatch"),
            ({**body, "transaction_id": "T4"}, "approval_binding_mismatch"),
            ({**body, "report_sha256": "f" * 64}, "approval_binding_mismatch"),
        ]
        for changed, code in cases:
            response = client.post(path, json=changed)
            assert response.status_code == 409 and response.json()["error_code"] == code
            assert client.get(path).json()["status"] == "pending"
        assert client.post(path, json=body).status_code == 200
        for changed in ({**body, "decision": "reject"},
                        {**body, "note": "Different note."},
                        {**body, "reviewer_id": "analyst-2"}):
            response = client.post(path, json=changed)
            assert response.status_code == 409 and response.json()["error_code"] == "approval_conflict"
        assert len(client.get(path).json()["audit"]) == 2


def test_approval_input_validation_and_nonapprovable_report():
    app, _ = _app("insufficient_evidence")
    with TestClient(app) as client:
        created = client.post("/api/v1/investigations", json=REQUEST).json()
        path = f"/api/v1/investigations/{created['run_id']}/approval"
        assert client.get(path).status_code == 409
    app, _ = _app()
    with TestClient(app) as client:
        created = client.post("/api/v1/investigations", json=REQUEST).json()
        path = f"/api/v1/investigations/{created['run_id']}/approval"
        body = _approval_body(created)
        for changed in ({**body, "note": "x" * 501},
                        {**body, "decision": "bypass"},
                        {**body, "reviewer_id": "not authenticated"}):
            assert client.post(path, json=changed).status_code == 422
        assert client.get(path).json()["status"] == "pending"
        blank = client.post(path, json={**body, "note": "  "})
        assert blank.status_code == 200 and blank.json()["note"] is None


def test_approval_expiry_is_distinct_from_run_expiry_and_restart():
    now = [datetime(2026, 9, 26, tzinfo=UTC)]
    app, _ = _app(clock=lambda: now[0], ttl=timedelta(hours=2))
    with TestClient(app) as client:
        created = client.post("/api/v1/investigations", json=REQUEST).json()
        path = f"/api/v1/investigations/{created['run_id']}/approval"
        now[0] += timedelta(minutes=30)
        expired = client.get(path)
        assert expired.status_code == 200 and expired.json()["status"] == "expired"
        assert expired.json()["audit"][-1]["reason"] == "ttl"
        denied = client.post(path, json=_approval_body(created))
        assert denied.status_code == 410 and denied.json()["error_code"] == "approval_expired"
        assert client.get(f"/api/v1/investigations/{created['run_id']}").status_code == 200
        now[0] += timedelta(minutes=90)
        assert client.get(path).json()["error_code"] == "run_expired"
    new_app, _ = _app()
    with TestClient(new_app) as client:
        assert client.get(path).json()["error_code"] == "run_instance_mismatch"
        assert client.post(path, json=_approval_body(created)).json()["error_code"] == "run_instance_mismatch"


def test_changed_frozen_report_is_not_approved():
    app, _ = _app()
    with TestClient(app) as client:
        created = client.post("/api/v1/investigations", json=REQUEST).json()
        service = app.state.stage06_service
        stored = service._records[created["run_id"]]
        stored.result.report_run.report.transaction_amount = 99
        path = f"/api/v1/investigations/{created['run_id']}/approval"
        failed = client.post(path, json=_approval_body(created))
        assert failed.status_code == 409 and failed.json()["error_code"] == "report_hash_mismatch"
        assert service._records[created["run_id"]].approval.status == "pending"
        assert service._records[created["run_id"]].approval.external_action_count == 0


def test_memory_mode_rejects_persistent_idempotency_header():
    app, runner = _app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/investigations", json=REQUEST,
            headers={"Idempotency-Key": "memory-key-123"},
        )
    assert response.status_code == 422
    assert response.json()["error_code"] == "invalid_request"
    assert runner.calls == 0

