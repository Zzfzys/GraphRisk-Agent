"""Offline budget-payload UI checks; ledger settlement is tested separately.

The transport substitutes only a fixture usage view around the real local QA
API. No budget authorization or DeepSeek Provider is created by these tests.
"""
from copy import deepcopy

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from extensions.case_qa_v1.workbench import QAClient
from test_case_qa_sessions_v1 import Harness
from test_case_qa_workbench_v1 import PAGE, Recorder, all_text, ask, page


def usage_view(**changes):
    return {"scope":"case_qa_offline_session", "provider":"mock", "real_deepseek_attempts":0,
            "mock_attempts_confirmed":0, "unresolved_slots":0,
            "budget_status":"offline_ledger", "authorization":"fixture_only_not_outbound",
            "session_remaining_calls":3, "case_remaining_calls":3, "batch_remaining_calls":6,
            "cost_estimate_cny":"0.000120", "unsettled_hold_cny":"0.100000",
            "batch_cost_estimate_cny":"0.000240", "global_remaining_calls":315,
            "global_deepseek_remaining_calls":62, "protected_route9_calls":200,
            "protected_web_calls":4, "block_reason":None,
            "cost_kind":"usage_estimate_not_invoice", "model":"deepseek-chat", **changes}


class BudgetView(Recorder):
    def __init__(self, service, usage):
        super().__init__(service)
        self.usage=usage

    def request(self, method, path, **kwargs):
        response=super().request(method,path,**kwargs)
        value=response.json()
        if "usage" in value:
            value["usage"]=deepcopy(self.usage)
            # The UI must honor a budget stop even if aggregate state lags.
            value["send_block_reason"]=None
        return httpx.Response(response.status_code,json=value)


def budget_page(h, session, usage):
    record=BudgetView(h.service,usage)
    at=AppTest.from_file(str(PAGE),default_timeout=15)
    at.session_state["qa_client"]=QAClient(record)
    at.query_params["qa_conversation"]=session["conversation_id"]
    at.run()
    assert not at.exception
    return at,record


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path/"qa-budget-ui.sqlite3")


def test_fixture_usage_readout_separates_estimate_hold_and_protected_quota(h):
    at,record=budget_page(h,h.create(),usage_view())
    text=all_text(at)
    for expected in ["离线合成账本", "非确认账单", "会话剩余3次", "本案跨会话剩余3次", "全批剩余6次",
                     "本会话usage费用估算0.000120元", "全批usage费用估算0.000240元",
                     "未结算保守预约0.100000元", "共享总剩余315次", "DeepSeek分项剩余62次",
                     "路线九200次", "网页首稿4次", "不能用于真实调用", "不借用旧报告授权", "DeepSeek尚未启用"]:
        assert expected in text
    assert "尚未配置授权" not in text and "未按零元展示" not in text
    assert not at.chat_input[0].disabled
    at.button(key="qa_refresh").click().run()
    assert not at.exception and not h.calls and not record.count("POST","/turns")


def test_quota_zero_disables_input_but_old_answer_and_evidence_stay_readable(h):
    session=h.create()
    h.follow(session)
    usage=usage_view(session_remaining_calls=0,case_remaining_calls=0,batch_remaining_calls=0,
                     mock_attempts_confirmed=1,block_reason="quota_exhausted")
    at,record=budget_page(h,session,usage)
    assert at.chat_input[0].disabled and "会话剩余0次" in all_text(at)
    assert "预算停止原因" in all_text(at)
    assert "模拟解释：这个关联也可能有正常原因。" in all_text(at)
    identifier=session["conversation_id"]
    message=h.service.get(identifier)["turns"][0]["message_id"]
    at.button(key="qa_evidence_"+identifier).click().run()
    at.button(key="raw_"+identifier+":"+message).click().run()
    assert not at.exception and record.count("GET","/evidence")==record.count("GET","/raw")==1
    assert not record.count("POST","/turns") and len(h.calls)==1


