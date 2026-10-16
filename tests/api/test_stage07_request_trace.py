"""Route 2: request IDs bind safe tool/agent/approval traces without body leakage."""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app
from graphrisk.demo.stage06_evaluation import _fixture_results, _service
from graphrisk.investigation.workflow import AuditEvent

REPO = Path(__file__).resolve().parents[2]
REQUEST = {"transaction_id": "T3", "dataset_source": "synthetic",
           "policy_as_of": "2026-07-01", "relation_limit": 3}
ID = re.compile(r"req-[0-9a-f]{32}\Z")


def _events(caplog):
    return [json.loads(record.message.removeprefix("graphrisk_trace "))
            for record in caplog.records if record.message.startswith("graphrisk_trace ")]


def _service_with_tools(tmp_path, scenario="normal"):
    results, version = _fixture_results(REPO, tmp_path / "index.json")
    result = results[scenario]
    audit = result.graph_run.investigation_audit
    assert audit is not None
    at = datetime(2026, 9, 28, tzinfo=UTC)
    audit.events = [AuditEvent(
        investigation_id=audit.investigation_id, sequence=number,
        tool=tool, started_at=at, completed_at=at + timedelta(milliseconds=number),
        duration_ms=number, status="ok",
    ) for number, tool in enumerate(("get_transaction", "get_risk_score"), start=1)]
    service, runner = _service(result, version)
    return service, runner


