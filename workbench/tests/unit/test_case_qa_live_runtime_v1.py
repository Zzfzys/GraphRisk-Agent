"""End-to-end production wrappers with synthetic credentials and MockTransport."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from extensions.case_qa_live_v1 import runtime
from extensions.case_qa_live_v1.service import LiveCaseQAService,LiveConversationStore
from extensions.case_qa_v1.sessions import SessionError
from graphrisk.agent.upgrade_contract import file_hash
from test_case_qa_live_budget_v1 import make_live


def wired(tmp_path,monkeypatch,*,approved=True):
    h=make_live(tmp_path/"billing",approved=approved,register=approved)
    cfg=deepcopy(h.config)
    cfg["conversation_path"]=str(tmp_path/"conversations.sqlite3")
    cfg["case_files"]=[]
    for index,case in enumerate(h.cases):
        path=tmp_path/f"case-{index}.json"
        path.write_text(json.dumps(case,ensure_ascii=False),encoding="utf-8")
        cfg["case_files"].append({"transaction_id_local_only":str(index+1000000),"path":str(path),"sha256":file_hash(path)})
    questions=tmp_path/"questions.json"
    questions.write_text(json.dumps({"questions":[{"order":i+1,"text":q} for i,q in enumerate(["为什么？","有没有正常解释？","还需要什么资料？"])]}),encoding="utf-8")
    cfg["questions_path"]=str(questions)
    cfg["trial_result_path"]=str(tmp_path/"trial-result.json")
    monkeypatch.setattr(runtime,"components",lambda *args:(cfg,h.parent,h.budget))
    return h,cfg


def test_api_startup_pending_grant_has_no_model_or_credential_access(tmp_path,monkeypatch):
    h,cfg=wired(tmp_path,monkeypatch,approved=False)
    before=h.path.read_bytes()
    with TestClient(runtime.build_app(h.config_path)) as client:
        assert client.get("/health").json()["model_calls_on_start"]==0
        view=client.post("/api/v1/case-qa/conversations",headers={"Idempotency-Key":"live_pending_create"},
            json={"transaction_id":"1000000","batch_id":None}).json()
        assert view["mode"]=="live_case_qa" and view["usage"]["budget_status"]=="authorization_required"
        response=client.post(f"/api/v1/case-qa/conversations/{view['conversation_id']}/turns",
            json={"message_id":"a"*32,"parent_turn_id":None,"evidence_version":view["evidence_version"],"message":"为什么？"})
        assert response.json()["error_code"]=="case_qa_not_authorized"
    assert h.path.read_bytes()==before and not h.calls and not h.credential_reads


def test_bounded_six_trial_stores_live_history_and_cannot_rerun(tmp_path,monkeypatch):
    h,cfg=wired(tmp_path,monkeypatch)
    output=runtime.trial(h.config_path,h.approval_path)
    assert len(h.calls)==6 and len(h.credential_reads)==6
    assert len(output["cases"])==2 and all(len(c["turns"])==3 for c in output["cases"])
    for case in output["cases"]:
        assert all(t["turn"]["result"]["source"]=="deepseek" for t in case["turns"])
    with pytest.raises(ValueError,match="saved_trial_result_exists"):
        runtime.trial(h.config_path,h.approval_path)
    assert len(h.calls)==6
    assert h.budget.usage()["batch_remaining_calls"]==0


def test_trial_stops_on_first_invalid_answer_without_repair(tmp_path,monkeypatch):
    h,cfg=wired(tmp_path,monkeypatch)
    h.body["choices"][0]["message"]["content"]='{"wrong":"schema"}'
    output=runtime.trial(h.config_path,h.approval_path)
    assert len(h.calls)==1 and output["stopped_on_non_answered"]
    assert output["cases"][0]["turns"][0]["turn"]["result"]["status"]=="invalid_response"
    assert output["repaired_drafts"]==output["paid_judges"]==0


def test_server_redacts_registered_ids_in_dynamic_question_and_history(tmp_path,monkeypatch):
    h,cfg=wired(tmp_path,monkeypatch)
    qa=runtime.service(cfg,h.budget)
    one=qa.create({"transaction_id":"1000000","batch_id":None},"live_redaction_case")
    request={"message_id":"b"*32,"parent_turn_id":None,"evidence_version":one["evidence_version"],"message":"解释交易1000000以及1000001的范围"}
    result=qa.follow(one["conversation_id"],request)
    assert "1000000" not in result["turn"]["message"] and "1000001" not in result["turn"]["message"]
    assert one["case_alias"] in result["turn"]["message"]
    encoded=json.dumps(h.calls,ensure_ascii=False)
    assert "1000000" not in encoded and "1000001" not in encoded


def test_catalog_change_stops_new_send_without_discarding_old_snapshot(tmp_path,monkeypatch):
    h,cfg=wired(tmp_path,monkeypatch)
    qa=runtime.service(cfg,h.budget)
    one=qa.create({"transaction_id":"1000000","batch_id":None},"live_catalog_case")
    Path(cfg["case_files"][0]["path"]).write_text("changed",encoding="utf-8")
    view=qa.get(one["conversation_id"])
    assert view["validity_error"]=="evidence_unavailable"
    request={"message_id":"c"*32,"parent_turn_id":None,"evidence_version":one["evidence_version"],"message":"为什么？"}
    with pytest.raises(SessionError):
        qa.follow(one["conversation_id"],request)
    assert not h.calls
    assert qa.evidence(one["conversation_id"])["source"]=="program"
