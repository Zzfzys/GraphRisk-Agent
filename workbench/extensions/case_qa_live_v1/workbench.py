"""Native live QA UI; independent version preserves the frozen offline UI."""
from base64 import b64decode
from decimal import Decimal
import json
import re
from uuid import uuid4

import streamlit as st

from extensions.case_qa_v1.behavior import ERRORS
from extensions.case_qa_v1.evidence_contract import validate_view
from extensions.case_qa_v1.sessions import NOTICES

BASE = "/api/v1/case-qa/conversations"
STATUS = {"in_flight":"处理中／待确认", "answered":"合同检查通过，内容仍需阅读复核", "invalid_response":"未通过格式或引用检查",
          "review_required":"内容待复核", "response_unknown":"响应未知，已暂停新问答"}


class ClientError(ValueError):
    def __init__(self, code, *, uncertain=False):
        self.code, self.uncertain = code, uncertain
        super().__init__(code)


class QAClient:
    """Uses the existing httpx-compatible client; preview injects TestClient.

    Transport does not retry. An uncertain POST must be queried, never resent
    by a page rerun. No URL, model or authorization flag comes from chat text.
    """
    def __init__(self, transport):
        self.transport = transport

    def call(self, method, path, body=None, key=None):
        try:
            response = self.transport.request(method, path, json=body,
                headers={"Idempotency-Key":key} if key else None)
            value = response.json()
            if type(value) is not dict or value.get("mode")!="live_case_qa":
                raise ValueError
        except Exception:
            raise ClientError("request_unconfirmed" if method in {"POST","PUT"} else "read_failed",
                              uncertain=method in {"POST","PUT"}) from None
        if response.status_code>=400:
            code = value.get("error_code")
            if code not in NOTICES and code not in ERRORS:
                code = "read_failed"
            raise ClientError(code, uncertain=code=="checkpoint_recovery_required")
        try:
            if path.endswith("/raw"):
                receipt=value["raw_response"]
                if type(receipt["complete"]) is not bool:
                    raise ValueError
                if "content_base64" in receipt:
                    b64decode(receipt["content_base64"],validate=True)
            elif path.endswith("/evidence"):
                if value["source"]!="program":
                    raise ValueError
                validate_view(value["evidence"])
            elif path.endswith("/turns"):
                _validate_turn(value["turn"])
                if type(value["replayed"]) is not bool or value["turn"]["message_id"]!=body["message_id"]:
                    raise ValueError
            else:
                if (not re.fullmatch(r"[0-9a-f]{32}",value["conversation_id"])
                        or not re.fullmatch(r"[0-9a-f]{64}",value["evidence_version"])
                        or not re.fullmatch(r"[0-9a-f]{32}",value["case_alias"])
                        or type(value["batch_revision"]) is not str or type(value["state"]) is not str
                        or type(value["turns"]) is not list or type(value["qa_enabled"]) is not bool
                        or type(value["usage"]) is not dict or type(value["model_signals"]) is not list):
                    raise ValueError
                for signal in value["model_signals"]:
                    if signal["kind"]!="model_signal" or type(signal["text"]) is not str:
                        raise ValueError
                for turn in value["turns"]:
                    _validate_turn(turn)
                _validate_usage(value["usage"])
        except Exception:
            raise ClientError("request_unconfirmed" if method in {"POST","PUT"} else "read_failed",
                              uncertain=method in {"POST","PUT"}) from None
        return value



def _validate_usage(usage):
    if usage.get("scope")!="live_case_qa" or usage.get("provider")!="deepseek" or usage.get("budget_status") not in {"shared_ledger","authorization_required","unavailable"}:
        raise ValueError
    for key in ("real_deepseek_attempts","unresolved_slots"):
        if type(usage[key]) is not int or usage[key]<0:
            raise ValueError
    if usage["budget_status"]!="shared_ledger":
        if usage.get("authorization")!="not_configured" or any(usage.get(key) is not None for key in
            ("session_remaining_calls","case_remaining_calls","batch_remaining_calls","cost_estimate_cny","batch_cost_estimate_cny","unsettled_hold_cny")):
            raise ValueError
        expected="case_qa_not_authorized" if usage["budget_status"]=="authorization_required" else "budget_unavailable"
        if usage.get("block_reason")!=expected:
            raise ValueError
    if usage["budget_status"]=="shared_ledger":
        if usage.get("authorization")!="approved_case_qa" or usage.get("cost_kind")!="usage_estimate_not_invoice":
            raise ValueError
        for key in ("session_remaining_calls","case_remaining_calls","batch_remaining_calls",
                    "global_remaining_calls","global_deepseek_remaining_calls","protected_route9_calls","protected_web_calls"):
            if type(usage[key]) is not int or usage[key]<0:
                raise ValueError
        for key in ("cost_estimate_cny","unsettled_hold_cny","batch_cost_estimate_cny"):
            if type(usage[key]) is not str or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?",usage[key]):
                raise ValueError


def _budget(usage):
    with st.expander("次数与费用",expanded=False):
        if usage["budget_status"]=="shared_ledger":
            st.caption("共享账本 · 费用为usage估算，非确认账单")
            st.text(f"会话剩余{usage['session_remaining_calls']}次 · 本案跨会话剩余{usage['case_remaining_calls']}次 · 全批剩余{usage['batch_remaining_calls']}次")
            st.text(f"本会话usage费用估算{usage['cost_estimate_cny']}元 · 全批usage费用估算{usage['batch_cost_estimate_cny']}元")
            st.text(f"未结算保守预约{usage['unsettled_hold_cny']}元；预约不等于确认扣费。")
            st.caption(f"保留旧网页{usage['protected_web_calls']}次及路线九{usage['protected_route9_calls']}次；没有自动重试、修复或付费评委。")
        elif usage["budget_status"]=="authorization_required":
            st.warning("尚未取得或注册本批问答授权；未发送模型请求。")
        else:
            st.warning("预算无法核实，禁止新提问；旧回答、证据、原文和下载仍可读取。")
        st.json(usage,expanded=False)


