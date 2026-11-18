"""Replay the unchanged v3 UI against the new prompt service, using local fakes.

These headless checks are not human page acceptance and make no real provider
calls. The optional historical replay reads frozen JSON, never the old database.
"""
from base64 import b64decode
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from extensions.case_qa_live_v3.service import REVISION, ANSWER_REVISION
from extensions.case_qa_live_v3.workbench import QAClient
from extensions.case_qa_short_v1.service import ShortService, ShortConversationStore, PROMPT_REVISION
from test_case_qa_evidence_v1 import output
from test_case_qa_live_quality_service_ui_v3 import (
    Harness as PreviousHarness, FakeV3Budget, PAGE, ask, visible_text,
)
from test_case_qa_live_service_ui_v1 import Recorder

ROOT = Path(__file__).parents[2]
HISTORICAL_RECEIPT = ROOT/"reports/case-qa-v1/20261117-live-quality-v3/receipts/eec30afe16534fd89a310cf5f7d927d1.json"
HISTORICAL_CASE = ROOT/"reports/case-qa-v1/20261117-live-trial-v1/cases/case-1.json"


class FakeShortBudget(FakeV3Budget):
    def __init__(self):
        super().__init__()
        self.m1_alias = "1"*32

    def usage(self, value):
        usage = super().usage(value)
        if self.approved:
            alias = value["case"]["case_alias"]
            prior = 2 if alias == self.m1_alias else 0
            own = sum(p["view"]["evidence"]["case_alias"] == alias for p in self.calls)
            usage.update(case_remaining_calls=3-prior-own, batch_remaining_calls=4-len(self.calls),
                global_remaining_calls=313-len(self.calls), global_deepseek_remaining_calls=60-len(self.calls),
                batch_cost_estimate_cny=format(Decimal(".033768")+Decimal(len(self.calls))*Decimal(".001"),".6f"))
            if min(usage[k] for k in ("session_remaining_calls","case_remaining_calls","batch_remaining_calls")) <= 0:
                usage["block_reason"] = "quota_exhausted"
        return usage


class Harness(PreviousHarness):
    def __init__(self, path):
        super().__init__(path)
        self.budget = FakeShortBudget()
        self.restart()

    def restart(self):
        self.service = ShortService(ShortConversationStore(self.path),self.resolve,self.check,
            budget=self.budget,
            id_aliases={"3558671":self.cases["1"]["case_alias"],"3559456":self.cases["2"]["case_alias"]},
            clock=lambda:1000)
        return self.service


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path/"short-only.sqlite3")


def page(h, session=None):
    record = Recorder(h.service)
    at = AppTest.from_file(str(PAGE),default_timeout=15)
    at.session_state["qa_client"] = QAClient(record)
    if session:
        at.query_params["qa_conversation"] = session["conversation_id"]
    at.run()
    assert not at.exception
    return at,record


def last(h,session):
    return h.service.get(session["conversation_id"])["turns"][-1]


def test_existing_v3_page_accepts_prompt_revision_without_adapter_or_answer_change(h):
    session = h.create()
    assert session["prompt_revision"] == PROMPT_REVISION
    assert session["adapter_revision"] == REVISION and session["answer_revision"] == ANSWER_REVISION
    at,record = page(h,session)
    assert at.title[0].value == "本案问答"
    assert "已封批的日终证据" in visible_text(at)
    assert not record.count("POST","/turns") and not h.budget.calls
    assert session["usage"]["case_remaining_calls"] == 1 and session["usage"]["batch_remaining_calls"] == 4
    other = h.create("2","short_other_case")
    assert other["usage"]["case_remaining_calls"] == 3 and other["usage"]["batch_remaining_calls"] == 4


