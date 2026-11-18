"""One workbench: natural-language QA and the existing local investigation view."""
from uuid import uuid4

import httpx
import streamlit as st

from extensions.case_qa_compact_v1.workbench import QAClient, ClientError, download_record
from extensions.case_qa_live_v1.workbench import BASE, _budget, _error
from extensions.case_qa_live_v3.workbench import _turn
from extensions.case_qa_web_v1 import legacy_ui


def render_qa(client, status):
    st.title("交易问答")
    st.caption("结合已封批日终证据回答；数据事实、DeepSeek解释和待核实假设分别展示。")
    if status["can_send"]:
        st.success(status["notice"])
    else:
        st.warning(status["notice"])
    state = st.session_state
    state.setdefault("web_qa_pending", None)
    state.setdefault("web_qa_error", None)
    choice = st.radio("查看内容", ["已保存的 L1 真实问答", "M1 本案问答"], horizontal=True)
    if choice.startswith("已保存"):
        identifier = status["archive_conversation_id"]
        st.caption("以下为已保存的两轮真实回答；追问原判为待复核，保留原文，当前只读。")
    else:
        st.caption("M1为本轮中调查优先级交易；原批仅剩最多一次调用，接续许可和预算均通过后才发送。")
        identifier = state.get("web_qa_m1")
        if not identifier and st.button("打开 M1 问答", disabled=bool(state.web_qa_pending)):
            try:
                value = client.call("POST", BASE, {"transaction_id":status["m1_transaction_id"], "batch_id":None}, uuid4().hex)
                state.web_qa_m1 = value["conversation_id"]
                st.rerun()
            except ClientError as error:
                _error(error)
        if not identifier:
            st.info("打开问答只建立本地会话，不调用模型。")
            return
    try:
        with st.spinner("正在读取已保存的问答并核验账本；此过程不调用模型…"):
            value = client.call("GET", BASE+"/"+identifier)
    except ClientError as error:
        _error(error)
        return
    pending = state.web_qa_pending
    if pending and pending["conversation_id"] == identifier:
        saved = next((t for t in value["turns"] if t["message_id"] == pending["message_id"]), None)
        if saved and saved["result"]["status"] != "in_flight":
            state.web_qa_pending = None
            state.web_qa_error = None
    for turn in value["turns"]:
        _turn(client, identifier, turn)
    with st.expander("次数与原始资料", expanded=False):
        _budget(value["usage"])
        st.download_button("下载问答与事实原值", download_record(value),
            file_name="case-qa-"+identifier+".json", mime="application/json", on_click="ignore")
    if state.web_qa_error:
        _error(ClientError(state.web_qa_error))
    if state.web_qa_pending:
        st.warning("原请求结果待确认，只能读取已保存状态；不会自动重发。")
    if st.button("只读刷新问答状态"):
        st.rerun()
    reason = value.get("send_block_reason") or value["usage"].get("block_reason")
    if reason and choice.startswith("M1"):
        st.info(value.get("web_gate", status)["notice"])
    message = st.chat_input("直接询问这笔交易，例如：这些线索有哪些正常解释？", key="web_qa_chat",
        max_chars=2000, disabled=bool(reason or state.web_qa_pending or choice.startswith("已保存")), submit_mode="disable")
    if message:
        message_id = uuid4().hex
        state.web_qa_pending = {"conversation_id":identifier, "message_id":message_id}
        body = {"message_id":message_id, "parent_turn_id":value["turns"][-1]["message_id"] if value["turns"] else None,
            "evidence_version":value["evidence_version"], "message":message}
        st.caption("本地执行核验和模型回答可能需要数分钟；请勿重复提交。结果未知时只查询原请求。")
        with st.status("正在请求 DeepSeek…", expanded=False) as progress:
            try:
                client.call("POST", BASE+"/"+identifier+"/turns", body)
            except ClientError as error:
                state.web_qa_error = error.code
                if not error.uncertain:
                    state.web_qa_pending = None
                progress.update(label="结果待确认" if error.uncertain else "请求未受理", state="error")
            else:
                state.web_qa_pending = None
                state.web_qa_error = None
                progress.update(label="本轮已结束", state="complete")
        st.rerun()


def main():
    st.set_page_config(page_title="GraphRisk-Agent 审查工作台", page_icon=":material/account_tree:", layout="wide")
    view = st.radio("工作区", ["交易问答", "本地调查与报告"], horizontal=True, key="web_workspace")
    if view == "本地调查与报告":
        st.info("此处是本地证据与原报告流程；自然语言提问请切换“交易问答”。旧付费首稿当前暂停。")
        legacy_ui.render_workbench()
        return
    if not isinstance(st.session_state.get("web_qa_client"), QAClient):
        st.session_state.web_qa_client = QAClient(httpx.Client(base_url="http://127.0.0.1:8003", timeout=360, trust_env=False))
    client = st.session_state.web_qa_client
    try:
        with st.spinner("正在核验问答授权与预算；此过程不调用模型…"):
            response = client.transport.get("/api/v1/case-qa/web-status")
            response.raise_for_status()
            status = response.json()
        if status.get("web_gate_revision") != "case-qa-web-v1":
            raise ValueError
    except (httpx.HTTPError, ValueError):
        st.error("问答服务未就绪。当前未发送问题，也不会自动重试；请检查本地8003服务。")
        return
    render_qa(client, status)


if __name__ == "__main__":
    main()
