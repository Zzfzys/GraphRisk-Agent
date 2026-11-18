"""Step2 deterministic sessions/API tests. All providers are local fakes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from extensions.case_qa_v1.api import router
from extensions.case_qa_v1.behavior import PreflightStop
from extensions.case_qa_v1.sessions import (
    CaseQAService, MAX_ANSWER_CHARS, MAX_CONTEXT_BYTES, MAX_EVIDENCE_BYTES,
    MAX_SESSION_MESSAGES, SESSION_SECONDS, SessionError, SQLiteConversationStore,
    canonical,
)

from extensions.case_qa_v1.evidence_contract import adapt_evidence

VERSION = "a" * 64
BINDING = "b" * 64


def source_bundle(alias="synthetic-1"):
    return {"schema_version":"investigation-facts-v1", "case_alias":("2" if alias=="synthetic-2" else "1")*32,
            "evidence_version":VERSION,"policy_as_of":"2026-11-16",
            "facts":[{"fact_id":"F-"+"1"*24,"metric":"history_transaction_count","subject":"heuristic_identity_history",
                      "unit":"transactions","window_seconds":3600,"temporal_scope":"strictly_before_target",
                      "source_tool":"get_transaction_features","query_ref":"c"*64,"source_version":"d"*64,
                      "model_version":None,"availability":"available","value":7,"missing_reason":None}],
            "tool_outcomes":[{"tool":"get_transaction_features","query_ref":"c"*64,"status":"ok","error_code":None}],
            "policies":[],"limitations":["synthetic fixture only"]}


def case(alias="synthetic-1"):
    evidence=adapt_evidence(source_bundle(alias))
    return {"case_alias":evidence["case_alias"],"batch_revision":"day-1-revision-1","binding":BINDING,"evidence":evidence}


def answer_text(text, evidence):
    return json.dumps({"version":"case-qa-answer-v1","case_alias":evidence["case_alias"],
                      "evidence_version":evidence["evidence_version"],"fact_refs":[],
                      "judgment":[{"text":text,"refs":[]}],"hypotheses":[],"gaps":[]},ensure_ascii=True)


class Harness:
    def __init__(self, path, provider=None):
        self.path = path
        self.cases = {"1": case(), "2": case("synthetic-2")}
        self.calls = []
        self.now = 1000
        self.provider = provider
        self.preflight = lambda: None
        self.service = self.restart()

    def restart(self):
        def resolve(transaction, batch):
            assert batch in (None, "day-1")
            return deepcopy(self.cases[transaction])
        def check(alias, binding):
            return next(deepcopy(c) for c in self.cases.values() if c["case_alias"] == alias)
        def mock(packet):
            self.calls.append(deepcopy(packet))
            output=self.provider(packet) if self.provider else "模拟解释：这个关联也可能有正常原因。"
            return answer_text(output,packet["view"]["evidence"]) if type(output) is str else output
        self.service = CaseQAService(SQLiteConversationStore(self.path), resolve, check,
                                    mock_provider=mock, preflight=lambda: self.preflight(), clock=lambda: self.now)
        return self.service

    def create(self, transaction="1", key="create_key_1"):
        return self.service.create({"transaction_id": transaction, "batch_id": "day-1"}, key)

    def follow(self, session, message="为什么？", message_id=None, **changes):
        view = self.service.get(session["conversation_id"])
        request = {"message_id": message_id or f"{len(view['turns'])+1:032x}", "message": message,
                   "parent_turn_id": view["turns"][-1]["message_id"] if view["turns"] else None,
                   "evidence_version": view["evidence_version"], **changes}
        return self.service.follow(session["conversation_id"], request), request


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path / "conversations.sqlite3")


def expect(code, fn):
    with pytest.raises(SessionError) as caught:
        fn()
    assert caught.value.code == code


def test_three_turns_send_original_question_history_and_facts(h):
    session = h.create()
    messages = ["为什么这笔交易高风险？", "这个关系是什么意思？", "那有没有正常解释？"]
    for n, message in enumerate(messages):
        result, _ = h.follow(session, message)
        packet = h.calls[-1]
        assert packet["view"]["question"] == message
        history = packet["view"]["dialogue_background"]
        assert [t["user"] for t in history] == messages[:n]
        assert all(t["source"] == "unverified_dialogue" for t in history)
        assert "不能作为数据事实" in packet["system_prompt"]
        assert packet["view"]["evidence"] == h.cases["1"]["evidence"]
        assert result["turn"]["result"]["source"] == "mock_model"
        assert result["turn"]["result"]["model_attempts"] == 1
        assert result["turn"]["result"]["tool_requests"] == 0
    assert len(h.calls) == 3
    assert len(h.service.get(session["conversation_id"])["turns"]) == 3


def test_create_is_idempotent_and_does_not_call_provider(h):
    first = h.create()
    assert h.create() == first
    assert h.calls == [] and first["state"] == "ready"
    assert "case" not in first and "binding" not in first
    expect("idempotency_conflict", lambda: h.create("2"))


@pytest.mark.parametrize("extra", ["history", "evidence", "provider", "path", "authorized", "operation"])
def test_client_cannot_inject_session_or_follow_fields(h, extra):
    expect("invalid_request", lambda: h.service.create({"transaction_id": "1", "batch_id": "day-1", extra: "bad"}, "valid_key"))
    session = h.create()
    request = {"message_id": "1" * 32, "parent_turn_id": None, "evidence_version": VERSION, "message": "为什么？", extra: "bad"}
    expect("invalid_request", lambda: h.service.follow(session["conversation_id"], request))
    assert not h.calls


def test_completed_message_replays_after_new_turn_and_restart(h):
    session = h.create()
    first, request = h.follow(session)
    h.follow(session, "然后呢？")
    h.restart()
    replay = h.service.follow(session["conversation_id"], request)
    assert replay["replayed"] and replay["turn"] == first["turn"]
    assert len(h.calls) == 2
    assert len(h.service.get(session["conversation_id"])["turns"]) == 2


@pytest.mark.parametrize("change", [{"message": "另一个问题"}, {"parent_turn_id": "f"*32}, {"evidence_version": "c"*64}])
def test_message_id_cannot_change_payload(h, change):
    session = h.create()
    _, request = h.follow(session)
    expect("idempotency_conflict", lambda: h.service.follow(session["conversation_id"], {**request, **change}))
    assert len(h.calls) == 1


def test_different_cases_and_sessions_have_no_shared_history(h):
    a = h.create()
    h.follow(a, "case one secret wording")
    same_case = h.create(key="create_same_case")
    b = h.create("2", "create_key_2")
    for session in [same_case, b]:
        h.follow(session)
        assert h.calls[-1]["view"]["dialogue_background"] == []
        assert h.calls[-1]["view"]["evidence"]["case_alias"] == session["case_alias"]
        assert "case one secret wording" not in canonical(h.calls[-1])


@pytest.mark.parametrize("field,value", [("batch_revision", "revision-2"), ("binding", "c"*64)])
def test_server_case_binding_change_blocks_before_send(h, field, value):
    session = h.create()
    h.cases["1"][field] = value
    expect("evidence_changed", lambda: h.service.follow(session["conversation_id"], {
        "message_id": "1"*32, "parent_turn_id": None, "evidence_version": VERSION, "message": "为什么？"}))
    assert not h.calls


@pytest.mark.parametrize("change", [{"evidence_version": "c"*64}, {"source_limitations": ["changed synthetic source"]}])
def test_server_evidence_content_or_version_change_blocks(h, change):
    session = h.create()
    h.cases["1"]["evidence"].update(change)
    evidence=h.cases["1"]["evidence"]
    from extensions.case_qa_v1.evidence_contract import sha
    evidence["view_sha256"]=sha({k:v for k,v in evidence.items() if k!="view_sha256"})
    expect("evidence_changed", lambda: h.follow(session))
    assert not h.calls


def test_wrong_client_version_or_parent_stops_before_claim(h):
    session = h.create()
    expect("evidence_changed", lambda: h.follow(session, evidence_version="c"*64))
    expect("parent_turn_changed", lambda: h.follow(session, parent_turn_id="f"*32))
    assert not h.calls and h.service.get(session["conversation_id"])["turns"] == []


def test_expiry_inclusive_and_safe_missing_session(h):
    session = h.create()
    h.now += SESSION_SECONDS - 1
    assert h.service.get(session["conversation_id"])
    h.now += 1
    assert h.service.get(session["conversation_id"])["validity_error"] == "conversation_expired"
    expect("conversation_expired", lambda: h.service.follow(session["conversation_id"], {
        "message_id":"1"*32,"parent_turn_id":None,"evidence_version":VERSION,"message":"why"}))
    expect("conversation_not_found", lambda: h.service.get("0"*32))
    assert not h.calls


def test_history_is_latest_three_complete_successful_pairs_with_visible_omissions(h):
    session = h.create()
    ids = []
    for i in range(6):
        result, _ = h.follow(session, "继续解释这个关系")
        context = result["turn"]["context"]
        assert context["included_turn_ids"] == ids[-3:]
        assert context["omitted_turn_ids"] == ids[:-3] if len(ids)>3 else context["omitted_turn_ids"] == []
        ids.append(result["turn"]["message_id"])
    assert len(h.service.get(session["conversation_id"])["turns"]) == 6
    assert len(h.calls[-1]["view"]["dialogue_background"]) == 3
    assert "未调用模型摘要" in h.calls[-1]["view"]["context"]["notice"]


def test_utf8_size_trims_whole_old_pairs_but_keeps_storage_and_evidence(h):
    h.provider = lambda packet: "答" * (MAX_ANSWER_CHARS-len("模型解释："))
    session = h.create()
    for i in range(5):
        result, _ = h.follow(session, "问" * 2000)
        packet = h.calls[-1]
        assert len(canonical(packet).encode("utf-8")) == result["turn"]["context_bytes"] <= MAX_CONTEXT_BYTES
        assert packet["view"]["evidence"] == h.cases["1"]["evidence"]
        assert packet["view"]["question"] == "问" * 2000
    assert result["turn"]["context"]["omitted_turn_ids"]
    assert len(h.service.get(session["conversation_id"])["turns"]) == 5
    assert all(set(t) == {"turn_id","user","assistant","source"} for t in h.calls[-1]["view"]["dialogue_background"])


@pytest.mark.parametrize("difference,ok", [(0, True), (1,False)])
def test_evidence_limit_exact_bytes(h, difference, ok):
    evidence = h.cases["1"]["evidence"]
    evidence["padding"] = ""
    evidence["padding"] = "x" * (MAX_EVIDENCE_BYTES - len(canonical(evidence).encode("utf-8")) + difference)
    from extensions.case_qa_v1.evidence_contract import sha
    evidence["view_sha256"]=sha({k:v for k,v in evidence.items() if k!="view_sha256"})
    assert len(canonical(evidence).encode("utf-8")) == MAX_EVIDENCE_BYTES + difference
    if ok:
        h.follow(h.create())
        assert len(h.calls) == 1
    else:
        expect("evidence_unavailable", h.create)
        assert not h.calls


def test_total_context_exact_boundary_and_one_byte_over(h, monkeypatch):
    import extensions.case_qa_v1.sessions as module
    session = h.create()
    request = {"message":"q", "message_id":"1"*32,"parent_turn_id":None,"evidence_version":VERSION}
    packet, _, size = h.service._packet(h.service.store.get(session["conversation_id"]), request)
    monkeypatch.setattr(module, "MAX_CONTEXT_BYTES", size)
    # Changing the limit's digits changes the serialized metadata; solve the small fixed point.
    for _ in range(3):
        size = len(canonical({**packet,"view":{**packet["view"],"context":{**packet["view"]["context"],"max_bytes":module.MAX_CONTEXT_BYTES}}}).encode("utf-8"))
        monkeypatch.setattr(module, "MAX_CONTEXT_BYTES", size)
    result = h.service.follow(session["conversation_id"], request)
    assert result["turn"]["context_bytes"] == module.MAX_CONTEXT_BYTES
    second = h.create(key="second_session")
    monkeypatch.setattr(module, "MAX_CONTEXT_BYTES", size-1)
    expect("context_too_large", lambda: h.service.follow(second["conversation_id"], request))
    assert len(h.calls) == 1


@pytest.mark.parametrize("answer", [None, "", {"tool":"lookup"}, "答"*(MAX_ANSWER_CHARS+1)], ids=["none","empty","object","too_long"])
def test_invalid_responses_not_in_next_history(h, answer):
    h.provider = lambda packet: answer
    session = h.create()
    first, request = h.follow(session)
    assert first["turn"]["result"]["status"] == "invalid_response"
    assert first["turn"]["result"]["model_attempts"] == 1
    assert h.service.follow(session["conversation_id"], request)["replayed"]
    h.provider = None
    h.follow(session)
    assert h.calls[-1]["view"]["dialogue_background"] == []
    assert len(h.calls) == 2


def test_answer_limit_inclusive(h):
    h.provider = lambda packet: "答"*(MAX_ANSWER_CHARS-len("模型解释："))
    result, _ = h.follow(h.create())
    assert result["turn"]["result"]["status"] == "answered"


@pytest.mark.parametrize("code", ["case_qa_not_authorized", "quota_exhausted", "budget_exhausted", "evidence_expired"])
def test_preflight_does_not_claim_or_send(h, code):
    session = h.create()
    def stop():
        raise PreflightStop(code)
    h.preflight = stop
    expect(code, lambda: h.follow(session))
    assert not h.calls and not h.service.get(session["conversation_id"])["turns"]


def test_unknown_persists_and_blocks_all_new_cases_but_replay_is_readonly(h):
    def timeout(packet):
        raise TimeoutError("private internal exception")
    h.provider = timeout
    session = h.create()
    result, request = h.follow(session)
    assert result["turn"]["result"]["status"] == "response_unknown"
    h.restart()
    assert h.service.follow(session["conversation_id"], request)["replayed"]
    other = h.create("2", "other_case")
    expect("response_unknown_paused", lambda: h.follow(other))
    expect("response_unknown_paused", lambda: h.follow(session))
    assert len(h.calls) == 1
    assert "private internal exception" not in canonical(h.service.get(session["conversation_id"]))


def test_parallel_double_click_and_same_case_session_cannot_claim_again(h):
    entered, release = Event(), Event()
    def provider(packet):
        entered.set()
        assert release.wait(10)
        return "mock answer"
    h.provider = provider
    session = h.create()
    other = h.create(key="same_case_two")
    request = {"message":"为什么？","message_id":"1"*32,"parent_turn_id":None,"evidence_version":VERSION}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(h.service.follow, session["conversation_id"], request)
        assert entered.wait(10)
        independent = h.restart()
        replay = independent.follow(session["conversation_id"], request)
        assert replay["replayed"] and replay["turn"]["result"]["status"] == "in_flight"
        assert replay["turn"]["result"]["model_attempts"] is None
        expect("turn_in_progress", lambda: independent.follow(session["conversation_id"], {**request,"message_id":"2"*32}))
        expect("turn_in_progress", lambda: independent.follow(other["conversation_id"], request))
        release.set()
        assert future.result()["turn"]["result"]["status"] == "answered"
    assert len(h.calls) == 1


def test_process_killed_after_claim_cannot_retry_with_new_service(h, tmp_path):
    session = h.create()
    request = {"message":"why","message_id":"1"*32,"parent_turn_id":None,"evidence_version":VERSION}
    script = """import json,os,sys
