from __future__ import annotations
from copy import deepcopy
import json
import httpx
import pytest
from graphrisk.agent import provider_comparison as suite
from graphrisk.agent.comparison_providers import EXPERIMENT, MODEL, ComparisonFailure, hosted_attempt, local_attempt
from graphrisk.agent.hosted_provider import QwenEvaluationConfig
from graphrisk.agent.local_probe import LocalModelSpec
from graphrisk.agent.provider_budget import EvaluationBudget, BudgetStop

KEY = "test-stage08-private-key-not-real"
CONFIG = QwenEvaluationConfig(endpoint="https://route3-fixture.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions", experiment_id=EXPERIMENT)

@pytest.fixture
def case():
    return suite.load_suite()[0]

def test_frozen_cases_provenance_groups_and_gold_are_not_sent():
    cases = suite.load_suite()
    assert len(cases) == 20
    for case in cases:
        assert suite.score(json.dumps(case["expected"]), case)["passed"]
        payload = json.loads(suite.messages(case)[1]["content"])
        assert set(payload) == {"case_id", "as_of", "target_entity", "evidence", "policies"}
        assert "rationale" not in payload and "expected" not in payload

def test_edited_suite_and_prompt_fail_before_provider(tmp_path, monkeypatch):
    path = tmp_path / "changed.json"
    path.write_bytes(suite.SUITE_PATH.read_bytes() + b" ")
    monkeypatch.setattr(suite, "SUITE_PATH", path)
    with pytest.raises(ValueError, match="hash_mismatch"):
        suite.load_suite()

@pytest.mark.parametrize("change", [
    {"case_id":"other"}, {"decision":"no_review"}, {"finding":"no_trigger"},
    {"evidence_ids":["E999"]}, {"policy_ids":["P999"]}, {"evidence_ids":["E01","E01"]},
    {"action":"freeze_account"}, {"evidence_ids":"E01"},
])
def test_mutations_cannot_pass_scorer(case, change):
    assert not suite.score(json.dumps({**case["expected"], **change}), case)["passed"]

def test_stale_policy_and_wrong_entity_are_explicitly_invalid():
    stale = suite.load_suite()[14]
    answer = {**stale["expected"],"policy_ids":["P15-expired"]}
    assert suite.score(json.dumps(answer),stale)["citation_invalid"]
    wrong = suite.load_suite()[17]
    answer = {**wrong["expected"],"evidence_ids":["E18"]}
    assert suite.score(json.dumps(answer),wrong)["citation_invalid"]

def test_duplicate_json_keys_and_missing_outputs_fail(case):
    assert not suite.score('{"case_id":"x","case_id":"y"}',case)["json_valid"]
    assert not suite.score(None,case)["passed"]

def rig(tmp_path, monkeypatch, case, *, mode="ok", alter=None):
    monkeypatch.setenv("DASHSCOPE_API_KEY",KEY)
    budget = EvaluationBudget(tmp_path / "budget.sqlite3")
    calls = []
    def handler(request):
        calls.append(request)
        if mode == "redirect":
            return httpx.Response(302,headers={"Location":"https://attacker.invalid"},text=KEY)
        if request.url.path.endswith("/tokenizer"):
            if mode == "count_timeout":
                raise httpx.ReadTimeout(KEY)
            return httpx.Response(200,json={"usage":{"input_tokens":5000 if mode=="count_overflow" else 250}})
        if mode == "timeout":
            raise httpx.ReadTimeout(KEY)
        body = {"model":MODEL,"usage":{"prompt_tokens":240,"completion_tokens":50,"total_tokens":290},
                "choices":[{"finish_reason":"stop","message":{"role":"assistant","content":json.dumps(case["expected"])}}]}
        if alter:
            alter(body)
        return httpx.Response(200,json=body)
    return budget, calls, httpx.MockTransport(handler)

def test_same_reviewed_messages_counter_before_generation_and_accounted_cost(tmp_path, monkeypatch, case):
    budget,calls,transport=rig(tmp_path,monkeypatch,case)
    row=hosted_attempt(case,CONFIG,budget,transport=transport)
    assert row["score"]["passed"] and row["error"] is None
    assert len(calls)==2
    counted=json.loads(calls[0].content); generated=json.loads(calls[1].content)
    assert counted["input"]["messages"] == generated["messages"] == suite.messages(case)
    assert counted["model"]==generated["model"]==MODEL
    assert generated["response_format"]=={"type":"json_object"} and generated["enable_search"] is False
    assert row["provider"]=="qwen_fake_http"
    assert budget.snapshot()["accounted_micro_cny"]==100292
    assert row["estimated_generation_cost_cny"]==0.000292
    assert KEY not in json.dumps(row)

def test_arbitrary_source_tag_does_not_allow_private_input(tmp_path, monkeypatch, case):
    budget,calls,transport=rig(tmp_path,monkeypatch,case)
    changed=deepcopy(case); changed["evidence"][0]["note"]="private transaction data"
    with pytest.raises(ValueError,match="unreviewed"):
        hosted_attempt(changed,CONFIG,budget,transport=transport)
    assert not calls and budget.snapshot()["accounted_micro_cny"]==0

def test_live_requires_two_explicit_switches_before_network(tmp_path, monkeypatch, case):
    monkeypatch.delenv("GRAPHRISK_STAGE08_LIVE",raising=False)
    with pytest.raises(ComparisonFailure,match="live_opt_in"):
        hosted_attempt(case,CONFIG,EvaluationBudget(tmp_path/"ledger"),live=True)

