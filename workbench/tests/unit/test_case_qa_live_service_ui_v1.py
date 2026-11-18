"""Step6 live-provenance UI checks with synthetic evidence and in-memory budget.

No real provider, network, shared project ledger, listening server or old preview.
The fake implements only the existing budget seam; money accounting is covered by
its dedicated fixture-ledger tests.
"""
from base64 import b64decode
from copy import deepcopy
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from extensions.case_qa_live_v1.api import router
from extensions.case_qa_live_v1.service import LiveCaseQAService, LiveConversationStore
from extensions.case_qa_live_v1.workbench import BASE, ClientError, QAClient, _validate_usage, transcript
from extensions.case_qa_v1.evidence_contract import adapt_evidence
from extensions.case_qa_v1.sessions import CaseQAService, SessionError, SQLiteConversationStore
from test_case_qa_evidence_v1 import daily_bundle, output

PAGE = """import streamlit as st
from extensions.case_qa_live_v1.workbench import render_case_qa
render_case_qa(st.session_state.qa_client)
"""


class FakeLiveBudget:
    def __init__(self):
        self.approved = True
        self.unavailable = False
        self.calls = []
        self.reservations = []
        self.raw_override = None

    def usage(self, value):
        if self.unavailable:
            raise ValueError("private ledger detail")
        usage = {"scope":"live_case_qa", "provider":"deepseek", "model":"deepseek-flash",
            "real_deepseek_attempts":len(self.calls), "mock_attempts_confirmed":0,
            "unresolved_slots":0, "batch_unresolved_slots":0, "session_remaining_calls":None,
            "case_remaining_calls":None, "batch_remaining_calls":None, "cost_estimate_cny":None,
            "batch_cost_estimate_cny":None, "unsettled_hold_cny":None,
            "budget_status":"authorization_required", "authorization":"not_configured",
            "block_reason":"case_qa_not_authorized", "cost_kind":"usage_estimate_not_invoice"}
        if self.approved:
            usage.update(budget_status="shared_ledger", authorization="approved_case_qa", block_reason=None,
                session_remaining_calls=3-len(self.calls), case_remaining_calls=3-len(self.calls),
                batch_remaining_calls=6-len(self.calls), global_remaining_calls=20-len(self.calls),
                global_deepseek_remaining_calls=10-len(self.calls), protected_route9_calls=200,
                protected_web_calls=4, cost_estimate_cny="0.001000", batch_cost_estimate_cny="0.001000",
                unsettled_hold_cny="0.000000")
        return usage

    def reserve(self, value, request, packet):
        if self.unavailable:
            raise ValueError("budget_unavailable")
        if not self.approved:
            raise ValueError("case_qa_not_authorized")
        self.reservations.append(deepcopy(request))
        return {"attempt_id":"synthetic-live-attempt-"+request["message_id"], "reserved_micro_cny":1000,
                "cost_estimate_micro_cny":0, "hold_micro_cny":1000, "state":"reserved", "transport_attempts":0}

    def generate(self, ticket, packet):
        self.calls.append(deepcopy(packet))
        evidence = packet["view"]["evidence"]
        raw = self.raw_override
        if raw is None:
            raw = json.dumps(output(evidence, fact_refs=["F1","F2"],
                judgment=[{"text":"近期频次和日终信号需要结合本案核实。", "refs":["F1","F2"]}],
                hypotheses=[{"text":"也可能是正常连续付款。", "refs":["F1"]}],
                gaps=[{"text":"需要核实支付授权及实际用途。", "refs":["F1"]}]), ensure_ascii=False)
        audit = {**ticket, "state":"settled", "transport_attempts":1,
                 "cost_estimate_micro_cny":1000, "hold_micro_cny":0}
        return {"raw":raw, "error_code":None, "audit":audit}