from extensions.case_qa_v1.sessions import CaseQAService,SQLiteConversationStore
case=json.loads(sys.argv[3])
s=CaseQAService(SQLiteConversationStore(sys.argv[1]),lambda *a:case,lambda *a:case,mock_provider=lambda p:os._exit(17),clock=lambda:1000)
s.follow(sys.argv[2],json.loads(sys.argv[4]))
"""
    process = subprocess.run([sys.executable,"-c",script,str(h.path),session["conversation_id"],json.dumps(h.cases["1"]),json.dumps(request)],
                             cwd=Path(__file__).parents[2], capture_output=True,timeout=15)
    assert process.returncode == 17, process.stderr.decode(errors="replace")
    h.restart()
    replay = h.service.follow(session["conversation_id"], request)
    assert replay["replayed"] and replay["turn"]["result"]["status"] == "in_flight"
    expect("turn_in_progress", lambda: h.follow(session))
    other = h.create(key="after_process_death")
    expect("turn_in_progress", lambda: h.follow(other))
    assert not h.calls


def test_completion_checkpoint_failure_retains_claim_and_does_not_resend(h, monkeypatch):
    session = h.create()
    original = h.service.store.mutate
    calls = []
    def mutation(identifier, fn):
        calls.append(None)
        if len(calls) == 3:
            raise OSError("private storage error")
        return original(identifier, fn)
    monkeypatch.setattr(h.service.store, "mutate", mutation)
    expect("checkpoint_recovery_required", lambda: h.follow(session))
    assert len(h.calls) == 1
    h.restart()
    saved = h.service.get(session["conversation_id"])
    assert saved["state"] == "in_flight"
    request = {key:saved["turns"][0][key] for key in ("message_id","parent_turn_id","evidence_version","message")}
    assert h.service.follow(session["conversation_id"], request)["replayed"]
    assert len(h.calls) == 1


def test_session_storage_cap_not_bypassed_by_replay(h):
    session = h.create()
    for _ in range(MAX_SESSION_MESSAGES):
        _, request = h.follow(session)
    assert h.service.follow(session["conversation_id"], request)["replayed"]
    expect("session_message_limit", lambda: h.follow(session))
    assert len(h.calls) == MAX_SESSION_MESSAGES


def test_store_rejects_foreign_database_without_modifying_bytes(tmp_path):
    path=tmp_path / "not_qa.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE protected (value TEXT)")
        db.execute("INSERT INTO protected VALUES ('retained')")
    before = path.read_bytes()
    expect("session_store_invalid", lambda: SQLiteConversationStore(path))
    assert path.read_bytes() == before


def test_store_detects_changed_content(h):
    session = h.create()
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE qa_conversations_v1 SET content='{}'")
    expect("session_store_invalid", lambda: h.service.get(session["conversation_id"]))


def test_api_three_turns_and_get_are_offline(h):
    app=FastAPI()
    app.include_router(router)
    app.state.case_qa_service=h.service
    base="/api/v1/case-qa/conversations"
    with TestClient(app) as client:
        created=client.post(base,json={"transaction_id":"1","batch_id":"day-1"},headers={"Idempotency-Key":"create_api_key"})
        assert created.status_code == 201
        identifier=created.json()["conversation_id"]
        parent=None
        for i in range(3):
            request={"message_id":f"{i+1:032x}","parent_turn_id":parent,"evidence_version":VERSION,"message":["为什么高风险？","它是什么意思？","有正常解释吗？"][i]}
            response=client.post(f"{base}/{identifier}/turns",json=request)
            assert response.status_code == 200 and response.json()["mode"] == "offline_simulation"
            parent=request["message_id"]
            assert client.post(f"{base}/{identifier}/turns",json=request).json()["replayed"]
            assert len(client.get(f"{base}/{identifier}").json()["turns"]) == i+1
        assert len(h.calls) == 3
        assert all(len(p["view"]["dialogue_background"])==n for n,p in enumerate(h.calls))


def test_api_bad_input_and_errors_are_system_not_model(h):
    app=FastAPI()
    app.include_router(router)
    app.state.case_qa_service=h.service
    base="/api/v1/case-qa/conversations"
    with TestClient(app) as client:
        response=client.post(base,json={"transaction_id":"1","batch_id":None,"path":"private/client/path"},headers={"Idempotency-Key":"valid_key"})
        assert response.status_code==422
        assert response.json()["source"]=="system"
        assert "private/client/path" not in response.text
        assert client.get(base+"/"+"0"*32).status_code==404
        assert client.get(base+"/bad").status_code==422
        del app.state.case_qa_service
        assert client.get(base+"/"+"0"*32).status_code==503
    assert not h.calls


@pytest.mark.parametrize("content", [
    '"private-invalid-shape"', '["private-invalid-shape"]', '{"secret":"private-invalid-shape",',
    'null', '1', ' ' * 32769 + 'private-invalid-shape',
], ids=['string','array','malformed','null','number','oversized'])
def test_api_parse_errors_do_not_echo_private_input(h, content):
    app=FastAPI()
    app.include_router(router)
    app.state.case_qa_service=h.service
    with TestClient(app, raise_server_exceptions=False) as client:
        response=client.post("/api/v1/case-qa/conversations",content=content,headers={"Content-Type":"application/json"})
    assert response.status_code==422
    assert response.json()["source"]=="system"
    assert "private-invalid-shape" not in response.text
    assert not h.calls


def test_invalid_utf8_question_or_answer_is_controlled(h):
    session=h.create()
    expect("invalid_question", lambda: h.follow(session, "\ud800"))
    assert not h.calls
    h.provider=lambda p:"\ud800"
    result,_=h.follow(session)
    assert result["turn"]["result"]["status"]=="invalid_response"
    assert len(h.calls)==1


def test_stale_results_and_replay_stay_readable_without_resending(h):
    session=h.create()
    first,request=h.follow(session)
    h.now += SESSION_SECONDS
    value=h.service.get(session["conversation_id"])
    assert value["validity_error"]=="conversation_expired"
    assert value["requires_new_conversation"]
    replay=h.service.follow(session["conversation_id"],request)
    assert replay["replayed"] and replay["turn"]==first["turn"]
    expect("conversation_expired",lambda:h.follow(session))
    h.now -= SESSION_SECONDS
    h.cases["1"]["batch_revision"]="new revision"
    assert h.service.get(session["conversation_id"])["validity_error"]=="evidence_changed"
    assert h.service.follow(session["conversation_id"],request)["replayed"]
    expect("evidence_changed",lambda:h.follow(session))
    assert len(h.calls)==1


def test_uncertain_inflight_claim_blocks_other_cases_as_well(h, monkeypatch):
    session=h.create()
    other=h.create("2","other_case")
    original=h.service.store.mutate
    calls=[]
    def mutation(identifier,fn):
        calls.append(None)
        if len(calls)==3:
            raise OSError("fixture finish failure")
        return original(identifier,fn)
    monkeypatch.setattr(h.service.store,"mutate",mutation)
    expect("checkpoint_recovery_required",lambda:h.follow(session))
    h.restart()
    expect("turn_in_progress",lambda:h.follow(other))
    assert len(h.calls)==1



def test_independent_process_and_parent_compete_for_same_durable_slot(h, tmp_path):
    import time
    session=h.create()
    other=h.create("2","other_process_case")
    ready,release=tmp_path/"provider-entered",tmp_path/"provider-release"
    request={"message":"why","message_id":"1"*32,"parent_turn_id":None,"evidence_version":VERSION}
    script="""import json,sys,time
