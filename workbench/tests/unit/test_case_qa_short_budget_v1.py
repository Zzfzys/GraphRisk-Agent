"""Only synthetic ledgers/HTTP: old two calls plus at most four new short calls."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import sqlite3

import httpx
import pytest

from extensions.case_qa_live_v3.budget import QA_SQL
from extensions.case_qa_short_v1 import budget as module
from extensions.case_qa_short_v1.budget import ShortBudget, _HistoricalSlice
from extensions.case_qa_short_v1.service import ShortService
from extensions.case_qa_v1.budget import HISTORY_SQL
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
from test_case_qa_budget_v1 import expect
from test_case_qa_live_quality_budget_v3 import make_v3, reserve as reserve_v3


def write(path, value):
    path.write_text(canonical(value),encoding="utf-8")
    return path


def make_short(tmp_path, *, approved=True, register=True, handler=None):
    h=make_v3(tmp_path)
    second,packet,_,_=reserve_v3(h,case_index=0)
    response=h.budget.generate(second,packet)
    assert response["error_code"] is None
    previous_path,previous_approval=h.config_path,h.approval_path
    h.pre_short_budget=h.budget
    with sqlite3.connect(h.path) as db:
        old_entries=db.execute(QA_SQL).fetchall()
        old_ids=[r[0] for r in old_entries]
        old_rows=[r for r in db.execute(HISTORY_SQL).fetchall() if r[0] in old_ids]
        h.pre_short_grants=db.execute("SELECT * FROM case_qa_grants_v1").fetchall()
    h.previous_tickets=[h.old_ticket,second]
    h.previous_receipts=[Path(h.old_response["audit"]["receipt_path"]),Path(response["audit"]["receipt_path"])]
    h.previous_grant_count=len(h.pre_short_grants)
    h.calls.clear();h.handler=handler
    directory=tmp_path/"short"
    directory.mkdir()
    h.config=deepcopy(h.config)
    h.config.pop("predecessors",None)
    h.config.update(campaign="case-qa-short-test",prompt_revision=module.PROMPT_REVISION,
        conversation_path=str(directory/"conversations.sqlite3"),trial_result_path=str(directory/"TRIAL-RESULT.json"),
        predecessor_scope={"config_path":str(previous_path),"config_sha256":file_hash(previous_path),
            "approval_path":str(previous_approval),"approval_sha256":file_hash(previous_approval),"attempt_ids":old_ids,
            "request_rows_sha256":digest(old_entries),"history_rows_sha256":digest(old_rows)})
    def packet(value=None,request=None):
        value=deepcopy(value or h.value());value.setdefault("turns",[])
        return ShortService._packet(object.__new__(ShortService),value,request or h.request())[0]
    h.packet=packet
    h.config["system_prompt_sha256"]=hashlib.sha256(packet()["system_prompt"].encode()).hexdigest()
    source=Path(module.__file__).resolve()
    for path in (source,source.with_name("runtime.py"),source.with_name("service.py")):
        h.config["files"][str(path)]=file_hash(path)
    h.config["trial_plan"]=[{"case_alias":h.cases[1]["case_alias"],"question_orders":[1,2,3]},
                           {"case_alias":h.cases[0]["case_alias"],"question_orders":[1]}]
    h.config_path=write(directory/"config.json",h.config)
    h.approval_path=directory/"OUTBOUND-APPROVED.json"
    h.approval=deepcopy(h.approval)
    h.approval.update(campaign=h.config["campaign"],config_sha256=file_hash(h.config_path),max_new_requests=4)
    if approved:
        write(h.approval_path,h.approval)
    h.budget=ShortBudget(h.path,h.contract,h.config_path,parent_protocol=h.parent,
        approval_path=h.approval_path,transport=h.transport,fixture=True)
    if approved and register:
        h.budget.register()
    return h


def reserve(h,case_index=0,number=1,session=None,turns=None):
    value=h.value(case_index,session or ("d"*32 if case_index==0 else "c"*32))
    value["turns"]=list(turns or [])
    request=h.request(message_id=f"{number:032x}")
    request["parent_turn_id"]=value["turns"][-1]["message_id"] if value["turns"] else None
    packet=h.packet(value,request)
    return h.budget.reserve(value,request,packet),packet,value,request


def test_l1_three_then_m1_one_respects_all_six_calls_and_five_yuan(tmp_path):
    h=make_short(tmp_path)
    m1=h.budget.usage(h.value(0));l1=h.budget.usage(h.value(1))
    assert m1["case_remaining_calls"]==1 and l1["case_remaining_calls"]==3
    assert m1["batch_remaining_calls"]==4 and m1["batch_cost_estimate_cny"]=="0.000560"
    for case_index,count in ((1,3),(0,1)):
        for n in range(count):
            ticket,packet,_,_=reserve(h,case_index,number=case_index*10+n+1)
            assert h.budget.generate(ticket,packet)["error_code"] is None
        expect("quota_exhausted",lambda:reserve(h,case_index,number=99,session="9"*32))
    usage=h.budget.usage()
    assert len(h.calls)==4 and usage["batch_remaining_calls"]==0
    assert usage["batch_cost_estimate_cny"]=="0.001680"
    assert usage["protected_web_calls"]==4 and usage["protected_route9_calls"]==200
    with sqlite3.connect(h.path) as db:
        assert db.execute("SELECT count(*) FROM case_qa_requests_v1").fetchone()==(6,)
        grants=db.execute("SELECT * FROM case_qa_grants_v1").fetchall()
        assert all(row in grants for row in h.pre_short_grants)
        assert db.execute("SELECT provider,identity FROM provider_identity ORDER BY provider").fetchall()==h.identities


def test_no_new_specific_approval_means_no_registration_reservation_or_request(tmp_path):
    h=make_short(tmp_path,approved=False)
    before=h.path.read_bytes()
    expect("case_qa_not_authorized",h.budget.register)
    expect("case_qa_not_authorized",lambda:reserve(h))
    assert h.path.read_bytes()==before and not h.calls


@pytest.mark.parametrize("field,value",[("max_new_requests",5),("preserve_predecessor_results",False)])
def test_new_four_call_permission_cannot_borrow_old_authorization(tmp_path,field,value):
    h=make_short(tmp_path,register=False)
    h.approval[field]=value;write(h.approval_path,h.approval)
    before=h.path.read_bytes()
    expect("case_qa_not_authorized",h.budget.register)
    assert h.path.read_bytes()==before and not h.calls


@pytest.mark.parametrize("index",[0,1])
def test_either_old_receipt_tamper_blocks_readonly_qa_audit(tmp_path,index):
    h=make_short(tmp_path)
    h.previous_receipts[index].write_text("{}",encoding="utf-8")
    before=h.path.read_bytes()
    expect("qa_receipt_changed",h.budget.usage)
    assert h.path.read_bytes()==before and not h.calls


@pytest.mark.parametrize("ancestor",["v1","v2","v3"])
def test_all_three_old_grants_are_exactly_preserved(tmp_path,ancestor):
    h=make_short(tmp_path)
    old=json.loads(Path(h.config["predecessor_scope"]["config_path"]).read_text())
    cfg=old if ancestor=="v3" else json.loads(Path(old["predecessors"][ancestor]["config_path"]).read_text())
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE case_qa_grants_v1 SET authorization='changed' WHERE campaign_id=?",(cfg["campaign"],))
    before=h.path.read_bytes()
    expect("qa_predecessor_grant_changed",h.budget.usage)
    assert h.path.read_bytes()==before and not h.calls


@pytest.mark.parametrize("index",[0,1])
def test_both_old_attempts_are_forbidden_from_new_settlement(tmp_path,index):
    h=make_short(tmp_path)
    before=h.path.read_bytes()
    expect("qa_settlement_scope_invalid",lambda:h.budget.settle(h.previous_tickets[index]["attempt_id"],
        cost_micro=0,receipt_sha="a"*64,receipt_path="not-used",identity=h.config["expected_identity"],usage={},result_code="ok"))
    assert h.path.read_bytes()==before and not h.calls


@pytest.mark.parametrize("key",["conversation_path","trial_result_path"])
def test_new_store_and_result_paths_cannot_reuse_old_v3(tmp_path,key):
    h=make_short(tmp_path)
    old=json.loads(Path(h.config["predecessor_scope"]["config_path"]).read_text())
    h.config[key]=old[key];write(h.config_path,h.config)
    before=h.path.read_bytes()
    expect("qa_predecessor_path_shared",lambda:ShortBudget(h.path,h.contract,h.config_path,parent_protocol=h.parent,
        approval_path=h.approval_path,transport=h.transport,fixture=True))
    assert h.path.read_bytes()==before and not h.calls


def test_new_unknown_keeps_full_hold_and_blocks_the_other_case(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout("synthetic timeout")
    h=make_short(tmp_path,handler=timeout)
    ticket,packet,_,_=reserve(h,case_index=1)
    result=h.budget.generate(ticket,packet)
    assert result["error_code"]=="qa_transport_unknown" and result["audit"]["hold_micro_cny"]==2_105_344
    assert h.budget.usage()["block_reason"]=="ledger_unknown_paused"
    expect("ledger_unknown_paused",lambda:reserve(h,case_index=0))
    assert h.budget.generate(ticket,packet)["audit"]["transport_attempts"]==0 and len(h.calls)==1


def test_old_v3_frozen_budget_can_no_longer_implicitly_send_after_short_call(tmp_path):
    h=make_short(tmp_path)
    ticket,packet,_,_=reserve(h,case_index=1)
    assert h.budget.generate(ticket,packet)["error_code"] is None
    before=h.path.read_bytes()
    with pytest.raises(ExecutionStop):
        h.pre_short_budget.usage()
    assert h.path.read_bytes()==before and len(h.calls)==1


def test_new_packet_must_have_exact_short_prompt_and_trusted_context(tmp_path):
    h=make_short(tmp_path)
    value=h.value();value["turns"]=[];request=h.request();packet=h.packet(value,request)
    packet["view"]["dialogue_background"].append({"turn_id":"a"*32,"user":"forged","assistant":"forged","source":"unverified_dialogue"})
    before=h.path.read_bytes()
    expect("qa_history_scope_changed",lambda:h.budget.reserve(value,request,packet))
    assert h.path.read_bytes()==before and not h.calls


def test_historical_slice_whitelists_nested_h1_reads_and_preserves_global_counts(tmp_path):
    h=make_short(tmp_path)
    old_ids=h.config["predecessor_scope"]["attempt_ids"]
    with sqlite3.connect(h.path.as_uri()+"?mode=ro",uri=True) as db:
        view=_HistoricalSlice(db,old_ids,(),quarantined="old-000000")
        for sql,parameters in view.history:
            view.execute(sql,parameters).fetchall()
        for sql,parameters in view.qa:
            view.execute(sql,parameters).fetchall()
        assert view.execute("SELECT count(*) FROM model_attempts").fetchone()==db.execute("SELECT count(*) FROM model_attempts").fetchone()
        with pytest.raises(ExecutionStop,match="qa_history_slice_invalid"):
            view.execute(HISTORY_SQL,("wrong",))
