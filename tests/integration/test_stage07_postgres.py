"""Real PostgreSQL recovery tests; set GRAPHRISK_STAGE07_DSN for the isolated DB."""

from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from graphrisk.agent.reliability import ReliabilityAudit, ReliableRun
from graphrisk.agent.reporting import DerivedReport, PolicyQuotation, ReportAudit, ReportRun
from graphrisk.serving.investigation_service import RunVersions, ServiceError
from graphrisk.serving.postgres_service import PersistentCostLedger, PostgresInvestigationService
from graphrisk.serving.postgres_store import PostgresStore


DSN = os.getenv("GRAPHRISK_STAGE07_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="isolated GRAPHRISK_STAGE07_DSN is required")
REQUEST = {"transaction_id": "T3", "dataset_source": "synthetic",
           "policy_as_of": "2026-07-01", "relation_limit": 3}
VERSIONS = RunVersions(graph_comparison_run="demo", policy_index_version="index-v1",
                       generation_model_id="fake")


def _result() -> ReliableRun:
    report = DerivedReport(
        transaction_id="T3", dataset_source="synthetic", policy_as_of="2026-07-01",
        investigation_id="route4-audit", investigation_status="completed",
        transaction_amount=12.5, transaction_record_id="T3",
        model_score=0.42, model_version="model-v1", model_evidence_ids=["model-v1"],
        related_transaction_ids=[], graph_status="ok", graph_model_version="graph-v1",
        target_graph_risk_score=0.5, graph_evidence_ids=[], graph_neighbor_ids=[],
        policy_conclusions=[PolicyQuotation(
            chunk_id="policy-1", doc_id="POLICY-V1", version=1, section="rule",
            quote="Human review is required.", source_ref="simulated-policy.md",
        )],
        uncertainty=["Human must verify applicability."], suggested_action="human_review",
        validation_status="source_quote_verified",
    )
    digest = hashlib.sha256(report.model_dump_json().encode()).hexdigest()
    return ReliableRun(
        status="verified", graph_run=None,
        report_run=ReportRun(status="verified", report=report, audit=ReportAudit(
            investigation_id="route4-audit", status="verified", prompt_sha256=None,
            model_id="fake", model_revision=None, index_version="index-v1",
            elapsed_ms=1, prompt_tokens=None, output_tokens=None,
            estimated_cost_cny=0, report_sha256=digest,
        )),
        audit=ReliabilityAudit(
            status="verified", elapsed_ms=1, policy_attempts=1, generation_attempts=1,
            retries=0, attempts=[], cumulative_cost_cny=0, api_stop_cny=24,
            api_budget_cny=30, cost_remaining_cny=24, graph_steps=0, graph_node_trace=[],
        ),
    )


class FakeRunner:
    def __init__(self):
        self.calls = 0

    def run(self, request):
        self.calls += 1
        return _result()


def _service(runner=None, **kwargs):
    return PostgresInvestigationService(
        dsn=DSN, dataset_source="synthetic", versions=VERSIONS,
        runner=runner or FakeRunner(), max_runs=1000, **kwargs,
    )


@pytest.fixture(scope="module", autouse=True)
def migration():
    store = PostgresStore(DSN)
    store.migrate()
    store.migrate()
    assert store.check()


def _decision(body, kind="reject", **changes):
    return {
        "run_id": body["run_id"], "session_id": body["session_id"],
        "transaction_id": "T3", "report_sha256": body["report_sha256"],
        "decision": kind, "reviewer_id": "analyst-1", "note": "reviewed",
    } | changes


def test_restart_retry_conflict_and_stable_report():
    runner = FakeRunner()
    first_service = _service(runner)
    with TestClient(create_app(investigation_service_factory=lambda: first_service)) as client:
        created = client.post("/api/v1/investigations", json=REQUEST)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["approval"]["status"] == "pending"
    second_service = _service()
    with TestClient(create_app(investigation_service_factory=lambda: second_service)) as client:
        loaded = client.get(f"/api/v1/investigations/{body['run_id']}")
        assert loaded.status_code == 200 and loaded.json() == body
        first = client.post(f"/api/v1/investigations/{body['run_id']}/approval",
                            json=_decision(body))
        assert first.status_code == 200, first.text
        retry = client.post(f"/api/v1/investigations/{body['run_id']}/approval",
                            json=_decision(body))
        assert retry.status_code == 200
        assert retry.json()["audit"] == first.json()["audit"]
        conflict = client.post(f"/api/v1/investigations/{body['run_id']}/approval",
                               json=_decision(body, kind="approve"))
        assert conflict.status_code == 409
        assert conflict.json()["error_code"] == "approval_conflict"
        wrong_hash = client.post(f"/api/v1/investigations/{body['run_id']}/approval",
                                 json=_decision(body, report_sha256="f" * 64))
        assert wrong_hash.status_code == 409
        assert wrong_hash.json()["error_code"] == "approval_binding_mismatch"
    assert runner.calls == 1
    with PostgresStore(DSN).connect() as conn:
        rows = conn.execute(
            "SELECT kind FROM approval_events WHERE run_id = %s ORDER BY sequence",
            (body["run_id"],),
        ).fetchall()
    assert [row["kind"] for row in rows] == ["created", "rejected"]


def test_creation_idempotency_and_write_interrupt():
    runner = FakeRunner()
    service = _service(runner)
    with TestClient(create_app(investigation_service_factory=lambda: service)) as client:
        key = "route4-cost-" + os.urandom(8).hex()
        created = client.post("/api/v1/investigations", json=REQUEST,
                              headers={"Idempotency-Key": key})
        assert created.status_code == 201, created.text
        replay = client.post("/api/v1/investigations", json=REQUEST,
                             headers={"Idempotency-Key": key})
        assert replay.status_code == 200
        assert replay.json() == created.json()
        assert runner.calls == 1
        changed = client.post("/api/v1/investigations",
                              json={**REQUEST, "relation_limit": 2},
                              headers={"Idempotency-Key": key})
        assert changed.status_code == 409
        assert changed.json()["error_code"] == "idempotency_conflict"

    other = _service(FakeRunner())
    original_publish = other.store.publish
    def fail_publish(*args, **kwargs):
        raise ServiceError("persistence_unavailable")
    other.store.publish = fail_publish
    key2 = "route4-interrupt-" + os.urandom(8).hex()
    with TestClient(create_app(investigation_service_factory=lambda: other)) as client:
        failed = client.post("/api/v1/investigations", json=REQUEST,
                             headers={"Idempotency-Key": key2})
        assert failed.status_code == 503
        assert failed.json()["error_code"] == "persistence_unavailable"
    other.store.publish = original_publish
    with PostgresStore(DSN).connect() as conn:
        pending_row = conn.execute(
            """SELECT r.run_id, r.storage_state FROM investigation_runs r
               JOIN create_idempotency i ON i.run_id = r.run_id
               WHERE i.key_sha256 = %s""",
            (hashlib.sha256(key2.encode()).hexdigest(),),
        ).fetchone()
    assert pending_row["storage_state"] == "preparing"
    retry_service = _service(FakeRunner())
    with pytest.raises(ServiceError, match="persistence_unavailable"):
        retry_service.get(pending_row["run_id"])
    with TestClient(create_app(investigation_service_factory=lambda: retry_service)) as client:
        repaired = client.post("/api/v1/investigations", json=REQUEST,
                               headers={"Idempotency-Key": key2})
        assert repaired.status_code == 201, repaired.text
        assert repaired.json()["approval"]["status"] == "pending"
        assert retry_service.runner.calls == 0


def test_expiry_outage_and_schema_tamper():
    service = _service(approval_ttl=timedelta(seconds=1))
    with TestClient(create_app(investigation_service_factory=lambda: service)) as client:
        created = client.post("/api/v1/investigations", json=REQUEST)
        assert created.status_code == 201, created.text
        body = created.json()
        original_dsn = service.store.dsn
        service.store.dsn = "postgresql://127.0.0.1:1/invalid?connect_timeout=1"
        unavailable = client.get(f"/api/v1/investigations/{body['run_id']}")
        assert unavailable.status_code == 503
        service.store.dsn = original_dsn
        recovered = client.get(f"/api/v1/investigations/{body['run_id']}")
        assert recovered.status_code == 200
    time.sleep(1.1)
    new_service = _service(approval_ttl=timedelta(seconds=1))
    with TestClient(create_app(investigation_service_factory=lambda: new_service)) as client:
        late = client.post(f"/api/v1/investigations/{body['run_id']}/approval",
                           json=_decision(body))
        assert late.status_code == 410, late.text
        current = client.get(f"/api/v1/investigations/{body['run_id']}")
        assert current.status_code == 200
        assert current.json()["approval"]["status"] == "expired"
        assert current.json()["report_sha256"] == body["report_sha256"]
    with PostgresStore(DSN).connect() as conn:
        events = conn.execute(
            "SELECT kind FROM approval_events WHERE run_id = %s ORDER BY sequence",
            (body["run_id"],),
        ).fetchall()
    assert [event["kind"] for event in events] == ["created", "expired"]


def test_persistent_cost_reserve_settle_once():
    store = PostgresStore(DSN)
    ledger = PersistentCostLedger(store, stop=Decimal("24"), budget=Decimal("30"))
    run_id, state = store.claim(
        run_id="run-cost-" + os.urandom(8).hex(), fingerprint="cost-test",
        key_hash=None, request_id=None, ttl=timedelta(hours=1), max_runs=1000,
    )
    assert state == "new"
    before = ledger.spent
    with ledger.run_context(run_id):
        ledger.reserve(Decimal("2"))
        ledger.settle(Decimal("2"), Decimal("1"))
        ledger.settle(Decimal("2"), Decimal("1"))
    assert ledger.spent == before + Decimal("1")
    with store.connect() as conn:
        attempts = conn.execute("SELECT state, actual_cny FROM cost_attempts WHERE run_id = %s",
                                (run_id,)).fetchall()
    assert attempts == [{"state": "settled", "actual_cny": Decimal("1")}]



def test_two_connections_choose_one_decision():
    creator = _service()
    record = creator.run(REQUEST)
    creator.close()
    body = {
        "run_id": record.run_id, "session_id": record.session_id,
        "report_sha256": record.report_sha256,
    }
    from graphrisk.agent.approval import ApprovalDecision

    def attempt(kind: str):
        service = _service()
        decision = ApprovalDecision.model_validate({
            key: value for key, value in _decision(body, kind).items() if key != "run_id"
        })
        try:
            return service.decide(record.run_id, decision).approval.status
        except ServiceError as error:
            return error.code
        finally:
            service.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("approve", "reject")))
    assert sorted(results) in (["approval_conflict", "approved"],
                               ["approval_conflict", "rejected"])
    final = _service()
    try:
        state = final.get(record.run_id).approval.status
        assert state in ("approved", "rejected")
    finally:
        final.close()
    with PostgresStore(DSN).connect() as conn:
        rows = conn.execute(
            "SELECT kind FROM approval_events WHERE run_id = %s ORDER BY sequence",
            (record.run_id,),
        ).fetchall()
    assert [row["kind"] for row in rows] == ["created", state]


