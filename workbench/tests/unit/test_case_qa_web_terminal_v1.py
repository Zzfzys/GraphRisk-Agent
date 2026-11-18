"""Legacy UI rejection/uncertainty regressions; no server or model requests."""
from copy import deepcopy

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from extensions.case_qa_web_v1 import legacy_ui as ui


VIEW = {"conversation_id": "a" * 32, "turns": [{"turn_id": "b" * 32, "state": "completed"}],
    "evidence": {"evidence_version": "c" * 64}}
SCRIPT = "from extensions.case_qa_web_v1.legacy_ui import render_workbench\nrender_workbench()"


def page(monkeypatch, handler, pending=None, pending_error=None):
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, path, deepcopy(kwargs)))
        return handler(method, path, **kwargs)
    monkeypatch.setattr(ui, "api_call", request)
    monkeypatch.setattr(ui, "conversation_selection", lambda: (False, None))
    monkeypatch.setattr(ui, "show_conversation", lambda value: ui.st.text("已保存的调查证据"))
    at = AppTest.from_string(SCRIPT, default_timeout=15)
    at.session_state["conversation_view"] = deepcopy(VIEW)
    at.session_state["conversation_pending"] = deepcopy(pending)
    if pending_error:
        at.session_state["conversation_pending_error"] = pending_error
    at.run()
    assert not at.exception
    return at, calls


def test_repeated_422_unlocks_chat_and_has_terminal_status(monkeypatch):
    error = ui.ClientError("clarification_required", "请使用支持的任务表达。", False)
    at, calls = page(monkeypatch, lambda *args, **kwargs: (None, error))
    for question in ("你好", "这个怎么理解？"):
        at.chat_input[0].set_value(question).run()
        assert not at.exception and not at.chat_input[0].disabled
        assert at.session_state["conversation_pending"] is None
        assert at.get("status")[0].label == "输入未受理"
        assert at.get("status")[0].state == "error"
    assert [call[0] for call in calls] == ["POST", "POST"]
    assert calls[0][2]["key"] != calls[1][2]["key"]
    at.run()
    assert not at.chat_input[0].disabled and len(calls) == 2


def test_timeout_keeps_key_and_refresh_only_reads(monkeypatch):
    def handler(method, path, **kwargs):
        return (deepcopy(VIEW), None) if method == "GET" else (None, ui.ClientError("request_unconfirmed", "响应尚未确认。", True))
    at, calls = page(monkeypatch, handler)
    at.chat_input[0].set_value("生成中文研判").run()
    pending = deepcopy(at.session_state["conversation_pending"])
    assert at.get("status")[0].label == "请求结果待核对"
    assert at.session_state["conversation_pending_error"]["uncertain"]
    at.run()
    assert not at.exception and at.chat_input[0].disabled
    assert not any("重新" in button.label and "查询" not in button.label for button in at.button)
    next(button for button in at.button if button.label == "只查询已有会话（不重新提交）").click().run()
    assert not at.exception and at.session_state["conversation_pending"] == pending
    assert [call[0] for call in calls] == ["POST", "GET"]


def test_old_unsupported_pending_clears_only_with_parser_proof(monkeypatch):
    pending = {"path": "/api/v1/conversations/" + VIEW["conversation_id"] + "/turns",
        "body": {"message": "你好"}, "key": "old_key_123"}
    at, calls = page(monkeypatch, lambda *args, **kwargs: pytest.fail("must not send"), pending)
    assert not calls and not at.chat_input[0].disabled
    assert at.session_state["conversation_pending"] is None
    assert at.session_state["conversation_last_rejection"] == {
        "key": "old_key_123", "error_code": "clarification_required",
        "basis": "same_frozen_parser_rejects_before_dispatch"}


@pytest.mark.parametrize("message,error", [("生成中文研判", None), ("你好", {"uncertain": True})])
def test_migrated_uncertain_request_never_clears_or_resends(monkeypatch, message, error):
    pending = {"path": "/api/v1/conversations/" + VIEW["conversation_id"] + "/turns",
        "body": {"message": message}, "key": "old_key_123"}
    at, calls = page(monkeypatch, lambda *args, **kwargs: pytest.fail("must not send"), pending, error)
    assert not calls and at.chat_input[0].disabled
    assert at.session_state["conversation_pending"] == pending


@pytest.mark.parametrize("status,value,code,uncertain", [
    (422, {"error_code": "clarification_required"}, "clarification_required", False),
    (422, {"detail": [{"loc": ["body", "message"], "type": "string_too_long"}]}, "validation_error", False),
    (409, {"error_code": "turn_in_progress_recovery_required"}, "turn_in_progress_recovery_required", True),
    (500, {"error_code": "unexpected_error"}, "unexpected_error", True),
    (200, {"not": "a conversation"}, "request_unconfirmed", True),
])
def test_client_keeps_error_code_and_uncertainty(monkeypatch, status, value, code, uncertain):
    factory = httpx.Client
    monkeypatch.setattr(ui.httpx, "Client", lambda **kwargs: factory(**kwargs,
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=value))))
    result, error = ui.api_call("POST", "/api/v1/conversations", body={}, key="request_key")
    assert result is None and error.error_code == code and error.uncertain is uncertain
