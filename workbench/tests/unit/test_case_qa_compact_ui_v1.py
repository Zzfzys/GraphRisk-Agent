"""Compact model answer through the existing short-first UI; synthetic only."""
from base64 import b64decode
from decimal import Decimal
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from extensions.case_qa_compact_v1.service import CompactService,CompactConversationStore
from extensions.case_qa_compact_v1.workbench import QAClient
from test_case_qa_live_quality_service_ui_v3 import Harness as PreviousHarness,FakeV3Budget,ask,visible_text
from test_case_qa_live_service_ui_v1 import Recorder

PAGE=Path(__file__).parents[2]/"extensions/case_qa_compact_v1/web.py"

def answer(evidence,kind="judgment",text="日终模型信号需结合本案核实。",refs=None):
    return json.dumps({"version":"case-qa-compact-answer-v1","case_alias":evidence["case_alias"],
        "evidence_version":evidence["evidence_version"],"paragraphs":[{"kind":kind,"text":text,"refs":["F1","F2"] if refs is None else refs}]},ensure_ascii=False)

class FakeCompactBudget(FakeV3Budget):
    def usage(self,value):
        usage=super().usage(value)
        if self.approved:
            alias=value["case"]["case_alias"];prior=2 if alias=="1"*32 else 1
            own=sum(p["view"]["evidence"]["case_alias"]==alias for p in self.calls)
            usage.update(case_remaining_calls=3-prior-own,batch_remaining_calls=3-len(self.calls),
                batch_cost_estimate_cny=format(Decimal(".049752")+Decimal(len(self.calls))*Decimal(".001"),".6f"))
            if min(usage[k] for k in ("session_remaining_calls","case_remaining_calls","batch_remaining_calls"))<=0:
                usage["block_reason"]="quota_exhausted"
        return usage
    def generate(self,ticket,packet):
        original=self.raw_override
        if original is None:self.raw_override=answer(packet["view"]["evidence"])
        try:return super().generate(ticket,packet)
        finally:self.raw_override=original

class Harness(PreviousHarness):
    def __init__(self,path):
        super().__init__(path)
        self.budget=FakeCompactBudget();self.restart()
    def restart(self):
        self.service=CompactService(CompactConversationStore(self.path),self.resolve,self.check,
            budget=self.budget,id_aliases={"3558671":"1"*32,"3559456":"2"*32},clock=lambda:1000)
        return self.service

@pytest.fixture
def h(tmp_path):return Harness(tmp_path/"compact.sqlite3")

def page(h,session):
    record=Recorder(h.service)
    at=AppTest.from_file(str(PAGE),default_timeout=15)
    at.session_state["qa_client"]=QAClient(record)
    at.query_params["qa_conversation"]=session["conversation_id"]
    at.run();assert not at.exception
    return at,record

def test_startup_reuses_saved_session_and_sends_nothing(h):
    session=h.create("2");at,record=page(h,session)
    assert at.title[0].value=="本案问答"
    assert session["adapter_revision"]=="case-qa-compact-live-v1"
    assert session["answer_revision"]=="case-qa-compact-v1"
    assert session["usage"]["case_remaining_calls"]==2 and session["usage"]["batch_remaining_calls"]==3
    assert not h.budget.calls and not record.count("POST","/turns")

@pytest.mark.parametrize("kind,text,label",[
    ("judgment","日终模型信号需结合本案核实。","DeepSeek回答 · 模型解释"),
    ("hypotheses","也可能是正常连续付款，尚待核实。","DeepSeek回答 · 未核实假设"),
    ("gaps","建议核实支付授权与交易用途；尚未执行。","信息缺口／核实建议 · 尚未执行"),
])
def test_each_compact_paragraph_kind_has_a_short_visible_answer_and_derived_facts(h,kind,text,label):
    session=h.create("2");h.budget.raw_override=answer(h.cases["2"]["evidence"],kind,text)
    at,record=page(h,session);ask(at,"这笔交易有什么依据？")
    result=h.service.get(session["conversation_id"])["turns"][-1]["result"]
    assert result["status"]=="answered" and result["check_version"]=="case-qa-compact-v1"
    assert len(result["answer_payload"]["facts"])==2 and result["answer_payload"]["source"]=="program_projection_and_deepseek"
    assert text in visible_text(at) and label in visible_text(at)
    assert not any(x in visible_text(at) for x in ("离线模拟","mock","合成"))
    assert record.count("POST","/turns")==len(h.budget.calls)==1
    raw=json.loads(h.budget.raw_override);assert "fact_refs" not in raw and set(raw)=={"version","case_alias","evidence_version","paragraphs"}

def test_two_rounds_use_only_completed_paragraph_answer_as_background_and_then_stop_at_case_cap(h):
    session=h.create("2");at,record=page(h,session)
    ask(at,"为什么需要关注？");ask(at,"这些线索有没有正常解释？")
    turns=h.service.get(session["conversation_id"])["turns"]
    assert [t["result"]["status"] for t in turns]==["answered","answered"]
    history=h.budget.calls[1]["view"]["dialogue_background"]
    assert len(history)==1 and history[0]["assistant"]==turns[0]["result"]["answer"]
    assert next(c["text"] for c in h.cases["2"]["evidence"]["cards"] if c.get("metric")=="primary_model_score") not in history[0]["assistant"]
    assert at.chat_input[0].disabled and record.count("POST","/turns")==2
    at.run();assert len(h.budget.calls)==2

def test_full_values_raw_and_download_survive_without_duplicate_fact_text_in_answer(h):
    session=h.create("2");at,record=page(h,session);ask(at,"为什么需要关注？")
    turn=h.service.get(session["conversation_id"])["turns"][-1]
    score=next(c for c in turn["result"]["answer_payload"]["facts"] if c["kind"]=="model_signal")
    assert score["value"]==.005006021354347467 and str(score["value"]) not in turn["result"]["answer"]
    raw=h.service.raw(session["conversation_id"],turn["message_id"])
    assert json.loads(b64decode(raw["raw_response"]["content_base64"]))["version"]=="case-qa-compact-answer-v1"
    from extensions.case_qa_compact_v1.workbench import download_record
    exported=json.loads(download_record(h.service.get(session["conversation_id"])))
    assert exported["turns"][-1]["result"]["answer_payload"]["facts"]==turn["result"]["answer_payload"]["facts"]
    assert str(score["value"]) in download_record(exported)
    assert any(b.label=="下载问答与引用原值（JSON）" for b in at.get("download_button"))
    assert len(h.budget.calls)==1

@pytest.mark.parametrize("flag",["approved","unavailable"])
def test_unauthorized_or_unknown_budget_disables_chat_without_post(h,flag):
    setattr(h.budget,flag,False if flag=="approved" else True)
    session=h.create("2");at,record=page(h,session)
    assert at.chat_input[0].disabled and not record.count("POST","/turns") and not h.budget.calls