class LiveHarness:
    def __init__(self, path):
        evidence = adapt_evidence(daily_bundle())
        self.case = {"case_alias":evidence["case_alias"], "batch_revision":"day-1-revision-1",
                     "binding":"b"*64, "evidence":evidence}
        self.path = path
        self.budget = FakeLiveBudget()
        self.service = self.restart()

    def resolve(self, transaction, batch):
        assert transaction == "1" and batch in (None, "day-1")
        return deepcopy(self.case)

    def check(self, alias, binding):
        assert alias == self.case["case_alias"] and binding == self.case["binding"]
        return deepcopy(self.case)

    def restart(self):
        self.service = LiveCaseQAService(LiveConversationStore(self.path), self.resolve, self.check,
            budget=self.budget, clock=lambda:1000)
        return self.service

    def create(self, key="live_create_key"):
        return self.service.create({"transaction_id":"1", "batch_id":"day-1"}, key)

    def request(self, session, message="为什么需要关注？"):
        value = self.service.get(session["conversation_id"])
        return {"message_id":f"{len(value['turns'])+1:032x}", "message":message,
                "parent_turn_id":value["turns"][-1]["message_id"] if value["turns"] else None,
                "evidence_version":value["evidence_version"]}


class Recorder:
    def __init__(self, service):
        app = FastAPI()
        app.include_router(router)
        app.state.case_qa_service = service
        self.client = TestClient(app)
        self.calls = []
        self.drop_reply = False

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, deepcopy(kwargs)))
        response = self.client.request(method, path, **kwargs)
        if self.drop_reply and method == "POST" and path.endswith("/turns"):
            raise httpx.ReadTimeout("private response transport detail")
        return response

    def count(self, method, suffix):
        return sum(m == method and p.endswith(suffix) for m,p,_ in self.calls)


@pytest.fixture
def h(tmp_path):
    return LiveHarness(tmp_path/"dedicated-live-conversations.sqlite3")


def page(h, session):
    record = Recorder(h.service)
    at = AppTest.from_string(PAGE, default_timeout=15)
    at.session_state["qa_client"] = QAClient(record)
    at.query_params["qa_conversation"] = session["conversation_id"]
    at.run()
    assert not at.exception
    return at, record


def text(at):
    return "\n".join(str(item.value) for kind in ("text","caption","info","warning","error","code") for item in at.get(kind))


def ask(at, message="为什么需要关注？"):
    at.chat_input(key="qa_chat").set_value(message).run()
    assert not at.exception


def test_live_service_storage_api_restart_and_download_provenance(h):
    session = h.create()
    request = h.request(session)
    record = Recorder(h.service)
    value = record.client.post(BASE+f"/{session['conversation_id']}/turns", json=request).json()
    assert value["mode"] == value["turn"]["result"]["mode"] == "live_case_qa"
    assert value["turn"]["result"]["source"] == "program_and_deepseek"
    persisted = h.service.store.get(session["conversation_id"])
    assert persisted["execution_mode"] == "live_case_qa"
    assert persisted["turns"][0]["result"]["source"] == "program_and_deepseek"
    restarted = h.restart().get(session["conversation_id"])
    assert restarted["mode"] == "live_case_qa"
    assert restarted["usage"]["provider"] == "deepseek" and restarted["usage"]["mock_attempts_confirmed"] == 0
    assert restarted["usage"]["real_deepseek_attempts"] == 1
    saved = transcript(restarted)
    assert "DeepSeek本案问答记录" in saved and "不是完整报告" in saved
    assert "离线模拟" not in saved
    replay = h.service.follow(session["conversation_id"], request)
    assert replay["replayed"] and replay["mode"] == "live_case_qa"
    assert replay["turn"]["result"]["source"] == "program_and_deepseek"
    assert len(h.budget.calls) == 1


def test_live_store_rejects_existing_mock_conversations(tmp_path):
    h = LiveHarness(tmp_path/"fresh-live.sqlite3")
    path = tmp_path/"old-offline.sqlite3"
    offline = CaseQAService(SQLiteConversationStore(path), h.resolve, h.check, clock=lambda:1000)
    session = offline.create({"transaction_id":"1", "batch_id":"day-1"}, "old_create_key")
    live = LiveCaseQAService(LiveConversationStore(path), h.resolve, h.check, budget=h.budget, clock=lambda:1000)
    with pytest.raises(SessionError) as error:
        live.get(session["conversation_id"])
    assert error.value.code == "session_store_invalid"
    assert not h.budget.calls