from pathlib import Path
from extensions.case_qa_v1.sessions import CaseQAService,SQLiteConversationStore
case=json.loads(sys.argv[3]); ready=Path(sys.argv[5]); release=Path(sys.argv[6])
def provider(packet):
    ready.write_text('entered')
    deadline=time.monotonic()+10
    while not release.exists():
        if time.monotonic()>deadline: raise TimeoutError()
        time.sleep(.01)
    return json.dumps({'version':'case-qa-answer-v1','case_alias':case['evidence']['case_alias'],'evidence_version':case['evidence']['evidence_version'],'fact_refs':[],'judgment':[{'text':'child mock answer','refs':[]}],'hypotheses':[],'gaps':[]})
s=CaseQAService(SQLiteConversationStore(sys.argv[1]),lambda *a:case,lambda *a:case,mock_provider=provider,clock=lambda:1000)
s.follow(sys.argv[2],json.loads(sys.argv[4]))
"""
    process=subprocess.Popen([sys.executable,"-c",script,str(h.path),session["conversation_id"],json.dumps(h.cases["1"]),json.dumps(request),str(ready),str(release)],
                             cwd=Path(__file__).parents[2],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+10
        while not ready.exists() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(.01)
        assert ready.exists()
        assert h.service.follow(session["conversation_id"],request)["replayed"]
        expect("turn_in_progress",lambda:h.follow(other))
        release.write_text("release")
        stdout,stderr=process.communicate(timeout=10)
        assert process.returncode==0,stderr.decode(errors="replace")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    saved=h.restart().get(session["conversation_id"])
    assert saved["turns"][0]["result"]["answer"]=="模型解释：child mock answer"
    assert saved["turns"][0]["result"]["model_attempts"]==1
    assert not h.calls  # Parent could not invoke its provider.


def test_follow_api_rejects_surrogate_and_oversized_message_without_claim(h):
    app=FastAPI()
    app.include_router(router)
    app.state.case_qa_service=h.service
    session=h.create()
    url=f"/api/v1/case-qa/conversations/{session['conversation_id']}/turns"
    with TestClient(app) as client:
        for message in ["\ud800", "问"*2001]:
            body={"message":message,"message_id":"1"*32,"parent_turn_id":None,"evidence_version":VERSION}
            response=client.post(url,content=json.dumps(body),headers={"Content-Type":"application/json"})
            assert response.status_code==422 and response.json()["source"]=="system"
        assert not h.service.get(session["conversation_id"])["turns"]
    assert not h.calls



def test_api_event_loop_can_read_inflight_while_provider_waits(h):
    import asyncio
    import httpx
    entered,release=Event(),Event()
    def provider(packet):
        entered.set()
        release.wait(3)
        return "answer after wait"
    h.provider=provider
    app=FastAPI()
    app.include_router(router)
    app.state.case_qa_service=h.service
    session=h.create()
    base=f"/api/v1/case-qa/conversations/{session['conversation_id']}"
    body={"message":"why","message_id":"1"*32,"parent_turn_id":None,"evidence_version":VERSION}
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://offline") as client:
            task=asyncio.create_task(client.post(base+"/turns",json=body))
            try:
                assert await asyncio.to_thread(entered.wait,5)
                result=await asyncio.wait_for(client.get(base),timeout=2)
                assert result.status_code==200
                assert result.json()["state"]=="in_flight"
                replay=await asyncio.wait_for(client.post(base+"/turns",json=body),timeout=2)
                assert replay.json()["replayed"]
                assert replay.json()["turn"]["result"]["status"]=="in_flight"
            finally:
                release.set()
                response=await task
            assert response.json()["turn"]["result"]["status"]=="answered"
    asyncio.run(scenario())
    assert len(h.calls)==1



def test_api_deep_json_is_fixed_error_without_echo(h):
    app=FastAPI()
    app.include_router(router)
    app.state.case_qa_service=h.service
    with TestClient(app,raise_server_exceptions=False) as client:
        response=client.post("/api/v1/case-qa/conversations",content='['*1500+'"private-depth-marker"'+']'*1500)
    assert response.status_code==422
    assert response.json()["source"]=="system"
    assert "private-depth-marker" not in response.text
    assert not h.calls


def test_corrupt_denormalized_state_cannot_bypass_unknown_guard(h):
    def timeout(packet):
        raise TimeoutError()
    h.provider=timeout
    session=h.create()
    other=h.create("2","another_case")
    h.follow(session)
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE qa_conversations_v1 SET state='ready' WHERE conversation_id=?",(session["conversation_id"],))
    expect("session_store_invalid",lambda:h.follow(other))
    assert len(h.calls)==1