def test_wrong_schema_and_missing_checkpoint_fail_closed():
    service = _service()
    record = service.run(REQUEST)
    service.close()
    store = PostgresStore(DSN)
    with store.connect() as conn:
        conn.execute(
            "UPDATE investigation_runs SET storage_schema_version = 9 WHERE run_id = %s",
            (record.run_id,),
        )
    reader = _service()
    try:
        with pytest.raises(ServiceError, match="persistence_unavailable"):
            reader.get(record.run_id)
    finally:
        reader.close()
    with store.connect() as conn:
        conn.execute(
            "UPDATE investigation_runs SET storage_schema_version = 1 WHERE run_id = %s",
            (record.run_id,),
        )
    with store.saver() as saver:
        saver.delete_thread(record.session_id)
    reader = _service()
    try:
        with pytest.raises(ServiceError, match="persistence_unavailable"):
            reader.get(record.run_id)
    finally:
        reader.close()



@pytest.mark.parametrize("failure_point", ["before_resume", "after_resume"])
def test_decision_intent_repairs_after_checkpoint_window(monkeypatch, failure_point):
    from graphrisk.agent.approval import ApprovalDecision, Stage05ApprovalWorkflow

    creator = _service()
    record = creator.run(REQUEST)
    creator.close()
    decision = ApprovalDecision(
        session_id=record.session_id, transaction_id="T3",
        report_sha256=record.report_sha256, decision="reject",
        reviewer_id="analyst-1", note="reviewed",
    )
    first = _service()
    if failure_point == "before_resume":
        original = Stage05ApprovalWorkflow.resume
        def fail_once(*args, **kwargs):
            monkeypatch.setattr(Stage05ApprovalWorkflow, "resume", original)
            raise RuntimeError("injected checkpoint interruption")
        monkeypatch.setattr(Stage05ApprovalWorkflow, "resume", fail_once)
    else:
        original = first._snapshot_data
        def fail_once(record_arg, view_arg):
            if record_arg.approval and record_arg.approval.status == "rejected":
                raise RuntimeError("injected domain write interruption")
            return original(record_arg, view_arg)
        monkeypatch.setattr(first, "_snapshot_data", fail_once)
    with pytest.raises(ServiceError, match="persistence_unavailable"):
        first.decide(record.run_id, decision)
    first.close()

    repaired = _service()
    try:
        outcome = repaired.decide(record.run_id, decision)
        assert outcome.approval.status == "rejected"
        assert len(outcome.approval.audit) == 2
        assert repaired.decide(record.run_id, decision).approval == outcome.approval
    finally:
        repaired.close()
    with PostgresStore(DSN).connect() as conn:
        events = conn.execute(
            "SELECT kind FROM approval_events WHERE run_id = %s ORDER BY sequence",
            (record.run_id,),
        ).fetchall()
    assert [event["kind"] for event in events] == ["created", "rejected"]



