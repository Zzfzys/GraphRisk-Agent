"""Compact first-verdict sessions; local providers and temporary stores only."""
from base64 import b64decode
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from extensions.case_qa_compact_v1.contract import ANSWER_RULE, VERSION, CHECK_VERSION
from extensions.case_qa_compact_v1.service import (
    CompactConversationStore, CompactService, REVISION, ANSWER_REVISION, PROMPT_REVISION,
)
from extensions.case_qa_live_v1.api import router
from extensions.case_qa_live_v3.service import LiveQualityConversationStore
from extensions.case_qa_short_v1.service import ShortConversationStore
from extensions.case_qa_v1.behavior import SYSTEM_PROMPT
from extensions.case_qa_v1.evidence_contract import MAX_RAW_BYTES
from extensions.case_qa_v1.sessions import (
    CONTEXT_RULE, MAX_CONTEXT_BYTES, SQLiteConversationStore, SessionError, canonical,
)
from test_case_qa_sessions_v1 import case


def answer(evidence, text="这个线索需要结合使用背景核实。", refs=None, kind="judgment"):
    return {"version":VERSION,"case_alias":evidence["case_alias"],
            "evidence_version":evidence["evidence_version"],
            "paragraphs":[{"kind":kind,"text":text,"refs":["F1"] if refs is None else refs}]}


class RecordingStore(CompactConversationStore):
    def __init__(self, path):
        self.transitions = []
        super().__init__(path)

    def mutate(self, identifier, fn):
        returned = super().mutate(identifier, fn)
        self.transitions.append(self.get(identifier))
        return returned


class FakeBudget:
    """Live protocol double; no ledger, credentials, network or actual fees."""
    def __init__(self):
        self.calls, self.reservations = [], []
        self.error_code = None
        self.response = lambda packet: json.dumps(answer(packet["view"]["evidence"]),ensure_ascii=False)

    def usage(self, value):
        return {"scope":"live_case_qa","provider":"deepseek","real_deepseek_attempts":len(self.calls),
                "mock_attempts_confirmed":0,"unresolved_slots":0,"batch_unresolved_slots":0,
                "session_remaining_calls":3-len(self.calls),"case_remaining_calls":3-len(self.calls),
                "batch_remaining_calls":3-len(self.calls),"cost_estimate_cny":"0.000000",
                "unsettled_hold_cny":"0.000000","budget_status":"shared_ledger",
                "authorization":"approved_case_qa","block_reason":None}

    def reserve(self, value, request, packet):
        self.reservations.append(deepcopy(packet))
        return {"attempt_id":"a"*32,"state":"reserved","reserved_micro_cny":1,
                "transport_attempts":0,"cost_estimate_micro_cny":None,"hold_micro_cny":1}

    def generate(self, ticket, packet):
        self.calls.append(deepcopy(packet))
        return {"raw":self.response(packet),"error_code":self.error_code,
                "audit":{**ticket,"state":"settled","transport_attempts":1,
                         "cost_estimate_micro_cny":0,"hold_micro_cny":0}}


class Harness:
    def __init__(self, path, budget=None, store_type=RecordingStore):
        self.path, self.budget, self.store_type = path, budget, store_type
        self.snapshot, self.calls = case(), []
        self.response = lambda packet: json.dumps(answer(packet["view"]["evidence"]),ensure_ascii=False)
        self.service = self.restart()

    def resolve(self, transaction, batch):
        assert transaction == "1" and batch in (None,"day-1")
        return deepcopy(self.snapshot)

    def check(self, alias, binding):
        assert (alias,binding) == (self.snapshot["case_alias"],self.snapshot["binding"])
        return deepcopy(self.snapshot)

    def provider(self, packet):
        self.calls.append(deepcopy(packet))
        return self.response(packet)

    def restart(self):
        self.service = CompactService(self.store_type(self.path),self.resolve,self.check,
            budget=self.budget,mock_provider=self.provider,clock=lambda:1000,
            id_aliases={"3559456":self.snapshot["case_alias"]})
        return self.service

    def create(self):
        return self.service.create({"transaction_id":"1","batch_id":"day-1"},"compact-create-key")

    def request(self, created, message="为什么需要关注？"):
        current = self.service.get(created["conversation_id"])
        return {"message_id":f"{len(current['turns'])+1:032x}","message":message,
                "parent_turn_id":current["turns"][-1]["message_id"] if current["turns"] else None,
                "evidence_version":current["evidence_version"]}

    def follow(self, created, message="为什么需要关注？"):
        request = self.request(created,message)
        return self.service.follow(created["conversation_id"],request),request


