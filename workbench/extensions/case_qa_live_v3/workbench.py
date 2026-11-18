"""Live short-first presentation, without changing the frozen offline or live UI."""
import json
from uuid import uuid4

import streamlit as st

from extensions.case_qa_v1.behavior import ERRORS
from extensions.case_qa_v1.sessions import NOTICES
from extensions.case_qa_live_v1.workbench import (
    BASE, STATUS, ClientError, QAClient as LiveQAClient,
    _budget, _control_intent, _error, _raw, transcript,
)
from extensions.case_qa_quality_v1.workbench import _details
from .service import REVISION, ANSWER_REVISION


class QAClient(LiveQAClient):
    def call(self, method, path, body=None, key=None):
        value = super().call(method,path,body,key)
        try:
            if value.get("adapter_revision") != REVISION or value.get("answer_revision") != ANSWER_REVISION:
                raise ValueError
            turns = [value["turn"]] if "turn" in value else value.get("turns",[])
            for turn in turns:
                result = turn["result"]
                if result.get("mode") != "live_case_qa" or result.get("source") not in {"system","deepseek","program_and_deepseek"}:
                    raise ValueError
                if result["status"] == "answered" and result["answer_payload"].get("source") != "program_projection_and_deepseek":
                    raise ValueError
                for field in ("diagnostics", "style_notes"):
                    if field in result and (type(result[field]) is not list or any(type(item) is not dict or type(item.get("message")) is not str for item in result[field])):
                        raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ClientError("request_unconfirmed" if method in {"POST","PUT"} else "read_failed",
                              uncertain=method in {"POST","PUT"}) from None
        return value


def compact_score_summary(signal):
    """Only label the card's existing priority; do not infer thresholds or round."""
    metric = signal.get("metric")
    daily = metric == "primary_model_score" and signal.get("temporal_scope") == "relative_day_end"
    if signal.get("availability") != "available":
        label = "日终模型信号" if daily else "实验图信号" if metric == "experimental_graph_score" else "模型信号"
        return label+"未取得（不等于零或安全）"
    if daily:
        level = {"high":"高", "medium":"中", "low":"低"}.get(signal.get("risk_priority"))
        if level:
            return "日终"+level+"调查优先级（未校准）"
    return "模型信号已取得（未校准；完整原值见程序证据）"


def _turn(client, identifier, turn):
    result = turn["result"]
    with st.chat_message("user"):
        st.text(turn["message"])
    with st.chat_message("assistant"):
        st.caption("系统状态 · "+STATUS.get(result["status"], "状态待核验"))
        if result["status"] == "answered":
            payload = result["answer_payload"]
            if not any(payload[part] for part in ("judgment", "hypotheses", "gaps")):
                for fact in payload["facts"]:
                    st.caption("程序事实答复" if fact["kind"] == "data_fact" else "程序模型信号")
                    st.text(fact["text"] if fact["kind"] == "data_fact" else compact_score_summary(fact))
            for part,label in (("judgment","DeepSeek回答 · 模型解释"),
                               ("hypotheses","DeepSeek回答 · 未核实假设"),
                               ("gaps","信息缺口／核实建议 · 尚未执行")):
                for item in payload[part]:
                    st.caption(label)
                    st.text(item["text"])
            _details(payload)
        elif result.get("notice"):
            st.warning("系统状态 · "+result["notice"])
        if result.get("diagnostics"):
            with st.expander("查看具体检查项", expanded=False):
                for item in result["diagnostics"]:
                    st.text(item["message"])
                st.json(result["diagnostics"], expanded=False)
        if result.get("style_notes"):
            with st.expander("表达建议（不改变检查状态）", expanded=False):
                for item in result["style_notes"]:
                    st.text(item["message"])
                st.json(result["style_notes"], expanded=False)
        attempts = result.get("model_attempts")
        st.caption("本轮DeepSeek请求尝试："+("未确认（已占位）" if attempts is None else str(attempts)+"次")+" · 新工具请求0次 · 不执行业务处置")
        if turn.get("context"):
            st.caption(turn["context"]["notice"])
        _raw(client, identifier, turn)


