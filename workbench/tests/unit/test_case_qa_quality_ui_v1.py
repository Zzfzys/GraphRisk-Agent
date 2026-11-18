"""Quality presentation checks; synthetic service and native headless UI only."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from extensions.case_qa_quality_v1.preview import build_fixture_service
from extensions.case_qa_quality_v1.workbench import QAClient, compact_score_summary
from test_case_qa_workbench_v1 import Recorder, all_text, ask

PAGE = Path(__file__).parents[2]/"extensions/case_qa_quality_v1/preview.py"


@pytest.fixture
def h(tmp_path):
    service = build_fixture_service(tmp_path/"quality-only.sqlite3")
    calls = []
    original = service.mock_provider
    def record(packet):
        calls.append(deepcopy(packet))
        return original(packet)
    service.mock_provider = record
    return SimpleNamespace(service=service, calls=calls)


def create(h, transaction="1", key="quality_create_key"):
    return h.service.create({"transaction_id":transaction, "batch_id":"demo-day-1"}, key)


def page(h, session=None):
    recorder = Recorder(h.service)
    at = AppTest.from_file(str(PAGE), default_timeout=15)
    at.session_state["qa_client"] = QAClient(recorder)
    if session:
        at.query_params["qa_conversation"] = session["conversation_id"]
    at.run()
    assert not at.exception
    return at, recorder


def outside_expanders(at):
    """AppTest also includes collapsed contents; inspect the actual container tree."""
    parts = []
    def walk(node):
        if node.type == "expander":
            return
        if node.type in {"text","caption","info","warning","error","code"}:
            parts.append(str(node.value))
        for child in getattr(node, "children", {}).values():
            walk(child)
    walk(at.main)
    return "\n".join(parts)


def saved_turn(h, session):
    return h.service.get(session["conversation_id"])["turns"][-1]


def test_quality_preview_creates_no_request_until_question(h):
    at, recorder = page(h)
    assert "固定模拟回答" in all_text(at) and "真实DeepSeek请求0次" in all_text(at)
    at.text_input(key="qa_transaction").set_value("1")
    at.button[0].click().run()
    assert not at.exception and not h.calls
    assert not recorder.count("POST","/turns") and not recorder.count("GET","/raw")
    assert "尚未配置授权" in all_text(at)


def test_default_short_answer_precedes_collapsed_original_facts(h):
    session = create(h)
    at, recorder = page(h, session)
    ask(at, "这笔交易为什么需要关注？")
    result = saved_turn(h, session)["result"]
    assert result["status"] == "answered"
    visible = outside_expanders(at)
    assert "近期频次与日终模型信号提示应先核实" in visible
    assert "日终高调查优先级（未校准；合成示例）" in visible
    assert "离线模拟问答 · 模型解释" in visible and "尚未执行" in visible
    assert "0.860123456789" not in visible and "此前1小时：7" not in visible
    details = next(x for x in at.expander if x.label == "本轮事实与引用（完整原值）")
    assert not details.proto.expanded
    assert "0.860123456789" in all_text(at) and "此前1小时：7" in all_text(at)
    assert result["answer_payload"]["facts"][1]["value"] == .860123456789
    assert len(h.calls) == recorder.count("POST","/turns") == 1



def test_facts_only_answer_displays_program_projection_in_main_area(h):
    def facts_only(packet):
        h.calls.append(deepcopy(packet))
        evidence = packet["view"]["evidence"]
        return json.dumps({"version":"case-qa-answer-v1", "case_alias":evidence["case_alias"],
            "evidence_version":evidence["evidence_version"], "fact_refs":["F1","F2"],
            "judgment":[], "hypotheses":[], "gaps":[]}, ensure_ascii=False)
    h.service.mock_provider = facts_only
    session = create(h)
    at, recorder = page(h, session)
    ask(at, "当前能直接看到哪些事实？")
    result = saved_turn(h, session)["result"]
    assert result["status"] == "answered"
    assert result["answer_payload"]["judgment"] == result["answer_payload"]["hypotheses"] == result["answer_payload"]["gaps"] == []
    visible = outside_expanders(at)
    assert "程序事实答复" in visible and "此前1小时：7" in visible
    assert "日终高调查优先级（未校准；合成示例）" in visible
    assert "0.860123456789" not in visible
    assert "0.860123456789" in all_text(at)
    assert result["answer_payload"]["facts"][1]["value"] == .860123456789
    assert any(x.label == "本轮事实与引用（完整原值）" and not x.proto.expanded for x in at.expander)
    assert len(h.calls) == recorder.count("POST", "/turns") == 1


def test_normal_explanation_does_not_force_fact_or_gap_repetition(h):
    session = create(h)
    at, _ = page(h, session)
    ask(at, "这些线索有没有正常解释？")
    result = saved_turn(h, session)["result"]
    assert result["status"] == "answered"
    payload = result["answer_payload"]
    assert payload["facts"] == payload["judgment"] == payload["gaps"] == []
    assert len(payload["hypotheses"]) == 1
    visible = outside_expanders(at)
    assert "未核实假设" in visible and "正常连续付款" in visible
    assert "近期频次与日终模型信号提示应先核实" not in visible
    assert "此前1小时：7" not in visible


def test_gap_question_works_without_padding_other_sections(h):
    session = create(h)
    at, _ = page(h, session)
    ask(at, "现有资料还缺什么，接下来优先核实什么？")
    payload = saved_turn(h, session)["result"]["answer_payload"]
    assert payload["facts"] == payload["judgment"] == payload["hypotheses"] == []
    assert len(payload["gaps"]) == 1
    visible = outside_expanders(at)
    assert "建议先核实支付授权与交易用途" in visible and "尚未执行" in visible
    assert "0.860123456789" not in visible


def test_refresh_and_stop_keep_original_request_and_server_state(h):
    session = create(h)
    at, recorder = page(h, session)
    ask(at)
    at.run()
    at.button(key="qa_refresh").click().run()
    assert not at.exception and len(h.calls) == recorder.count("POST","/turns") == 1
    at.checkbox[0].check().run()
    assert not at.exception and at.chat_input[0].disabled
    assert not h.service.get(session["conversation_id"])["qa_enabled"]
    at.run()
    assert recorder.count("PUT","/control") == 1 and len(h.calls) == 1
    assert "近期频次与日终模型信号提示应先核实" in outside_expanders(at)


def test_cross_window_stop_is_not_undone_by_quality_ui(h):
    session = create(h)
    at, recorder = page(h, session)
    h.service.control(session["conversation_id"], {"enabled":False})
    at.run()
    assert not at.exception and at.chat_input[0].disabled and at.checkbox[0].value
    assert not recorder.count("PUT","/control") and not h.calls


def test_lost_reply_recovers_saved_result_without_resend(h):
    session = create(h)
    at, recorder = page(h, session)
    recorder.drop_reply = True
    ask(at)
    fresh = AppTest.from_file(str(PAGE), default_timeout=15)
    fresh.session_state["qa_client"] = QAClient(recorder)
    fresh.query_params.update(at.query_params)
    fresh.run()
    assert not fresh.exception
    assert "近期频次与日终模型信号提示应先核实" in outside_expanders(fresh)
    assert len(h.calls) == recorder.count("POST","/turns") == 1


def test_contract_failure_has_human_notice_details_and_separate_raw(h):
    evidence = h.service.resolve("1","demo-day-1")["evidence"]
    raw = json.dumps({"version":"case-qa-answer-v1", "case_alias":evidence["case_alias"],
        "evidence_version":evidence["evidence_version"], "fact_refs":["F1"],
        "judgment":{"text":"只供未审原文验证的句子。", "refs":["F1"]}, "hypotheses":[], "gaps":[]}, ensure_ascii=False)
    def invalid(packet):
        h.calls.append(deepcopy(packet))
        return raw
    h.service.mock_provider = invalid
    session = create(h)
    at, recorder = page(h, session)
    ask(at)
    turn = saved_turn(h, session)
    assert turn["result"]["status"] == "invalid_response" and turn["result"]["answer"] is None
    assert turn["result"]["diagnostics"]
    assert any(x["path"] == "$.judgment" for x in turn["result"]["diagnostics"])
    visible = outside_expanders(at)
    assert "未通过格式或引用检查" in visible
    assert "answer_schema_or_reference_invalid" not in visible
    assert "只供未审原文验证的句子" not in all_text(at)
    assert any(x.label == "查看具体检查项" and not x.proto.expanded for x in at.expander)
    at.button(key="raw_"+session["conversation_id"]+":"+turn["message_id"]).click().run()
    assert not at.exception and "只供未审原文验证的句子" in all_text(at)
    assert len(h.calls) == recorder.count("POST","/turns") == 1


def test_style_note_is_only_advice_and_does_not_hide_an_answer(h):
    session = create(h)
    request = {"message_id":"a"*32, "parent_turn_id":None, "message":"为什么？", "evidence_version":session["evidence_version"]}
    turn = deepcopy(h.service.follow(session["conversation_id"],request)["turn"])
    turn["result"]["style_notes"] = [{"code":"synthetic_style_advice", "message":"可以进一步精简表达；这条建议不改变通过状态。"}]
    at = AppTest.from_string("import streamlit as st\nfrom extensions.case_qa_quality_v1.workbench import _turn\n_turn(None, '"+session["conversation_id"]+"', st.session_state.turn)", default_timeout=15)
    at.session_state["turn"] = turn
    at.run()
    assert not at.exception
    assert turn["result"]["status"] == "answered"
    assert "近期频次与日终模型信号提示应先核实" in outside_expanders(at)
    assert any(x.label == "表达建议（不改变检查状态）" and not x.proto.expanded for x in at.expander)


def test_downloads_preserve_full_payload_and_unreviewed_raw(h, monkeypatch):
    import streamlit as st
    captures = []
    actual = st.download_button
    def capture(label, data, **kwargs):
        captures.append((label,data,kwargs))
        return actual(label,data,**kwargs)
    monkeypatch.setattr(st,"download_button",capture)
    session = create(h)
    at, recorder = page(h,session)
    ask(at)
    turn = saved_turn(h,session)
    at.button(key="qa_evidence_"+session["conversation_id"]).click().run()
    at.button(key="raw_"+session["conversation_id"]+":"+turn["message_id"]).click().run()
    assert not at.exception
    labels = {label for label,_,_ in captures}
    assert {"下载离线问答记录","下载程序证据","下载未审原始回答字节"} <= labels
    for _,_,kwargs in captures:
        assert kwargs["on_click"] == "ignore"
    # Earlier reruns legitimately expose an empty transcript before the question.
    # Verify the current download for each label, not those historical render calls.
    latest = {label:(data,kwargs) for label,data,kwargs in captures}
    for label,(data,kwargs) in latest.items():
        if label == "下载离线问答记录":
            assert "0.860123456789" in data and "不是DeepSeek真实回答" in data
        elif label == "下载程序证据":
            value = json.loads(data)
            assert value["source"] == "program"
            signal = next(c for c in value["evidence"]["cards"] if c.get("metric") == "primary_model_score")
            assert signal["value"] == .860123456789 and "0.860123456789" in signal["text"]
        elif label == "下载未审原始回答字节":
            assert kwargs["file_name"].startswith("case-qa-unreviewed-raw-")
            assert json.loads(data)["version"] == "case-qa-answer-v1"
    assert recorder.count("GET","/raw") == 1 and len(h.calls) == 1
    link = at.get("link_button")[0]
    assert link.proto.label == "打开原报告工作台" and link.proto.url == "http://127.0.0.1:8501/"


def test_compact_signal_is_program_owned_and_does_not_mutate_original(h):
    case = h.service.resolve("2","demo-day-1")
    signal = next(c for c in case["evidence"]["cards"] if c.get("metric") == "primary_model_score")
    before = deepcopy(signal)
    assert compact_score_summary(signal) == "日终中调查优先级（未校准；合成示例）"
    assert signal == before and signal["value"] == .450123456789
    signal.update(availability="not_measured",value=None)
    text = compact_score_summary(signal)
    assert "未取得" in text and "中调查优先级" not in text


def test_default_preview_factory_uses_only_dedicated_temporary_store(tmp_path, monkeypatch):
    import streamlit as st
    import extensions.case_qa_quality_v1.service as quality
    original = quality.QualityConversationStore
    class TemporaryStore(original):
        def __init__(self, ignored):
            super().__init__(tmp_path/"default-preview.sqlite3")
    # Keep a real dedicated-store type, including the production isinstance guard.
    monkeypatch.setattr(quality,"QualityConversationStore",TemporaryStore)
    st.cache_resource.clear()
    at = AppTest.from_file(str(PAGE), default_timeout=20).run()
    assert not at.exception
    at.text_input(key="qa_transaction").set_value("3")
    at.button[0].click().run()
    assert not at.exception
    assert "日终低调查优先级" in outside_expanders(at)
    ask(at)
    assert "真实DeepSeek请求0次" in outside_expanders(at)
    st.cache_resource.clear()
