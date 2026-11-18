"""Terminal input errors unlock the legacy UI; unknown requests stay read-only."""
from dataclasses import dataclass
import os
import re
from uuid import uuid4

import httpx
import streamlit as st

from graphrisk.demo.conversation_workbench import (
    ERRORS, conversation_selection, show_conversation,
)
from graphrisk.serving.conversations import ConversationError, normalize_task


# These API codes occur before a turn is claimed. Other failures may follow work.
PRE_DISPATCH = {
    "clarification_required", "transaction_required", "invalid_idempotency_key",
    "invalid_conversation_id", "case_switch_requires_new_conversation",
    "parent_turn_conflict", "evidence_version_conflict", "conversation_turn_limit",
    "conversation_budget_limit", "narrative_unconfigured", "investigation_generation_disabled",
    "conversation_service_unconfigured",
}


@dataclass(frozen=True)
class ClientError:
    error_code: str
    message: str
    uncertain: bool


def api_call(method, path, *, body=None, key=None):
    base = os.getenv("GRAPHRISK_CONVERSATION_API_URL", "http://127.0.0.1:8002")
    try:
        with httpx.Client(base_url=base, timeout=610, trust_env=False, follow_redirects=False) as client:
            response = client.request(method, path, json=body,
                headers={"Idempotency-Key": key} if key else {})
        value = response.json()
    except (httpx.HTTPError, ValueError):
        return None, ClientError("request_unconfirmed",
            "API 无法连接或响应尚未确认。保留原请求，请只查询已有会话，避免重复生成。", True)
    if response.is_error:
        code = value.get("error_code") if isinstance(value, dict) else None
        code = code if isinstance(code, str) else None
        # FastAPI's structured 422 is also rejected before the handler runs.
        validation = response.status_code == 422 and isinstance(value, dict) and isinstance(value.get("detail"), list)
        terminal = code in PRE_DISPATCH or validation
        return None, ClientError(code if isinstance(code, str) else "validation_error" if validation else "request_unconfirmed",
            ERRORS.get(code, "输入未通过服务校验，请修改后重新提交。" if terminal else
                "请求结果尚未确认；请只查询已有会话，不要重复提交。"), not terminal)
    if not isinstance(value, dict) or not isinstance(value.get("conversation_id"), str) or not isinstance(value.get("turns"), list):
        return None, ClientError("request_unconfirmed", "接口响应格式无效；保留原请求，先核对已有会话。", True)
    return value, None


def recover_rejected_legacy_input():
    """Only the same frozen parser can prove an old unsupported input never ran."""
    pending = st.session_state.get("conversation_pending")
    if not pending or st.session_state.get("conversation_pending_error"):
        return
    path = pending.get("path", "")
    if not (path == "/api/v1/conversations" or re.fullmatch(r"/api/v1/conversations/[a-f0-9]{32}/turns", path)):
        return
    body = pending.get("body")
    if not isinstance(body, dict) or not isinstance(body.get("message"), str):
        return
    try:
        normalize_task(body["message"], body.get("transaction_id") if path == "/api/v1/conversations" else None)
    except ConversationError as error:
        if str(error) != "clarification_required":
            return
        st.session_state.conversation_last_rejection = {
            "key": pending.get("key"), "error_code": str(error),
            "basis": "same_frozen_parser_rejects_before_dispatch",
        }
        st.session_state.conversation_pending = None
        st.info("上次输入未被任务接口受理，已解除输入锁定；没有重新发送请求。")


def render_workbench():
    st.title("自主调查与中文研判")
    st.caption("任务式证据调查入口，并非自由问答；本地事实、模型研判和未执行建议分别展示。")
    st.session_state.setdefault("conversation_view", None)
    st.session_state.setdefault("conversation_pending", None)
    recover_rejected_legacy_input()
    submitted, request = conversation_selection()
    if submitted:
        st.session_state.conversation_pending = {"path": "/api/v1/conversations", "body": request, "key": uuid4().hex}
        st.session_state.conversation_pending_error = None
    if st.button("刷新已有会话") and st.session_state.conversation_view:
        value, error = api_call("GET", "/api/v1/conversations/" + st.session_state.conversation_view["conversation_id"])
        if value:
            st.session_state.conversation_view = value
        else:
            st.error(error.message)
    value = st.session_state.conversation_view
    message = None
    if value:
        show_conversation(value)
        message = st.chat_input("例如：解释依据与反证、政策前提、生成中文研判、继续核查",
            disabled=bool(st.session_state.conversation_pending), submit_mode="disable")
        if message:
            st.session_state.conversation_pending = {
                "path": "/api/v1/conversations/" + value["conversation_id"] + "/turns",
                "body": {"parent_turn_id": value["turns"][-1]["turn_id"],
                    "evidence_version": value["evidence"]["evidence_version"], "message": message}, "key": uuid4().hex}
            st.session_state.conversation_pending_error = None
    pending = st.session_state.conversation_pending
    if pending and not (submitted or message):
        error_record = st.session_state.get("conversation_pending_error")
        if error_record:
            st.status("请求结果待核对", state="error", expanded=False)
            st.error(error_record.get("message", "原请求结果尚未确认。"))
        st.warning("原请求结果尚未确认，输入暂时锁定。刷新和下方查询都不会重新提交或调用模型。")
        st.caption("原请求标识：" + pending["key"])
        if value and st.button("只查询已有会话（不重新提交）"):
            result, error = api_call("GET", "/api/v1/conversations/" + value["conversation_id"])
            if result:
                st.session_state.conversation_view = result
                st.rerun()
            else:
                st.error(error.message)
        elif not value:
            st.info("创建会话的结果未知，尚无可查询的会话编号；需先核对服务端原请求。")
    if pending and (submitted or message):
        with st.status("正在处理本轮任务…", expanded=False) as status:
            result, error = api_call("POST", pending["path"], body=pending["body"], key=pending["key"])
            if result:
                status.update(label="本轮任务已完成", state="complete")
            else:
                status.update(label="请求结果待核对" if error.uncertain else "输入未受理", state="error")
        if result:
            st.session_state.conversation_view = result
            st.session_state.conversation_pending = None
            st.session_state.conversation_pending_error = None
            st.rerun()
        else:
            st.error(error.message)
            st.session_state.conversation_pending_error = vars(error)
            if not error.uncertain:
                st.session_state.conversation_last_rejection = {"key": pending["key"], **vars(error)}
                st.session_state.conversation_pending = None
            else:
                st.caption("原请求标识已保留；只可查询，不自动重发。")
                # The input was drawn before POST; redraw it disabled now.
                st.rerun()