def test_unapproved_api_cannot_reserve_or_send_and_balances_are_unknown(h):
    h.budget.approved = False
    session = h.create()
    assert session["mode"] == "live_case_qa"
    assert session["usage"]["budget_status"] == "authorization_required"
    for key in ("session_remaining_calls","case_remaining_calls","batch_remaining_calls",
                "cost_estimate_cny","batch_cost_estimate_cny","unsettled_hold_cny"):
        assert session["usage"][key] is None
    record = Recorder(h.service)
    response = record.client.post(BASE+f"/{session['conversation_id']}/turns", json=h.request(session))
    assert response.status_code == 409
    assert response.json()["mode"] == "live_case_qa"
    assert response.json()["error_code"] == "case_qa_not_authorized"
    assert response.json()["new_model_attempts"] == 0
    assert not h.budget.reservations and not h.budget.calls
    at, _ = page(h, session)
    assert at.chat_input[0].disabled and "未发送模型请求" in text(at)


def test_unavailable_budget_preserves_saved_answer_raw_and_evidence(h):
    session = h.create()
    request = h.request(session)
    h.service.follow(session["conversation_id"], request)
    h.budget.unavailable = True
    value = h.service.get(session["conversation_id"])
    assert value["mode"] == "live_case_qa" and value["usage"]["provider"] == "deepseek"
    assert value["usage"]["budget_status"] == "unavailable"
    assert value["usage"]["real_deepseek_attempts"] == 1 and value["usage"]["mock_attempts_confirmed"] == 0
    assert all(value["usage"][key] is None for key in ("session_remaining_calls","batch_remaining_calls","cost_estimate_cny","unsettled_hold_cny"))
    at, record = page(h, value)
    assert at.chat_input[0].disabled and "预算无法核实" in text(at)
    assert "正常连续付款" in text(at) and "private ledger detail" not in text(at)
    at.button(key="raw_"+session["conversation_id"]+":"+request["message_id"]).click().run()
    at.button(key="qa_evidence_"+session["conversation_id"]).click().run()
    assert not at.exception and record.count("GET","/raw") == record.count("GET","/evidence") == 1
    assert at.code and len(h.budget.calls) == 1 and not record.count("POST","/turns")


def test_live_natural_question_once_and_source_labels_remain_distinct(h):
    session = h.create()
    at, record = page(h, session)
    ask(at, "这些线索有没有正常解释？")
    assert record.count("POST","/turns") == len(h.budget.calls) == 1
    assert h.budget.calls[0]["view"]["question"] == "这些线索有没有正常解释？"
    for label in ("程序证据","XGBoost／图模型信号","DeepSeek回答 · 模型解释", "DeepSeek回答 · 未核实假设", "信息缺口／核实建议 · 尚未执行"):
        assert label in text(at)
    assert "本轮DeepSeek请求尝试：1次" in text(at) and "非确认账单" in text(at)
    assert "离线模拟问答" not in text(at) and "DeepSeek尚未启用" not in text(at)
    at.run()
    at.button(key="qa_refresh").click().run()
    assert not at.exception and record.count("POST","/turns") == 1
    assert not record.count("GET","/raw") and "qa_message" not in at.query_params


def test_live_stop_persists_without_refresh_reenable_or_resend(h):
    session = h.create()
    at, record = page(h, session)
    ask(at)
    at.checkbox[0].check().run()
    assert not at.exception and at.chat_input[0].disabled
    assert not h.restart().get(session["conversation_id"])["qa_enabled"]
    assert record.count("PUT","/control") == 1
    at.run()
    at.button(key="qa_refresh").click().run()
    assert record.count("PUT","/control") == 1 and len(h.budget.calls) == 1
    assert "正常连续付款" in text(at)


def test_other_window_stop_is_not_undone_by_stale_live_ui(h):
    session = h.create()
    at, record = page(h, session)
    h.service.control(session["conversation_id"], {"enabled":False})
    at.run()
    assert not at.exception and at.checkbox[0].value and at.chat_input[0].disabled
    assert not record.count("PUT","/control") and not h.budget.calls