def test_run_ttl_cleanup_and_unknown_migration_version():
    store = PostgresStore(DSN)
    service = _service(ttl=timedelta(seconds=1), approval_ttl=timedelta(seconds=1))
    record = service.run(REQUEST)
    service.close()
    time.sleep(1.1)
    reader = _service()
    try:
        with pytest.raises(ServiceError, match="run_expired"):
            reader.get(record.run_id)
    finally:
        reader.close()
    result = store.cleanup_expired(batch_size=1000)
    assert result["cleared_reports"] >= 1
    with store.connect() as conn:
        row = conn.execute(
            "SELECT storage_state, record_json FROM investigation_runs WHERE run_id = %s",
            (record.run_id,),
        ).fetchone()
    assert row == {"storage_state": "expired", "record_json": None}
    with store.saver() as saver:
        assert not saver.get_tuple({"configurable": {"thread_id": record.session_id}})
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO schema_migrations(version, checksum) VALUES (2, 'unknown-test')"
        )
    try:
        assert not store.check()
        with pytest.raises(ServiceError, match="persistence_unavailable"):
            _service()
    finally:
        with store.connect() as conn:
            conn.execute("DELETE FROM schema_migrations WHERE version = 2")
    assert store.check()



def test_historical_run_uses_pinned_versions_after_new_service_loads():
    first = _service()
    record = first.run(REQUEST)
    first.close()
    upgraded = PostgresInvestigationService(
        dsn=DSN, dataset_source="synthetic",
        versions=RunVersions(graph_comparison_run="next-run",
                             policy_index_version="next-index",
                             generation_model_id="next-model"),
        runner=FakeRunner(), max_runs=1000,
    )
    try:
        recovered = upgraded.get(record.run_id)
        assert recovered.report_sha256 == record.report_sha256
        assert recovered.versions == record.versions
        assert recovered.approval.status == "pending"
    finally:
        upgraded.close()



