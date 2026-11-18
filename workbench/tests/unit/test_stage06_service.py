"""Route 1: one-process ownership, binding, expiry and evidence semantics."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from graphrisk.agent.reliability import ReliabilityAudit, ReliableRun
from graphrisk.agent.reporting import DerivedReport, PolicyQuotation, ReportAudit, ReportRun
from graphrisk.serving.investigation_service import RunVersions, ServiceError, Stage06Service


REQUEST = {"transaction_id": "T3", "dataset_source": "synthetic",
           "policy_as_of": "2026-07-01", "relation_limit": 3}


def _result(status="verified", *, transaction_id="T3", corrupt_hash=False):
    report = DerivedReport(
        transaction_id=transaction_id, dataset_source="synthetic", policy_as_of="2026-07-01",
        investigation_id="investigation-1",
        investigation_status="partial" if status == "partial_evidence" else "completed",
        transaction_amount=12.5, transaction_record_id=transaction_id,
        model_score=0.42, model_version="model-v1", model_evidence_ids=["model-v1"],
        related_transaction_ids=[], graph_status="unavailable" if status == "partial_evidence" else "ok",
        graph_model_version="graph-v1", target_graph_risk_score=None if status == "partial_evidence" else 0.5,
        graph_evidence_ids=[], graph_neighbor_ids=[],
        policy_conclusions=[] if status == "insufficient_evidence" else [PolicyQuotation(
            chunk_id="policy-1", doc_id="POLICY-V1", version=1, section="rule",
            quote="Human review is required.", source_ref="simulated-policy.md")],
        uncertainty=["Human must verify applicability."],
        suggested_action="insufficient_evidence" if status == "insufficient_evidence" else "human_review",
        validation_status="insufficient_evidence" if status == "insufficient_evidence" else "source_quote_verified",
    )
    digest = hashlib.sha256(report.model_dump_json().encode()).hexdigest()
    report_run = ReportRun(
        status=status, report=report,
        audit=ReportAudit(investigation_id="investigation-1", status=status,
                          prompt_sha256=None, model_id="fake", model_revision=None,
                          index_version="index-v1", elapsed_ms=1, prompt_tokens=None,
                          output_tokens=None, estimated_cost_cny=0,
                          report_sha256="0" * 64 if corrupt_hash else digest),
    )
    return ReliableRun(
        status=status, graph_run=None, report_run=report_run,
        audit=ReliabilityAudit(
            status=status, elapsed_ms=1, policy_attempts=1, generation_attempts=1,
            retries=0, attempts=[], cumulative_cost_cny=0, api_stop_cny=24,
            api_budget_cny=30, cost_remaining_cny=24, graph_steps=0, graph_node_trace=[],
        ),
    )


class FakeRunner:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def run(self, request):
        self.calls += 1
        return self.result


def _service(result=None, *, max_runs=2, ttl=timedelta(minutes=30), clock=None):
    runner = FakeRunner(result or _result())
    service = Stage06Service(
        dataset_source="synthetic", versions=RunVersions(
            graph_comparison_run="demo", policy_index_version="index-v1",
            generation_model_id="fake"), runner=runner, max_runs=max_runs,
        ttl=ttl, clock=clock,
    )
    return service, runner


def _error_code(call):
    with pytest.raises(ServiceError) as error:
        call()
    return error.value.code


def test_repeated_reads_keep_one_report_and_approval_checkpoint():
    service, runner = _service()
    record = service.run(REQUEST)
    assert record.run_id.startswith(f"run-{service.instance_id}-")
    assert record.session_id == f"session-{record.run_id}"
    assert record.evidence_state == "source_verified" and record.status == "verified"
    assert record.approval.status == "pending"
    assert record.report_sha256 == record.approval.payload.report_sha256
    assert record.versions.policy_index_version == "index-v1"
    first = service.get(record.run_id)
    first.result.report_run.report.uncertainty.append("tampered copy")
    assert service.get(record.run_id).result.report_run.report.uncertainty == ["Human must verify applicability."]
    assert runner.calls == 1
    assert service.approval._graph.get_state(service.approval._config(record.session_id)).next == ("await_approval",)


@pytest.mark.parametrize("status,evidence_state,approvable", [
    ("partial_evidence", "partial", True),
    ("insufficient_evidence", "insufficient", False),
])
def test_partial_and_insufficient_are_not_promoted_to_complete(status, evidence_state, approvable):
    service, _ = _service(_result(status))
    record = service.run(REQUEST)
    assert record.evidence_state == evidence_state
    assert (record.approval is not None) is approvable
    assert (record.session_id is not None) is approvable
    assert record.result.report_run.report.validation_status == (
        "source_quote_verified" if approvable else "insufficient_evidence")


def test_wrong_source_and_report_binding_fail_before_storing_state():
    service, runner = _service()
    assert _error_code(lambda: service.run({**REQUEST, "dataset_source": "ieee_cis"})) == "dataset_source_mismatch"
    assert runner.calls == 0
    runner.result = _result(transaction_id="OTHER")
    assert _error_code(lambda: service.run(REQUEST)) == "report_request_binding_mismatch"
    runner.result = _result(corrupt_hash=True)
    assert _error_code(lambda: service.run(REQUEST)) == "report_hash_mismatch"
    runner.result = _result()
    runner.result.report_run.audit.index_version = "stale-index"
    assert _error_code(lambda: service.run(REQUEST)) == "policy_index_version_mismatch"
    assert len(service._records) == 0 and service._inflight == 0


def test_stage06_factory_rejects_wrong_dataset_before_loading_components(monkeypatch):
    from types import SimpleNamespace
    import graphrisk.serving.investigation_service as module

    monkeypatch.setattr(module, "load_stage05_config", lambda _: SimpleNamespace(dataset_source="synthetic"))
    with pytest.raises(ValueError, match="requires the IEEE-CIS data source"):
        Stage06Service.from_config("unused.yaml")


def test_capacity_ttl_checkpoint_cleanup_and_restart_are_explicit():
    now = [datetime(2026, 9, 26, tzinfo=UTC)]
    service, runner = _service(max_runs=1, ttl=timedelta(minutes=5), clock=lambda: now[0])
    first = service.run(REQUEST)
    assert _error_code(lambda: service.run(REQUEST)) == "run_capacity_exceeded"
    assert runner.calls == 1
    now[0] += timedelta(minutes=5)
    assert _error_code(lambda: service.get(first.run_id)) == "run_expired"
    assert not service.approval._graph.get_state(service.approval._config(first.session_id)).values
    second = service.run(REQUEST)
    assert second.run_id != first.run_id and runner.calls == 2
    restarted, _ = _service()
    assert _error_code(lambda: restarted.get(second.run_id)) == "run_instance_mismatch"
    service.close()
    assert _error_code(lambda: service.get(second.run_id)) == "service_stopped"


def test_inflight_run_reserves_capacity_before_model_call_finishes():
    entered, release = Event(), Event()
    service, runner = _service(max_runs=1)

    def blocking_run(request):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test runner was not released")
        return _result()

    runner.run = blocking_run
    outcome = []
    worker = Thread(target=lambda: outcome.append(service.run(REQUEST)))
    worker.start()
    try:
        assert entered.wait(5)
        assert _error_code(lambda: service.run(REQUEST)) == "run_capacity_exceeded"
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert len(outcome) == 1 and outcome[0].approval.status == "pending"


def test_app_lifespan_owns_exactly_one_injected_service_without_changing_old_api():
    service, _ = _service()
    calls = []

    def factory():
        calls.append(1)
        return service

    with TestClient(create_app(investigation_service_factory=factory)) as client:
        assert len(calls) == 1
        assert client.app.state.stage06_service is service
        assert client.get("/health").status_code == 200
        assert "/risk/predict" in client.get("/openapi.json").json()["paths"]
    assert _error_code(lambda: service.run(REQUEST)) == "service_stopped"
