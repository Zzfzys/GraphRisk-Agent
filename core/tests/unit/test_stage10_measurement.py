"""Offline experiment contracts, durable budgets and real provider transport wiring."""
import json
import math
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from test_stage05_reporting import graph_run
from graphrisk.agent.config import Stage05Generation, Stage05Limits
from graphrisk.agent.quality_contracts import QualityDraft
from graphrisk.agent.quality_evidence import investigation_packet
from graphrisk.agent.quality_reporting import QUALITY_PROMPT, OllamaQualityProvider, QualityReportWorkflow
from graphrisk.agent.reliability import CostLedger, Deadline, RetryingProvider
from graphrisk.demo.stage09_evaluation import digest
from graphrisk.demo.stage10_measurement import (
    AppendLedger, BudgetStop, GuardedTransport, PROTOCOL, Sample, binding,
    load_protocol, percentile, quality_summary, select_cases, summarize,
)
from graphrisk.demo.stage10_registration import old_ids, check_registered_source

KEYS = [f"case-{i:02d}" for i in range(1, 11)] + ["case-01-repeat", "case-06-repeat"]
PACKET = {"fixture": "CANARY_PRIVATE_PROMPT"}
DRAFT = {"policy_ref": "P1", "history_activity": "none", "relationship_evidence": "none",
         "graph_evidence": "unavailable", "policy_applicability": "not_established",
         "risk_interpretation": "uncalibrated_signal", "evidence_refs": ["F2", "F3", "F4", "F5"],
         "unknowns": ["policy_conditions", "verified_identity", "calibration", "graph_evidence"]}


def make_ledger(tmp_path):
    parent = tmp_path / "parent.json"
    parent.write_text(json.dumps({"attempts": [{"sequence": i} for i in range(1, 131)]}), encoding="utf-8")
    ledger = AppendLedger(tmp_path / "child.jsonl", parent, binding(parent), "a" * 64, "fixture", KEYS)
    ledger.initialize()
    return ledger


def generation():
    return Stage05Generation(provider="local_http", model_id=PROTOCOL["model_id"],
                             model_revision=PROTOCOL["model_revision"], endpoint=PROTOCOL["endpoint"],
                             max_output_tokens=512, request_timeout_seconds=20.)


def response(request, draft=None):
    if request.method == "GET":
        return httpx.Response(200, json={"models": [{"name": PROTOCOL["model_id"], "digest": PROTOCOL["model_revision"]}]})
    return httpx.Response(200, json={"done": True, "model": PROTOCOL["model_id"],
                                   "response": json.dumps(draft or DRAFT)})


def invoke(transport, packet=PACKET):
    with httpx.Client(transport=transport, trust_env=False) as client:
        return OllamaQualityProvider(generation(), client).with_deadline(lambda: 5.).generate(
            QUALITY_PROMPT + "\nEVIDENCE_JSON:\n" + json.dumps(packet), QualityDraft.model_json_schema())


def candidate_rows():
    return [{"transaction_id": f"{split}-{i}", "event_second": 100 + i, "selection_cutoff": 99,
             "split": split} for split in ("validation", "test") for i in range(30)]


def sample(**overrides):
    return Sample(**({"case_id": "case-01", "input_sha256": "a" * 64, "protocol_sha256": "b" * 64,
                     "scope": "fixture", "component": "api_read", "outcome": "ok", "elapsed_ms": 10.} | overrides))


def test_time_selection_is_order_independent_excludes_old_ids_and_never_accepts_scores():
    rows = candidate_rows()
    selected = select_cases(list(reversed(rows)), {"validation-0", "test-0"})
    assert selected == select_cases(rows, {"validation-0", "test-0"})
    assert [c["transaction_id"] for c in selected[:5]] == ["validation-1", "validation-8", "validation-15", "validation-22", "validation-29"]
    assert len({c["transaction_id"] for c in selected}) == 10
    assert all(set(c["request"]) == {"transaction_id", "dataset_source", "policy_as_of", "relation_limit"} for c in selected)
    with pytest.raises(ValueError, match="only_id_time"):
        select_cases([{**r, "primary_score": .99} for r in rows], set())
    with pytest.raises(ValueError, match="only_id_time"):
        select_cases([{**r, "is_fraud": 1} for r in rows], set())


@pytest.mark.parametrize("change", ["duplicate", "equal_time", "float_time", "split", "quota"])
def test_selection_rejects_invalid_time_duplicates_and_quota_without_fallback(change):
    rows = candidate_rows()
    if change == "duplicate": rows.append(rows[0].copy())
    elif change == "equal_time": rows[0]["event_second"] = 99
    elif change == "float_time": rows[0]["event_second"] = 100.
    elif change == "split": rows[0]["split"] = "train"
    else: rows = rows[:4]
    with pytest.raises(ValueError): select_cases(rows, set())


