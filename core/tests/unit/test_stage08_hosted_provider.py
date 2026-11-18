"""Meaningful protocol, input isolation and persistent budget fault tests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from graphrisk.agent.hosted_provider import (
    ProviderFailure, QwenEvaluationConfig, QwenReportProvider, ReviewedCase,
)
from graphrisk.agent.provider_budget import BudgetStop, EvaluationBudget
from graphrisk.agent.reporting import ReportDraft

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "https://route1-fixture.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
KEY = "route1-fake-secret-never-log"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", KEY)
    raw = (ROOT / "data/evaluation/stage08/provider_contract_smoke.json").read_bytes()
    case = ReviewedCase.load(ROOT / "data/evaluation/stage08/provider_contract_smoke.json",
                             hashlib.sha256(raw).hexdigest())
    budget = EvaluationBudget(tmp_path / "budget.sqlite3")
    config = QwenEvaluationConfig(endpoint=ENDPOINT, experiment_id="unit")
    draft = {"transaction_id": case.transaction_id, "dataset_source": "synthetic",
             "evidence_ids": list(case.evidence_ids), "policy_choices": [{"chunk_id": case.policy_chunk_ids[0]}],
             "uncertainty": case.uncertainty, "suggested_action": "human_review"}
    body = {"model": config.model_id, "choices": [{"finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(draft)}}],
            "usage": {"prompt_tokens": 300, "completion_tokens": 80, "total_tokens": 380}}
    calls = []

    def build(handler=None, **options):
        def transport(request):
            calls.append(request)
            return handler(request) if handler else httpx.Response(200, json=body)
        return QwenReportProvider(config, case, budget, transport=httpx.MockTransport(transport), **options)

    return case, config, budget, draft, body, calls, build


def run(rig, provider=None):
    case, _, _, _, _, _, build = rig
    return (provider or build()).generate(case.prompt, ReportDraft.model_json_schema())


def test_request_pins_snapshot_schema_and_authentication(rig):
    result = run(rig)
    _, config, budget, _, _, calls, _ = rig
    assert result.model_id == result.model_revision == config.model_id
    assert result.transport_kind == "fake_http" and result.finish_reason == "stop"
    assert result.estimated_cost_cny == pytest.approx(0.0004)
    request = calls[0]
    payload = json.loads(request.content)
    assert request.headers["Authorization"] == "Bearer " + KEY
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["enable_thinking"] is False and payload["stream"] is False
    assert payload["max_tokens"] == 512 and payload["temperature"] == 0
    assert "JSON" in payload["messages"][0]["content"]
    assert "policy_choices" in payload["messages"][0]["content"]
    assert budget.snapshot() == {"accounted_micro_cny": 400, "pending_attempts": 0}
    assert KEY not in result.model_dump_json()
    assert rig[0].prompt not in result.model_dump_json()


@pytest.mark.parametrize("endpoint", [
    "http://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
    ENDPOINT + "?key=secret", ENDPOINT + "#fragment", ENDPOINT + "/",
    ENDPOINT.replace("aliyuncs.com", "aliyuncs.com.attacker.example"),
    ENDPOINT.replace("route1-fixture", "{WorkspaceId}"),
    ENDPOINT.replace("route1-fixture", "user:password@route1-fixture"),
    ENDPOINT.replace("cn-beijing", "ap-southeast-1"),
    "https://127.0.0.1/compatible-mode/v1/chat/completions",
])
def test_endpoint_rejects_redirect_targets_credentials_and_templates(endpoint):
    with pytest.raises(ValidationError):
        QwenEvaluationConfig(endpoint=endpoint, experiment_id="unit")


@pytest.mark.parametrize("change", [
    {"model_id": "qwen-plus"}, {"max_output_tokens": 513}, {"max_input_tokens": 4097},
    {"timeout_seconds": float("nan")}, {"api_key": "not-allowed"}, {"enabled": True},
])
def test_config_cannot_unpin_or_enable_live(change):
    with pytest.raises(ValidationError):
        QwenEvaluationConfig(endpoint=ENDPOINT, experiment_id="unit", **change)


def test_live_transport_is_hard_disabled(rig):
    case, config, budget, *_ = rig
    with pytest.raises(ProviderFailure, match="live_transport_disabled"):
        QwenReportProvider(config, case, budget)


def test_unreviewed_prompt_or_schema_never_reaches_transport(rig):
    case, _, budget, _, _, calls, build = rig
    for prompt, schema in [(case.prompt + " extra", ReportDraft.model_json_schema()), (case.prompt, {})]:
        with pytest.raises(ProviderFailure, match="unreviewed_input"):
            build().generate(prompt, schema)
    assert not calls and budget.snapshot()["accounted_micro_cny"] == 0


def test_changed_case_file_requires_reviewed_hash(rig, tmp_path):
    path = tmp_path / "case.json"
    path.write_text(rig[0].model_dump_json() + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed_case_invalid"):
        ReviewedCase.load(path, "0" * 64)


def test_missing_key_and_expired_deadline_do_not_reserve(rig, monkeypatch):
    _, _, budget, _, _, calls, build = rig
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    with pytest.raises(ProviderFailure, match="authentication_unconfigured"):
        run(rig)
    monkeypatch.setenv("DASHSCOPE_API_KEY", KEY)
    with pytest.raises(ProviderFailure, match="deadline_exceeded"):
        run(rig, build().with_deadline(lambda: 0))
    assert not calls and budget.snapshot()["accounted_micro_cny"] == 0


@pytest.mark.parametrize("kind,expected", [
    ("length", "output_truncated"), ("model", "model_revision_mismatch"),
    ("bad_json", "invalid_output_schema"), ("extra_field", "invalid_output_schema"),
    ("wrong_type", "invalid_output_schema"), ("duplicate_key", "invalid_output_schema"),
    ("citation", "output_evidence_mismatch"), ("transaction", "output_evidence_mismatch"),
    ("source", "output_evidence_mismatch"), ("evidence", "output_evidence_mismatch"),
    ("duplicate_evidence", "output_evidence_mismatch"), ("uncertainty", "output_evidence_mismatch"),
    ("action", "invalid_output_schema"), ("tool_call", "invalid_response"),
])
def test_invalid_outputs_are_rejected_but_known_usage_is_charged(rig, kind, expected):
    _, _, budget, draft, body, calls, _ = rig
    choice = body["choices"][0]
    if kind == "length":
        choice["finish_reason"] = "length"
    elif kind == "model":
        body["model"] = "qwen-plus"
    elif kind == "tool_call":
        choice["message"]["tool_calls"] = [{"function": {"name": "freeze_account"}}]
    elif kind == "bad_json":
        choice["message"]["content"] = "not JSON " + KEY
    elif kind == "duplicate_key":
        choice["message"]["content"] = '{"transaction_id":"a","transaction_id":"b"}'
    else:
        if kind == "extra_field": draft["untrusted"] = KEY
        if kind == "wrong_type": draft["evidence_ids"] = 7
        if kind == "citation": draft["policy_choices"] = [{"chunk_id": "invented"}]
        if kind == "transaction": draft["transaction_id"] = "another"
        if kind == "source": draft["dataset_source"] = "ieee_cis"
        if kind == "evidence": draft["evidence_ids"] = ["invented"]
        if kind == "duplicate_evidence": draft["evidence_ids"] *= 2
        if kind == "uncertainty": draft["uncertainty"] = "partial_evidence"
        if kind == "action": draft["suggested_action"] = "freeze_account"
        choice["message"]["content"] = json.dumps(draft)
    with pytest.raises(ProviderFailure) as caught:
        run(rig)
    assert caught.value.audit.code == expected
    assert caught.value.audit.cost_state == "settled"
    assert budget.snapshot()["accounted_micro_cny"] == 400
    assert len(calls) == 1  # no automatic correction/retry
    assert KEY not in str(caught.value) + repr(asdict(caught.value.audit))


@pytest.mark.parametrize("status,expected", [
    (401, "authentication_failed"), (403, "authentication_failed"), (429, "rate_limited"),
    (500, "upstream_error"), (400, "request_rejected"), (302, "redirect_rejected"),
])
def test_http_errors_never_echo_bodies_or_follow_redirects(rig, status, expected):
    _, _, budget, _, _, calls, build = rig
    provider = build(lambda request: httpx.Response(status, text=KEY + rig[0].prompt,
                     headers={"Location": "https://attacker.example"}))
    with pytest.raises(ProviderFailure) as caught:
        run(rig, provider)
    assert caught.value.audit.code == expected
    assert len(calls) == 1 and budget.snapshot()["pending_attempts"] == 1
    assert KEY not in repr(asdict(caught.value.audit))


@pytest.mark.parametrize("exception,expected", [
    (httpx.ReadTimeout, "transport_timeout"), (httpx.ConnectError, "transport_unavailable"),
    (httpx.RemoteProtocolError, "transport_unavailable"), (httpx.DecodingError, "transport_unavailable"),
])
def test_uncertain_transport_cost_survives_restart_and_blocks_calls(rig, exception, expected):
    case, config, budget, _, _, calls, build = rig
    def failure(request):
        raise exception(KEY + case.prompt)
    with pytest.raises(ProviderFailure) as caught:
        run(rig, build(failure))
    assert caught.value.audit.code == expected and caught.value.audit.estimated_cost_cny is None
    fresh_budget = EvaluationBudget(budget.path)
    provider = QwenReportProvider(config, case, fresh_budget,
        transport=httpx.MockTransport(lambda request: pytest.fail("must not send")))
    with pytest.raises(ProviderFailure, match="budget_unreconciled"):
        run(rig, provider)
    assert fresh_budget.snapshot() == {"accounted_micro_cny": 100000, "pending_attempts": 1}
    assert len(calls) == 1


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": True, "completion_tokens": 1, "total_tokens": 2},
    {"prompt_tokens": -1, "completion_tokens": 1, "total_tokens": 0},
    {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 99}])
def test_missing_or_invalid_usage_keeps_reservation(rig, usage):
    rig[4]["usage"] = usage
    with pytest.raises(ProviderFailure, match="invalid_response_or_usage"):
        run(rig)
    assert rig[2].snapshot()["pending_attempts"] == 1


def test_token_overrun_is_charged_and_stops_future_calls(rig):
    rig[4]["usage"] = {"prompt_tokens": 300, "completion_tokens": 513, "total_tokens": 813}
    with pytest.raises(ProviderFailure, match="token_limit_exceeded"):
        run(rig)
    assert rig[2].snapshot()["accounted_micro_cny"] == 1266
    with pytest.raises(ProviderFailure, match="budget_unreconciled"):
        run(rig)
    assert len(rig[5]) == 1


def test_late_success_is_not_returned_as_success(rig):
    now = [0.0]
    def handler(request):
        now[0] = 61.0
        return httpx.Response(200, json=rig[4])
    with pytest.raises(ProviderFailure, match="deadline_exceeded"):
        run(rig, rig[6](handler, monotonic=lambda: now[0]))
    assert rig[2].snapshot()["accounted_micro_cny"] == 400


def test_persistent_budget_cannot_reset_by_recreating_provider(rig):
    run(rig)
    run(rig)
    with pytest.raises(ProviderFailure, match="attempt_limit"):
        run(rig)
    assert len(rig[5]) == 2
    with pytest.raises(BudgetStop, match="budget_settings_changed"):
        EvaluationBudget(rig[2].path, stop_cny=1)


def test_budget_stop_at_boundary_without_extra_reservation(tmp_path):
    path = tmp_path / "limits.sqlite3"
    budget = EvaluationBudget(path, stop_cny=1, experiment_cny=1)
    for i in range(10):
        attempt = budget.reserve("exp", str(i))
        budget.settle(attempt, 100000)
    with pytest.raises(BudgetStop, match="budget_exhausted"):
        EvaluationBudget(path, stop_cny=1, experiment_cny=1).reserve("other", "case")
    assert budget.snapshot()["accounted_micro_cny"] == 1000000


def test_concurrent_reservations_are_serialized(tmp_path):
    budget = EvaluationBudget(tmp_path / "concurrent.sqlite3")
    def reserve(i):
        try:
            return EvaluationBudget(budget.path).reserve("exp", str(i))
        except BudgetStop:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, [1, 2]))
    assert sum(item is not None for item in results) == 1
    assert budget.snapshot()["pending_attempts"] == 1