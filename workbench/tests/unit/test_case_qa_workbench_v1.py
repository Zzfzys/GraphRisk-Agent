"""Step4 headless Streamlit/API checks. No listening server or external calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from threading import Event

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from extensions.case_qa_v1.api import router
from extensions.case_qa_v1.evidence_contract import adapt_evidence
from extensions.case_qa_v1.workbench import BASE, ClientError, QAClient, transcript
from test_case_qa_sessions_v1 import Harness, expect
from test_case_qa_evidence_v1 import daily_bundle

PAGE=Path(__file__).parents[2]/"extensions/case_qa_v1/preview.py"


class Recorder:
    def __init__(self, service):
        app=FastAPI()
        app.include_router(router)
        app.state.case_qa_service=service
        self.client=TestClient(app)
        self.calls=[]
        self.drop_reply=False
    def request(self, method, path, **kwargs):
        self.calls.append((method,path,deepcopy(kwargs)))
        result=self.client.request(method,path,**kwargs)
        if self.drop_reply and method=="POST" and path.endswith("/turns"):
            raise httpx.ReadTimeout("private transport detail")
        return result
    def count(self,method,suffix):
        return sum(m==method and p.endswith(suffix) for m,p,_ in self.calls)


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path/"qa-ui.sqlite3")


def page(h, session=None):
    record=Recorder(h.service)
    at=AppTest.from_file(str(PAGE),default_timeout=15)
    at.session_state["qa_client"]=QAClient(record)
    if session:
        at.query_params["qa_conversation"]=session["conversation_id"]
    at.run()
    assert not at.exception
    return at,record


def all_text(at):
    return "\n".join(str(item.value) for kind in ["text","caption","info","warning","error","code"] for item in at.get(kind))


def ask(at,text="为什么需要关注？"):
    at.chat_input(key="qa_chat").set_value(text).run()
    assert not at.exception


def test_initial_screen_and_creation_have_no_mock_or_raw_requests(h):
    at,record=page(h)
    assert "DeepSeek尚未启用" in all_text(at)
    assert "离线界面预览" in all_text(at)
    at.text_input(key="qa_transaction").set_value("1")
    at.text_input(key="qa_batch").set_value("day-1")
    at.button[0].click().run()
    assert not at.exception and not h.calls
    assert at.query_params["qa_conversation"]
    assert not record.count("POST","/turns") and not record.count("GET","/raw")
    assert "真实DeepSeek请求0次" in all_text(at)
    assert "尚未配置授权" in all_text(at) and "未按零元展示" in all_text(at)


def test_natural_qa_once_rerun_and_readonly_refresh_never_repost(h):
    session=h.create()
    at,record=page(h,session)
    ask(at,"有没有正常解释？")
    assert record.count("POST","/turns")==len(h.calls)==1
    assert "模拟解释：这个关联也可能有正常原因。" in all_text(at)
    assert "离线模拟问答 · 模型解释" in all_text(at)
    assert "复用已封批的日终评分" not in all_text(at)
    at.run()
    at.button(key="qa_refresh").click().run()
    assert not at.exception and record.count("POST","/turns")==1
    assert not record.count("GET","/raw")
    assert "qa_message" not in at.query_params


def test_facts_signals_hypotheses_and_citations_have_distinct_sources(h):
    bundle=daily_bundle()
    h.cases["1"]["evidence"]=adapt_evidence(bundle)
    evidence=h.cases["1"]["evidence"]
    def mock(packet):
        h.calls.append(packet)
        return json.dumps({"version":"case-qa-answer-v1","case_alias":evidence["case_alias"],"evidence_version":evidence["evidence_version"],
            "fact_refs":["F1","F2"],"judgment":[{"text":"频次信号需要关注。","refs":["F1"]}],
            "hypotheses":[{"text":"也可能是正常连续付款。","refs":["F1"]}],
            "gaps":[{"text":"需要核实授权及交易用途。","refs":["F1"]}]})
    h.service.mock_provider=mock
    at,record=page(h,h.create())
    ask(at)
    text=all_text(at)
    for label in ["程序证据","XGBoost／图模型信号","未核实假设","尚未执行","不是已验证的欺诈概率"]:
        assert label in text
    assert any(exp.label=="查看本轮引用" for exp in at.expander)
    assert not record.count("GET","/raw")


def test_raw_and_evidence_are_only_loaded_by_explicit_button(h):
    session=h.create()
    at,record=page(h,session)
    ask(at)
    identifier=session["conversation_id"]
    message=h.service.get(identifier)["turns"][0]["message_id"]
    assert not record.count("GET","/evidence") and not record.count("GET","/raw")
    at.button(key="qa_evidence_"+identifier).click().run()
    assert record.count("GET","/evidence")==1 and not record.count("GET","/raw")
    at.button(key="raw_"+identifier+":"+message).click().run()
    assert record.count("GET","/raw")==1
    assert "原文完整留存" in all_text(at)
    at.run()
    assert record.count("GET","/raw")==1 and len(h.calls)==1


@pytest.mark.parametrize("raw,status",[("broken JSON","未通过格式或引用检查"),
    ("REVIEW","内容待复核")])
def test_invalid_and_reviewed_outputs_are_system_messages_not_answer(h,raw,status):
    evidence=h.cases["1"]["evidence"]
    if raw=="REVIEW":
        raw=json.dumps({"version":"case-qa-answer-v1","case_alias":evidence["case_alias"],"evidence_version":evidence["evidence_version"],
            "fact_refs":[],"judgment":[{"text":"已确认同一客户实施盗刷。","refs":["F1"]}],"hypotheses":[],"gaps":[]})
    def mock(packet):
        h.calls.append(packet)
        return raw
    h.service.mock_provider=mock
    at,record=page(h,h.create())
    ask(at)
    assert status in all_text(at)
    assert "已确认同一客户实施盗刷。" not in all_text(at)
    assert "broken JSON" not in all_text(at)
    assert record.count("POST","/turns")==1


def test_server_stopped_by_other_window_is_not_reenabled_by_readonly_rerun(h):
    session=h.create()
    at,record=page(h,session)
    assert not at.checkbox[0].value
    h.service.control(session["conversation_id"],{"enabled":False})
    at.run()
    assert not at.exception
    assert not h.service.get(session["conversation_id"])["qa_enabled"]
    assert record.count("PUT","/control")==0
    assert at.checkbox[0].value and at.chat_input[0].disabled


def test_stop_is_persisted_but_does_not_hide_evidence_or_old_results(h):
    session=h.create()
    at,record=page(h,session)
    ask(at)
    at.checkbox[0].check().run()
    assert not at.exception
    assert at.chat_input[0].disabled
    assert not h.restart().get(session["conversation_id"])["qa_enabled"]
    assert "不撤回已开始的请求" in all_text(at)
    assert record.count("PUT","/control")==1
    at.button(key="qa_evidence_"+session["conversation_id"]).click().run()
    assert record.count("GET","/evidence")==1 and len(h.calls)==1


def test_stop_committed_before_claim_blocks_but_after_claim_does_not_erase_result(h):
    session=h.create()
    h.service.control(session["conversation_id"],{"enabled":False})
    expect("qa_disabled",lambda:h.follow(session))
    assert not h.calls
    h.service.control(session["conversation_id"],{"enabled":True})
    entered,release=Event(),Event()
    def wait(packet):
        entered.set()
        assert release.wait(10)
        return "模拟响应"
    h.provider=wait
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(h.follow,session)
        assert entered.wait(10)
        h.service.control(session["conversation_id"],{"enabled":False})
        release.set()
        result,request=future.result()
    assert result["turn"]["result"]["status"]=="answered"
    assert not h.service.get(session["conversation_id"])["qa_enabled"]
    assert h.service.follow(session["conversation_id"],request)["replayed"]
    assert len(h.calls)==1


def test_unknown_in_another_case_disables_input_and_budget_stays_unknown(h):
    first=h.create()
    def timeout(packet):raise TimeoutError()
    h.provider=timeout
    h.follow(first)
    other=h.create("2","other_case")
    at,record=page(h,other)
    assert at.chat_input[0].disabled
    assert "响应未知" in all_text(at)
    usage=h.service.get(other["conversation_id"])["usage"]
    assert usage["real_deepseek_attempts"]==0 and usage["session_remaining_calls"] is None
    assert usage["batch_remaining_calls"] is None and usage["cost_estimate_cny"] is None and usage["unsettled_hold_cny"] is None
    assert not record.count("POST","/turns")


def test_lost_post_response_recovers_by_get_and_new_websocket_does_not_resend(h):
    session=h.create()
    at,record=page(h,session)
    record.drop_reply=True
    ask(at)
    assert len(h.calls)==record.count("POST","/turns")==1
    # Simulate a new page/WebSocket with only persistent opaque IDs, not session_state.
    fresh=AppTest.from_file(str(PAGE),default_timeout=15)
    fresh.session_state["qa_client"]=QAClient(record)
    fresh.query_params.update(at.query_params)
    fresh.run()
    assert not fresh.exception
    assert "private transport detail" not in all_text(fresh)
    assert len(h.calls)==record.count("POST","/turns")==1


def test_missing_pending_message_is_never_automatically_recreated(h):
    session=h.create()
    at,record=page(h,session)
    at.query_params["qa_message"]="f"*32
    at.run()
    assert not at.exception and at.chat_input[0].disabled
    assert "原消息状态尚未确认" in all_text(at)
    assert record.count("POST","/turns")==0
    at.checkbox[0].check().run()
    assert not at.exception and not h.service.get(session["conversation_id"])["qa_enabled"]
    assert "qa_message" not in at.query_params


def test_creation_recovery_by_opaque_key_is_readonly(h):
    session=h.create(key="persistent_create_key")
    at=AppTest.from_file(str(PAGE),default_timeout=15)
    record=Recorder(h.service)
    at.session_state["qa_client"]=QAClient(record)
    at.query_params["qa_create"]="persistent_create_key"
    at.run()
    assert not at.exception
    assert at.query_params["qa_conversation"]==[session["conversation_id"]]
    assert not any(method=="POST" for method,_,_ in record.calls)


def test_downloads_and_report_link_are_separate_and_do_not_send(h,monkeypatch):
    import streamlit as st
    captured=[]
    actual=st.download_button
    def download(label,data,**kwargs):
        captured.append((label,data,kwargs))
        return actual(label,data,**kwargs)
    monkeypatch.setattr(st,"download_button",download)
    session=h.create()
    at,record=page(h,session)
    ask(at)
    at.button(key="qa_evidence_"+session["conversation_id"]).click().run()
    assert not at.exception
    labels={label for label,_,_ in captured}
    assert {"下载程序证据","下载离线问答记录"} <= labels
    for label,data,kwargs in captured:
        assert kwargs["on_click"]=="ignore"
        if label=="下载程序证据":
            assert kwargs["file_name"].startswith("case-qa-program-evidence-") and json.loads(data)["source"]=="program"
        if label=="下载离线问答记录":
            assert kwargs["file_name"].startswith("case-qa-offline-transcript-") and "不是完整报告" in data
    link=at.get("link_button")[0]
    assert link.proto.label=="打开原报告工作台" and link.proto.url=="http://127.0.0.1:8501/"
    assert record.count("POST","/turns")==1


def test_200_missing_fields_is_fixed_error_not_unconfirmed_success():
    class Bad:
        def request(self,*args,**kwargs):
            return httpx.Response(200,json={"mode":"offline_simulation"})
    client=QAClient(Bad())
    with pytest.raises(ClientError) as error:
        client.call("POST",BASE+"/"+"1"*32+"/turns",{"message_id":"2"*32})
    assert error.value.uncertain



def test_bad_get_response_is_system_error_without_page_crash():
    class Bad:
        def request(self,*args,**kwargs):
            return httpx.Response(200,json={"mode":"offline_simulation"})
    at=AppTest.from_file(str(PAGE),default_timeout=15)
    at.session_state["qa_client"]=QAClient(Bad())
    at.query_params["qa_conversation"]="1"*32
    at.run()
    assert not at.exception
    assert "无法读取问答服务" in all_text(at)
    assert len(at.chat_input)==0


def test_pending_none_attempt_is_not_rendered_as_zero(h):
    session=h.create()
    identifier=session["conversation_id"]
    def reserve(value):
        value["state"]="in_flight"
        value["turns"]=[{"message_id":"f"*32,"message":"为什么？","parent_turn_id":None,"evidence_version":value["evidence_version"],
                         "request_sha":"c"*64,"context":{},"result":{"status":"in_flight","source":"system","answer":None,
                         "model_attempts":None,"attempt_slots_claimed":1,"notice":"处理中，尚未确认是否已发送。"}}]
        return value,None
    h.service.store.mutate(identifier,reserve)
    at,record=page(h,h.service.get(identifier))
    assert not at.exception and at.chat_input[0].disabled
    assert "本轮模拟大语言模型尝试：未确认（已占位）" in all_text(at)
    assert "未确认占位1个" in all_text(at)
    assert not record.count("POST","/turns")


def test_unrequested_score_never_displays_hidden_high_priority(h):
    h.cases["1"]["evidence"]=adapt_evidence(daily_bundle(available=False))
    at,_=page(h,h.create())
    text=all_text(at)
    assert "没有取得这项数据" in text and "高风险调查优先级" not in text
    assert "0.86" not in text and "86%" not in text


def test_raw_cache_cannot_appear_in_another_case(h):
    first=h.create()
    at,record=page(h,first)
    ask(at)
    message=h.service.get(first["conversation_id"])["turns"][0]["message_id"]
    at.button(key="raw_"+first["conversation_id"]+":"+message).click().run()
    assert at.code
    second=h.create("2","another_case")
    at.query_params["qa_conversation"]=second["conversation_id"]
    at.run()
    assert not at.exception and not at.code
    assert not at.chat_message
    assert record.count("GET","/raw")==1


def test_invalid_unicode_raw_display_uses_replacement_but_download_retains_bytes(h):
    def mock(packet):
        h.calls.append(packet)
        return "\ud800"
    h.service.mock_provider=mock
    session=h.create()
    at,_=page(h,session)
    ask(at)
    message=h.service.get(session["conversation_id"])["turns"][0]["message_id"]
    at.button(key="raw_"+session["conversation_id"]+":"+message).click().run()
    assert not at.exception and at.code
    assert "原文完整留存" in all_text(at)
    assert len(h.calls)==1


def test_standalone_preview_factory_uses_synthetic_only_and_temp_store(tmp_path,monkeypatch):
    import streamlit as st
    import extensions.case_qa_v1.sessions as sessions
    original=sessions.SQLiteConversationStore
    monkeypatch.setattr(sessions,"SQLiteConversationStore",lambda ignored:original(tmp_path/"standalone.sqlite3"))
    st.cache_resource.clear()
    at=AppTest.from_file(str(PAGE),default_timeout=20).run()
    assert not at.exception
    at.text_input(key="qa_transaction").set_value("1")
    at.button[0].click().run()
    assert not at.exception and "高风险调查优先级" in all_text(at)
    ask(at,"这个关系是什么意思？")
    assert "尚未进行真实DeepSeek分析" in all_text(at)
    assert "真实DeepSeek请求0次" in all_text(at)
    st.cache_resource.clear()