def test_exclusion_reads_id_fields_even_from_nested_development_and_source_cases():
    assert old_ids({"cases": [{"transaction_id": "T1", "score": .1}, {"nested": {"source_id": "T2"}}]}) == {"T1", "T2"}


def test_registered_source_cannot_change_and_later_orchestration_must_be_registered():
    assert check_registered_source({"provider.py": "a"}, {"provider.py": "a", "execution.py": "b"}) == {"execution.py": "b"}
    with pytest.raises(ValueError): check_registered_source({"provider.py": "a"}, {"provider.py": "changed"})
    with pytest.raises(ValueError): check_registered_source({"provider.py": "a"}, {})


def test_frozen_protocol_cannot_silently_relax_quality_or_budget(tmp_path):
    path = tmp_path / "protocol.json";path.write_text(json.dumps(PROTOCOL))
    assert load_protocol(path)["additional_limit"] == 14
    value = deepcopy(PROTOCOL);value["minimum_joint_pass"] = 8;path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="contract_drift"): load_protocol(path)


def test_percentiles_include_failures_and_denominator_keeps_missing_samples():
    assert percentile([10., 20., 30., 40.], .95) == pytest.approx(38.5)
    rows = [sample(elapsed_ms=float(i)) for i in range(1, 11)] + [sample(outcome="timeout", elapsed_ms=1000.)]
    value = summarize(rows, 12);group = value["groups"][0]
    assert group["p95_ms"] == 505. and group["successful_p95_ms"] == pytest.approx(9.55)
    assert group["p99_ms"] is None and group["p99_reason"] == "insufficient_samples"
    assert value["not_run"] == 1 and value["success_rate_of_expected"] == 10 / 12


def test_percentiles_do_not_pool_fixture_real_residency_or_concurrency():
    rows = [sample(elapsed_ms=1.) for _ in range(100)]
    rows += [sample(scope="real", elapsed_ms=900., concurrency=2, residency="cold",
                    residency_basis="matched_digest_absent_before")]
    value = summarize(rows, 101)
    assert len(value["groups"]) == 2
    assert value["groups"][0]["p99_ms"] == 1.
    assert value["groups"][1]["p99_ms"] is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1., True])
def test_invalid_latency_does_not_create_plausible_metrics(value):
    with pytest.raises(ValueError): percentile([value], .5)
    if value is not True:
        with pytest.raises(ValidationError): sample(elapsed_ms=value)


def test_quality_never_counts_repeats_as_first_cases_or_drops_not_run():
    first = KEYS[:10]
    rows = [{"run_key": k, "joint_pass": True, "unsafe": False} for k in first[:9]]
    assert quality_summary(first, rows)["status"] == "pending"
    rows.append({"run_key": first[9], "joint_pass": False, "unsafe": False})
    rows.append({"run_key": "case-01-repeat", "joint_pass": True, "unsafe": False})
    value = quality_summary(first, rows)
    assert value["status"] == "passed" and value["joint_pass"] == 9 and value["first_denominator"] == 10
    rows[-1]["unsafe"] = True
    assert quality_summary(first, rows)["status"] == "failed"
    with pytest.raises(ValueError): quality_summary(first, rows + [rows[0]])


def test_unknown_latency_and_residency_are_never_invented():
    with pytest.raises(ValidationError): sample(elapsed_ms=None)
    with pytest.raises(ValidationError): sample(outcome="not_run")
    with pytest.raises(ValidationError): sample(residency="warm")
    row = sample(outcome="not_run", elapsed_ms=None)
    value = summarize([row], 10)
    assert value["not_run"] == 10 and value["groups"][0]["p50_ms"] is None


def test_append_budget_caps_at_144_across_reopened_instances_and_never_modifies_parent(tmp_path):
    ledger = make_ledger(tmp_path);before = ledger.parent.read_bytes()
    for i in range(14):
        key = KEYS[i % 12]
        seq = ledger.reserve(key, "b" * 64);assert seq == 131 + i
        ledger.finish(seq, 200 if i % 2 else 503)
    resumed = AppendLedger(ledger.path, ledger.parent, ledger.parent_binding, "a" * 64, "fixture", KEYS)
    resumed.initialize()
    with pytest.raises(BudgetStop, match="exhausted"): resumed.reserve("case-03", "b" * 64)
    assert resumed.state()["global_used"] == 144 and ledger.parent.read_bytes() == before
    changed = AppendLedger(ledger.path, ledger.parent, ledger.parent_binding, "c" * 64, "fixture", KEYS)
    with pytest.raises(BudgetStop, match="corrupt"): changed.initialize()