def render_case_qa(client):
    """Live short-first UI; existing report workbench stays separate."""
    st.title("本案问答")
    st.info("DeepSeek本案问答 · 已封批的日终证据 · 每条问题最多一次请求")
    st.caption("先看简短解释；完整事实、评分原值与检查详情按需展开。证据是已封批的日终快照，不表示交易发生时可用。")
    st.caption("问题中的本批交易编号会替换为别名后发送；问答不新增数据检索或业务处置。")
    state=st.session_state
    state.setdefault("qa_pending",None)
    state.setdefault("qa_last_error",None)
    identifier=st.query_params.get("qa_conversation")
    creation=st.query_params.get("qa_create")
    value=None
    try:
        if identifier:
            value=client.call("GET",BASE+"/"+identifier)
        elif creation:
            value=client.call("GET",BASE+"/created/"+creation)
            identifier=value["conversation_id"]
            st.query_params["qa_conversation"]=identifier
    except ClientError as error:
        _error(error)
    # URL keeps only opaque IDs; never question text or raw evidence.
    pending_id=st.query_params.get("qa_message")
    if value and pending_id:
        saved=next((t for t in value["turns"] if t["message_id"]==pending_id),None)
        if saved and saved["result"]["status"]!="in_flight":
            st.query_params.pop("qa_message",None)
            state.qa_pending=None
            state.qa_last_error=None
        else:
            state.qa_pending=pending_id
    with st.form("qa_create_form"):
        transaction=st.text_input("交易编号（仅限本轮两笔已登记交易）",key="qa_transaction")
        batch=st.text_input("封批编号（留空使用获准快照）",value="",key="qa_batch")
        create=st.form_submit_button("新建本案问答",disabled=bool(state.qa_pending))
    if create:
        key=uuid4().hex
        st.query_params.clear()
        st.query_params["qa_create"]=key
        state.qa_last_error=None
        try:
            value=client.call("POST",BASE,{"transaction_id":transaction.strip(),"batch_id":batch.strip() or None},key)
            st.query_params["qa_conversation"]=value["conversation_id"]
            state.qa_pending=None
            st.rerun()
        except ClientError as error:
            state.qa_last_error=error.code
            _error(error)
    st.link_button("打开原报告工作台", "http://127.0.0.1:8501/",help="沿用独立完整报告流程；本页不触发报告生成。")
    if not value:
        st.caption("选择交易后可直接自然提问。新建或查询会话不调用模型。")
        return
    identifier=value["conversation_id"]
    st.caption("当前交易别名："+value["case_alias"]+" · "+value["batch_revision"])
    if value.get("validity_error"):
        st.warning(value.get("notice") or "证据已失效；仅可查看旧记录，请新建会话。")
    for signal in value.get("model_signals",[]):
        st.caption("程序投影 · XGBoost／图模型信号")
        st.text(compact_score_summary(signal))
    enabled=value.get("qa_enabled",True)
    control_key="qa_stop_"+identifier
    intent=state.get("qa_control_intent")==identifier
    if not intent:
        state[control_key]=not enabled
    stop=st.checkbox("停止本会话后续问答",key=control_key,on_change=_control_intent,args=(identifier,))
    st.caption("停止不撤回已开始的请求；证据、原文和旧记录仍可查看。")
    if intent:
        state.qa_control_intent=None
        try:
            value=client.call("PUT",BASE+f"/{identifier}/control",{"enabled":not stop})
            if stop:
                st.query_params.pop("qa_message",None)
                state.qa_pending=None
            st.rerun()
        except ClientError as error:
            _error(error)
    if st.button("只读刷新状态",key="qa_refresh"):
        st.rerun()
    usage=value["usage"]
    st.caption(f"本会话已确认DeepSeek尝试{usage['real_deepseek_attempts']}次 · 未确认占位{usage['unresolved_slots']}个；成功、失败与未知分别保留")
    _budget(usage)
    with st.expander("程序证据",expanded=False):
        if st.button("读取本案程序证据",key="qa_evidence_"+identifier):
            try:
                state["qa_evidence_view_"+identifier]=client.call("GET",BASE+f"/{identifier}/evidence")
            except ClientError as error:
                _error(error)
        evidence=state.get("qa_evidence_view_"+identifier)
        if evidence:
            if evidence.get("validity_error"):
                st.warning("这是会话绑定的旧证据快照，当前有效性检查未通过。")
            for card in evidence["evidence"]["cards"]:
                st.text(card["ref"]+" · "+card["text"])
            st.download_button("下载程序证据",json.dumps(evidence,ensure_ascii=False,indent=2),file_name="case-qa-program-evidence-"+identifier+".json",
                               mime="application/json",key="evidence_download_"+identifier,on_click="ignore")
    for turn in value["turns"]:
        _turn(client,identifier,turn)
    st.download_button("下载DeepSeek问答记录",transcript(value),file_name="case-qa-deepseek-transcript-"+identifier+".txt",
                       mime="text/plain",key="transcript_"+identifier,on_click="ignore")
    if state.qa_last_error:
        _error(ClientError(state.qa_last_error))
    if state.qa_pending:
        st.warning("原消息状态尚未确认；这里只读查询，不自动补发。可停止本会话后续问答，再新建会话。")
    reason=value.get("send_block_reason") or usage.get("block_reason")
    if reason:
        st.warning("系统状态 · "+NOTICES.get(reason,ERRORS.get(reason,"当前暂停新问答。")))
    message=st.chat_input("直接询问当前交易，例如：为什么需要关注？有没有正常解释？",key="qa_chat",max_chars=2000,
                          disabled=bool(reason or state.qa_pending),submit_mode="disable")
    if message:
        message_id=uuid4().hex
        state.qa_pending=message_id
        st.query_params["qa_message"]=message_id
        request={"message_id":message_id,"parent_turn_id":value["turns"][-1]["message_id"] if value["turns"] else None,
                 "evidence_version":value["evidence_version"],"message":message}
        with st.status("正在请求DeepSeek本案问答…",expanded=False) as progress:
            try:
                client.call("POST",BASE+f"/{identifier}/turns",request)
            except ClientError as error:
                state.qa_last_error=error.code
                if not error.uncertain:
                    state.qa_pending=None
                    st.query_params.pop("qa_message",None)
                progress.update(label="系统状态：请求未确认或未完成",state="error")
            else:
                state.qa_pending=None
                state.qa_last_error=None
                st.query_params.pop("qa_message",None)
                progress.update(label="本轮处理结束，读取已保存状态",state="complete")
        st.rerun()
