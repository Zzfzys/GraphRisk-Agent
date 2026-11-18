"""Four-request short successor trial uses synthetic evidence and MockTransport only."""
from base64 import b64decode
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from extensions.case_qa_short_v1 import runtime
from extensions.case_qa_short_v1.service import ANSWER_RULE
from graphrisk.agent.upgrade_contract import file_hash
from test_case_qa_short_budget_v1 import make_short


def wired(tmp_path, monkeypatch, *, approved=True):
    h = make_short(tmp_path/"billing", approved=approved)
    cfg = deepcopy(h.config)
    cfg["conversation_path"] = str(tmp_path/"short-conversations.sqlite3")
    cfg["case_files"] = []
    for index, case in enumerate(h.cases):
        path = tmp_path/f"case-{index}.json"
        path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        cfg["case_files"].append({"transaction_id_local_only":str(index+1000000),
            "path":str(path), "sha256":file_hash(path)})
    questions = tmp_path/"questions.json"
    questions.write_text(json.dumps({"questions":[{"order":i+1,"text":q}
        for i,q in enumerate(["为什么？","有没有正常解释？","还需要什么资料？"])]}), encoding="utf-8")
    cfg.update(questions_path=str(questions), trial_result_path=str(tmp_path/"trial-result.json"),
        trial_plan=[{"case_alias":h.cases[1]["case_alias"], "question_orders":[1,2,3]},
                    {"case_alias":h.cases[0]["case_alias"], "question_orders":[1]}])
    monkeypatch.setattr(runtime, "components", lambda *args:(cfg,h.parent,h.budget))
    return h,cfg


def test_four_new_rounds_keep_old_two_and_try_l1_three_before_m1_one(tmp_path, monkeypatch):
    h,cfg = wired(tmp_path,monkeypatch)
    start = len(h.calls)
    output = runtime.trial(h.config_path,h.approval_path)
    assert len(h.calls)-start == 4
    assert [len(c["turns"]) for c in output["cases"]] == [3,1]
    assert output["adapter_revision"] == "case-qa-live-quality-v3"
    assert output["answer_revision"] == "case-qa-quality-v1"
    assert output["prompt_revision"] == "case-qa-short-v1"
    assert [c["case_alias"] for c in output["cases"]]==[h.cases[1]["case_alias"],h.cases[0]["case_alias"]]
    assert all(t["turn"]["result"]["status"]=="answered" and t["turn"]["result"]["check_version"]=="case-qa-quality-v1"
               for c in output["cases"] for t in c["turns"])
    assert h.budget.usage()["batch_remaining_calls"] == 0
    bodies=[json.loads(call.content) if hasattr(call,"content") else call for call in h.calls[start:]]
    assert all(b["messages"][0]["content"].endswith(ANSWER_RULE) for b in bodies)
    contexts=[json.loads(b["messages"][1]["content"]) for b in bodies]
    assert [len(v["dialogue_background"]) for v in contexts]==[0,1,2,0]
    assert all(t["source"]=="unverified_dialogue" for v in contexts for t in v["dialogue_background"])
    before=h.path.read_bytes()
    with pytest.raises(ValueError,match="saved_trial_result_exists"):
        runtime.trial(h.config_path,h.approval_path)
    assert h.path.read_bytes()==before and len(h.calls)-start==4


def test_first_invalid_output_stops_and_saves_new_diagnostics_and_raw(tmp_path,monkeypatch):
    h,cfg=wired(tmp_path,monkeypatch)
    h.body["choices"][0]["message"]["content"]='{"wrong":"schema"}'
    start=len(h.calls)
    output=runtime.trial(h.config_path,h.approval_path)
    assert len(h.calls)-start==1 and output["stopped_on_non_answered"]
    assert len(output["cases"])==1 and len(output["cases"][0]["turns"])==1
    turn=output["cases"][0]["turns"][0]["turn"]
    assert turn["result"]["status"]=="invalid_response" and turn["result"]["diagnostics"]
    qa=runtime.service(cfg,h.budget)
    raw=qa.raw(output["cases"][0]["conversation_id"],turn["message_id"])
    assert raw["source"]=="unreviewed_deepseek_output"
    assert b64decode(raw["raw_response"]["content_base64"])==b'{"wrong":"schema"}'
    assert output["repaired_drafts"]==output["paid_judges"]==0


def test_first_unknown_stops_and_preserves_hold_without_retry(tmp_path,monkeypatch):
    h,cfg=wired(tmp_path,monkeypatch)
    def timeout(request):
        raise httpx.ReadTimeout("synthetic private text")
    h.handler=timeout
    start=len(h.calls)
    output=runtime.trial(h.config_path,h.approval_path)
    assert len(h.calls)-start==1 and output["stopped_on_non_answered"]
    assert output["cases"][0]["turns"][0]["turn"]["result"]["status"]=="response_unknown"
    assert h.budget.usage()["block_reason"]=="ledger_unknown_paused"
    assert h.budget.usage()["unsettled_hold_cny"]=="2.105344"


def test_unapproved_app_start_and_first_question_do_not_send_or_read_credentials(tmp_path,monkeypatch):
    h,cfg=wired(tmp_path,monkeypatch,approved=False)
    before=h.path.read_bytes();calls=len(h.calls);keys=len(h.credential_reads)
    with TestClient(runtime.build_app(h.config_path,h.approval_path)) as client:
        health=client.get("/health").json()
        assert health["model_calls_on_start"]==0 and health["adapter_revision"]=="case-qa-live-quality-v3"
        one=client.post("/api/v1/case-qa/conversations",headers={"Idempotency-Key":"short_pending_start"},
            json={"transaction_id":"1000000","batch_id":None}).json()
        assert one["usage"]["budget_status"]=="authorization_required"
        response=client.post(f"/api/v1/case-qa/conversations/{one['conversation_id']}/turns",json={
            "message_id":"a"*32,"parent_turn_id":None,"evidence_version":one["evidence_version"],"message":"为什么？"})
        assert response.json()["error_code"]=="case_qa_not_authorized"
    assert h.path.read_bytes()==before and len(h.calls)==calls and len(h.credential_reads)==keys


def test_invalid_trial_plan_stops_before_registration_or_session_creation(tmp_path,monkeypatch):
    h,cfg=wired(tmp_path,monkeypatch)
    cfg["trial_plan"][0]["question_orders"]=[1,1]
    before=h.path.read_bytes();calls=len(h.calls)
    with pytest.raises(ValueError,match="trial_plan_invalid"):
        runtime.trial(h.config_path,h.approval_path)
    assert h.path.read_bytes()==before and len(h.calls)==calls
    assert not Path(cfg["conversation_path"]).exists() and not Path(cfg["trial_result_path"]).exists()