@pytest.mark.parametrize("finish", [False, True])
def test_crash_or_unknown_send_blocks_retries_and_new_cases_without_refund(tmp_path, finish):
    ledger = make_ledger(tmp_path);seq = ledger.reserve("case-01", "b" * 64)
    if finish: ledger.finish(seq, None)
    assert ledger.state()["remaining"] == 13 and ledger.state()["unresolved"] == 1
    for key in ("case-01", "case-02"):
        with pytest.raises(BudgetStop, match="requires_audit"): ledger.reserve(key, "b" * 64)


@pytest.mark.parametrize("change", ["parent", "truncated", "hash", "missing"])
def test_tampered_or_missing_evidence_fails_closed(tmp_path, change):
    ledger = make_ledger(tmp_path)
    if change == "parent": ledger.parent.write_text("{}")
    elif change == "truncated": ledger.path.write_bytes(ledger.path.read_bytes()[:-1])
    elif change == "hash": ledger.path.write_bytes(ledger.path.read_bytes().replace(b'"limit": 144', b'"limit": 999'))
    else: ledger.path.unlink()
    with pytest.raises(BudgetStop): ledger.reserve("case-01", "b" * 64)


def test_native_provider_deadline_retains_http_guard_and_records_unknown_tokens(tmp_path):
    ledger = make_ledger(tmp_path);sent = []
    def handler(request):
        if request.method == "POST":
            # Proves persistence happens before the actual send, not at run completion.
            assert ledger.state()["appended_attempts"] == 1
            sent.append(request)
        return response(request)
    clock = iter((1., 1.025))
    guard = GuardedTransport(httpx.MockTransport(handler), ledger, "case-01", digest(PACKET), clock=lambda: next(clock))
    result = invoke(guard)
    assert result.model_revision == PROTOCOL["model_revision"] and len(sent) == 1
    row = guard.samples[0]
    assert row.elapsed_ms == pytest.approx(25.) and row.residency == "unknown"
    assert row.prompt_tokens is None and row.output_tokens is None and row.load_ms is None
    public = json.dumps(summarize(guard.samples, 1)) + row.model_dump_json()
    assert "CANARY_PRIVATE_PROMPT" not in public and "EVIDENCE_JSON" not in public
    with pytest.raises(ValidationError): sample(raw_prompt="secret")


def test_unknown_timeout_is_counted_once_even_with_existing_retrying_provider(tmp_path):
    ledger = make_ledger(tmp_path);posts = []
    def handler(request):
        if request.method == "POST":
            posts.append(1);raise httpx.ReadTimeout("CANARY_EXCEPTION_SECRET", request=request)
        return response(request)
    guard = GuardedTransport(httpx.MockTransport(handler), ledger, "case-01", digest(PACKET))
    limits = Stage05Limits(max_steps=12, max_model_calls=2, max_policy_queries=2, max_retries=1,
                           max_total_seconds=30., api_budget_cny=30., api_stop_cny=24.)
    with httpx.Client(transport=guard) as client:
        provider = RetryingProvider(OllamaQualityProvider(generation(), client), limits, Deadline(30.), [], CostLedger(limits), per_attempt_upper_cny=0)
        with pytest.raises(BudgetStop, match="requires_audit"):
            provider.generate(QUALITY_PROMPT + "\nEVIDENCE_JSON:\n" + json.dumps(PACKET), QualityDraft.model_json_schema())
    assert len(posts) == 1 and ledger.state()["appended_attempts"] == 1
    assert [r.outcome for r in guard.samples] == ["timeout", "budget_stop"]
    assert "CANARY_EXCEPTION_SECRET" not in json.dumps([r.model_dump() for r in guard.samples])


def test_resource_and_request_mismatch_stop_before_budget_or_send(tmp_path):
    ledger = make_ledger(tmp_path);posts = []
    def stop(): raise MemoryError("below resource floor")
    guard = GuardedTransport(httpx.MockTransport(lambda r: response(r) if r.method == "GET" else posts.append(1)), ledger, "case-01", digest(PACKET), resource_guard=stop)
    with pytest.raises(MemoryError): invoke(guard)
    assert not posts and ledger.state()["appended_attempts"] == 0
    assert guard.samples[0].outcome == "resource_stop"
    guard = GuardedTransport(httpx.MockTransport(response), ledger, "case-01", digest({"other": True}))
    with pytest.raises(ValueError, match="request_changed"): invoke(guard)
    assert ledger.state()["appended_attempts"] == 0


@pytest.mark.parametrize("url,method", [("https://api.example.com/api/generate", "POST"),
                                        ("http://127.0.0.1:11434/api/chat", "POST"),
                                        ("http://127.0.0.1:11434/api/generate?key=secret", "POST"),
                                        ("http://secret@127.0.0.1:11434/api/tags", "GET")])