def test_unknown_provider_result_holds_budget_for_reconciliation():
    store = PostgresStore(DSN)
    ledger = PersistentCostLedger(store, stop=Decimal("24"), budget=Decimal("30"))
    run_id, _ = store.claim(
        run_id="run-uncertain-" + os.urandom(8).hex(), fingerprint="uncertain-test",
        key_hash=None, request_id=None, ttl=timedelta(hours=1), max_runs=1000,
    )
    before = ledger.spent
    with ledger.run_context(run_id):
        ledger.reserve(Decimal("2"))
    ledger.mark_uncertain(run_id)
    assert ledger.spent == before + Decimal("2")
    with store.connect() as conn:
        state = conn.execute(
            "SELECT state, actual_cny FROM cost_attempts WHERE run_id = %s",
            (run_id,),
        ).fetchone()
    assert state == {"state": "uncertain", "actual_cny": None}



def test_frozen_restart_case_scores_from_real_database(tmp_path):
    from pathlib import Path

    from graphrisk.demo.stage07_evaluation import load_contract, score_case
    from graphrisk.demo.stage07_probes import probe_pending_restart

    repo = Path(__file__).resolve().parents[2]
    contract, chunks, _ = load_contract(repo)
    case = next(item for item in contract["suites"]["frozen"]["cases"]
                if item["id"] == "pending_restart_recovers")
    observation = probe_pending_restart(repo, tmp_path / "index.json", dsn=DSN)
    scored = score_case(case, observation, chunks)
    assert scored["status"] == "passed", scored


def test_graph_failure_remains_partial_without_approval(tmp_path):
    from pathlib import Path
    from graphrisk.demo.stage06_evaluation import CountingRunner, _fixture_results, _service as fixture_service

    repo = Path(__file__).resolve().parents[2]
    results, index_version = _fixture_results(repo, tmp_path / "policy-index.json")
    source = results["partial_graph"]
    memory, _ = fixture_service(source, index_version)
    service = PostgresInvestigationService(
        dsn=DSN, dataset_source="synthetic", versions=memory.versions,
        runner=CountingRunner(source), max_runs=1000,
    )
    with TestClient(create_app(investigation_service_factory=lambda: service)) as client:
        created = client.post("/api/v1/investigations", json=REQUEST)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["status"] == "partial_evidence"
        assert body["approval"] is None
        assert body["report"]["graph"]["status"] == "unavailable"
        loaded = client.get(f"/api/v1/investigations/{body['run_id']}")
        assert loaded.status_code == 200 and loaded.json() == body