@pytest.mark.parametrize("part,question,text,label",[
    ("judgment","为什么需要关注？","日终信号提示需要核实，不能直接认定欺诈。","DeepSeek回答 · 模型解释"),
    ("hypotheses","这些线索有没有正常解释？","也可能是正常连续付款，尚待核实。","DeepSeek回答 · 未核实假设"),
    ("gaps","现有资料还缺什么？","建议先核实支付授权与实际用途；核实尚未执行。","信息缺口／核实建议 · 尚未执行"),
])
def test_three_sparse_short_answers_use_original_live_presentation(h,part,question,text,label):
    evidence = h.cases["1"]["evidence"]
    refs = ["F2"] if part == "judgment" else ["F1"] if part == "hypotheses" else []
    h.budget.raw_override = json.dumps(output(evidence,**{part:[{"text":text,"refs":refs}]}),ensure_ascii=False)
    session = h.create()
    at,record = page(h,session)
    ask(at,question)
    result = last(h,session)["result"]
    assert result["status"] == "answered" and result["prompt_revision"] == PROMPT_REVISION
    assert result["answer_payload"]["facts"] == []
    assert all(result["answer_payload"][p] == [] for p in ("judgment","hypotheses","gaps") if p != part)
    visible = visible_text(at)
    assert text in visible and label in visible
    assert not any(word in visible for word in ("合成","mock","离线模拟"))
    assert len(h.budget.calls) == record.count("POST","/turns") == 1


def test_fact_only_answer_stays_visible_and_original_score_stays_folded(h):
    evidence = h.cases["1"]["evidence"]
    h.budget.raw_override = json.dumps(output(evidence,fact_refs=["F1","F2"]),ensure_ascii=False)
    session = h.create()
    at,_ = page(h,session)
    ask(at,"当前有哪些数据事实？")
    result = last(h,session)["result"]
    assert result["status"] == "answered" and len(result["answer_payload"]["facts"]) == 2
    visible = visible_text(at)
    assert "程序事实答复" in visible and "此前1小时：7" in visible
    assert "日终中调查优先级（未校准）" in visible
    assert ".02983763813972473" not in visible
    assert any(e.label == "本轮事实与引用（完整原值）" and not e.proto.expanded for e in at.expander)
    assert result["answer_payload"]["facts"][1]["value"] == .02983763813972473


def test_l1_three_turns_keep_context_and_stop_at_remaining_case_limit(h):
    session = h.create("2","three_turn_l1")
    at,record = page(h,session)
    evidence = h.cases["2"]["evidence"]
    for part,text in (("judgment","现有信号需要结合本案核实。"),("hypotheses","也可能是正常使用，尚待核实。"),("gaps","建议先核实支付授权；尚未执行。")):
        h.budget.raw_override = json.dumps(output(evidence,**{part:[{"text":text,"refs":[]}]}),ensure_ascii=False)
        ask(at,text)
        assert last(h,session)["result"]["status"] == "answered"
    assert len(h.budget.calls[-1]["view"]["dialogue_background"]) == 2
    assert all(t["source"] == "unverified_dialogue" for t in h.budget.calls[-1]["view"]["dialogue_background"])
    assert at.chat_input[0].disabled
    assert h.service.get(session["conversation_id"])["usage"]["case_remaining_calls"] == 0
    assert len(h.budget.calls) == record.count("POST","/turns") == 3


def test_refresh_stop_restart_and_lost_reply_never_resend(h):
    session = h.create()
    at,record = page(h,session)
    record.drop_reply = True
    ask(at)
    fresh = AppTest.from_file(str(PAGE),default_timeout=15)
    fresh.session_state["qa_client"] = QAClient(record)
    fresh.query_params.update(at.query_params)
    fresh.run()
    assert not fresh.exception
    fresh.button(key="qa_refresh").click().run()
    fresh.checkbox[0].check().run()
    assert not fresh.exception and fresh.chat_input[0].disabled
    persisted = h.restart().get(session["conversation_id"])
    assert not persisted["qa_enabled"] and persisted["prompt_revision"] == PROMPT_REVISION
    assert len(h.budget.calls) == record.count("POST","/turns") == 1
    assert record.count("PUT","/control") == 1