def test_first_finish_uses_compact_checker_and_keeps_raw_before_atomic_verdict(tmp_path):
    h=Harness(tmp_path/"compact.sqlite3");created=h.create()
    response,request=h.follow(created);result=response["turn"]["result"]
    assert result["status"] == "answered" and result["check_version"] == CHECK_VERSION
    assert result["diagnostics"] == [] and result["style_notes"] == []
    assert result["answer_payload"]["facts"] == [next(c for c in h.snapshot["evidence"]["cards"] if c["ref"] == "F1")]
    assert result["answer"] == "模型解释：这个线索需要结合使用背景核实。"
    assert h.snapshot["evidence"]["cards"][0]["text"] not in result["answer"]
    transitions=h.service.store.transitions
    assert [v["state"] for v in transitions] == ["in_flight","in_flight","ready"]
    assert "raw_response" not in transitions[0]["turns"][0]["result"]
    assert transitions[1]["turns"][0]["result"]["raw_response"]["complete"]
    stored=transitions[2]["turns"][0]["result"]
    assert stored["status"] == "answered" and stored["check_version"] == CHECK_VERSION
    assert stored["diagnostics"] == [] and stored["answer"] == result["answer"]
    assert "raw_response" not in result
    raw=h.service.raw(created["conversation_id"],request["message_id"])["raw_response"]
    original=h.response(h.calls[0]).encode("utf-8")
    assert b64decode(raw["content_base64"]) == original
    assert raw["sha256"] == hashlib.sha256(original).hexdigest()


@pytest.mark.parametrize("live",[False,True])
def test_sources_modes_and_all_public_surfaces_are_explicit(tmp_path, live):
    budget=FakeBudget() if live else None
    h=Harness(tmp_path/"provenance.sqlite3",budget);created=h.create()
    response,request=h.follow(created);result=response["turn"]["result"]
    mode="live_case_qa" if live else "offline_simulation"
    for value in [created,response,h.service.get(created["conversation_id"]),
                  h.service.evidence(created["conversation_id"]),h.service.raw(created["conversation_id"],request["message_id"])]:
        assert value["mode"] == mode
        assert (value["adapter_revision"],value["answer_revision"],value["prompt_revision"]) == (REVISION,ANSWER_REVISION,PROMPT_REVISION)
    assert result["mode"] == mode
    assert result["source"] == ("program_and_deepseek" if live else "program_and_mock_model")
    assert result["answer_payload"]["source"] == ("program_projection_and_deepseek" if live else "program_projection_and_mock_model")
    assert h.service.raw(created["conversation_id"],request["message_id"])["source"] == ("unreviewed_deepseek_output" if live else "unreviewed_mock_output")
    stored=h.service.store.get(created["conversation_id"])
    assert stored["execution_mode"] == mode and stored["turns"][0]["result"]["mode"] == mode
    if not live:
        assert h.service.get(created["conversation_id"])["usage"]["real_deepseek_attempts"] == 0


def test_real_transaction_id_redaction_applies_before_persist_and_send(tmp_path):
    h=Harness(tmp_path/"redact.sqlite3",FakeBudget());created=h.create()
    request=h.request(created,"交易3559456为什么需要关注？13559456和35594560是其他编号。")
    original=deepcopy(request)
    result=h.service.follow(created["conversation_id"],request)
    sent=h.budget.calls[0]["view"]["question"]
    assert sent == "交易"+h.snapshot["case_alias"]+"为什么需要关注？13559456和35594560是其他编号。"
    assert result["turn"]["message"] == sent and request == original
    assert h.service.store.get(created["conversation_id"])["turns"][0]["message"] == sent


def test_duplicate_replay_restart_get_raw_and_stop_never_generate_again(tmp_path):
    h=Harness(tmp_path/"replay.sqlite3");created=h.create();first,request=h.follow(created)
    h.restart()
    replay=h.service.follow(created["conversation_id"],request)
    h.service.get(created["conversation_id"]);h.service.raw(created["conversation_id"],request["message_id"])
    h.service.control(created["conversation_id"],{"enabled":False})
    with pytest.raises(SessionError,match="qa_disabled"):
        h.follow(created,"接下来呢？")
    assert replay["replayed"] and replay["turn"]["result"] == first["turn"]["result"]
    assert len(h.calls) == 1


