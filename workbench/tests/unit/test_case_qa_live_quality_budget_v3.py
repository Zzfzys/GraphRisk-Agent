"""Synthetic predecessor and bounded quality continuation; no project live ledger."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import sqlite3

import httpx
import pytest

from extensions.case_qa_live_v1.budget import LiveCaseQABudget
from extensions.case_qa_live_v2.budget import LiveCaseQABudgetV2
from extensions.case_qa_live_v3 import budget as module
from extensions.case_qa_live_v3.budget import LiveQualityBudget, _ScopeView
from extensions.case_qa_quality_v1.service import QualityService
from extensions.case_qa_v1.budget import HISTORY_SQL
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
from test_case_qa_budget_v1 import expect
from test_case_qa_live_budget_v1 import claim
from test_case_qa_live_identity_v2 import make_v2


def write(path, value):
    path.write_text(canonical(value), encoding="utf-8")
    return path


def make_v3(tmp_path, *, approved=True, register=True, handler=None):
    h = make_v2(tmp_path)
    root = Path(__file__).parents[2]
    v1_path, v1_approval = tmp_path/"live-config.json", tmp_path/"OUTBOUND-APPROVED.json"
    # This helper's first live-v1 grant had no requests, matching the real predecessor.
    previous_v1 = LiveCaseQABudget(h.path, h.contract, v1_path, parent_protocol=h.parent,
        approval_path=v1_approval, transport=h.transport, fixture=True)
    previous_v1.register()
    old_ticket, old_packet = claim(h)
    old_response = h.budget.generate(old_ticket, old_packet)
    assert old_response["error_code"] is None
    v2_path, v2_approval = h.config_path, h.approval_path
    with sqlite3.connect(h.path) as db:
        old_entries = db.execute("SELECT * FROM case_qa_requests_v1").fetchall()
        old_rows = [r for r in db.execute(HISTORY_SQL).fetchall() if r[0] == old_ticket["attempt_id"]]
        h.old_grants = db.execute("SELECT * FROM case_qa_grants_v1").fetchall()
    h.old_ticket, h.old_packet, h.old_response = old_ticket, old_packet, old_response
    h.calls.clear()
    h.handler = handler
    h.config = deepcopy(h.config)
    h.config.update(campaign="case-qa-live-quality-test-v3", adapter_revision=module.ADAPTER_REVISION,
        answer_revision=module.ANSWER_REVISION, base_contract=h.contract,
        conversation_path=str(tmp_path/"quality-conversations.sqlite3"), trial_result_path=str(tmp_path/"TRIAL-RESULT.json"))
    h.config["predecessors"] = {
        "v1":{"config_path":str(v1_path),"config_sha256":file_hash(v1_path),"approval_path":str(v1_approval),"approval_sha256":file_hash(v1_approval)},
        "v2":{"config_path":str(v2_path),"config_sha256":file_hash(v2_path),"approval_path":str(v2_approval),"approval_sha256":file_hash(v2_approval)},
        "attempt_ids":[old_ticket["attempt_id"]], "request_rows_sha256":digest(old_entries), "history_rows_sha256":digest(old_rows)}
    def packet(value=None, request=None):
        value = deepcopy(value or h.value())
        value.setdefault("turns", [])
        return QualityService._packet(object.__new__(QualityService), value, request or h.request())[0]
    h.packet = packet
    h.config["system_prompt_sha256"] = hashlib.sha256(packet()["system_prompt"].encode()).hexdigest()
    for relative in ("extensions/case_qa_live_v3/budget.py", "extensions/case_qa_live_v3/runtime.py",
                     "extensions/case_qa_live_v3/service.py", "extensions/case_qa_quality_v1/service.py",
                     "extensions/case_qa_quality_v1/contract.py"):
        source = (root/relative).resolve()
        h.config["files"][str(source)] = file_hash(source)
    h.config["case_files"] = []
    for index, case in enumerate(h.cases):
        path = write(tmp_path/f"case-{index+1}.json", case)
        h.config["case_files"].append({"path":str(path),"sha256":file_hash(path),"transaction_id_local_only":str(index+1)})
        h.config["files"][str(path)] = file_hash(path)
    questions = write(tmp_path/"questions.json", {"questions":[{"order":i,"text":f"Synthetic question {i}"} for i in (1,2,3)]})
    h.config["questions_path"] = str(questions)
    h.config["files"][str(questions)] = file_hash(questions)
    h.config["trial_plan"] = [{"case_alias":h.cases[0]["case_alias"],"question_orders":[1,2]},
                             {"case_alias":h.cases[1]["case_alias"],"question_orders":[1,2,3]}]
    h.config_path = write(tmp_path/"quality-config.json", h.config)
    h.approval_path = tmp_path/"QUALITY-APPROVED.json"
    h.approval = deepcopy(h.approval)
    h.approval.update(campaign=h.config["campaign"],config_sha256=file_hash(h.config_path),
        max_new_requests=5,preserve_predecessor_results=True)
    if approved:
        write(h.approval_path,h.approval)
    h.budget = LiveQualityBudget(h.path,h.contract,h.config_path,parent_protocol=h.parent,
        approval_path=h.approval_path,transport=h.transport,fixture=True)
    if register and approved:
        h.budget.register()
    return h


def reserve(h, case_index=0, number=1, session=None, turns=None):
    value = h.value(case_index, session or ("e"*32 if case_index == 0 else "f"*32))
    value["turns"] = list(turns or [])
    request = h.request(message_id=f"{number:032x}")
    request["parent_turn_id"] = value["turns"][-1]["message_id"] if value["turns"] else None
    packet = h.packet(value,request)
    return h.budget.reserve(value,request,packet),packet,value,request


def test_five_new_calls_count_old_one_and_preserve_grants_identity_and_allocations(tmp_path):
    h = make_v3(tmp_path)
    for case_index, count in ((0,2),(1,3)):
        for index in range(count):
            ticket,packet,_,_ = reserve(h,case_index,number=case_index*3+index+1)
            result = h.budget.generate(ticket,packet)
            assert result["error_code"] is None
        expect("quota_exhausted",lambda:reserve(h,case_index,number=99,session="9"*32))
    status = h.budget.usage()
    assert len(h.calls) == 5 and status["batch_remaining_calls"] == 0
    assert status["batch_cost_estimate_cny"] == "0.001680"
    assert status["protected_web_calls"] == 4 and status["protected_route9_calls"] == 200
    with sqlite3.connect(h.path) as db:
        assert db.execute("SELECT count(*) FROM case_qa_requests_v1").fetchone() == (6,)
        assert all(row in db.execute("SELECT * FROM case_qa_grants_v1").fetchall() for row in h.old_grants)
        assert db.execute("SELECT identity FROM provider_identity WHERE provider='deepseek'").fetchone() == (h.identities[0][1],)


def test_unapproved_continuation_does_not_register_reserve_or_send(tmp_path):
    h = make_v3(tmp_path,approved=False)
    before = h.path.read_bytes()
    expect("case_qa_not_authorized",h.budget.register)
    expect("case_qa_not_authorized",lambda:reserve(h))
    assert h.path.read_bytes() == before and h.calls == []


@pytest.mark.parametrize("field,bad", [("max_new_requests",6),("preserve_predecessor_results",False)])
def test_new_remaining_scope_approval_is_explicit(tmp_path,field,bad):
    h = make_v3(tmp_path,register=False)
    h.approval[field] = bad
    write(h.approval_path,h.approval)
    before = h.path.read_bytes()
    expect("case_qa_not_authorized",h.budget.register)
    assert h.path.read_bytes() == before and not h.calls


@pytest.mark.parametrize("change", ["grant_v1","grant_v2","old_row","old_packet","old_receipt","old_identity"])
def test_old_grants_receipt_packet_and_history_remain_authenticated(tmp_path,change):
    h = make_v3(tmp_path)
    if change == "old_receipt":
        Path(h.old_response["audit"]["receipt_path"]).write_text("{}",encoding="utf-8")
    else:
        with sqlite3.connect(h.path) as db:
            if change.startswith("grant"):
                cfg = json.loads(Path(h.config["predecessors"][change.split("_")[1]]["config_path"]).read_text())
                db.execute("UPDATE case_qa_grants_v1 SET authorization='changed' WHERE campaign_id=?",(cfg["campaign"],))
            elif change == "old_row":
                db.execute("UPDATE execution_requests SET accounted=accounted+1 WHERE attempt_id=?",(h.old_ticket["attempt_id"],))
            elif change == "old_packet":
                db.execute("UPDATE case_qa_requests_v1 SET packet_json='{}' WHERE attempt_id=?",(h.old_ticket["attempt_id"],))
            else:
                db.execute("UPDATE provider_identity SET identity='changed' WHERE provider='deepseek'")
    before = h.path.read_bytes()
    with pytest.raises((ExecutionStop,AssertionError)):
        h.budget.usage()
    assert h.path.read_bytes() == before and not h.calls


def test_old_attempt_cannot_be_resettled_or_replayed_as_new_packet(tmp_path):
    h = make_v3(tmp_path)
    before = h.path.read_bytes()
    expect("qa_settlement_scope_invalid",lambda:h.budget.settle(h.old_ticket["attempt_id"],cost_micro=0,
        receipt_sha="a"*64,receipt_path="forbidden",identity=h.config["expected_identity"],usage={},result_code="ok"))
    expect("qa_prompt_changed",lambda:h.budget.generate(h.old_ticket,h.old_packet))
    assert h.path.read_bytes() == before and not h.calls


def test_only_new_prompt_and_trusted_history_can_be_reserved(tmp_path):
    h = make_v3(tmp_path)
    value=h.value();value["turns"]=[];request=h.request();packet=h.packet(value,request)
    packet["view"]["dialogue_background"].append({"turn_id":"a"*32,"user":"fake","assistant":"fake","source":"unverified_dialogue"})
    expect("qa_history_scope_changed",lambda:h.budget.reserve(value,request,packet))
    assert not h.calls


def test_new_unknown_keeps_hold_stops_entire_remaining_batch_and_never_retries(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout("synthetic timeout")
    h = make_v3(tmp_path,handler=timeout)
    ticket,packet,_,_ = reserve(h)
    result=h.budget.generate(ticket,packet)
    assert result["error_code"] == "qa_transport_unknown" and result["audit"]["hold_micro_cny"] == 2_105_344
    assert h.budget.usage()["block_reason"] == "ledger_unknown_paused"
    expect("ledger_unknown_paused",lambda:reserve(h,case_index=1))
    replay=h.budget.generate(ticket,packet)
    assert replay["audit"]["transport_attempts"] == 0 and len(h.calls) == 1


def test_new_receipt_identity_violation_retains_stop_and_preserves_old_qa(tmp_path):
    h = make_v3(tmp_path)
    h.body["system_fingerprint"]="unapproved"
    ticket,packet,_,_=reserve(h)
    result=h.budget.generate(ticket,packet)
    assert result["error_code"] and result["audit"]["integrity_stop"]
    assert h.budget.usage()["block_reason"] == "budget_integrity_stop"
    with sqlite3.connect(h.path) as db:
        assert db.execute("SELECT state FROM model_attempts WHERE attempt_id=?",(h.old_ticket["attempt_id"],)).fetchone()==("settled",)


def test_projection_does_not_change_global_sums_or_unrelated_queries(tmp_path):
    h=make_v3(tmp_path)
    with sqlite3.connect(h.path.as_uri()+"?mode=ro",uri=True) as db:
        count=db.execute("SELECT count(*) FROM model_attempts").fetchone()
        view=_ScopeView(db,(),(h.old_ticket["attempt_id"],))
        assert view.execute("SELECT count(*) FROM model_attempts").fetchone()==count
        assert len(view.execute(HISTORY_SQL).fetchall())==count[0]-1
        assert len(view.execute("SELECT * FROM case_qa_requests_v1").fetchall())==0
        with pytest.raises(ExecutionStop,match="qa_scope_projection_invalid"):
            view.execute(HISTORY_SQL,("unexpected",))