@pytest.mark.parametrize("mode,expected,generation_calls",[
    ("count_timeout","transport_timeout",0), ("count_overflow","unverified_or_excessive_input_tokens",0),
    ("timeout","transport_timeout",1),("redirect","redirect_rejected",0)])
def test_uncertain_cost_blocks_followup_and_survives_restart(tmp_path,monkeypatch,case,mode,expected,generation_calls):
    budget,calls,transport=rig(tmp_path,monkeypatch,case,mode=mode)
    row=hosted_attempt(case,CONFIG,budget,transport=transport)
    assert row["error"]==expected
    assert sum(r.url.path.endswith("/chat/completions") for r in calls)==generation_calls
    assert budget.snapshot()["pending_attempts"]==1
    count=len(calls)
    again=hosted_attempt(case,CONFIG,EvaluationBudget(budget.path),transport=transport)
    assert again["error"]=="budget_unreconciled" and len(calls)==count
    assert KEY not in json.dumps(row)

@pytest.mark.parametrize("kind,error",[
    ("revision","model_revision_mismatch"),("truncated","output_truncated_or_rejected"),
    ("usage","invalid_usage"),("counter","token_limit_or_counter_mismatch"),
    ("secret","credential_reflected"),("tool","unexpected_reasoning_or_tools"),
])
def test_invalid_provider_response_never_becomes_success(tmp_path,monkeypatch,case,kind,error):
    def alter(body):
        if kind=="revision": body["model"]="qwen-plus"
        if kind=="truncated": body["choices"][0]["finish_reason"]="length"
        if kind=="usage": body["usage"]["prompt_tokens"]=True
        if kind=="counter": body["usage"]={"prompt_tokens":251,"completion_tokens":50,"total_tokens":301}
        if kind=="secret": body["choices"][0]["message"]["content"]=KEY
        if kind=="tool": body["choices"][0]["message"]["tool_calls"]=[{"name":"freeze"}]
    budget,_,transport=rig(tmp_path,monkeypatch,case,alter=alter)
    row=hosted_attempt(case,CONFIG,budget,transport=transport)
    assert row["error"]==error and not row["score"]["passed"]
    assert KEY not in json.dumps(row)

def test_unknown_bill_reconciliation_is_persistent_idempotent_and_audited(tmp_path):
    budget=EvaluationBudget(tmp_path/"ledger")
    attempt=budget.reserve(EXPERIMENT,"x")
    budget.reconcile(attempt,1234,"a"*64)
    assert EvaluationBudget(budget.path).snapshot()=={"accounted_micro_cny":1234,"pending_attempts":0}
    budget.reconcile(attempt,1234,"a"*64)
    with pytest.raises(BudgetStop,match="conflict"):
        budget.reconcile(attempt,0,"b"*64)
    assert budget.audit_rows()[0]["accounted_micro_cny"]==1234

def test_bill_cannot_clear_a_token_or_cost_violation(tmp_path):
    budget=EvaluationBudget(tmp_path/"ledger")
    attempt=budget.reserve(EXPERIMENT,"x")
    budget.settle(attempt,10,violation=True)
    budget.reconcile(attempt,0,"a"*64)
    with pytest.raises(BudgetStop,match="unreconciled"):
        budget.reserve(EXPERIMENT,"y")

@pytest.mark.parametrize("fault", [None, "truncated", "malformed_message", "wrong_role"])
def test_local_chat_uses_exact_same_messages_and_keeps_digest(case, fault):
    spec=LocalModelSpec(model="qwen3:4b",digest="a"*64)
    payloads=[]
    def handler(request):
        if request.url.path=="/api/tags":
            return httpx.Response(200,json={"models":[{"name":spec.model,"digest":spec.digest}]})
        if request.url.path=="/api/ps":
            return httpx.Response(200,json={"models":[{"name":spec.model,"digest":spec.digest,"context_length":4096}]})
        payloads.append(json.loads(request.content))
        body = {"model":spec.model,"done":True,"done_reason":"stop",
            "message":{"role":"assistant","content":json.dumps(case["expected"])},
            "prompt_eval_count":300,"eval_count":50,"load_duration":1,
            "prompt_eval_duration":2,"eval_duration":3,"total_duration":6}
        if fault == "truncated": body["done_reason"] = "length"
        if fault == "malformed_message": body["message"] = None
        if fault == "wrong_role": body["message"]["role"] = "user"
        return httpx.Response(200,json=body)
    row=local_attempt(case,spec,transport=httpx.MockTransport(handler))
    if fault is None:
        assert row["error"] is None and row["score"]["passed"]
    else:
        assert row["error"] == ("output_truncated" if fault == "truncated" else "invalid_output")
        assert not row["score"]["passed"]
        if fault == "truncated": assert row["raw_response"] is not None
    assert payloads[0]["messages"]==suite.messages(case)
    assert payloads[0]["format"]=="json" and payloads[0]["options"]["num_predict"]==512

def test_failed_and_unrun_cases_are_not_silently_removed(case):
    row={"attempted":True,"error":"transport_timeout","generation_wall_ms":60000,
         "score":suite.score(None,case),"group":case["group"]}
    result=suite.aggregate([row],suite.load_suite())
    assert result["planned"]==20 and result["attempted"]==1 and result["not_run"]==19
    assert result["passed"]==0 and result["generation_wall_p95_ms"]==60000


def test_only_case_output_failure_allows_next_independent_case():
    from graphrisk.agent.comparison_providers import stop_after
    for error in (None, "output_truncated", "output_truncated_or_rejected"):
        assert not stop_after({"error": error})
    for error in ("transport_timeout", "model_revision_mismatch", "token_limit_exceeded", "budget_unreconciled"):
        assert stop_after({"error": error})
