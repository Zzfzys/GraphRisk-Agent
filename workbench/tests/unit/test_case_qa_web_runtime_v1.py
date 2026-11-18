"""Real web routing with synthetic old requests and MockTransport; no paid calls."""
from copy import deepcopy
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from extensions.case_qa_web_v1 import runtime
from graphrisk.agent.upgrade_contract import file_hash
from test_case_qa_web_gate_v1 import make_gate, write


def app_fixture(tmp_path, monkeypatch, *, approved=True):
    h = make_gate(tmp_path / "billing", approved=approved)
    cfg = deepcopy(h.config)
    cfg["case_files"] = []
    for index, case in enumerate(h.cases):
        path = write(tmp_path / f"case-{index}.json", case)
        cfg["case_files"].append({"transaction_id_local_only":str(1000000+index),
            "path":str(path), "sha256":file_hash(path)})
    old = runtime.service(cfg, h.budget)
    archived = old.create({"transaction_id":"1000001", "batch_id":None}, "archive_case_create")
    trial_path = write(tmp_path / "archive-trial.json", {"cases":[{"conversation_id":archived["conversation_id"],
        "case_alias":h.cases[1]["case_alias"]}]})
    cfg["trial_result_path"] = str(trial_path)
    h.manifest.update(conversation_path=str(h.manifest_path.parent / "conversations.sqlite3"),
        archive_conversation_id=archived["conversation_id"], archive_trial_path=str(trial_path),
        archive_trial_sha256=file_hash(trial_path), audit_path=str(h.manifest_path.parent / "requests.jsonl"))
    write(h.manifest_path, h.manifest)
    h.resume["manifest_sha256"] = file_hash(h.manifest_path)
    if approved:
        write(h.resume_path, h.resume)
    monkeypatch.setattr(runtime, "components", lambda *args:(cfg,h.parent,h.budget))
    return h, cfg, archived, runtime.build_app(h.manifest_path)


def create(client):
    response = client.post("/api/v1/case-qa/conversations", headers={"Idempotency-Key":"new_web_create"},
        json={"transaction_id":"1000000", "batch_id":None})
    assert response.status_code == 201
    return response.json()


def body(value):
    return {"message_id":"f"*32, "parent_turn_id":None, "evidence_version":value["evidence_version"],
        "message":"这些线索能有哪些正常解释？请结合当前交易证据回答。"}


def test_paused_web_reads_archive_and_rejects_repeated_questions_without_call_or_write(tmp_path, monkeypatch):
    h,cfg,archived,app = app_fixture(tmp_path,monkeypatch,approved=False)
    ledger = h.path.read_bytes()
    archive = Path(cfg["conversation_path"]).read_bytes()
    with TestClient(app) as client:
        assert client.get("/health").json()["model_calls_on_start"] == 0
        status = client.get("/api/v1/case-qa/web-status").json()
        assert status["reason"] == "approval_required" and status["aggregate_requests"] == 5
        assert client.get("/api/v1/case-qa/conversations/"+archived["conversation_id"]).json()["send_block_reason"] == "qa_disabled"
        value = create(client)
        for _ in range(2):
            response = client.post("/api/v1/case-qa/conversations/"+value["conversation_id"]+"/turns",json=body(value))
            assert response.status_code == 409 and response.json()["error_code"] == "qa_disabled"
        assert not h.calls and not Path(h.manifest["audit_path"]).exists()
    assert h.path.read_bytes() == ledger and Path(cfg["conversation_path"]).read_bytes() == archive


def test_natural_question_uses_compact_contract_once_then_replay_and_quota_block(tmp_path, monkeypatch):
    h,cfg,archived,app = app_fixture(tmp_path,monkeypatch)
    archive = Path(cfg["conversation_path"]).read_bytes()
    with TestClient(app) as client:
        assert client.get("/api/v1/case-qa/web-status").json()["can_send"]
        value = create(client)
        path = "/api/v1/case-qa/conversations/"+value["conversation_id"]+"/turns"
        response = client.post(path,json=body(value))
        assert response.status_code == 200 and response.json()["turn"]["result"]["status"] == "answered"
        assert len(h.calls) == 1
        sent = json.loads(h.calls[0].content) if hasattr(h.calls[0],"content") else h.calls[0]
        assert body(value)["message"] in sent["messages"][-1]["content"]
        assert client.post(path,json=body(value)).json()["replayed"]
        assert len(h.calls) == 1
        next_body = {**body(value), "message_id":"e"*32, "parent_turn_id":"f"*32}
        assert client.post(path,json=next_body).json()["error_code"] == "qa_disabled"
        assert client.get("/api/v1/case-qa/web-status").json()["reason"] == "quota_exhausted"
        assert client.post("/api/v1/case-qa/conversations/"+archived["conversation_id"]+"/turns",json=next_body).json()["error_code"] == "qa_disabled"
    assert len(h.calls) == 1 and Path(cfg["conversation_path"]).read_bytes() == archive
    audit = [json.loads(line) for line in Path(h.manifest["audit_path"]).read_text().splitlines()]
    assert [r["event"] for r in audit] == ["before_follow", "saved_result"]
    assert all(body(value)["message"] not in json.dumps(r,ensure_ascii=False) for r in audit)


def test_modified_archive_descriptor_prevents_service_start(tmp_path, monkeypatch):
    h,_,_,_ = app_fixture(tmp_path,monkeypatch)
    Path(h.manifest["archive_trial_path"]).write_text("{}",encoding="utf-8")
    with pytest.raises(ValueError,match="archive_trial_changed"):
        runtime.build_app(h.manifest_path)
    assert not h.calls


def test_frozen_database_cannot_be_selected_as_new_writable_store(tmp_path, monkeypatch):
    h,cfg,_,_ = app_fixture(tmp_path,monkeypatch)
    before = Path(cfg["conversation_path"]).read_bytes()
    h.manifest["conversation_path"] = cfg["conversation_path"]
    write(h.manifest_path,h.manifest)
    with pytest.raises(ValueError,match="web_output_path_invalid"):
        runtime.build_app(h.manifest_path)
    assert Path(cfg["conversation_path"]).read_bytes() == before and not h.calls