def test_context_has_only_last_three_successful_pairs_and_current_evidence(tmp_path):
    h=Harness(tmp_path/"context.sqlite3");created=h.create()
    for index in range(5):h.follow(created,f"解释第{index}个问题。")
    packet=h.calls[-1];context=packet["view"]["context"]
    assert context["included_turn_ids"] == [f"{i:032x}" for i in (2,3,4)]
    assert context["omitted_turn_ids"] == [f"{1:032x}"]
    assert all(t["source"] == "unverified_dialogue" for t in packet["view"]["dialogue_background"])
    assert packet["view"]["evidence"] == h.snapshot["evidence"]
    assert packet["system_prompt"] == SYSTEM_PROMPT+CONTEXT_RULE+ANSWER_RULE
    assert len(canonical(packet).encode("utf-8")) <= MAX_CONTEXT_BYTES


def test_invalid_response_is_saved_and_excluded_from_successful_context(tmp_path):
    h=Harness(tmp_path/"invalid.sqlite3");created=h.create()
    h.response=lambda packet: "{broken-json"
    first,_=h.follow(created)
    assert first["turn"]["result"]["status"] == "invalid_response"
    assert first["turn"]["result"]["diagnostics"] and first["turn"]["result"]["answer_payload"] is None
    h.response=lambda packet: json.dumps(answer(packet["view"]["evidence"]))
    h.follow(created)
    assert not h.calls[-1]["view"]["dialogue_background"]
    assert h.service.raw(created["conversation_id"],first["turn"]["message_id"])["status"] == "invalid_response"


def test_old_seven_fields_are_not_converted_or_accepted(tmp_path):
    h=Harness(tmp_path/"seven.sqlite3");created=h.create();view=h.snapshot["evidence"]
    legacy={"version":"case-qa-answer-v1","case_alias":view["case_alias"],"evidence_version":view["evidence_version"],
            "fact_refs":[],"judgment":[{"text":"需要结合背景核实。","refs":["F1"]}],"hypotheses":[],"gaps":[]}
    raw=json.dumps(legacy,ensure_ascii=False);h.response=lambda packet:raw
    response,request=h.follow(created);result=response["turn"]["result"]
    assert result["status"] == "invalid_response" and result["answer"] is None
    assert result["diagnostics"][0]["code"] == "answer_schema_invalid"
    assert b64decode(h.service.raw(created["conversation_id"],request["message_id"])["raw_response"]["content_base64"]).decode("utf-8") == raw


def test_per_paragraph_numeric_failure_is_not_published(tmp_path):
    h=Harness(tmp_path/"review.sqlite3");created=h.create()
    h.response=lambda packet:json.dumps(answer(packet["view"]["evidence"],"此前1小时有9笔交易。"))
    response,_=h.follow(created);result=response["turn"]["result"]
    assert result["status"] == "review_required" and result["answer_payload"] is None
    assert "numeric_basis_requires_review" in result["issues"]
    assert result["diagnostics"][0]["path"] == "$.paragraphs[0].text"


def test_unknown_response_is_not_checked_or_resent_and_blocks_new_messages(tmp_path):
    h=Harness(tmp_path/"unknown.sqlite3");created=h.create()
    def timeout(packet):raise TimeoutError("local simulated timeout")
    h.response=timeout
    first,request=h.follow(created)
    assert first["turn"]["result"]["status"] == "response_unknown"
    assert first["turn"]["result"]["answer_payload"] is None
    assert h.service.follow(created["conversation_id"],request)["replayed"]
    with pytest.raises(SessionError,match="response_unknown_paused"):h.follow(created,"再问。")
    assert len(h.calls) == 1


def test_provider_integrity_failure_keeps_raw_and_cannot_become_answered(tmp_path):
    budget=FakeBudget();budget.error_code="provider_identity_changed"
    h=Harness(tmp_path/"identity.sqlite3",budget);created=h.create()
    response,request=h.follow(created);result=response["turn"]["result"]
    assert result["status"] == "review_required" and result["issues"] == ["provider_identity_changed"]
    assert result["answer_payload"] is None and result["source"] == "system"
    assert h.service.raw(created["conversation_id"],request["message_id"])["raw_response"]["complete"]
    assert len(budget.calls) == 1


def test_checker_exception_keeps_raw_and_is_not_reclassified(tmp_path, monkeypatch):
    h=Harness(tmp_path/"checker.sqlite3");created=h.create()
    def broken(raw,evidence):raise RuntimeError("local checker failure")
    monkeypatch.setattr("extensions.case_qa_compact_v1.service.check_answer",broken)
    response,request=h.follow(created)
    assert response["turn"]["result"]["issues"] == ["checker_failed"]
    assert response["turn"]["result"]["status"] == "review_required"
    assert h.service.raw(created["conversation_id"],request["message_id"])["raw_response"]["complete"]


