from __future__ import annotations

import json

import httpx
import pytest

from graphrisk.agent.local_probe import (
    LocalModelSpec, ProbeConfig, check_answer, percentile, probe_once, summarize, unload, idle_resource_errors,
)

SPEC = LocalModelSpec(model="qwen3:4b", digest="a" * 64)
CONFIG = ProbeConfig()
EXPECTED = {"case_id": "demo", "decision": "human_review", "evidence_ids": ["E1"], "policy_ids": ["P1"]}
CASE = {"id": "demo", "question": "Does E1 establish P1?", "evidence": [{"id": "E1", "text": "count 7"}],
        "policies": [{"id": "P1", "text": "count >= 5 requires review"}], "expected": EXPECTED}


def setup_client(*, alter=None, resident=False, digest="a" * 64):
    calls = []
    ps = {"name": SPEC.model, "digest": digest, "context_length": 4096,
          "size": 3_000_000_000, "size_vram": 3_000_000_000}
    state = {"resident": resident}
    response = {"model": SPEC.model, "done": True, "done_reason": "stop",
                "response": json.dumps(EXPECTED), "total_duration": 1_000_000_000,
                "load_duration": 100_000_000, "prompt_eval_duration": 100_000_000,
                "eval_duration": 800_000_000, "prompt_eval_count": 200, "eval_count": 50}
    if alter:
        alter(response)

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": SPEC.model, "digest": digest}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [ps] if state["resident"] else []})
        if request.url.path == "/api/generate":
            payload = json.loads(request.content)
            state["resident"] = payload["keep_alive"] != 0
            return httpx.Response(200, json=response)
        raise AssertionError(request.url.path)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls, state


def test_pinned_request_json_mode_and_cold_warm_are_distinct():
    client, calls, _ = setup_client()
    cold = probe_once(client, CONFIG, SPEC, CASE, phase="cold")
    warm = probe_once(client, CONFIG, SPEC, CASE, phase="warm")
    assert cold["ps_before"] is None and warm["ps_before"]["digest"] == SPEC.digest
    assert cold["expected_match"] and cold["schema_valid"]
    assert cold["prompt_sha256"] == warm["prompt_sha256"]
    payloads = [json.loads(r.content) for r in calls if r.method == "POST"]
    assert payloads[0] == payloads[1]
    assert payloads[0]["format"] == "json" and payloads[0]["think"] is False
    assert payloads[0]["options"]["num_ctx"] == 4096
    assert payloads[0]["options"]["num_predict"] == 512
    assert "expected" not in json.loads(payloads[0]["prompt"])
    assert cold["load_duration"] == 100_000_000
    assert cold["wall_ms"] >= 0 and cold["total_duration"] == 1_000_000_000
    unload(client, CONFIG, SPEC)
    assert not client.get(CONFIG.endpoint + "/api/ps").json()["models"]


@pytest.mark.parametrize("phase,resident,error", [
    ("cold", True, "cold_model_still_loaded"),
    ("warm", False, "warm_model_not_loaded"),
])
def test_cold_warm_preconditions_cannot_be_mislabelled(phase, resident, error):
    client, calls, _ = setup_client(resident=resident)
    with pytest.raises(ValueError, match=error):
        probe_once(client, CONFIG, SPEC, CASE, phase=phase)
    assert not any(r.method == "POST" for r in calls)


def test_wrong_digest_rejected_before_generation():
    client, calls, _ = setup_client(digest="b" * 64)
    with pytest.raises(ValueError, match="model_digest_mismatch"):
        probe_once(client, CONFIG, SPEC, CASE, phase="cold")
    assert not any(r.method == "POST" for r in calls)


@pytest.mark.parametrize("field,value,error", [
    ("done", False, "incomplete_or_wrong_model"),
    ("model", "another:latest", "incomplete_or_wrong_model"),
    ("done_reason", "length", "truncated_or_unexpected_finish"),
    ("thinking", "unexpected chain", "unexpected_thinking_output"),
    ("eval_count", 513, "token_limit_exceeded"),
    ("prompt_eval_count", 4096, "token_limit_exceeded"),
    ("eval_duration", -1, "invalid_ollama_metrics"),
    ("prompt_eval_count", None, "invalid_ollama_metrics"),
    ("eval_count", True, "invalid_ollama_metrics"),
    ("response", "not json", "invalid_output_schema"),
])
def test_failed_responses_keep_measurements_and_do_not_pass(field, value, error):
    client, _, _ = setup_client(alter=lambda response: response.update({field: value}))
    row = probe_once(client, CONFIG, SPEC, CASE, phase="cold")
    assert row["error"] == error and not row["expected_match"]
    assert "wall_ms" in row


@pytest.mark.parametrize("change", [
    {"decision": "freeze_account"}, {"evidence_ids": ["E999"]}, {"policy_ids": ["P999"]},
    {"case_id": "wrong"}, {"evidence_ids": ["E1", "E1"]}, {"extra": "unsupported"},
])
def test_schema_and_evidence_safety(change):
    row = check_answer(json.dumps({**EXPECTED, **change}), EXPECTED)
    assert not row["expected_match"]
    assert row["error"] in {"invalid_output_schema", "answer_or_evidence_mismatch"}


def test_insufficient_evidence_is_scored_not_assumed():
    expected = {"case_id": "empty", "decision": "insufficient_evidence", "evidence_ids": [], "policy_ids": []}
    assert check_answer(json.dumps(expected), expected)["expected_match"]
    assert not check_answer(json.dumps({**expected, "decision": "human_review"}), expected)["expected_match"]


def test_duplicate_json_keys_rejected():
    result = check_answer('{"case_id":"demo","case_id":"wrong"}', EXPECTED)
    assert not result["schema_valid"]


def test_percentiles_and_failure_denominator_are_explicit():
    assert percentile([], .95) is None
    assert percentile([10, 20, 30, 40, 50], .95) == 48
    rows = [{"case_id": "a", "phase": "warm", "wall_ms": 20, "total_duration": 1_000_000,
             "schema_valid": True, "expected_match": True, "error": None},
            {"case_id": "a", "phase": "warm", "wall_ms": 60_000, "schema_valid": False,
             "expected_match": False, "error": "transport_timeout"}]
    summary = summarize(rows)
    assert summary["calls"] == 2 and summary["passed"] == 1 and summary["warm_count"] == 2
    assert summary["warm_wall_p95_ms"] > 50_000
    assert summary["warm_server_p50_ms"] == 1

def test_busy_game_or_low_memory_cannot_be_reported_as_idle_measurement():
    busy = {"ram_available_bytes": 500_000_000,
            "gpus": [{"used_mib": 2642, "utilization_percent": 50}]}
    assert set(idle_resource_errors(busy)) == {
        "ram_headroom_below_4_gib", "gpu_background_memory_above_1024_mib",
        "gpu_background_utilization_above_10_percent"}
    idle = {"ram_available_bytes": 5 * 1024**3,
            "gpus": [{"used_mib": 300, "utilization_percent": 0}]}
    assert idle_resource_errors(idle) == []


def test_missing_resource_measurements_do_not_pass_idle_preflight():
    assert set(idle_resource_errors({})) == {
        "ram_measurement_unavailable", "gpu_measurement_unavailable"}