def test_lost_live_reply_and_new_websocket_use_get_only(h):
    session = h.create()
    at, record = page(h, session)
    record.drop_reply = True
    ask(at)
    fresh = AppTest.from_string(PAGE, default_timeout=15)
    fresh.session_state["qa_client"] = QAClient(record)
    fresh.query_params.update(at.query_params)
    fresh.run()
    assert not fresh.exception and "正常连续付款" in text(fresh)
    assert "private response transport detail" not in text(fresh)
    assert record.count("POST","/turns") == len(h.budget.calls) == 1


def test_live_raw_download_is_explicit_unreviewed_and_report_link_independent(h, monkeypatch):
    import streamlit as st
    captured = []
    actual = st.download_button
    def capture(label, data, **kwargs):
        captured.append((label, data, kwargs))
        return actual(label, data, **kwargs)
    monkeypatch.setattr(st, "download_button", capture)
    session = h.create()
    at, record = page(h, session)
    ask(at)
    message_id = h.service.get(session["conversation_id"])["turns"][0]["message_id"]
    assert not record.count("GET","/raw")
    at.button(key="raw_"+session["conversation_id"]+":"+message_id).click().run()
    at.button(key="qa_evidence_"+session["conversation_id"]).click().run()
    assert not at.exception
    labels = {label for label,_,_ in captured}
    assert {"下载程序证据","下载DeepSeek问答记录","下载未审原始回答字节"} <= labels
    for label, data, kwargs in captured:
        assert kwargs["on_click"] == "ignore"
        if label == "下载DeepSeek问答记录":
            assert kwargs["file_name"].startswith("case-qa-deepseek-transcript-")
            assert "DeepSeek本案问答记录" in data and "不是完整报告" in data and "离线模拟" not in data
        elif label == "下载程序证据":
            assert json.loads(data)["source"] == "program" and json.loads(data)["mode"] == "live_case_qa"
        elif label == "下载未审原始回答字节":
            assert kwargs["file_name"].startswith("case-qa-unreviewed-raw-")
            assert type(data) is bytes
    raw = h.service.raw(session["conversation_id"], message_id)
    assert raw["source"] == "unreviewed_deepseek_output" and raw["mode"] == "live_case_qa"
    assert json.loads(b64decode(raw["raw_response"]["content_base64"]))["version"] == "case-qa-answer-v1"
    link = at.get("link_button")[0]
    assert link.proto.label == "打开原报告工作台" and link.proto.url == "http://127.0.0.1:8501/"
    assert len(h.budget.calls) == record.count("POST","/turns") == 1


def test_invalid_live_prose_is_not_published_as_an_accepted_answer(h):
    h.budget.raw_override = "private broken JSON"
    session = h.create()
    at, record = page(h, session)
    ask(at)
    assert "未通过格式或引用检查" in text(at)
    assert "private broken JSON" not in text(at)
    message_id = h.service.get(session["conversation_id"])["turns"][0]["message_id"]
    at.button(key="raw_"+session["conversation_id"]+":"+message_id).click().run()
    assert "private broken JSON" in text(at)
    assert record.count("POST","/turns") == len(h.budget.calls) == 1


@pytest.mark.parametrize("status", ["authorization_required","unavailable"])
@pytest.mark.parametrize("key", ["session_remaining_calls","batch_remaining_calls","cost_estimate_cny","unsettled_hold_cny"])
def test_live_client_rejects_fabricated_zero_balances_when_unconfirmed(h, status, key):
    h.budget.approved = False
    session = h.create()
    if status == "unavailable":
        h.budget.unavailable = True
    usage = h.service.get(session["conversation_id"])["usage"]
    usage[key] = "0.000000" if key.endswith("_cny") else 0
    with pytest.raises(ValueError):
        _validate_usage(usage)


def test_live_client_rejects_offline_api_response():
    class OfflineResponse:
        def request(self, *args, **kwargs):
            return httpx.Response(200, json={"mode":"offline_simulation"})
    with pytest.raises(ClientError) as error:
        QAClient(OfflineResponse()).call("POST", BASE+"/"+"1"*32+"/turns", {"message_id":"2"*32})
    assert error.value.uncertain