def test_oversized_raw_retention_preserves_receipt_and_blocks_sending(tmp_path):
    h=Harness(tmp_path/"retention.sqlite3");created=h.create()
    h.response=lambda packet:"甲"*MAX_RAW_BYTES
    response,request=h.follow(created);result=response["turn"]["result"]
    assert result["status"] == "review_required" and result["issues"] == ["raw_retention_incomplete"]
    receipt=h.service.raw(created["conversation_id"],request["message_id"])["raw_response"]
    assert not receipt["complete"] and receipt["byte_length"] == MAX_RAW_BYTES*3
    with pytest.raises(SessionError,match="raw_retention_incomplete"):h.follow(created,"再问。")


@pytest.mark.parametrize("store_type",[SQLiteConversationStore,LiveQualityConversationStore,ShortConversationStore])
def test_old_stores_are_rejected_without_changing_old_bytes(tmp_path, store_type):
    path=tmp_path/"old.sqlite3";store_type(path);before=path.read_bytes()
    with pytest.raises(SessionError,match="session_store_invalid"):CompactConversationStore(path)
    assert path.read_bytes() == before


def test_compact_store_marker_rejects_old_adapter_and_mode_switch(tmp_path):
    h=Harness(tmp_path/"dedicated.sqlite3");h.create()
    with sqlite3.connect(h.path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (CompactConversationStore.STORE_MARKER,)
    before=h.path.read_bytes()
    with pytest.raises(SessionError,match="session_store_invalid"):ShortConversationStore(h.path)
    with pytest.raises(SessionError,match="session_store_invalid"):
        CompactService(CompactConversationStore(h.path),h.resolve,h.check,budget=FakeBudget())
    assert h.path.read_bytes() == before


def test_finish_checkpoint_failure_keeps_original_raw_and_no_retry(tmp_path):
    class FailingStore(RecordingStore):
        def mutate(self,identifier,fn):
            def fail_finish(value):
                changed,returned=fn(value)
                if changed["state"] == "ready" and changed["turns"]:
                    raise OSError("local simulated checkpoint failure")
                return changed,returned
            return super().mutate(identifier,fail_finish)
    h=Harness(tmp_path/"finish.sqlite3",store_type=FailingStore);created=h.create()
    request=h.request(created)
    with pytest.raises(SessionError,match="checkpoint_recovery_required"):
        h.service.follow(created["conversation_id"],request)
    assert h.service.raw(created["conversation_id"],request["message_id"])["raw_response"]["complete"]
    assert h.service.get(created["conversation_id"])["state"] == "in_flight"
    assert h.service.follow(created["conversation_id"],request)["replayed"] and len(h.calls) == 1


def test_api_exposes_new_verdict_and_raw_without_old_schema_conversion(tmp_path):
    h=Harness(tmp_path/"api.sqlite3",FakeBudget());app=FastAPI()
    app.state.case_qa_service=h.service;app.include_router(router)
    with TestClient(app) as client:
        base="/api/v1/case-qa/conversations"
        created=client.post(base,json={"transaction_id":"1","batch_id":None},headers={"Idempotency-Key":"compact-api-key"}).json()
        request=h.request(created)
        response=client.post(base+"/"+created["conversation_id"]+"/turns",json=request)
        assert response.status_code == 200
        result=response.json()["turn"]["result"]
        assert result["status"] == "answered" and result["check_version"] == CHECK_VERSION
        assert result["answer_payload"]["source"] == "program_projection_and_deepseek"
        raw=client.get(base+"/"+created["conversation_id"]+"/turns/"+request["message_id"]+"/raw").json()
        assert raw["source"] == "unreviewed_deepseek_output"
        assert json.loads(b64decode(raw["raw_response"]["content_base64"]))["version"] == VERSION
    assert len(h.budget.calls) == 1


def test_context_overflow_fails_before_provider_without_cropping_evidence(tmp_path):
    h=Harness(tmp_path/"overflow.sqlite3")
    value={"case":deepcopy(h.snapshot),"turns":[]}
    value["case"]["evidence"]["cards"][0]["text"]="甲"*12000
    before=deepcopy(value)
    with pytest.raises(SessionError,match="context_too_large"):
        h.service._packet(value,{"message":"解释。"})
    assert value == before and not h.calls
