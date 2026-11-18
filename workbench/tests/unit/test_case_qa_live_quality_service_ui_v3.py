"""Live quality composition checks; synthetic cases, memory budget, no network."""
from base64 import b64decode
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from extensions.case_qa_live_v1.service import LiveConversationStore
from extensions.case_qa_live_v3.service import (
    LiveQualityConversationStore, LiveQualityService, REVISION, ANSWER_REVISION,
)
from extensions.case_qa_live_v3.workbench import BASE, ClientError, QAClient, compact_score_summary
from extensions.case_qa_quality_v1.contract import ANSWER_RULE
from extensions.case_qa_quality_v1.service import QualityConversationStore
from extensions.case_qa_v1.evidence_contract import adapt_evidence
from extensions.case_qa_v1.sessions import SessionError
from test_case_qa_evidence_v1 import daily_bundle, output
from test_case_qa_live_service_ui_v1 import FakeLiveBudget, Recorder

PAGE = Path(__file__).parents[2]/"extensions/case_qa_live_v3/web.py"


class FakeV3Budget(FakeLiveBudget):
    """Only the live protocol seam; ledger/cost arithmetic has separate tests."""
    def usage(self, value):
        usage = super().usage(value)
        if self.approved:
            alias = value["case"]["case_alias"]
            current = sum(type(t["result"].get("model_attempts")) is int for t in value["turns"])
            case_calls = sum(p["view"]["evidence"]["case_alias"] == alias for p in self.calls)
            old_calls = 1 if alias == "1"*32 else 0
            usage.update(real_deepseek_attempts=current, session_remaining_calls=3-len(value["turns"]),
                case_remaining_calls=3-old_calls-case_calls, batch_remaining_calls=5-len(self.calls),
                global_remaining_calls=314-len(self.calls), global_deepseek_remaining_calls=61-len(self.calls),
                cost_estimate_cny=format(Decimal(current)*Decimal(".001"),".6f"),
                batch_cost_estimate_cny=format(Decimal(".016176")+Decimal(len(self.calls))*Decimal(".001"),".6f"))
        return usage

    def reserve(self, value, request, packet):
        if self.approved and not self.unavailable:
            usage = self.usage(value)
            if min(usage[k] for k in ("session_remaining_calls","case_remaining_calls","batch_remaining_calls")) <= 0:
                raise ValueError("quota_exhausted")
        return super().reserve(value,request,packet)


class Harness:
    def __init__(self, path):
        self.path = path
        self.budget = FakeV3Budget()
        self.cases = {}
        for number,score,level in ((1,.02983763813972473,"medium"),(2,.005006021354347467,"low")):
            bundle = daily_bundle()
            bundle["case_alias"] = str(number)*32
            bundle["evidence_version"] = str(number)*64
            bundle["facts"][1]["value"] = score
            bundle["scoring_context"].update(score=score,risk_level=level)
            evidence = adapt_evidence(bundle)
            self.cases[str(number)] = {"case_alias":evidence["case_alias"],"binding":str(number)*64,
                "batch_revision":"day-1-revision-1","evidence":evidence}
        self.service = self.restart()

    def resolve(self, transaction, batch):
        assert batch in (None,"day-1")
        return deepcopy(self.cases[transaction])

    def check(self, alias, binding):
        return next(deepcopy(c) for c in self.cases.values() if c["case_alias"] == alias and c["binding"] == binding)

    def restart(self):
        self.service = LiveQualityService(LiveQualityConversationStore(self.path),self.resolve,self.check,
            budget=self.budget,id_aliases={"3558671":"1"*32,"3559456":"2"*32},clock=lambda:1000)
        return self.service

    def create(self, transaction="1", key="live_quality_create_key"):
        return self.service.create({"transaction_id":transaction,"batch_id":"day-1"},key)

    def request(self, session, message="为什么需要关注？"):
        value = self.service.get(session["conversation_id"])
        return {"message_id":f"{len(value['turns'])+1:032x}","message":message,
                "parent_turn_id":value["turns"][-1]["message_id"] if value["turns"] else None,
                "evidence_version":value["evidence_version"]}


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path/"live-quality-v3.sqlite3")


def page(h, session=None):
    record = Recorder(h.service)
    at = AppTest.from_file(str(PAGE),default_timeout=15)
    at.session_state["qa_client"] = QAClient(record)
    if session:
        at.query_params["qa_conversation"] = session["conversation_id"]
    at.run()
    assert not at.exception
    return at,record