def test_unknown_hold_remains_visible_and_readonly_refresh_never_resends(h):
    usage=usage_view(block_reason="ledger_unknown_paused",unresolved_slots=1,cost_estimate_cny="0.000000",
                     batch_cost_estimate_cny="0.000000",unsettled_hold_cny="2.129920")
    at,record=budget_page(h,h.create(),usage)
    assert at.chat_input[0].disabled
    assert "未结算保守预约2.129920元" in all_text(at)
    assert "本会话usage费用估算0.000000元" in all_text(at)
    assert "未确认占位1个" in all_text(at)
    at.run()
    at.button(key="qa_refresh").click().run()
    assert not at.exception and not record.count("POST","/turns") and not h.calls


@pytest.mark.parametrize("change",[
    {"cost_estimate_cny":"NaN"}, {"unsettled_hold_cny":"Infinity"},
    {"batch_cost_estimate_cny":"-0.1"}, {"cost_estimate_cny":0.1},
    {"session_remaining_calls":True}, {"batch_remaining_calls":-1},
    {"authorization":"old_report_approved"}, {"real_deepseek_attempts":1},
    {"cost_kind":"invoice"}, {"budget_status":"paid_live"},
],ids=["nan","infinity","negative","float","bool_count","negative_count","old_grant","real_attempt","invoice","live_mode"])
def test_malformed_budget_view_is_fixed_system_error_without_page_crash(h,change):
    at,record=budget_page(h,h.create(),usage_view(**change))
    assert "无法读取问答服务" in all_text(at)
    assert not at.chat_input and not record.count("POST","/turns") and not h.calls


def test_cleared_fixture_budget_stop_only_enables_explicit_user_submission(h):
    usage=usage_view(block_reason="quota_exhausted")
    at,record=budget_page(h,h.create(),usage)
    assert at.chat_input[0].disabled
    usage["block_reason"]=None
    at.button(key="qa_refresh").click().run()
    assert not at.exception and not at.chat_input[0].disabled
    assert not record.count("POST","/turns")
    ask(at,"有没有正常解释？")
    assert len(h.calls)==record.count("POST","/turns")==1


def test_budget_query_failure_preserves_old_answers_evidence_raw_and_readonly_refresh(h):
    session=h.create()
    h.follow(session)
    class UnavailableBudget:
        def usage(self,value):
            raise OSError("private ledger detail")
    h.service.budget=UnavailableBudget()
    at,record=page(h,session)
    assert at.chat_input[0].disabled
    text=all_text(at)
    assert "预算无法核实" in text and "当前不能确认，未按零展示" in text
    assert "模拟解释：这个关联也可能有正常原因。" in text
    assert "private ledger detail" not in text
    identifier=session["conversation_id"]
    message=h.service.get(identifier)["turns"][0]["message_id"]
    at.button(key="qa_evidence_"+identifier).click().run()
    at.button(key="raw_"+identifier+":"+message).click().run()
    at.button(key="qa_refresh").click().run()
    assert not at.exception and at.chat_input[0].disabled
    assert record.count("GET","/evidence")==record.count("GET","/raw")==1
    assert not record.count("POST","/turns") and len(h.calls)==1
    assert any(item.proto.label=="下载离线问答记录" for item in at.get("download_button"))


def test_unavailable_budget_cannot_smuggle_confirmed_zero_or_available_funds(h):
    session=h.create()
    usage=deepcopy(session["usage"])
    usage.update(budget_status="unavailable",block_reason="budget_unavailable",cost_estimate_cny="0.000000")
    at,record=budget_page(h,session,usage)
    assert "无法读取问答服务" in all_text(at)
    assert not at.chat_input and not record.count("POST","/turns") and not h.calls


def test_flash_profile_is_displayed_as_simulation_only(h):
    at,record=budget_page(h,h.create(),usage_view(model="deepseek-flash"))
    text=all_text(at)
    assert "deepseek-flash仅为模拟配置，DeepSeek尚未启用" in text
    assert "deepseek-chat仅为模拟配置" not in text
    assert not at.chat_input[0].disabled
    assert not record.count("POST","/turns") and not h.calls
