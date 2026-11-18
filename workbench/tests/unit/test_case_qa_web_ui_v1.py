"""The integrated natural QA page uses only fixture HTTP and synthetic evidence."""
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from extensions.case_qa_compact_v1.workbench import QAClient
from extensions.case_qa_live_v1.workbench import BASE
from test_case_qa_compact_ui_v1 import Harness, answer
from test_case_qa_live_service_ui_v1 import Recorder


PAGE = Path(__file__).parents[2] / "extensions/case_qa_web_v1/web.py"


class WebRecorder(Recorder):
    def __init__(self, service, status):
        super().__init__(service)
        self.status = status
        self.reject_next = False
        self.timeout_next = False

    def get(self, path):
        return self.request("GET", path)

    def request(self, method, path, **kwargs):
        if path == "/api/v1/case-qa/web-status":
            self.calls.append((method, path, deepcopy(kwargs)))
            return httpx.Response(200, json=self.status, request=httpx.Request(method, "http://fixture" + path))
        if method == "POST" and path.endswith("/turns"):
            if self.reject_next or self.timeout_next:
                self.calls.append((method, path, deepcopy(kwargs)))
                if self.timeout_next:
                    self.timeout_next = False
                    raise httpx.ReadTimeout("synthetic request outcome is unknown")
                self.reject_next = False
                return httpx.Response(422, json={"mode":"live_case_qa", "source":"system",
                    "error_code":"empty_question", "new_model_attempts":0})
        return super().request(method, path, **kwargs)


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path / "web-fixture.sqlite3")


def page(h, *, archived=False):
    archive = h.create("2", "web_archive_fixture_key")
    if archived:
        h.service.follow(archive["conversation_id"], h.request(archive, "这笔交易为何需要关注？"))
        h.budget.raw_override = answer(h.cases["2"]["evidence"], text="已确认欺诈。", refs=["F1"])
        h.service.follow(archive["conversation_id"], h.request(archive, "还有什么解释？"))
        h.budget.raw_override = None
    current = h.create("1", "web_current_fixture_key")
    status = {"web_gate_revision":"case-qa-web-v1", "can_send":True,
        "notice":"合成测试专用授权状态，不会连接真实模型。",
        "archive_conversation_id":archive["conversation_id"], "m1_transaction_id":"1"}
    record = WebRecorder(h.service, status)
    at = AppTest.from_file(str(PAGE), default_timeout=15)
    at.session_state["web_qa_client"] = QAClient(record)
    at.session_state["web_qa_m1"] = current["conversation_id"]
    at.run()
    assert not at.exception
    return at, record, archive, current


def current_case(at):
    at.radio[1].set_value("M1 本案问答").run()
    assert not at.exception and not at.chat_input(key="web_qa_chat").disabled


def text(at):
    return "\n".join(str(item.value) for kind in ("text", "caption", "info", "warning", "error") for item in at.get(kind))


def test_natural_question_uses_dedicated_qa_endpoint_and_displays_answer(h):
    at, record, _, current = page(h)
    current_case(at)
    question = "这些关联有哪些正常解释，我应该先核实什么？"
    at.chat_input(key="web_qa_chat").set_value(question).run()
    assert not at.exception
    posts = [(path, kwargs) for method, path, kwargs in record.calls if method == "POST"]
    assert len(posts) == 1 and posts[0][0] == BASE + "/" + current["conversation_id"] + "/turns"
    assert posts[0][1]["json"]["message"] == question
    assert len(h.budget.calls) == 1 and h.budget.calls[0]["view"]["question"] == question
    assert "DeepSeek回答 · 模型解释" in text(at)
    assert "日终模型信号需结合本案核实。" in text(at)
    assert "请使用支持的任务表达" not in text(at)
    at.run()
    assert record.count("POST", "/turns") == 1


def test_explicit_422_allows_next_question_without_stale_pending(h):
    at, record, _, _ = page(h)
    current_case(at)
    record.reject_next = True
    at.chat_input(key="web_qa_chat").set_value("这个问题被服务端拒绝").run()
    assert not at.exception and not at.chat_input(key="web_qa_chat").disabled
    assert at.session_state["web_qa_pending"] is None
    assert at.session_state["web_qa_error"] == "empty_question"
    assert not h.budget.calls
    at.chat_input(key="web_qa_chat").set_value("哪些信息尚未核实？").run()
    assert not at.exception and at.session_state["web_qa_pending"] is None
    assert at.session_state["web_qa_error"] is None
    assert record.count("POST", "/turns") == 2 and len(h.budget.calls) == 1
    assert "DeepSeek回答 · 模型解释" in text(at)


def test_timeout_preserves_message_id_and_refresh_never_posts_again(h):
    at, record, _, current = page(h)
    current_case(at)
    record.timeout_next = True
    at.chat_input(key="web_qa_chat").set_value("是否有正常原因？").run()
    pending = deepcopy(at.session_state["web_qa_pending"])
    assert not at.exception and at.chat_input(key="web_qa_chat").disabled
    assert pending["conversation_id"] == current["conversation_id"]
    assert at.session_state["web_qa_error"] == "request_unconfirmed"
    assert "不会自动重发" in text(at)
    at.run()
    next(button for button in at.button if button.label == "只读刷新问答状态").click().run()
    assert not at.exception and at.chat_input(key="web_qa_chat").disabled
    assert at.session_state["web_qa_pending"] == pending
    assert record.count("POST", "/turns") == 1 and not h.budget.calls
    sent = next(kwargs["json"] for method, path, kwargs in record.calls if method == "POST" and path.endswith("/turns"))
    assert sent["message_id"] == pending["message_id"]


def test_archive_shows_saved_provenance_and_stays_read_only(h):
    at, record, archive, _ = page(h, archived=True)
    before = deepcopy(h.service.get(archive["conversation_id"]))
    assert [turn["result"]["status"] for turn in before["turns"]] == ["answered", "review_required"]
    assert at.chat_input(key="web_qa_chat").disabled
    visible = text(at)
    assert "已保存的两轮真实回答" in visible and "当前只读" in visible
    assert "DeepSeek回答 · 模型解释" in visible
    assert "系统状态 · 内容待复核" in visible
    assert "本轮DeepSeek请求尝试：1次" in visible
    assert any(button.label == "下载问答与事实原值" for button in at.get("download_button"))
    at.run()
    next(button for button in at.button if button.label == "只读刷新问答状态").click().run()
    assert not at.exception and not record.count("POST", "/turns")
    assert len(h.budget.calls) == 2
    assert h.service.get(archive["conversation_id"]) == before