def visible_text(at):
    values = []
    def walk(node):
        if node.type == "expander":
            return
        if node.type in {"text","caption","info","warning","error","code"}:
            values.append(str(node.value))
        for child in getattr(node,"children",{}).values():
            walk(child)
    walk(at.main)
    return "\n".join(values)


def ask(at, text="为什么需要关注？"):
    at.chat_input(key="qa_chat").set_value(text).run()
    assert not at.exception


def last(h, session):
    return h.service.get(session["conversation_id"])["turns"][-1]


def test_new_live_store_and_response_keep_quality_diagnostics_and_provenance(h):
    session = h.create()
    request = h.request(session)
    reply = h.service.follow(session["conversation_id"],request)
    result = reply["turn"]["result"]
    assert reply["adapter_revision"] == result["adapter_revision"] == REVISION
    assert reply["answer_revision"] == result["answer_revision"] == ANSWER_REVISION
    assert result["source"] == "program_and_deepseek"
    assert result["answer_payload"]["source"] == "program_projection_and_deepseek"
    stored = h.service.store.get(session["conversation_id"])
    assert stored["execution_mode"] == "live_case_qa" and stored["adapter_revision"] == REVISION
    assert stored["turns"][0]["result"]["answer_payload"]["source"] == "program_projection_and_deepseek"
    assert result["check_version"] == ANSWER_REVISION and "diagnostics" in result
    assert h.restart().get(session["conversation_id"])["mode"] == "live_case_qa"
    replay = h.service.follow(session["conversation_id"],request)
    assert replay["replayed"] and replay["turn"]["result"] == result
    assert len(h.budget.calls) == 1


@pytest.mark.parametrize("store_type",[QualityConversationStore,LiveConversationStore])
def test_new_marker_refuses_old_quality_or_live_store_without_writing(tmp_path,store_type):
    path = tmp_path/"old-store.sqlite3"
    store_type(path)
    before = path.read_bytes()
    with pytest.raises(SessionError) as error:
        LiveQualityConversationStore(path)
    assert error.value.code == "session_store_invalid" and path.read_bytes() == before


def test_quality_packet_is_used_and_old_transaction_identifier_is_redacted(h):
    session = h.create()
    h.service.follow(session["conversation_id"],h.request(session,"3558671为什么需要关注？"))
    packet = h.budget.calls[-1]
    assert packet["system_prompt"].endswith(ANSWER_RULE)
    assert "3558671" not in json.dumps(packet,ensure_ascii=False)
    assert "1"*32 in packet["view"]["question"]
    assert packet["view"]["evidence"] == h.cases["1"]["evidence"]
    first = last(h,session)
    h.service.follow(session["conversation_id"],h.request(session,"这些线索有没有正常解释？"))
    history = h.budget.calls[-1]["view"]["dialogue_background"]
    assert len(history) == 1 and history[0]["source"] == "unverified_dialogue"
    assert history[0]["assistant"] == first["result"]["answer"]