def _validate_turn(turn):
    if type(turn["message"]) is not str or not re.fullmatch(r"[0-9a-f]{32}",turn["message_id"]):
        raise ValueError
    result=turn["result"]
    if result["status"] not in STATUS or type(result.get("model_attempts")) not in (int,type(None)):
        raise ValueError
    if result["status"]=="answered":
        if type(result["answer"]) is not str:
            raise ValueError
        for key in ("facts","judgment","hypotheses","gaps","citations"):
            if type(result["answer_payload"][key]) is not list:
                raise ValueError
        payload=result["answer_payload"]
        for fact in payload["facts"]:
            if fact["kind"] not in {"data_fact","model_signal"} or type(fact["text"]) is not str:
                raise ValueError
        for part in ("judgment","hypotheses","gaps"):
            if any(type(item["text"]) is not str for item in payload[part]):
                raise ValueError
        for ref in payload["citations"]:
            if type(ref["ref"]) is not str or type(ref["text"]) is not str:
                raise ValueError
    if turn.get("context") and type(turn["context"]["notice"]) is not str:
        raise ValueError


def _control_intent(identifier):
    # Only an actual widget event may PUT; stale widget state cannot undo server stop.
    st.session_state.qa_control_intent=identifier


def transcript(value):
    lines=["DeepSeek本案问答记录（事实由程序投影；模型解释及假设仍需核实；不是完整报告）", "交易别名："+value["case_alias"]]
    for turn in value["turns"]:
        result=turn["result"]
        lines.extend(["用户："+turn["message"], "系统状态："+STATUS.get(result["status"],result["status"])])
        if result["status"]=="answered":
            lines.append(result["answer"])
        elif result.get("notice"):
            lines.append("系统："+result["notice"])
    return "\n\n".join(lines)


def _error(error):
    messages={"request_unconfirmed":"请求状态尚未确认，请只查询原请求；不会自动补发。", "read_failed":"无法读取问答服务，请检查服务后只读刷新。"}
    st.error("系统状态 · "+messages.get(error.code, NOTICES.get(error.code, ERRORS.get(error.code,"服务状态不可用。"))))


def _raw(client, identifier, turn):
    token=identifier+":"+turn["message_id"]
    with st.expander("原始回答与技术信息",expanded=False):
        st.caption("本区为未审模型原文；不会作为通过检查的答案或后续事实来源。")
        if st.button("读取未审原文",key="raw_"+token):
            try:
                st.session_state["qa_raw_"+token]=client.call("GET",BASE+f"/{identifier}/turns/{turn['message_id']}/raw")
            except ClientError as error:
                _error(error)
        raw=st.session_state.get("qa_raw_"+token)
        if raw:
            receipt=raw["raw_response"]
            st.caption("原文完整留存" if receipt["complete"] else "原文留存不完整，仅有前缀或元数据；禁止当作完整原文。")
            if "content_base64" in receipt:
                data=b64decode(receipt["content_base64"])
                st.code(data.decode("utf-8",errors="replace"),language=None)
                label="下载未审原始回答字节" if receipt["complete"] else "下载已留存原文前缀"
                st.download_button(label,data,file_name=("case-qa-unreviewed-raw-" if receipt["complete"] else "case-qa-raw-prefix-")+turn["message_id"]+".bin",
                                   key="raw_download_"+token,on_click="ignore")
            st.json({k:v for k,v in receipt.items() if k!="content_base64"},expanded=False)
        st.json({"context":turn.get("context"),"result":{k:v for k,v in turn["result"].items() if k not in {"answer","answer_payload"}}},expanded=False)


def _turn(client, identifier, turn):
    result=turn["result"]
    with st.chat_message("user"):
        st.text(turn["message"])
    with st.chat_message("assistant"):
        st.caption("系统状态 · "+STATUS.get(result["status"],"状态待核验"))
        if result["status"]=="answered":
            payload=result["answer_payload"]
            for fact in payload["facts"]:
                st.caption("程序证据" if fact["kind"]=="data_fact" else "XGBoost／图模型信号")
                st.text(fact["text"])
            for part,label in [("judgment","DeepSeek回答 · 模型解释"),("hypotheses","DeepSeek回答 · 未核实假设"),("gaps","信息缺口／核实建议 · 尚未执行")]:
                for item in payload[part]:
                    st.caption(label)
                    st.text(item["text"])
            with st.expander("查看本轮引用",expanded=False):
                for citation in payload["citations"]:
                    st.text(citation["ref"]+" · "+citation["text"])
                    st.json(citation,expanded=False)
        elif result.get("notice"):
            st.warning("系统状态 · "+result["notice"])
        attempts=result.get("model_attempts")
        st.caption("本轮DeepSeek请求尝试："+("未确认（已占位）" if attempts is None else str(attempts)+"次")+" · 新工具请求0次 · 不执行业务处置")
        if turn.get("context"):
            st.caption(turn["context"]["notice"])
        _raw(client,identifier,turn)


def render_case_qa(client):
    """Independent approved QA UI; original report workbench stays separate."""
    st.title("本案问答")
    st.info("DeepSeek本案问答 · 已封批的日终证据 · 每条问题最多一次请求")
    st.caption("程序事实、XGBoost调查优先级、DeepSeek判断与待核实假设分开显示；资料及授权不足时停止。")
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
        st.caption("XGBoost／图模型信号")
        st.text(signal["text"])
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
