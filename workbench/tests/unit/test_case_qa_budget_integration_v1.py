"""Step5 complete in-process UI/API/session/shared-schema budget checks."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
import json
import sqlite3

import httpx
import pytest

from extensions.case_qa_v1.budget import CaseQABudget
from extensions.case_qa_v1.sessions import SessionError
from test_case_qa_sessions_v1 import Harness, expect
from test_case_qa_budget_v1 import make_budget
from test_case_qa_workbench_v1 import page, ask, all_text


def integrated(tmp_path):
    qa = Harness(tmp_path/"conversations.sqlite3")
    billing = make_budget(tmp_path/"billing", cases=list(qa.cases.values()))
    qa.service.budget = billing.budget
    return qa,billing


def restart(qa,billing):
    qa.restart()
    billing.budget = CaseQABudget(billing.budget.path,billing.contract,billing.config_path,
        parent_protocol=billing.parent,transport=billing.budget.transport)
    qa.service.budget = billing.budget


def test_two_cases_three_turns_ui_to_ledger_and_case_quota_cannot_reset(tmp_path):
    qa,b=integrated(tmp_path)
    one=qa.create()
    at,record=page(qa,one)
    for question in ["为什么需要关注？","这个关联是什么意思？","有没有正常解释？"]:
        ask(at,question)
    assert at.chat_input[0].disabled
    assert "本案跨会话剩余0次" in all_text(at)
    assert record.count("POST","/turns")==len(b.calls)==3 and not qa.calls
    same_case=qa.create(key="another_case_session")
    blocked=qa.service.get(same_case["conversation_id"])
    assert blocked["usage"]["session_remaining_calls"]==3
    assert blocked["usage"]["case_remaining_calls"]==0
    expect("quota_exhausted",lambda:qa.follow(same_case))
    two=qa.create("2",key="second_case")
    at2,record2=page(qa,two)
    for question in ["这笔的依据是什么？","还有什么缺失？","该如何核实？"]:
        ask(at2,question)
    assert len(b.calls)==6 and at2.chat_input[0].disabled
    assert all(t["result"]["status"]=="answered" for session in (one,two)
               for t in qa.service.get(session["conversation_id"])["turns"])
    usage=qa.service.get(two["conversation_id"])["usage"]
    assert usage["batch_remaining_calls"]==0 and usage["real_deepseek_attempts"]==0
    assert usage["protected_route9_calls"]==200
    assert usage["cost_kind"]=="usage_estimate_not_invoice"
    for payload in b.calls:
        assert payload["max_tokens"]<=2048 and payload["response_format"]=={"type":"json_object"}
    assert "全批剩余0次" in all_text(at2)


def test_completed_duplicate_and_new_websocket_only_read_saved_result(tmp_path):
    qa,b=integrated(tmp_path)
    one=qa.create()
    first,request=qa.follow(one)
    again=qa.service.follow(one["conversation_id"],request)
    assert again["replayed"] and first["turn"]==again["turn"] and len(b.calls)==1
    restart(qa,b)
    at,record=page(qa,one)
    at.query_params["qa_message"]=request["message_id"]
    at.run()
    at.button(key="qa_refresh").click().run()
    assert not at.exception and not record.count("POST","/turns") and len(b.calls)==1
    assert "qa_message" not in at.query_params
    assert qa.service.get(one["conversation_id"])["usage"]["session_remaining_calls"]==2


def test_missing_usage_keeps_raw_hold_and_restart_pauses_other_case(tmp_path):
    qa,b=integrated(tmp_path)
    b.body.pop("usage")
    one=qa.create()
    out,request=qa.follow(one)
    result=out["turn"]["result"]
    assert result["status"]=="response_unknown" and result["answer"] is None
    assert result["model_attempts"]==1
    assert qa.service.raw(one["conversation_id"],request["message_id"])["raw_response"]["complete"]
    audit=result["budget_audit"]
    assert audit["state"]=="unknown" and audit["hold_micro_cny"]==audit["reserved_micro_cny"]>0
    restart(qa,b)
    two=qa.create("2",key="unknown_other_case")
    expect("response_unknown_paused",lambda:qa.follow(two))
    assert len(b.calls)==1
    u=qa.service.get(two["conversation_id"])["usage"]
    assert u["unresolved_slots"]==0 and u["block_reason"]=="ledger_unknown_paused"
    assert float(u["unsettled_hold_cny"])>0


def test_timeout_never_automatically_retries(tmp_path):
    qa,b=integrated(tmp_path)
    def timeout(request):
        raise httpx.ReadTimeout("private provider detail")
    b.handler=timeout
    one=qa.create()
    out,request=qa.follow(one)
    assert out["turn"]["result"]["status"]=="response_unknown"
    assert len(b.calls)==1 and out["turn"]["result"]["model_attempts"]==1
    replay=qa.service.follow(one["conversation_id"],request)
    assert replay["replayed"] and len(b.calls)==1
    assert qa.service.get(one["conversation_id"])["usage"]["block_reason"]=="ledger_unknown_paused"


def test_invalid_answer_is_billed_once_but_not_published(tmp_path):
    qa,b=integrated(tmp_path)
    b.body["choices"][0]["message"]["content"]='{"bad":"answer"}'
    one=qa.create()
    out,request=qa.follow(one)
    result=out["turn"]["result"]
    assert result["status"]=="invalid_response" and result["answer"] is None
    assert result["budget_audit"]["state"]=="settled"
    assert result["budget_audit"]["cost_estimate_micro_cny"]>0
    assert result["budget_audit"]["hold_micro_cny"]==0
    assert qa.service.raw(one["conversation_id"],request["message_id"])["raw_response"]["complete"]
    assert len(b.calls)==1


def test_reservation_then_checkpoint_failure_retains_hold_and_blocks_resend(tmp_path,monkeypatch):
    qa,b=integrated(tmp_path)
    one=qa.create()
    original=qa.service.store.mutate
    def broken(identifier,fn):
        def crash(value):
            updated,result=fn(value)
            if updated["state"]=="in_flight":
                raise OSError("simulated checkpoint disk failure")
            return updated,result
        return original(identifier,crash)
    with monkeypatch.context() as patch:
        patch.setattr(qa.service.store,"mutate",broken)
        expect("checkpoint_recovery_required",lambda:qa.follow(one))
    view=qa.service.get(one["conversation_id"])
    assert not view["turns"] and not b.calls
    assert view["send_block_reason"]=="ledger_unknown_paused"
    assert view["usage"]["mock_attempts_confirmed"]==0
    assert view["usage"]["unresolved_slots"]==1
    expect("qa_request_already_reserved",lambda:qa.follow(one))
    assert not b.calls


def test_legacy_unknown_stops_before_new_budget_reservation(tmp_path):
    qa,b=integrated(tmp_path)
    qa.service.budget=None
    qa.provider=lambda packet: (_ for _ in ()).throw(TimeoutError("synthetic"))
    one=qa.create()
    qa.follow(one)
    qa.service.budget=b.budget
    two=qa.create("2",key="legacy_unknown_other")
    expect("response_unknown_paused",lambda:qa.follow(two))
    with sqlite3.connect(b.budget.path) as db:
        assert db.execute("select count(*) from case_qa_requests_v1").fetchone()[0]==0
    assert not b.calls


def test_identity_drift_is_preserved_and_stops_followup(tmp_path):
    qa,b=integrated(tmp_path)
    b.body["system_fingerprint"]="unapproved-fixture-revision"
    one=qa.create()
    out,request=qa.follow(one)
    result=out["turn"]["result"]
    assert result["status"]=="review_required" and result["answer"] is None
    assert result["budget_audit"]["state"]=="settled"
    assert qa.service.raw(one["conversation_id"],request["message_id"])["raw_response"]["complete"]
    assert qa.service.get(one["conversation_id"])["usage"]["block_reason"]=="budget_integrity_stop"
    expect("budget_integrity_stop",lambda:qa.follow(one))
    assert len(b.calls)==1


def test_concurrent_messages_claim_only_one_budget_slot(tmp_path):
    qa,b=integrated(tmp_path)
    one=qa.create()
    def send(number):
        request={"message_id":f"{number:032x}","parent_turn_id":None,
                 "evidence_version":one["evidence_version"],"message":"并发的同级问题"}
        try:
            return qa.service.follow(one["conversation_id"],request)
        except SessionError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(send,[1,2]))
    assert sum(type(result) is dict for result in results)==1
    assert len(b.calls)==1
    assert next(result for result in results if type(result) is str) in {"turn_in_progress","parent_turn_changed"}
    assert qa.service.get(one["conversation_id"])["usage"]["session_remaining_calls"]==2


def test_stop_after_claim_preserves_inflight_billing_then_disables_new_questions(tmp_path):
    qa,b=integrated(tmp_path)
    entered,release=Event(),Event()
    def delayed(request):
        entered.set()
        assert release.wait(10)
        return httpx.Response(200,json=deepcopy(b.body))
    b.handler=delayed
    one=qa.create()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(qa.follow,one)
        try:
            assert entered.wait(10)
            qa.service.control(one["conversation_id"],{"enabled":False})
        finally:
            release.set()
        out,request=pending.result(timeout=10)
    assert out["turn"]["result"]["status"]=="answered"
    state=qa.service.get(one["conversation_id"])
    assert not state["qa_enabled"] and state["usage"]["session_remaining_calls"]==2
    expect("qa_disabled",lambda:qa.follow(one))
    assert qa.service.raw(one["conversation_id"],request["message_id"])["raw_response"]["complete"]
    assert len(b.calls)==1


def test_lost_ui_response_recovers_committed_ledger_without_second_post(tmp_path):
    qa,b=integrated(tmp_path)
    one=qa.create()
    at,record=page(qa,one)
    record.drop_reply=True
    ask(at)
    at.run()
    at.button(key="qa_refresh").click().run()
    assert not at.exception and len(b.calls)==record.count("POST","/turns")==1
    state=qa.service.get(one["conversation_id"])
    assert state["usage"]["session_remaining_calls"]==2
    assert state["usage"]["unsettled_hold_cny"]=="0.000000"