def test_no_unregistered_or_nonlocal_model_traffic(tmp_path, url, method):
    ledger = make_ledger(tmp_path);sent = []
    guard = GuardedTransport(httpx.MockTransport(lambda r: sent.append(1)), ledger, "case-01", digest(PACKET))
    with httpx.Client(transport=guard) as client:
        with pytest.raises(ValueError): client.request(method, url, json={})
    assert not sent and ledger.state()["appended_attempts"] == 0


def test_failed_durable_reservation_prevents_http_send(tmp_path, monkeypatch):
    ledger = make_ledger(tmp_path);posts = []
    def broken(_): raise OSError("fsync failure")
    guard = GuardedTransport(httpx.MockTransport(lambda r: response(r) if r.method == "GET" else posts.append(1)), ledger, "case-01", digest(PACKET))
    monkeypatch.setattr("graphrisk.demo.stage10_measurement.os.fsync", broken)
    with pytest.raises(OSError): invoke(guard)
    assert not posts


def test_real_budget_cannot_be_used_with_mock_or_hidden_retry_transport(tmp_path):
    fixture = make_ledger(tmp_path)
    with pytest.raises(ValueError): GuardedTransport(None, fixture, "case-01", digest(PACKET))
    real = AppendLedger(tmp_path / "real.jsonl", fixture.parent, fixture.parent_binding, "b" * 64, "real", KEYS)
    with pytest.raises(ValueError): GuardedTransport(httpx.MockTransport(response), real, "case-01", digest(PACKET))
    with pytest.raises(ValueError): GuardedTransport(None, real, "case-01", digest(PACKET))


@pytest.mark.parametrize("field,value", [("model", "other"), ("keep_alive", -1), ("think", True),
                                         ("stream", True), ("options", {"temperature": 1, "num_predict": 512}),
                                         ("format", {})])
def test_changed_generation_settings_never_spend_budget(tmp_path, field, value):
    ledger = make_ledger(tmp_path)
    guard = GuardedTransport(httpx.MockTransport(response), ledger, "case-01", digest(PACKET))
    payload = {"model": "qwen3:4b", "prompt": QUALITY_PROMPT + "\nEVIDENCE_JSON:\n" + json.dumps(PACKET),
               "format": QualityDraft.model_json_schema(), "stream": False,
               "options": {"temperature": 0, "num_predict": 512}, "keep_alive": 0, "think": False}
    payload[field] = value
    with httpx.Client(transport=guard) as client:
        with pytest.raises(ValueError): client.post(PROTOCOL["endpoint"], json=payload)
    assert ledger.state()["appended_attempts"] == 0


def test_guarded_native_quality_workflow_rejects_wrong_judgment_not_silent_repair(tmp_path, graph_run):
    graph = graph_run();packet = investigation_packet(graph)
    ledger = make_ledger(tmp_path)
    wrong = {**DRAFT, "risk_interpretation": "confirmed_fraud"}
    guard = GuardedTransport(httpx.MockTransport(lambda r: response(r, wrong)), ledger, "case-01", digest(packet))
    with httpx.Client(transport=guard) as client:
        run = QualityReportWorkflow(OllamaQualityProvider(generation(), client)).run(graph)
    assert run.status == "rejected" and run.report is None
    assert ledger.state()["appended_attempts"] == 1 and guard.samples[0].outcome == "ok"
    # HTTP success is deliberately distinct from independent semantic quality.


def test_guarded_native_quality_workflow_preserves_correct_bound_report(tmp_path, graph_run):
    graph = graph_run();packet = investigation_packet(graph);facts = packet["facts"]
    graph_available = facts["F5"]["value"] is not None
    correct = {**DRAFT,
               "history_activity": "observed" if facts["F2"]["value"]["tx_count_last_7d"] else "none",
               "relationship_evidence": "observed" if facts["F4"]["value"]["returned_count"] else "none",
               "graph_evidence": "available" if graph_available else "unavailable",
               "unknowns": ["policy_conditions", "verified_identity", "calibration"] + ([] if graph_available else ["graph_evidence"])}
    ledger = make_ledger(tmp_path)
    guard = GuardedTransport(httpx.MockTransport(lambda r: response(r, correct)), ledger, "case-01", digest(packet))
    with httpx.Client(transport=guard) as client:
        run = QualityReportWorkflow(OllamaQualityProvider(generation(), client)).run(graph)
    assert run.status == "verified" and run.report.analysis.draft.risk_interpretation == "uncalibrated_signal"
    assert run.report.analysis.packet_sha256 == digest(packet)
    assert ledger.state()["appended_attempts"] == 1
