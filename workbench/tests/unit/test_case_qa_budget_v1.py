"""Deterministic Step5 ledger/HTTP fixtures. Never opens a project live ledger."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from graphrisk.agent.autonomous_budget import ExecutionStop, InvestigationBudget
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash, register_budget
from extensions.case_qa_v1.budget import CAPS, HISTORY_SQL, LIMITS, VERSION, CaseQABudget
from extensions.case_qa_v1.sessions import CaseQAService, CONTEXT_RULE, SYSTEM_PROMPT
from extensions.case_qa_v1.evidence_contract import ANSWER_RULE
from test_case_qa_sessions_v1 import answer_text, case


class FixtureParent:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.seen_global_counts = []

    def _partition(self, db):
        rows = db.execute(HISTORY_SQL).fetchall()
        assert rows == self.rows, "unclassified parent history"
        # The projection may only alter history, never global SQL.
        self.seen_global_counts.append(db.execute("SELECT count(*) FROM model_attempts").fetchone()[0])
        return rows,rows[-2:],[]


class BudgetHarness:
    def value(self, case_index=0, session="1"*32):
        return {"conversation_id":session,"case":deepcopy(self.cases[case_index])}

    def request(self, message_id="a"*32, message="why", parent=None):
        return {"message_id":message_id,"parent_turn_id":parent,"evidence_version":self.cases[0]["evidence"]["evidence_version"],"message":message}

    def packet(self, value=None, request=None):
        value = deepcopy(value or self.value())
        value["turns"] = []
        return object.__new__(CaseQAService)._packet(value,request or self.request())[0]

    def reserve(self, *, case_index=0, session="1"*32, message_id="a"*32):
        value = self.value(case_index,session)
        request = self.request(message_id)
        packet = self.packet(value,request)
        return self.budget.reserve(value,request,packet),packet


def make_budget(tmp_path, *, cases=None, handler=None, historical_calls=2, historical_cost=100, deepseek_calls=None, extra_files=None):
    """Reusable offline helper for complete API/UI integration tests."""
    h = BudgetHarness()
    h.cases = deepcopy(cases or [case(),case("synthetic-2")])
    h.calls = []
    h.handler = handler
    h.body = {"model":"deepseek-flash","system_fingerprint":"fixture-v1",
        "usage":{"prompt_tokens":100,"completion_tokens":10,"total_tokens":110,"prompt_cache_hit_tokens":0,"prompt_cache_miss_tokens":100},
        "choices":[{"finish_reason":"stop","message":{"role":"assistant","content":answer_text("fixture response",h.cases[0]["evidence"])}}]}
    h.default_answer = h.body["choices"][0]["message"]["content"]
    def dispatch(request):
        payload = json.loads(request.content)
        h.calls.append(payload)
        if h.handler:
            return h.handler(request)
        body = deepcopy(h.body)
        if body.get("choices") and body["choices"][0].get("message",{}).get("content") == h.default_answer:
            evidence = json.loads(payload["messages"][-1]["content"])["evidence"]
            body["choices"][0]["message"]["content"] = answer_text("fixture response",evidence)
        return httpx.Response(200,json=body)
    h.transport = httpx.MockTransport(dispatch)
    h.contract = {"experiment_id":"case-qa-fixture","budget":{"total_attempts":1000,"deepseek_attempts":600,"local_attempts":400,"hard_limit_cny":100,"pause_estimate_cny":80}}
    h.path = tmp_path/"shared-fixture.sqlite3"
    register_budget(h.path,h.contract)
    InvestigationBudget(h.path,h.contract,mode="fixture")
    identity = canonical(["deepseek-flash","fixture-v1"])
    with sqlite3.connect(h.path) as db:
        db.execute("INSERT INTO provider_identity VALUES ('deepseek',?)",(identity,))
        for i in range(historical_calls):
            attempt = f"old-{i:06d}"
            provider = "deepseek" if deepseek_calls is None or i < deepseek_calls else "local"
            # Parent-specific old unknown hold is retained exactly, not forgiven.
            amount = historical_cost if provider == "deepseek" else 0
            state = "unknown" if i == historical_calls-2 else "settled"
            db.execute("INSERT INTO model_attempts VALUES (?,?,?,?)",(attempt,provider,state,amount))
            db.execute("INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES (?,?,?,?,?)",(attempt,attempt,"old-phase",digest(attempt),amount))
        old = db.execute(HISTORY_SQL).fetchall()
    h.parent = FixtureParent(old)
    parent_file = tmp_path/"parent-fixture.json"
    parent_file.write_text(canonical({"kind":"synthetic parent history","sha256":digest(old)}),encoding="utf-8")
    source = Path(__file__).parents[2]/"extensions/case_qa_v1/budget.py"
    h.config = {"version":VERSION,"purpose":"case_qa","fixture":True,"campaign":"case-qa-offline-test",
        "ledger_path":str(h.path),"base_contract_sha256":digest(h.contract),"parent_bindings":{str(parent_file):file_hash(parent_file)},
        "profile":{"pricing_scope":"fixture_only_not_current_quote","max_output_tokens":1024,"timeout_seconds":2,
                   "deepseek":{"endpoint":"https://api.deepseek.com/chat/completions","model":"deepseek-flash","documented_revision":"deepseek-flash",
                     "context_limit":131072,"peak_cny_per_million":{"cache_hit":"0.02","cache_miss":"0.2","output":"0.4"}}},
        "expected_identity":identity,"system_prompt_sha256":hashlib.sha256((SYSTEM_PROMPT+CONTEXT_RULE+ANSWER_RULE).encode()).hexdigest(),
        "caps":deepcopy(CAPS),"limits":deepcopy(LIMITS),"files":{str(source.resolve()):file_hash(source)},
        "cases":[{"case_alias":c["case_alias"],"binding":c["binding"],"batch_revision":c["batch_revision"],
                  "evidence_version":c["evidence"]["evidence_version"],"view_sha256":c["evidence"]["view_sha256"]} for c in h.cases]}
    h.config["files"].update(extra_files or {})
    h.config_path = tmp_path/"case-qa-fixture.json"
    h.config_path.write_text(canonical(h.config),encoding="utf-8")
    h.budget = CaseQABudget(h.path,h.contract,h.config_path,parent_protocol=h.parent,transport=h.transport)
    return h


def expect(code, fn):
    with pytest.raises(ExecutionStop) as caught:
        fn()
    assert str(caught.value) == code


def test_reservation_shared_rows_usage_settlement_and_receipt(tmp_path):
    h = make_budget(tmp_path)
    original = h.path.read_bytes()
    view = h.budget.usage(h.value())
    assert h.path.read_bytes() == original
    assert view["batch_remaining_calls"] == 6 and view["session_remaining_calls"] == 3
    assert view["authorization"] == "fixture_only_not_outbound" and view["real_deepseek_attempts"] == 0
    ticket,packet = h.reserve()
    assert ticket["transport_attempts"] == 0 and ticket["state"] == "reserved" and ticket["hold_micro_cny"] > 0
    held = h.budget.usage(h.value())
    assert held["block_reason"] == "ledger_unknown_paused" and held["mock_attempts_confirmed"] == 0
    assert held["cost_estimate_cny"] == "0.000000" and float(held["unsettled_hold_cny"]) > 0
    response = h.budget.generate(ticket,packet)
    assert response["error_code"] is None and response["audit"]["state"] == "settled"
    assert response["audit"]["transport_attempts"] == 1 and response["audit"]["hold_micro_cny"] == 0
    assert response["raw"] == h.body["choices"][0]["message"]["content"]
    assert Path(response["audit"]["receipt_path"]).is_file()
    assert h.budget.usage(h.value())["cost_estimate_cny"] == "0.000024"
    assert h.parent.seen_global_counts[-1] == 3 and len(h.calls) == 1
    replay = h.budget.generate(ticket,packet)
    assert replay["audit"]["transport_attempts"] == 0 and len(h.calls) == 1
    assert h.budget.usage(h.value())["mock_attempts_confirmed"] == 1


def test_duplicate_claim_and_cross_session_case_cap(tmp_path):
    h = make_budget(tmp_path)
    first = None
    for n in range(3):
        ticket,packet = h.reserve(session=f"{n+1:032x}",message_id=f"{n+10:032x}")
        first = first or (ticket,packet)
        h.budget.generate(ticket,packet)
    expect("quota_exhausted",lambda:h.reserve(session="f"*32))
    assert len(h.calls) == 3
    ticket,packet = first
    value = h.value(session=f"{1:032x}")
    request = h.request(message_id=f"{10:032x}")
    expect("qa_request_already_reserved",lambda:h.budget.reserve(value,request,packet))


def test_batch_cap_does_not_borrow_report_grant(tmp_path):
    h = make_budget(tmp_path)
    for case_index in (0,1):
        for n in range(3):
            h.body["choices"][0]["message"]["content"] = answer_text("fixture response",h.cases[case_index]["evidence"])
            ticket,packet = h.reserve(case_index=case_index,session=f"{case_index+1:032x}",message_id=f"{n+1:032x}")
            h.budget.generate(ticket,packet)
    state = h.budget.usage(h.value())
    assert state["batch_remaining_calls"] == 0 and state["protected_web_calls"] == 4 and state["protected_route9_calls"] == 200
    assert len(h.calls) == 6


@pytest.mark.parametrize("failure",["timeout","missing_usage","malformed_usage_details"])
def test_unknown_reserves_full_hold_and_raw_if_returned(tmp_path,failure):
    h = make_budget(tmp_path)
    if failure == "timeout":
        def failed(request):
            raise httpx.ReadTimeout("fixture timeout")
        h.handler = failed
    elif failure == "missing_usage":
        h.body["usage"] = {}
    else:
        h.body["usage"]["completion_tokens_details"] = None
    ticket,packet = h.reserve()
    response = h.budget.generate(ticket,packet)
    assert response["error_code"] in {"qa_transport_unknown","qa_usage_unknown"}
    assert response["audit"]["state"] == "unknown" and response["audit"]["hold_micro_cny"] == ticket["reserved_micro_cny"]
    assert response["raw"] is None if failure == "timeout" else response["raw"] == h.body["choices"][0]["message"]["content"]
    assert h.budget.usage(h.value())["block_reason"] == "ledger_unknown_paused"
    assert h.budget.usage(h.value())["mock_attempts_confirmed"] == 1
    assert h.budget.usage(h.value(case_index=1,session="2"*32))["unresolved_slots"] == 0
    assert h.budget.usage(h.value(case_index=1,session="2"*32))["batch_unresolved_slots"] == 1
    expect("ledger_unknown_paused",lambda:h.reserve(case_index=1,session="2"*32))
    h.budget.generate(ticket,packet)
    assert len(h.calls) == 1


@pytest.mark.parametrize("change",["identity","reasoning_text","reasoning_tokens","total_context"])
def test_identity_reasoning_context_integrity_is_retained(tmp_path,change):
    h = make_budget(tmp_path)
    if change == "identity":
        h.body["system_fingerprint"] = "unexpected"
    elif change == "reasoning_text":
        h.body["choices"][0]["message"]["reasoning_content"] = "unexpected hidden analysis"
    elif change == "reasoning_tokens":
        h.body["usage"]["completion_tokens_details"] = {"reasoning_tokens":1}
    else:
        h.body["usage"].update(prompt_tokens=131072,prompt_cache_miss_tokens=131072,total_tokens=131082)
    ticket,packet = h.reserve()
    response = h.budget.generate(ticket,packet)
    assert response["audit"]["state"] == "settled" and response["audit"]["integrity_stop"]
    assert response["error_code"] == "budget_integrity_stop" and response["raw"] is not None
    assert h.budget.usage(h.value())["block_reason"] == "budget_integrity_stop"


def test_concurrent_reserve_has_one_atomic_slot(tmp_path):
    h = make_budget(tmp_path)
    def claim(n):
        try:
            return h.reserve(case_index=n,session=f"{n+1:032x}")[0]
        except ExecutionStop as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim,(0,1)))
    assert sum(type(r) is dict for r in results) == 1 and "ledger_unknown_paused" in results
    assert not h.calls


@pytest.mark.parametrize("changed",["config","source","parent","scope","identity"])
def test_drift_blocks_before_send(tmp_path,changed):
    extra = tmp_path/"extra.py"
    extra.write_text("x=1")
    h = make_budget(tmp_path,extra_files={str(extra):file_hash(extra)})
    if changed == "config":
        h.config_path.write_text(h.config_path.read_text()+" ")
        code = "qa_config_changed"
    elif changed == "source":
        extra.write_text("x=2")
        code = "qa_bound_file_changed"
    elif changed == "parent":
        Path(next(iter(h.config["parent_bindings"]))).write_text("changed")
        code = "qa_parent_binding_changed"
    elif changed == "scope":
        h.cases[0]["binding"] = "f"*64
        code = "case_qa_not_authorized"
    else:
        with sqlite3.connect(h.path) as db:
            db.execute("UPDATE provider_identity SET identity='changed'")
        code = "budget_integrity_stop"
    expect(code,h.reserve)
    assert not h.calls


def test_live_mode_or_real_transport_is_rejected_without_modifying_database(tmp_path):
    h = make_budget(tmp_path)
    before = h.path.read_bytes()
    with httpx.HTTPTransport() as transport:
        expect("qa_fixture_only",lambda:CaseQABudget(h.path,h.contract,h.config_path,parent_protocol=h.parent,transport=transport))
    assert before == h.path.read_bytes()
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE execution_settings SET mode='live'")
    before = h.path.read_bytes()
    expect("qa_fixture_only",lambda:CaseQABudget(h.path,h.contract,h.config_path,parent_protocol=h.parent,transport=h.transport))
    assert before == h.path.read_bytes()


def test_pause_line_and_protected_calls(tmp_path):
    h = make_budget(tmp_path,historical_cost=40_000_000)
    expect("budget_exhausted",h.reserve)
    assert not h.calls
    other = tmp_path/"protected"
    other.mkdir()
    h = make_budget(other,historical_calls=1818,deepseek_calls=1768)
    expect("global_quota_protected",h.reserve)
    assert not h.calls


def test_unclassified_old_rows_cannot_be_hidden_as_qa(tmp_path):
    h = make_budget(tmp_path)
    with sqlite3.connect(h.path) as db:
        db.execute("INSERT INTO model_attempts VALUES ('foreign','deepseek','settled',0)")
        db.execute("INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES ('foreign','foreign','foreign',?,0)",("f"*64,))
    with pytest.raises(AssertionError,match="unclassified parent history"):
        h.budget.usage(h.value())
    assert not h.calls


def test_crash_after_transport_marker_is_not_retried(tmp_path):
    h = make_budget(tmp_path)
    ticket,packet = h.reserve()
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE case_qa_requests_v1 SET transport_started=1")
    result = h.budget.generate(ticket,packet)
    assert result["error_code"] == "qa_saved_request_unresolved" and result["audit"]["transport_attempts"] == 0
    assert result["audit"]["state"] == "reserved" and not h.calls
    assert h.budget.usage(h.value())["mock_attempts_confirmed"] == 0



def test_exact_nested_h1_projection_keeps_qa_out_of_parent_history(tmp_path):
    h = make_budget(tmp_path)
    class NestedParent(FixtureParent):
        recovery_cfg = {"unknown":{"attempt_id":"old-000000"}}
        def _partition(self, db):
            rows,web,route9 = super()._partition(db)
            sql = HISTORY_SQL.replace(" ORDER BY a.rowid"," WHERE a.attempt_id<>? ORDER BY a.rowid")
            projected = db.execute(sql,("old-000000",)).fetchall()
            assert projected == [r for r in self.rows if r[0] != "old-000000"]
            return rows,web,route9
    h.budget.parent_protocol = NestedParent(h.parent.rows)
    ticket,packet = h.reserve()
    assert h.budget.usage(h.value())["global_calls"] == 3
    response = h.budget.generate(ticket,packet)
    assert response["error_code"] is None
    assert h.budget.parent_protocol.seen_global_counts[-1] == 3



@pytest.mark.parametrize("field",["integrity_stop","transport_started","request_parent"])
def test_changed_qa_integrity_markers_fail_readonly_verification(tmp_path,field):
    h = make_budget(tmp_path)
    h.body["system_fingerprint"] = "unexpected"
    ticket,packet = h.reserve()
    response = h.budget.generate(ticket,packet)
    assert response["audit"]["integrity_stop"]
    with sqlite3.connect(h.path) as db:
        if field == "integrity_stop":
            db.execute("UPDATE execution_requests SET integrity_stop=0 WHERE attempt_id=?",(ticket["attempt_id"],))
            expected = "qa_accounting_changed"
        elif field == "transport_started":
            db.execute("UPDATE case_qa_requests_v1 SET transport_started=0")
            expected = "qa_request_binding_changed"
        else:
            request = h.request()
            request["parent_turn_id"] = "invalid-parent"
            db.execute("UPDATE case_qa_requests_v1 SET request_json=?",(canonical(request),))
            expected = "qa_request_binding_changed"
    before = h.path.read_bytes()
    expect(expected,lambda:h.budget.usage(h.value()))
    assert h.path.read_bytes() == before and len(h.calls) == 1