def test_unapproved_or_unknown_budget_disables_new_questions_but_keeps_old_raw(h):
    h.budget.approved = False
    session = h.create()
    at,record = page(h,session)
    assert at.chat_input[0].disabled and not h.budget.calls
    assert session["usage"]["session_remaining_calls"] is None and session["usage"]["cost_estimate_cny"] is None
    response = record.client.post("/api/v1/case-qa/conversations/"+session["conversation_id"]+"/turns",json=h.request(session))
    assert response.json()["error_code"] == "case_qa_not_authorized" and not h.budget.calls
    h.budget.approved = True
    h.service.follow(session["conversation_id"],h.request(session))
    h.budget.unavailable = True
    at,record = page(h,session)
    assert at.chat_input[0].disabled and "无法核实问答预算" in visible_text(at)
    message = last(h,session)["message_id"]
    at.button(key="raw_"+session["conversation_id"]+":"+message).click().run()
    assert not at.exception and at.code and len(h.budget.calls) == 1
    assert not record.count("POST","/turns")


def test_original_page_downloads_keep_fact_values_and_unreviewed_raw(h,monkeypatch):
    import streamlit as st
    captures = []
    actual = st.download_button
    def capture(label,data,**kwargs):
        captures.append((label,data,kwargs))
        return actual(label,data,**kwargs)
    monkeypatch.setattr(st,"download_button",capture)
    session = h.create()
    at,record = page(h,session)
    ask(at)
    message = last(h,session)["message_id"]
    at.button(key="qa_evidence_"+session["conversation_id"]).click().run()
    at.button(key="raw_"+session["conversation_id"]+":"+message).click().run()
    assert not at.exception
    latest = {label:(data,kwargs) for label,data,kwargs in captures}
    assert {"下载程序证据","下载DeepSeek问答记录","下载未审原始回答字节"} <= set(latest)
    evidence = json.loads(latest["下载程序证据"][0])
    assert evidence["prompt_revision"] == PROMPT_REVISION and evidence["evidence"] == h.cases["1"]["evidence"]
    assert ".02983763813972473" in latest["下载DeepSeek问答记录"][0]
    assert "DeepSeek本案问答记录" in latest["下载DeepSeek问答记录"][0]
    assert json.loads(latest["下载未审原始回答字节"][0])["version"] == "case-qa-answer-v1"
    assert all(kwargs["on_click"] == "ignore" for _,_,kwargs in captures)
    assert len(h.budget.calls) == record.count("POST","/turns") == 1
    assert at.get("link_button")[0].proto.url == "http://127.0.0.1:8501/"


@pytest.mark.skipif(not HISTORICAL_RECEIPT.is_file() or not HISTORICAL_CASE.is_file(),reason="Optional frozen local audit JSON is not bundled with the repository.")
def test_unchanged_real_v3_failure_is_only_replayed_in_a_new_temporary_store(h):
    before_receipt = HISTORICAL_RECEIPT.read_bytes()
    before_case = HISTORICAL_CASE.read_bytes()
    receipt = json.loads(before_receipt)
    raw = receipt["body"]["choices"][0]["message"]["content"]
    assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == "27f0b4eae577a6500a15a257b6f4908cb6198294e1b5a9f1b1f29d4027ae7e9a"
    h.cases["1"] = json.loads(before_case)
    h.budget.m1_alias = h.cases["1"]["case_alias"]
    h.budget.raw_override = raw
    h.restart()
    session = h.create()
    at,record = page(h,session)
    ask(at,"这笔交易为什么需要关注？")
    result = last(h,session)["result"]
    assert result["status"] == "invalid_response" and result["answer"] is None
    assert any(d["code"] == "total_items_out_of_range" and d["actual"] == 22 for d in result["diagnostics"])
    stored = h.service.raw(session["conversation_id"],last(h,session)["message_id"])
    assert b64decode(stored["raw_response"]["content_base64"]) == raw.encode("utf-8")
    assert "未通过格式或引用检查" in visible_text(at)
    assert HISTORICAL_RECEIPT.read_bytes() == before_receipt and HISTORICAL_CASE.read_bytes() == before_case
    assert len(h.budget.calls) == record.count("POST","/turns") == 1  # One local fake, zero real calls.
