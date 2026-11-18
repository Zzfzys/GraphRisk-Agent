"""Frozen v2 contract must reject corrupted provenance and approval bindings."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

import graphrisk.demo.stage07_evaluation as evaluation
from graphrisk.demo.stage07_evaluation import evaluate_contract, load_contract, score_case
from graphrisk.demo.stage07_probes import probe_pending_restart, probe_trace_continuity

REPO = Path(__file__).resolve().parents[2]


def _observation(case, chunks):
    """A scorer-only complete observation; never counted as a product probe."""
    request = deepcopy(case["input"]["request"])
    run_id = "run-fixture-123"
    digest = "a" * 64
    citations = []
    for chunk_id in case["expected_policy_chunks"]:
        chunk = chunks[chunk_id]
        citations.append({
            "chunk_id": chunk.chunk_id, "doc_id": chunk.doc_id, "section": chunk.section,
            "quote": chunk.text, "version": chunk.version, "source_ref": chunk.source_ref,
            "content_sha256": chunk.content_sha256,
        })
    placeholders = {"$created_run_id": run_id, "$report_sha256_before": digest}
    return {
        "probe_kind": "controlled_fault", "applied_fault": case["input"]["fault"]["kind"],
        "source_request": request, "primary_tool": case["expected_tool"],
        "primary_parameters": {key: placeholders.get(value, value) if isinstance(value, str) else value
                               for key, value in case["expected_parameters"].items()},
        "evidence_statuses": deepcopy(case["expected_evidence"]),
        **case["expected_response"],
        "tool_error_code": case.get("expected_tool_error_code"),
        "report_citations": citations, "policy_conclusion_count": len(citations),
        "retrieved_policy_chunk_ids": [item["chunk_id"] for item in citations],
        "policy_corpus_sha256": evaluation.POLICY_CORPUS_SHA256,
        "created_run_id": run_id, "target_run_id": run_id,
        "report_sha256_before": digest, "report_sha256_after": digest,
        "session_id": "session-fixture-123",
        "decision_record": {"run_id": run_id, "transaction_id": request["transaction_id"],
                            "session_id": "session-fixture-123", "report_sha256": digest},
        "decision_event_count": 1, "external_action_count": 0,
        "public_pending_without_checkpoint": False,
        "cost_settlement_count": 1, "cost_settlement_keys": ["key-1"],
        "neighbor_count": 0, "reported_neighbor_count": 0,
        "target_graph_risk_score": None,
        "trace_events": [{"operation": op, "run_id": run_id, "request_id": f"request-{op}"}
                         for op in ("create", "get", "approval")],
        "trace_gap_detected": True, "model_attempts": 2, "elapsed_seconds": 2.0,
    }


def test_frozen_suites_are_distinct_and_unrun_is_not_passed():
    contract, chunks, _ = load_contract(REPO)
    frozen = contract["suites"]["frozen"]["cases"]
    dev = contract["suites"]["development"]["cases"]
    assert len(frozen) == 15 and len(dev) == 3
    assert len({case["mechanism"] for case in frozen}) == 15
    assert contract["hashes"]["frozen"] == evaluation.FROZEN_SHA256
    assert len(chunks) == 16
    result = evaluate_contract(REPO)
    assert result["splits"]["frozen"]["not_run"] == 15
    assert result["splits"]["frozen"]["pass_rate_observed"] is None
    assert result["splits"]["development"]["not_run"] == 3


def test_citation_and_approval_binding_mutations_fail_independently():
    contract, chunks, _ = load_contract(REPO)
    cases = {case["id"]: case for case in contract["suites"]["frozen"]["cases"]}
    retry = cases["decision_retry_after_lost_response"]
    good = _observation(retry, chunks)
    assert score_case(retry, good, chunks)["status"] == "passed"
    wrong_quote = deepcopy(good)
    wrong_quote["report_citations"][0]["quote"] += " invented"
    assert "citation_source_or_date" in score_case(retry, wrong_quote, chunks)["failed_checks"]
    malformed = deepcopy(good)
    malformed["report_citations"][0]["chunk_id"] = []
    assert "citation_format" in score_case(retry, malformed, chunks)["failed_checks"]
    wrong_hash = deepcopy(good)
    wrong_hash["report_citations"][0]["content_sha256"] = "0" * 64
    assert "citation_hash" in score_case(retry, wrong_hash, chunks)["failed_checks"]
    wrong_binding = deepcopy(good)
    wrong_binding["decision_record"]["report_sha256"] = "0" * 64
    assert "bound_decision" in score_case(retry, wrong_binding, chunks)["failed_checks"]
    stale_retrieval = deepcopy(good)
    stale_retrieval["retrieved_policy_chunk_ids"].append("policy-6675acd96fe0ad61a285de87")
    assert "retrieval_source_or_date" in score_case(retry, stale_retrieval, chunks)["failed_checks"]
    missing_fault = deepcopy(good)
    missing_fault["applied_fault"] = None
    assert "fault_probe_provenance" in score_case(retry, missing_fault, chunks)["failed_checks"]


def test_each_failed_case_remains_visible_in_summary():
    contract, chunks, _ = load_contract(REPO)
    cases = {case["id"]: case for case in contract["suites"]["frozen"]["cases"]}
    passing = _observation(cases["decision_retry_after_lost_response"], chunks)
    failing = deepcopy(passing)
    failing["report_citations"][0]["quote"] = "unsupported"
    rows = evaluate_contract(REPO, {
        "decision_retry_after_lost_response": passing,
        "pending_restart_recovers": failing,
    })["splits"]["frozen"]
    assert (rows["total"], rows["observed"], rows["passed"], rows["failed"], rows["not_run"]) == (15, 2, 1, 1, 13)
    failure = next(row for row in rows["rows"] if row["id"] == "pending_restart_recovers")
    assert failure["status"] == "failed"
    assert "citation_source_or_date" in failure["failed_checks"]


def test_frozen_hash_change_is_rejected(monkeypatch):
    monkeypatch.setattr(evaluation, "FROZEN_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="Frozen frozen case file changed"):
        load_contract(REPO)


def test_current_stage06_restart_gap_is_actual_api_failure(tmp_path):
    observation = probe_pending_restart(REPO, tmp_path / "index.json")
    assert observation["probe_kind"] == "fake_api"
    assert observation["http_status"] == 410
    assert observation["error_code"] == "run_instance_mismatch"
    result = evaluate_contract(REPO, {"pending_restart_recovers": observation})
    row = result["splits"]["frozen"]["rows"][0]
    assert row["status"] == "failed"
    assert {"response_http_status", "stable_report_hash"} <= set(row["failed_checks"])
    assert result["splits"]["frozen"]["not_run"] == 14


def test_request_trace_frozen_challenge_passes_with_observed_api_calls(tmp_path):
    observation = probe_trace_continuity(REPO, tmp_path / "index.json")
    assert {event["operation"] for event in observation["trace_events"]} == {
        "create", "get", "approval"}
    assert len({event["request_id"] for event in observation["trace_events"]}) == 3
    result = evaluate_contract(REPO, {"trace_links_create_read_approval": observation})
    row = next(item for item in result["splits"]["frozen"]["rows"]
               if item["id"] == "trace_links_create_read_approval")
    assert row["status"] == "passed" and row["failed_checks"] == []
    assert result["splits"]["frozen"]["not_run"] == 14

def test_full_probe_requires_explicit_isolated_database_acknowledgment(monkeypatch, tmp_path):
    from graphrisk.demo.stage07_probe_suite import probe_agent_cases, probe_persistence_cases

    monkeypatch.delenv("GRAPHRISK_STAGE07_EVAL_ISOLATED", raising=False)
    with pytest.raises(ValueError, match="isolated evaluation database"):
        probe_persistence_cases(REPO, tmp_path / "index.json", "invalid-dsn")
    with pytest.raises(ValueError, match="isolated evaluation database"):
        probe_agent_cases(REPO, tmp_path / "index.json", "invalid-dsn", "invalid-dsn")