def test_invalid_answer_has_atomic_diagnostics_without_repair_or_raw_change(h):
    evidence = h.cases["1"]["evidence"]
    raw = json.dumps(output(evidence,fact_refs=["F1"],judgment={"text":"未审原句，不能改成通过。","refs":["F1"]}),ensure_ascii=False)
    h.budget.raw_override = raw
    session = h.create()
    reply = h.service.follow(session["conversation_id"],h.request(session))
    result = reply["turn"]["result"]
    assert result["status"] == "invalid_response" and result["answer"] is None
    assert any(d["path"] == "$.judgment" for d in result["diagnostics"])
    assert h.restart().get(session["conversation_id"])["turns"][0]["result"]["diagnostics"] == result["diagnostics"]
    receipt = h.service.raw(session["conversation_id"],reply["turn"]["message_id"])
    data = b64decode(receipt["raw_response"]["content_base64"])
    assert data == raw.encode("utf-8")
    assert receipt["raw_response"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert receipt["source"] == "unreviewed_deepseek_output" and receipt["adapter_revision"] == REVISION
    assert len(h.budget.calls) == 1


def test_five_new_slots_and_three_per_case_include_prior_failed_request(h):
    first = h.create()
    other = h.create("2","other_case_create_key")
    assert first["usage"]["batch_remaining_calls"] == other["usage"]["batch_remaining_calls"] == 5
    assert first["usage"]["case_remaining_calls"] == 2 and other["usage"]["case_remaining_calls"] == 3
    assert first["usage"]["protected_route9_calls"] == 200 and first["usage"]["protected_web_calls"] == 4
    h.service.follow(first["conversation_id"],h.request(first))
    reopened = h.create("1","another_same_case_session")
    assert reopened["usage"]["case_remaining_calls"] == 1 and reopened["usage"]["batch_remaining_calls"] == 4
    assert reopened["usage"]["batch_cost_estimate_cny"] == "0.017176"


def test_live_default_is_short_first_daily_and_has_no_simulation_labels(h):
    session = h.create()
    at,record = page(h,session)
    ask(at)
    text = visible_text(at)
    for word in ("合成","mock","离线模拟"):
        assert word not in text
    assert "已封批的日终证据" in text and "不表示交易发生时可用" in text
    assert "日终中调查优先级（未校准）" in text and "DeepSeek回答 · 模型解释" in text
    assert ".02983763813972473" not in text
    assert any(e.label == "本轮事实与引用（完整原值）" and not e.proto.expanded for e in at.expander)
    assert len(h.budget.calls) == record.count("POST","/turns") == 1


def test_facts_only_live_answer_is_visible_but_score_raw_value_stays_folded(h):
    evidence = h.cases["1"]["evidence"]
    h.budget.raw_override = json.dumps(output(evidence,fact_refs=["F1","F2"]),ensure_ascii=False)
    session = h.create()
    at,_ = page(h,session)
    ask(at)
    assert last(h,session)["result"]["status"] == "answered"
    text = visible_text(at)
    assert "程序事实答复" in text and "此前1小时：7" in text
    assert "日终中调查优先级（未校准）" in text and ".02983763813972473" not in text


def test_live_invalid_output_notice_and_folded_diagnostics_do_not_publish_raw(h):
    evidence = h.cases["1"]["evidence"]
    h.budget.raw_override = json.dumps(output(evidence,judgment={"text":"只在未审区出现的原句。","refs":[]}),ensure_ascii=False)
    session = h.create()
    at,record = page(h,session)
    ask(at)
    assert "未通过格式或引用检查" in visible_text(at)
    assert "只在未审区出现的原句" not in visible_text(at)
    assert any(e.label == "查看具体检查项" and not e.proto.expanded for e in at.expander)
    message = last(h,session)["message_id"]
    at.button(key="raw_"+session["conversation_id"]+":"+message).click().run()
    assert not at.exception and at.code and "只在未审区出现的原句" in at.code[0].value
    assert len(h.budget.calls) == record.count("POST","/turns") == 1


def test_refresh_stop_and_old_raw_read_never_resend(h):
    session = h.create()
    at,record = page(h,session)
    ask(at)
    at.run()
    at.button(key="qa_refresh").click().run()
    at.checkbox[0].check().run()
    assert not at.exception and at.chat_input[0].disabled
    assert not h.restart().get(session["conversation_id"])["qa_enabled"]
    message = last(h,session)["message_id"]
    at.button(key="raw_"+session["conversation_id"]+":"+message).click().run()
    assert not at.exception and record.count("PUT","/control") == 1
    assert len(h.budget.calls) == record.count("POST","/turns") == 1


def test_cross_window_stop_and_lost_response_recover_readonly(h):
    session = h.create()
    at,record = page(h,session)
    record.drop_reply = True
    ask(at)
    fresh = AppTest.from_file(str(PAGE),default_timeout=15)
    fresh.session_state["qa_client"] = QAClient(record)
    fresh.query_params.update(at.query_params)
    fresh.run()
    assert not fresh.exception and len(h.budget.calls) == record.count("POST","/turns") == 1
    h.service.control(session["conversation_id"],{"enabled":False})
    fresh.run()
    assert not fresh.exception and fresh.checkbox[0].value and fresh.chat_input[0].disabled
    assert not record.count("PUT","/control")


def test_unapproved_and_unavailable_live_state_keep_unknown_amounts_and_old_raw(h):
    h.budget.approved = False
    session = h.create()
    at,record = page(h,session)
    assert at.chat_input[0].disabled and not h.budget.calls
    assert session["usage"]["session_remaining_calls"] is None and session["usage"]["cost_estimate_cny"] is None
    h.budget.approved = True
    h.service.follow(session["conversation_id"],h.request(session))
    h.budget.unavailable = True
    at,record = page(h,session)
    assert at.chat_input[0].disabled
    assert "无法核实问答预算" in visible_text(at)
    assert h.service.get(session["conversation_id"])["usage"]["block_reason"] == "budget_unavailable"
    message = last(h,session)["message_id"]
    at.button(key="raw_"+session["conversation_id"]+":"+message).click().run()
    assert not at.exception and at.code and len(h.budget.calls) == 1
    assert not record.count("POST","/turns")


def test_live_downloads_keep_nested_source_exact_facts_and_unreviewed_raw(h,monkeypatch):
    import streamlit as st
    captured = []
    actual = st.download_button
    def capture(label,data,**kwargs):
        captured.append((label,data,kwargs))
        return actual(label,data,**kwargs)
    monkeypatch.setattr(st,"download_button",capture)
    session = h.create()
    at,record = page(h,session)
    ask(at)
    message = last(h,session)["message_id"]
    at.button(key="qa_evidence_"+session["conversation_id"]).click().run()
    at.button(key="raw_"+session["conversation_id"]+":"+message).click().run()
    assert not at.exception
    latest = {label:(data,kw) for label,data,kw in captured}
    assert {"下载DeepSeek问答记录","下载程序证据","下载未审原始回答字节"} <= set(latest)
    text,kw = latest["下载DeepSeek问答记录"]
    assert "DeepSeek本案问答记录" in text and "离线模拟" not in text and ".02983763813972473" in text
    assert kw["file_name"].startswith("case-qa-deepseek-transcript-")
    evidence = json.loads(latest["下载程序证据"][0])
    assert evidence["mode"] == "live_case_qa" and evidence["adapter_revision"] == REVISION and evidence["source"] == "program"
    assert evidence["evidence"] == h.cases["1"]["evidence"]
    assert all(kw["on_click"] == "ignore" for _,_,kw in captured)
    assert json.loads(latest["下载未审原始回答字节"][0])["version"] == "case-qa-answer-v1"
    link = at.get("link_button")[0]
    assert link.proto.label == "打开原报告工作台" and link.proto.url == "http://127.0.0.1:8501/"
    assert len(h.budget.calls) == record.count("POST","/turns") == 1


def test_live_client_refuses_stale_adapter_and_nested_mock_metadata(h):
    session = h.create()
    value = deepcopy(session)
    value.pop("adapter_revision")
    class Response:
        def request(self,*args,**kwargs):
            return httpx.Response(200,json=value)
    with pytest.raises(ClientError):
        QAClient(Response()).call("GET",BASE+"/"+session["conversation_id"])
    h.service.follow(session["conversation_id"],h.request(session))
    value = h.service.get(session["conversation_id"])
    value["turns"][0]["result"]["answer_payload"]["source"] = "program_projection_and_mock_model"
    with pytest.raises(ClientError):
        QAClient(Response()).call("GET",BASE+"/"+session["conversation_id"])


def test_program_daily_short_label_preserves_card_and_does_not_infer_priority(h):
    signal = next(c for c in h.cases["1"]["evidence"]["cards"] if c.get("metric") == "primary_model_score")
    before = deepcopy(signal)
    assert compact_score_summary(signal) == "日终中调查优先级（未校准）" and signal == before
    signal.pop("risk_priority")
    assert "中调查优先级" not in compact_score_summary(signal)
    signal.update(availability="not_measured",value=None)
    assert "未取得" in compact_score_summary(signal)


def test_live_web_replaces_stale_client_instead_of_bypassing_revision_checks(h,monkeypatch):
    session = h.create()
    record = Recorder(h.service)
    class StaleClient:
        def call(self,*args,**kwargs):
            raise AssertionError("stale client must not handle the new adapter")
    monkeypatch.setattr(httpx,"Client",lambda **kwargs:record)
    at = AppTest.from_file(str(PAGE),default_timeout=15)
    at.session_state["qa_client"] = StaleClient()
    at.query_params["qa_conversation"] = session["conversation_id"]
    at.run()
    assert not at.exception
    assert isinstance(at.session_state["qa_client"],QAClient)
    assert not record.count("POST","/turns") and not h.budget.calls
