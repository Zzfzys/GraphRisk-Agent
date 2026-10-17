"""Operational metadata must not change reports or invent missing measurements."""
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from graphrisk.demo.stage06_evaluation import _fixture_results, _service
from graphrisk.monitoring.operations import (OperationEvent, binding, emit_run, error_family,
    latency_summary, project_run, summarize_events)
from graphrisk.monitoring.drift import freeze_reference, compare_population

ROOT = Path(__file__).resolve().parents[2]
REQUEST = {"transaction_id": "T3", "dataset_source": "synthetic", "policy_as_of": "2026-07-01", "relation_limit": 3}


def record(tmp_path):
    results, version = _fixture_results(ROOT, tmp_path / "index.json")
    service, _ = _service(results["normal"], version)
    return service.run(REQUEST)


@pytest.fixture(autouse=True)
def capture_uvicorn_logs_after_streamlit_import(monkeypatch, caplog):
    # Other suites import UI logging configuration, which disables propagation.
    # Parent uvicorn can also disable propagation; capture directly and restore.
    logger = logging.getLogger("uvicorn.error")
    monkeypatch.setattr(logger, "propagate", False)
    logger.addHandler(caplog.handler)
    try:
        yield
    finally:
        logger.removeHandler(caplog.handler)


def test_projection_is_read_only_hashes_versions_and_does_not_invent_ids(tmp_path):
    original = record(tmp_path)
    original.versions.generation_model_id = "secret-model-canary:/private/path"
    before = original.model_dump_json()
    events = project_run(original)
    assert original.model_dump_json() == before
    assert all(e.request_id is None for e in events)
    assert events[0].versions.llm_model == binding(original.versions.generation_model_id)
    rendered = "\n".join(e.model_dump_json() for e in events)
    assert "secret-model-canary" not in rendered and '"transaction_id"' not in rendered
    assert events[0].cold_warm == "unknown"
    attempts = [e for e in events if e.kind == "attempt"]
    assert attempts and len({e.attempt_id for e in attempts}) == len(attempts)


def test_replayed_run_is_not_counted_as_new_generation(tmp_path):
    original = record(tmp_path)
    replay = original.model_copy(update={"idempotent_replay": True})
    events = project_run(original) + project_run(replay)
    summary = summarize_events(events)
    assert summary["run_count"] == 1 and summary["replays_not_counted_as_execution"] == 1
    assert summarize_events(events + events) == summary
    assert summary["run_latency"]["production_slo_claimed"] is False


