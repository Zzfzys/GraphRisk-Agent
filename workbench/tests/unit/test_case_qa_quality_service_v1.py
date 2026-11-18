"""First-response/SQLite/API/history integration for the offline quality candidate."""
from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from extensions.case_qa_v1.api import router
from extensions.case_qa_v1.evidence_contract import check_answer as old_check
from extensions.case_qa_v1.sessions import MAX_CONTEXT_BYTES, SessionError, canonical, SQLiteConversationStore
from extensions.case_qa_quality_v1.contract import ANSWER_RULE
from extensions.case_qa_quality_v1 import service as implementation
from extensions.case_qa_quality_v1.service import QualityService, QualityConversationStore, REVISION
from test_case_qa_sessions_v1 import Harness, answer_text

ROOT = Path(__file__).parents[2]
REAL = ROOT/"reports/case-qa-v1/20261117-live-identity-fix-v2"
SOURCE = ROOT/"reports/case-qa-v1/20261117-live-trial-v1/cases/case-1.json"


class QualityHarness(Harness):
    def restart(self):
        def resolve(transaction, batch):
            return deepcopy(self.cases[transaction])
        def check(alias, binding):
            return next(deepcopy(c) for c in self.cases.values()
                        if c["case_alias"] == alias and c["binding"] == binding)
        def mock(packet):
            self.calls.append(deepcopy(packet))
            if self.provider is not None:
                return self.provider(packet)
            return answer_text("匿名关联本身不足以认定欺诈，需要结合正常使用情况核实。", packet["view"]["evidence"])
        self.service = QualityService(QualityConversationStore(self.path), resolve, check,
            mock_provider=mock, preflight=lambda:self.preflight(), clock=lambda:self.now)
        return self.service


def real_raw():
    receipt=json.loads((REAL/"receipts/6f5af84575b24b66b72e492295ccb06f.json").read_text("utf-8"))
    return receipt["body"]["choices"][0]["message"]["content"]


def test_new_prompt_three_turn_history_and_saved_result_replay(tmp_path):
    h=QualityHarness(tmp_path/"quality.sqlite3")
    one=h.create(); requests=[]
    for question in ["为什么需要关注？","有没有正常解释？","还缺什么资料？"]:
        response,request=h.follow(one,question);requests.append(request)
        assert response["turn"]["result"]["status"]=="answered"
        assert response["turn"]["result"]["check_version"]==REVISION
        assert "diagnostics" in response["turn"]["result"]
        assert h.calls[-1]["system_prompt"].endswith(ANSWER_RULE)
    assert [len(c["view"]["dialogue_background"]) for c in h.calls]==[0,1,2]
    assert all(t["source"]=="unverified_dialogue" for t in h.calls[-1]["view"]["dialogue_background"])
    saved=h.service.get(one["conversation_id"])
    h.restart()
    assert h.service.get(one["conversation_id"])==saved
    replay=h.service.follow(one["conversation_id"],requests[0])
    assert replay["replayed"] and len(h.calls)==3
    assert replay["turn"]["result"]==response_for(saved,0)


def response_for(view,index):
    return view["turns"][index]["result"]


def test_original_real_failure_gets_precise_first_saved_diagnostics_without_repair(tmp_path):
    raw=real_raw()
    source_hash=hashlib.sha256((REAL/"receipts/6f5af84575b24b66b72e492295ccb06f.json").read_bytes()).hexdigest()
    h=QualityHarness(tmp_path/"quality.sqlite3",provider=lambda packet:raw)
    h.cases["1"]=json.loads(SOURCE.read_text("utf-8"))
    one=h.create()
    response,request=h.follow(one,"这笔交易为什么需要关注？")
    result=response["turn"]["result"]
    baseline=old_check(raw,h.cases["1"]["evidence"])
    assert result["status"]==baseline["status"]=="invalid_response"
    assert result["issues"]==baseline["issues"] and result["answer"] is None
    assert result["answer_payload"] is None and result["source"]=="system"
    paths=[d["path"] for d in result["diagnostics"]]
    assert any("fact_refs" in p for p in paths) and any("judgment" in p for p in paths)
    assert "21" in result["notice"] and "12" in result["notice"]
    saved=h.service.get(one["conversation_id"])
    assert saved["turns"][0]["result"]["diagnostics"]==result["diagnostics"]
    old=h.service.raw(one["conversation_id"],request["message_id"])["raw_response"]
    assert b64decode(old["content_base64"]).decode("utf-8")==raw and old["complete"]
    replay=h.service.follow(one["conversation_id"],request)
    assert replay["replayed"] and len(h.calls)==1
    assert hashlib.sha256((REAL/"receipts/6f5af84575b24b66b72e492295ccb06f.json").read_bytes()).hexdigest()==source_hash


def test_old_store_is_refused_before_any_writes(tmp_path):
    path=tmp_path/"old.sqlite3"
    old=Harness(path);old.create()
    before=path.read_bytes()
    with pytest.raises(SessionError,match="session_store_invalid"):
        QualityConversationStore(path)
    assert path.read_bytes()==before and not old.calls


