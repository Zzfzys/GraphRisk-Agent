"""Compact continuation checks: synthetic old three plus new three, no live ledger."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import sqlite3

import httpx
import pytest

from extensions.case_qa_compact_v1 import budget as module
from extensions.case_qa_compact_v1.budget import CompactBudget, _ResultProjection
from extensions.case_qa_compact_v1.service import CompactService
from extensions.case_qa_live_v3.budget import QA_SQL, ORDER
from extensions.case_qa_v1.budget import HISTORY_SQL
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
from test_case_qa_budget_v1 import expect
from test_case_qa_short_budget_v1 import make_short, reserve as reserve_short


def write(path,value):
    path.write_text(canonical(value),encoding="utf-8")
    return path


def compact_text(evidence):
    return canonical({"version":"case-qa-compact-answer-v1","case_alias":evidence["case_alias"],
        "evidence_version":evidence["evidence_version"],"paragraphs":[{"kind":"judgment",
        "text":"当前匿名历史可供核实，不能直接认定异常。","refs":["F1"]}]})


def make_compact(tmp_path, *, approved=True, register=True, handler=None):
    h=make_short(tmp_path)
    third,packet,_,_=reserve_short(h,case_index=1)
    response=h.budget.generate(third,packet)
    assert response["error_code"] is None
    previous_path,previous_approval=h.config_path,h.approval_path
    h.pre_compact_budget=h.budget
    with sqlite3.connect(h.path) as db:
        old_entries=db.execute(QA_SQL).fetchall();old_ids=[r[0] for r in old_entries]
        old_rows=[r for r in db.execute(HISTORY_SQL).fetchall() if r[0] in old_ids]
        h.pre_compact_grants=db.execute("SELECT * FROM case_qa_grants_v1").fetchall()
    h.old_tickets=[*h.previous_tickets,third]
    h.old_receipts=[*h.previous_receipts,Path(response["audit"]["receipt_path"])]
    h.calls.clear()
    h.config=deepcopy(h.config)
    directory=tmp_path/"compact";directory.mkdir()
    h.config.update(campaign="case-qa-compact-test",adapter_revision=module.ADAPTER_REVISION,
        answer_revision=module.REVISION,prompt_revision=module.REVISION,
        conversation_path=str(directory/"conversations.sqlite3"),trial_result_path=str(directory/"TRIAL-RESULT.json"),
        predecessor_scope={"config_path":str(previous_path),"config_sha256":file_hash(previous_path),
            "approval_path":str(previous_approval),"approval_sha256":file_hash(previous_approval),"attempt_ids":old_ids,
            "request_rows_sha256":digest(old_entries),"history_rows_sha256":digest(old_rows)})
    def packet(value=None,request=None):
        value=deepcopy(value or h.value());value.setdefault("turns",[])
        return CompactService._packet(object.__new__(CompactService),value,request or h.request())[0]
    h.packet=packet
    h.config["system_prompt_sha256"]=hashlib.sha256(packet()["system_prompt"].encode()).hexdigest()
    for name in ("budget.py","runtime.py","service.py","contract.py"):
        source=Path(module.__file__).with_name(name).resolve()
        h.config["files"][str(source)]=file_hash(source)
    h.config["trial_plan"]=[{"case_alias":h.cases[1]["case_alias"],"question_orders":[1,2]},
                           {"case_alias":h.cases[0]["case_alias"],"question_orders":[1]}]
    h.config_path=write(directory/"config.json",h.config)
    h.approval_path=directory/"OUTBOUND-APPROVED.json"
    h.approval=deepcopy(h.approval)
    h.approval.update(campaign=h.config["campaign"],config_sha256=file_hash(h.config_path),max_new_requests=3)
    if approved:
        write(h.approval_path,h.approval)
    template=compact_text(h.cases[0]["evidence"])
    h.body["choices"][0]["message"]["content"]=template
    def default_handler(request):
        body=deepcopy(h.body)
        if body.get("choices") and body["choices"][0].get("message",{}).get("content")==template:
            evidence=json.loads(json.loads(request.content)["messages"][-1]["content"])["evidence"]
            body["choices"][0]["message"]["content"]=compact_text(evidence)
        return httpx.Response(200,json=body)
    h.handler=handler or default_handler
    h.budget=CompactBudget(h.path,h.contract,h.config_path,parent_protocol=h.parent,
        approval_path=h.approval_path,transport=h.transport,fixture=True)
    if approved and register:
        h.budget.register()
    return h


def reserve(h,case_index=0,number=1,session=None,turns=None):
    value=h.value(case_index,session or ("b"*32 if case_index==0 else "a"*32))
    value["turns"]=list(turns or [])
    request=h.request(message_id=f"{number:032x}")
    request["parent_turn_id"]=value["turns"][-1]["message_id"] if value["turns"] else None
    packet=h.packet(value,request)
    return h.budget.reserve(value,request,packet),packet,value,request


def test_l1_two_then_m1_one_keep_cumulative_six_and_old_cost_receipts(tmp_path):
    h=make_compact(tmp_path)
    before_receipts=[p.read_bytes() for p in h.old_receipts]
    assert h.budget.usage(h.value(0))["case_remaining_calls"]==1
    assert h.budget.usage(h.value(1))["case_remaining_calls"]==2
    assert h.budget.usage()["batch_cost_estimate_cny"]=="0.000840"
    for case_index,count in ((1,2),(0,1)):
        for n in range(count):
            ticket,packet,_,_=reserve(h,case_index,number=case_index*10+n+1)
            result=h.budget.generate(ticket,packet)
            assert result["error_code"] is None
            assert set(json.loads(result["raw"]))=={"version","case_alias","evidence_version","paragraphs"}
        expect("quota_exhausted",lambda:reserve(h,case_index,number=99,session="9"*32))
    status=h.budget.usage()
    assert len(h.calls)==3 and status["batch_remaining_calls"]==0
    assert status["batch_cost_estimate_cny"]=="0.001680"
    assert status["protected_web_calls"]==4 and status["protected_route9_calls"]==200
    assert [p.read_bytes() for p in h.old_receipts]==before_receipts
    with sqlite3.connect(h.path) as db:
        assert db.execute("SELECT count(*) FROM case_qa_requests_v1").fetchone()==(6,)
        assert all(r in db.execute("SELECT * FROM case_qa_grants_v1").fetchall() for r in h.pre_compact_grants)
        assert db.execute("SELECT provider,identity FROM provider_identity ORDER BY provider").fetchall()==h.identities


def test_no_compact_specific_permission_prevents_registration_and_reservation(tmp_path):
    h=make_compact(tmp_path,approved=False)
    before=h.path.read_bytes()
    expect("case_qa_not_authorized",h.budget.register)
    expect("case_qa_not_authorized",lambda:reserve(h))
    assert h.path.read_bytes()==before and not h.calls


@pytest.mark.parametrize("field,value",[("max_new_requests",4),("preserve_predecessor_results",False)])
def test_new_three_call_approval_cannot_expand_original_six(tmp_path,field,value):
    h=make_compact(tmp_path,register=False)
    h.approval[field]=value;write(h.approval_path,h.approval)
    before=h.path.read_bytes()
    expect("case_qa_not_authorized",h.budget.register)
    assert h.path.read_bytes()==before and not h.calls


@pytest.mark.parametrize("index",[0,1,2])
def test_three_old_receipts_are_still_verified(tmp_path,index):
    h=make_compact(tmp_path);h.old_receipts[index].write_text("{}",encoding="utf-8")
    before=h.path.read_bytes()
    expect("qa_receipt_changed",h.budget.usage)
    assert h.path.read_bytes()==before and not h.calls


@pytest.mark.parametrize("ancestor",["v1","v2","v3","short"])
def test_each_of_four_old_grants_is_exactly_bound(tmp_path,ancestor):
    h=make_compact(tmp_path)
    short=json.loads(Path(h.config["predecessor_scope"]["config_path"]).read_text())
    v3=json.loads(Path(short["predecessor_scope"]["config_path"]).read_text())
    cfg=short if ancestor=="short" else v3 if ancestor=="v3" else json.loads(Path(v3["predecessors"][ancestor]["config_path"]).read_text())
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE case_qa_grants_v1 SET authorization='changed' WHERE campaign_id=?",(cfg["campaign"],))
    before=h.path.read_bytes()
    expect("qa_predecessor_grant_changed",h.budget.usage)
    assert h.path.read_bytes()==before and not h.calls


@pytest.mark.parametrize("index",[0,1,2])
def test_old_three_attempts_cannot_be_settled_by_compact_adapter(tmp_path,index):
    h=make_compact(tmp_path);before=h.path.read_bytes()
    expect("qa_settlement_scope_invalid",lambda:h.budget.settle(h.old_tickets[index]["attempt_id"],
        cost_micro=0,receipt_sha="a"*64,receipt_path="unused",identity=h.config["expected_identity"],usage={},result_code="ok"))
    assert h.path.read_bytes()==before and not h.calls


@pytest.mark.parametrize("key",["conversation_path","trial_result_path"])
def test_old_store_and_result_path_cannot_be_reused(tmp_path,key):
    h=make_compact(tmp_path)
    old=json.loads(Path(h.config["predecessor_scope"]["config_path"]).read_text())
    h.config[key]=old[key];write(h.config_path,h.config)
    before=h.path.read_bytes()
    expect("qa_predecessor_path_shared",lambda:CompactBudget(h.path,h.contract,h.config_path,parent_protocol=h.parent,
        approval_path=h.approval_path,transport=h.transport,fixture=True))
    assert h.path.read_bytes()==before and not h.calls


def test_new_unknown_blocks_whole_batch_and_retains_full_reservation(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout("synthetic timeout")
    h=make_compact(tmp_path,handler=timeout)
    ticket,packet,_,_=reserve(h,case_index=1)
    result=h.budget.generate(ticket,packet)
    assert result["error_code"]=="qa_transport_unknown" and result["audit"]["hold_micro_cny"]==2_105_344
    expect("ledger_unknown_paused",lambda:reserve(h,case_index=0))
    assert h.budget.generate(ticket,packet)["audit"]["transport_attempts"]==0 and len(h.calls)==1


def test_unrelated_non_qa_unknown_is_not_hidden_by_result_projection(tmp_path):
    h=make_compact(tmp_path)
    with sqlite3.connect(h.path) as db:
        db.execute("INSERT INTO model_attempts VALUES('alien','deepseek','unknown',1)")
        db.execute("INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES('alien','alien','alien','alien',1)")
    before=h.path.read_bytes()
    with pytest.raises((ExecutionStop,AssertionError)):
        h.budget.usage()
    assert h.path.read_bytes()==before and not h.calls


def test_forged_current_scope_is_rejected_before_old_history_projection(tmp_path):
    h=make_compact(tmp_path)
    ticket,packet,_,_=reserve(h,case_index=1)
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE case_qa_requests_v1 SET packet_json='{}' WHERE attempt_id=?",(ticket["attempt_id"],))
    before=h.path.read_bytes()
    with pytest.raises((ExecutionStop,KeyError)):
        h.budget.usage()
    assert h.path.read_bytes()==before and not h.calls


def test_result_projection_handles_nested_h1_without_changing_sql_counts_or_sums(tmp_path):
    h=make_compact(tmp_path)
    excluded=(h.old_tickets[-1]["attempt_id"],)
    with sqlite3.connect(h.path.as_uri()+"?mode=ro",uri=True) as db:
        view=_ResultProjection(db,excluded)
        sql=HISTORY_SQL.replace(ORDER," WHERE a.attempt_id<>? AND a.attempt_id NOT IN (?) AND a.attempt_id NOT IN (?,?)"+ORDER)
        params=("h1-fixture","no-a","no-b","no-c")
        real=db.execute(sql,params).fetchall()
        assert view.execute(sql,params).fetchall()==[r for r in real if r[0] not in excluded]
        qa=QA_SQL+" WHERE attempt_id NOT IN (?) AND attempt_id NOT IN (?,?)"
        assert len(view.execute(qa,("no-a","no-b","no-c")).fetchall())==2
        for query in ("SELECT count(*) FROM model_attempts","SELECT sum(accounted) FROM execution_requests",
                      "SELECT provider,identity FROM provider_identity ORDER BY provider"):
            assert view.execute(query).fetchall()==db.execute(query).fetchall()
        view.verified()


@pytest.mark.parametrize("sql,params",[(HISTORY_SQL,("wrong",)),
    (HISTORY_SQL.replace(ORDER," WHERE a.provider=?"+ORDER),("deepseek",)),
    (QA_SQL+" WHERE attempt_id=?",("x",)),(QA_SQL+" WHERE attempt_id NOT IN (?)",())])
def test_result_projection_rejects_unsupported_query_family_shapes(tmp_path,sql,params):
    h=make_compact(tmp_path)
    with sqlite3.connect(h.path.as_uri()+"?mode=ro",uri=True) as db:
        view=_ResultProjection(db,())
        expect("qa_result_projection_invalid",lambda:view.execute(sql,params))