def test_api_operational_events_correlate_and_exclude_prompt_and_review_note(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    results, version = _fixture_results(ROOT, tmp_path / "index.json")
    service, runner = _service(results["normal"], version)
    with TestClient(create_app(lambda: service)) as client:
        response = client.post("/api/v1/investigations", json=REQUEST)
        assert response.status_code == 201
        body = response.json()
        assert client.get("/api/v1/investigations/" + body["run_id"]).json() == body
        decision = {"run_id": body["run_id"], "session_id": body["session_id"], "transaction_id": "T3",
                    "report_sha256": body["report_sha256"], "decision": "reject", "reviewer_id": "reviewer",
                    "note": "private-note-canary"}
        approved = client.post(f"/api/v1/investigations/{body['run_id']}/approval", json=decision)
        assert approved.status_code == 200 and runner.calls == 1
    logs = [r.message.removeprefix("graphrisk_operation ") for r in caplog.records if r.message.startswith("graphrisk_operation ")]
    parsed = [OperationEvent.model_validate_json(row) for row in logs]
    run = next(e for e in parsed if e.kind == "run")
    assert run.request_id == response.headers["X-Request-ID"] and run.run_id == body["run_id"]
    approval = next(e for e in parsed if e.kind == "approval")
    assert approval.run_id == run.run_id and approval.request_id == approved.headers["X-Request-ID"]
    assert approval.status == "rejected"
    assert all(v not in "\n".join(logs) for v in ("private-note-canary", "EVIDENCE_JSON", "Human review is required", '"transaction_id"'))


def test_bad_telemetry_is_reported_without_serializing_bad_payload(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    source = record(tmp_path)
    source.result.audit.prompt_tokens = -1
    emit_run(source, "req-" + "a"*32)
    events = [OperationEvent.model_validate_json(r.message.removeprefix("graphrisk_operation "))
              for r in caplog.records if r.message.startswith("graphrisk_operation ")]
    assert len(events) == 1 and events[0].kind == "measurement_error"


@pytest.mark.parametrize("status,code,expected", [
    ("generation_error", "draft_schema_invalid", "schema"),
    ("generation_error", "policy_quote_mismatch", "evidence"),
    ("error", "persistence_unavailable", "storage"),
    ("error", "inference_resource_stop", "resource"),
    ("deadline_exceeded", None, "deadline"),
    ("insufficient_evidence", None, "missing_evidence"),
    ("verified", None, "none"),
])
def test_failure_families_preserve_operational_distinctions(status, code, expected):
    assert error_family(status, code) == expected


def test_missing_tokens_are_not_zero_and_small_p95_is_descriptive(tmp_path):
    source = record(tmp_path)
    source.result.audit.prompt_tokens = None
    summary = summarize_events(project_run(source))
    assert summary["prompt_tokens"] == {"observed_runs": 0, "total_observed": 0}
    assert latency_summary([10, 20])["p95_ms"] == 19.5
    assert latency_summary([])["p95_ms"] is None
    with pytest.raises(ValueError):
        latency_summary([float("inf")])


def test_drift_identity_shift_and_missingness_use_the_same_frozen_bins():
    values = list(range(100))
    reference = freeze_reference(values, feature="transaction_amount")
    frozen = json.dumps(reference, sort_keys=True)
    assert compare_population(reference, values)["psi"] == 0
    shifted = compare_population(reference, [10000]*75 + [None]*25, population_kind="synthetic_shift")
    assert shifted["status"] == "alert" and shifted["current_missing_rate"] == .25
    assert shifted["predictive_accuracy_change"] == "not_measured_no_labels"
    assert json.dumps(reference, sort_keys=True) == frozen


def test_drift_rejects_changed_reference_model_and_labels():
    reference = freeze_reference([.1, .2, .3], feature="risk_score", model_binding="model-a")
    with pytest.raises(ValueError, match="model binding"):
        compare_population(reference, [.1], model_binding="model-b")
    changed = {**reference, "warning_psi": 99}
    with pytest.raises(ValueError, match="changed"):
        compare_population(changed, [.1], model_binding="model-a")
    with pytest.raises(ValueError):
        freeze_reference([0, 1], feature="is_fraud")
    with pytest.raises(ValueError):
        compare_population(reference, [1.1], model_binding="model-a")


@pytest.mark.parametrize("values", [[], [None, float("nan")], [float("inf")], ["12"], [True]])
def test_invalid_drift_reference_fails_with_actionable_error(values):
    with pytest.raises(ValueError):
        freeze_reference(values, feature="transaction_amount")


def test_constant_reference_has_overflow_and_missing_buckets():
    reference = freeze_reference([5, 5, 5], feature="transaction_amount")
    assert compare_population(reference, [5, 5, 5])["psi"] == 0
    result = compare_population(reference, [0, 10, None])
    assert sum(result["counts"]) == 3 and result["current_missing_rate"] == pytest.approx(1/3)


def test_operational_inputs_reject_escape_and_changed_bytes(tmp_path):
    from graphrisk.demo.stage09_operations import inside, pin_inputs, require_inputs_unchanged
    original = tmp_path/"frozen.json"
    original.write_text("frozen", encoding="utf-8")
    bindings = pin_inputs(tmp_path, tmp_path/"evidence", [original])
    require_inputs_unchanged(tmp_path, bindings)
    with pytest.raises(ValueError, match="inside"):
        inside(tmp_path, "../outside.json")
    original.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        require_inputs_unchanged(tmp_path, bindings)


def test_recovery_check_cannot_pass_with_empty_probes(tmp_path):
    from graphrisk.demo.stage09_operations import verify_recovery_evidence
    with pytest.raises(ValueError, match="missing"):
        verify_recovery_evidence(tmp_path, tmp_path, {"probes": []})