def test_live_budget_or_old_store_cannot_enter_offline_candidate(tmp_path):
    store=QualityConversationStore(tmp_path/"quality.sqlite3")
    with pytest.raises(SessionError,match="provider_unconfigured"):
        QualityService(store,lambda *a:None,lambda *a:None,budget=object())
    with pytest.raises(SessionError,match="session_store_invalid"):
        QualityService(SQLiteConversationStore(tmp_path/"old.sqlite3"),lambda *a:None,lambda *a:None)


def test_diagnostics_failure_keeps_original_verdict_and_raw_without_second_call(tmp_path,monkeypatch):
    h=QualityHarness(tmp_path/"quality.sqlite3");one=h.create()
    def failed(*args):
        raise RuntimeError("diagnostic implementation failure")
    monkeypatch.setattr(implementation,"check_answer",failed)
    response,request=h.follow(one)
    result=response["turn"]["result"]
    assert result["status"]=="answered" and result["diagnostics_unavailable"]
    assert result["model_attempts"]==1 and len(h.calls)==1
    assert h.service.raw(one["conversation_id"],request["message_id"])["raw_response"]["complete"]
    assert h.service.follow(one["conversation_id"],request)["replayed"]
    assert len(h.calls)==1


def test_unknown_stays_paused_and_does_not_become_format_failure(tmp_path):
    def failed(packet):
        raise RuntimeError("local mock failure")
    h=QualityHarness(tmp_path/"quality.sqlite3",provider=failed);one=h.create()
    response,request=h.follow(one)
    result=response["turn"]["result"]
    assert result["status"]=="response_unknown"
    assert not result.get("diagnostics")
    assert h.service.get(one["conversation_id"])["state"]=="response_unknown"
    assert h.service.follow(one["conversation_id"],request)["replayed"] and len(h.calls)==1
    with pytest.raises(SessionError,match="response_unknown_paused"):
        h.follow(one,message_id="a"*32)


def test_longer_prompt_trims_whole_history_pairs_and_records_omissions(tmp_path):
    h=QualityHarness(tmp_path/"quality.sqlite3");one=h.create()
    value=h.service.store.get(one["conversation_id"])
    value["turns"]=[{"message_id":f"{i:032x}","message":"问"*1800,
        "result":{"status":"answered","answer":"答"*3900}} for i in range(1,5)]
    before=deepcopy(value)
    packet,context,size=h.service._packet(value,{"message":"当前问题"})
    assert size==len(canonical(packet).encode("utf-8"))<=MAX_CONTEXT_BYTES
    included=context["included_turn_ids"];omitted=context["omitted_turn_ids"]
    assert len(included)<3 and len(omitted)==4-len(included)
    assert set(included+omitted)=={t["message_id"] for t in value["turns"]}
    assert not set(included)&set(omitted)
    assert all(set(t)=={"turn_id","user","assistant","source"} for t in packet["view"]["dialogue_background"])
    assert value==before


def test_first_invalid_api_response_carries_saved_human_readable_diagnosis(tmp_path):
    raw=real_raw();h=QualityHarness(tmp_path/"quality.sqlite3",provider=lambda packet:raw)
    h.cases["1"]=json.loads(SOURCE.read_text("utf-8"))
    app=FastAPI();app.include_router(router);app.state.case_qa_service=h.service
    with TestClient(app) as client:
        one=client.post("/api/v1/case-qa/conversations",headers={"Idempotency-Key":"quality_api_start"},
            json={"transaction_id":"1","batch_id":None}).json()
        request={"message_id":"a"*32,"parent_turn_id":None,"evidence_version":one["evidence_version"],"message":"为什么？"}
        first=client.post(f"/api/v1/case-qa/conversations/{one['conversation_id']}/turns",json=request).json()
        assert first["mode"]=="offline_simulation"
        assert first["turn"]["result"]["status"]=="invalid_response"
        assert first["turn"]["result"]["diagnostics"]
        again=client.get(f"/api/v1/case-qa/conversations/{one['conversation_id']}").json()
        assert again["turns"][0]["result"]==first["turn"]["result"]
        replay=client.post(f"/api/v1/case-qa/conversations/{one['conversation_id']}/turns",json=request).json()
        assert replay["replayed"] and len(h.calls)==1


def test_inflight_get_and_duplicate_keep_single_mock_call(tmp_path):
    started=Event();release=Event()
    def blocking(packet):
        started.set()
        assert release.wait(10)
        return answer_text("这个关联需要核实。",packet["view"]["evidence"])
    h=QualityHarness(tmp_path/"quality.sqlite3",provider=blocking);one=h.create()
    request={"message_id":"a"*32,"parent_turn_id":None,"evidence_version":one["evidence_version"],"message":"为什么？"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(h.service.follow,one["conversation_id"],request)
        assert started.wait(5)
        view=h.service.get(one["conversation_id"])
        assert view["state"]=="in_flight" and view["turns"][0]["result"]["model_attempts"] is None
        assert h.service.follow(one["conversation_id"],request)["replayed"]
        assert len(h.calls)==1
        release.set()
        result=future.result(timeout=10)
    assert result["turn"]["result"]["status"]=="answered"
    assert result["turn"]["result"]["check_version"]==REVISION and len(h.calls)==1
