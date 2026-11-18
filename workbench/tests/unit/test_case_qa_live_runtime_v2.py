"""Real runtime control flow over synthetic successor identities; no network."""
from base64 import b64decode
from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from extensions.case_qa_live_v2 import runtime
from extensions.case_qa_live_v1.budget import LiveCaseQABudget
from graphrisk.agent.upgrade_contract import file_hash
from test_case_qa_live_identity_v2 import make_v2, identities


def wired(tmp_path, monkeypatch, *, approved=True):
    h = make_v2(tmp_path/"billing", approved=approved, register=approved)
    cfg = deepcopy(h.config)
    cfg["conversation_path"] = str(tmp_path/"conversations.sqlite3")
    cfg["case_files"] = []
    for index, case in enumerate(h.cases):
        path = tmp_path/f"case-{index}.json"
        path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        cfg["case_files"].append({"transaction_id_local_only":str(index+1000000),
            "path":str(path), "sha256":file_hash(path)})
    questions = tmp_path/"questions.json"
    questions.write_text(json.dumps({"questions":[{"order":i+1,"text":q}
        for i,q in enumerate(["为什么？","有没有正常解释？","还需要什么资料？"])]}),encoding="utf-8")
    cfg["questions_path"] = str(questions)
    cfg["trial_result_path"] = str(tmp_path/"trial-result.json")
    monkeypatch.setattr(runtime,"components",lambda *args:(cfg,h.parent,h.budget))
    return h,cfg


def test_six_round_trial_preserves_historical_identity_and_rejects_rerun(tmp_path,monkeypatch):
    h,cfg = wired(tmp_path,monkeypatch)
    result = runtime.trial(h.config_path,h.approval_path)
    assert len(h.calls) == 6
    assert len(result["cases"]) == 2 and all(len(c["turns"]) == 3 for c in result["cases"])
    assert identities(h) == h.identities
    assert h.budget.usage()["batch_remaining_calls"] == 0
    before = h.path.read_bytes()
    with pytest.raises(ValueError,match="saved_trial_result_exists"):
        runtime.trial(h.config_path,h.approval_path)
    assert h.path.read_bytes() == before and len(h.calls) == 6


def test_pre_send_identity_block_is_saved_without_external_attempt(tmp_path,monkeypatch):
    h,cfg = wired(tmp_path,monkeypatch)
    # Exercise the actual v1 failure path, keeping the valid successor grant.
    h.budget = LiveCaseQABudget(h.path,h.contract,h.config_path,parent_protocol=h.parent,
        approval_path=h.approval_path,transport=h.transport,fixture=True)
    result = runtime.trial(h.config_path,h.approval_path)
    assert result["stopped_on_pre_send_or_checkpoint_error"]
    assert result["error_code"] == "budget_integrity_stop"
    assert result["cases"][0]["turns"] == []
    assert not h.calls and h.budget.usage()["real_deepseek_attempts"] == 0
    assert json.loads(Path(cfg["trial_result_path"]).read_text("utf-8")) == result


def test_first_invalid_output_stops_whole_trial_and_keeps_raw(tmp_path,monkeypatch):
    h,cfg = wired(tmp_path,monkeypatch)
    h.body["choices"][0]["message"]["content"] = '{"wrong":"schema"}'
    result = runtime.trial(h.config_path,h.approval_path)
    assert len(h.calls) == 1 and result["stopped_on_non_answered"]
    assert len(result["cases"]) == 1 and len(result["cases"][0]["turns"]) == 1
    response = result["cases"][0]["turns"][0]
    assert response["turn"]["result"]["status"] == "invalid_response"
    qa = runtime.service(cfg,h.budget)
    raw = qa.raw(result["cases"][0]["conversation_id"],response["turn"]["message_id"])
    assert raw["source"] == "unreviewed_deepseek_output"
    assert b64decode(raw["raw_response"]["content_base64"]) == b'{"wrong":"schema"}'
    assert raw["raw_response"]["complete"] is True


def test_pending_api_start_and_question_do_not_read_credentials_or_send(tmp_path,monkeypatch):
    h,cfg = wired(tmp_path,monkeypatch,approved=False)
    before = h.path.read_bytes()
    with TestClient(runtime.build_app(h.config_path,h.approval_path)) as client:
        health = client.get("/health").json()
        assert health["model_calls_on_start"] == 0
        assert health["execution_revision"] == "case-qa-live-identity-v2"
        one = client.post("/api/v1/case-qa/conversations",
            headers={"Idempotency-Key":"v2_pending_start"},json={"transaction_id":"1000000","batch_id":None}).json()
        assert one["usage"]["budget_status"] == "authorization_required"
        result = client.post(f"/api/v1/case-qa/conversations/{one['conversation_id']}/turns",
            json={"message_id":"a"*32,"parent_turn_id":None,"evidence_version":one["evidence_version"],"message":"为什么？"})
        assert result.json()["error_code"] == "case_qa_not_authorized"
    assert h.path.read_bytes() == before and not h.calls and not h.credential_reads