def test_create_read_approval_have_distinct_ids_and_bound_trace(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    service, runner = _service_with_tools(tmp_path)
    with TestClient(create_app(lambda: service)) as client:
        created = client.post("/api/v1/investigations", json=REQUEST,
                              headers={"X-Request-ID": "client-forged-id"})
        assert created.status_code == 201
        create_id = created.headers["X-Request-ID"]
        assert ID.fullmatch(create_id) and create_id != "client-forged-id"
        body = created.json()
        run_id = body["run_id"]
        assert body["audit"]["creation_request_id"] == create_id
        assert {event["tool"] for event in body["audit"]["tool_events"]} >= {
            "get_transaction", "get_risk_score", "policy_retrieval", "generation"}
        assert all(event["request_id"] == create_id for event in body["audit"]["tool_events"])
        assert body["approval"]["audit"][0]["request_id"] == create_id

        read = client.get(f"/api/v1/investigations/{run_id}")
        read_id = read.headers["X-Request-ID"]
        assert ID.fullmatch(read_id) and read_id != create_id
        assert read.json() == body  # immutable creation audit survives a new HTTP request
        pending = client.get(f"/api/v1/investigations/{run_id}/approval")
        assert ID.fullmatch(pending.headers["X-Request-ID"])
        assert pending.headers["X-Request-ID"] not in {create_id, read_id}

        decision = {"run_id": run_id, "session_id": body["session_id"],
                    "transaction_id": "T3", "report_sha256": body["report_sha256"],
                    "decision": "reject", "reviewer_id": "analyst-1", "note": "private-note-canary"}
        decided = client.post(f"/api/v1/investigations/{run_id}/approval", json=decision)
        decide_id = decided.headers["X-Request-ID"]
        assert ID.fullmatch(decide_id) and decide_id not in {create_id, read_id}
        assert decided.status_code == 200
        assert decided.json()["audit"][-1]["request_id"] == decide_id
        assert runner.calls == 1

    events = _events(caplog)
    assert any(event["request_id"] == create_id and event["run_id"] == run_id
               and event["phase"] == "tool" and event["tool"] == "get_risk_score" for event in events)
    assert any(event["request_id"] == create_id and event["run_id"] == run_id
               and event["phase"] == "agent_node" and event["tool"] == "retrieve_policy" for event in events)
    assert any(event["request_id"] == create_id and event["phase"] == "dependency_attempt"
               and event["tool"] == "generation" for event in events)
    assert any(event["request_id"] == read_id and event["run_id"] == run_id
               and event["phase"] == "http" for event in events)
    assert any(event["request_id"] == decide_id and event["run_id"] == run_id
               and event["phase"] == "approval" and event["status"] == "rejected" for event in events)
    trace_text = "\n".join(record.message for record in caplog.records if record.message.startswith("graphrisk_trace "))
    assert all(token not in trace_text for token in ("client-forged-id", "private-note-canary",
                                                     "Human review is required", "is_fraud", "card_id",
                                                     "device_id", "EVIDENCE_JSON"))
    assert all(set(event) == {"at", "request_id", "run_id", "phase", "tool", "status",
                              "error_code", "elapsed_ms", "model_version", "index_version",
                              "http_status"} for event in events)


def test_no_policy_and_error_paths_still_have_trace(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    service, _ = _service_with_tools(tmp_path, "no_policy")
    with TestClient(create_app(lambda: service)) as client:
        no_policy = client.post("/api/v1/investigations", json=REQUEST)
        assert no_policy.status_code == 201
        no_policy_id = no_policy.headers["X-Request-ID"]
        assert no_policy.json()["status"] == "insufficient_evidence"
        assert no_policy.json()["audit"]["generation_attempts"] == 0
        assert all(event["request_id"] == no_policy_id for event in no_policy.json()["audit"]["tool_events"])
        invalid = client.post("/api/v1/investigations", json={**REQUEST, "secret": "body-secret-canary"})
        assert invalid.status_code == 422 and ID.fullmatch(invalid.headers["X-Request-ID"])
        missing = client.get("/api/v1/investigations/run-wrong-123")
        assert missing.status_code == 410 and ID.fullmatch(missing.headers["X-Request-ID"])
    events = _events(caplog)
    assert any(event["request_id"] == no_policy_id and event["tool"] == "policy_retrieval" for event in events)
    assert not any(event["request_id"] == no_policy_id and event["tool"] == "generation"
                   and event["phase"] == "dependency_attempt" for event in events)
    assert any(event["request_id"] == invalid.headers["X-Request-ID"]
               and event["error_code"] == "invalid_request" for event in events)
    assert any(event["request_id"] == missing.headers["X-Request-ID"]
               and event["error_code"] == "run_instance_mismatch" for event in events)
    assert "body-secret-canary" not in "\n".join(record.message for record in caplog.records)


def test_concurrent_creates_do_not_mix_request_ids(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    service, runner = _service_with_tools(tmp_path)
    with TestClient(create_app(lambda: service)) as client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: client.post("/api/v1/investigations", json=REQUEST), range(4)))
    pairs = [(response.headers["X-Request-ID"], response.json()["run_id"])
             for response in responses]
    assert all(response.status_code == 201 for response in responses)
    assert len({request_id for request_id, _ in pairs}) == 4
    assert len({run_id for _, run_id in pairs}) == 4
    assert runner.calls == 4
    events = _events(caplog)
    for request_id, run_id in pairs:
        scoped = [event for event in events if event["request_id"] == request_id]
        assert scoped and all(event["run_id"] == run_id for event in scoped)
        assert any(event["phase"] == "tool" for event in scoped)


def test_expiry_audit_event_binds_the_request_that_observed_expiry(tmp_path):
    results, version = _fixture_results(REPO, tmp_path / "index.json")
    now = [datetime(2026, 9, 28, tzinfo=UTC)]
    service, _ = _service(results["normal"], version, clock=lambda: now[0])
    with TestClient(create_app(lambda: service)) as client:
        created = client.post("/api/v1/investigations", json=REQUEST).json()
        now[0] += timedelta(minutes=30)
        expired = client.get(f"/api/v1/investigations/{created['run_id']}/approval")
        assert expired.status_code == 200
        assert expired.json()["status"] == "expired"
        assert expired.json()["audit"][-1]["request_id"] == expired.headers["X-Request-ID"]


def test_unhandled_error_keeps_request_id_and_hides_exception(caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    app = create_app()
    assert logging.getLogger("uvicorn.access").disabled

    @app.get("/api/v1/trace-test-error")
    def trace_test_error():
        raise RuntimeError("exception-secret-canary")

    with TestClient(app) as client:
        response = client.get("/api/v1/trace-test-error")
    assert response.status_code == 500
    assert ID.fullmatch(response.headers["X-Request-ID"])
    assert response.json()["error_code"] == "internal_error"
    assert "exception-secret-canary" not in response.text
    events = _events(caplog)
    assert any(event["request_id"] == response.headers["X-Request-ID"]
               and event["error_code"] == "internal_error" for event in events)
    assert "exception-secret-canary" not in "\n".join(record.message for record in caplog.records)
