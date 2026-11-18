"""Native offline QA UI, including fixture-budget readout; no live calls."""
from base64 import b64decode
from decimal import Decimal
import json
import re
from uuid import uuid4

import streamlit as st

from .behavior import ERRORS
from .evidence_contract import validate_view
from .sessions import NOTICES

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
            if type(value) is not dict or value.get("mode")!="offline_simulation":
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
    counts=("mock_attempts_confirmed","unresolved_slots","real_deepseek_attempts")
    if usage.get("budget_status")=="offline_ledger":
        if (usage.get("authorization")!="fixture_only_not_outbound"
                or usage.get("cost_kind")!="usage_estimate_not_invoice"
                or usage.get("provider")!="mock" or usage.get("real_deepseek_attempts")!=0
                or usage.get("model") not in {"deepseek-chat","deepseek-flash"}
                or usage.get("block_reason") not in (None,*NOTICES,*ERRORS)):
            raise ValueError
        counts+=("session_remaining_calls","case_remaining_calls","batch_remaining_calls",
                 "global_remaining_calls","global_deepseek_remaining_calls","protected_route9_calls","protected_web_calls")
        for key in ("cost_estimate_cny","unsettled_hold_cny","batch_cost_estimate_cny"):
            amount=usage[key]
            if type(amount) is not str or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?",amount) or Decimal(amount)<0:
                raise ValueError
    elif usage.get("budget_status")=="unavailable":
        if (set(usage)!={"scope","provider","real_deepseek_attempts","mock_attempts_confirmed","unresolved_slots",
                        "session_remaining_calls","batch_remaining_calls","cost_estimate_cny","unsettled_hold_cny",
                        "budget_status","authorization","block_reason"}
                or usage["scope"]!="case_qa_offline_session" or usage["provider"]!="mock"
                or usage["real_deepseek_attempts"]!=0 or usage["authorization"]!="not_configured"
                or usage["block_reason"]!="budget_unavailable"
                or any(usage[key] is not None for key in
                       ("session_remaining_calls","batch_remaining_calls","cost_estimate_cny","unsettled_hold_cny"))):
            raise ValueError
    elif usage.get("budget_status")!="not_connected":
        raise ValueError
    for key in counts:
        if type(usage[key]) is not int or usage[key]<0:
            raise ValueError


def _budget(usage):
    with st.expander("次数与费用",expanded=False):
        if usage["budget_status"]=="offline_ledger":
            st.caption("离线合成账本 · 费用为usage估算，非确认账单")
            st.text(f"会话剩余{usage['session_remaining_calls']}次 · 本案跨会话剩余{usage['case_remaining_calls']}次 · 全批剩余{usage['batch_remaining_calls']}次")
            st.text(f"本会话usage费用估算{usage['cost_estimate_cny']}元 · 全批usage费用估算{usage['batch_cost_estimate_cny']}元")
            st.text(f"未结算保守预约{usage['unsettled_hold_cny']}元；预约不等于确认扣费，也不计为已确认账单。")
            st.text(f"共享总剩余{usage['global_remaining_calls']}次 · DeepSeek分项剩余{usage['global_deepseek_remaining_calls']}次")
            st.text(f"保护中的旧任务余量：路线九{usage['protected_route9_calls']}次 · 网页首稿{usage['protected_web_calls']}次")
            st.caption("以上仅为合成测试额度，不能用于真实调用；不借用旧报告授权。"+usage["model"]+"仅为模拟配置，DeepSeek尚未启用。")
            if usage.get("block_reason"):
                st.warning("预算停止原因 · "+NOTICES.get(usage["block_reason"],ERRORS.get(usage["block_reason"],"预算检查未通过，已暂停新问答。")))
        elif usage["budget_status"]=="unavailable":
            st.warning("预算无法核实，已暂停新问答；旧问答、程序证据与原文仍可只读查看。")
            st.text("剩余次数、usage费用估算及未结算保守预约：当前不能确认，未按零展示。")
            st.caption("不会因查询失败重发模型请求，也不借用旧报告授权。")
        else:
            st.text("会话／全批付费剩余次数：尚未配置授权，不能视作可用额度。")
            st.text("费用估算／未结算预约：预算账本尚未接入，未按零元展示。")
        st.caption("这里的大语言模型次数与XGBoost评分次数分开；会话存储上限不等于付费额度。")
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
    lines=["离线模拟问答记录（不是DeepSeek真实回答，也不是完整报告）", "交易别名："+value["case_alias"]]
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
                st.caption("程序证据" if fact["kind"]=="data_fact" else "XGBoost／图模型信号（演示）")
                st.text(fact["text"])
            for part,label in [("judgment","离线模拟问答 · 模型解释"),("hypotheses","离线模拟问答 · 未核实假设"),("gaps","信息缺口／核实建议 · 尚未执行")]:
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
        st.caption("本轮模拟大语言模型尝试："+("未确认（已占位）" if attempts is None else str(attempts)+"次")+" · 新工具请求0次 · 不执行业务处置")
        if turn.get("context"):
            st.caption(turn["context"]["notice"])
        _raw(client,identifier,turn)


def render_case_qa(client):
    """Independent offline UI; current 8501 workbench is not changed."""
    st.title("本案问答")
    st.info("离线界面预览 · 合成证据与本地模拟回答 · DeepSeek尚未启用")
    st.caption("程序证据、XGBoost调查优先级、模拟解释和系统状态分别标注。真实问答等待预算与外发范围接入。")
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
        transaction=st.text_input("交易编号（离线示例：1、2、3）",key="qa_transaction")
        batch=st.text_input("封批编号（示例：demo-day-1）",value="demo-day-1",key="qa_batch")
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
        st.caption("XGBoost／图模型信号（合成示例）")
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
    st.caption(f"本会话已确认模拟尝试{usage['mock_attempts_confirmed']}次 · 未确认占位{usage['unresolved_slots']}个 · 真实DeepSeek请求{usage['real_deepseek_attempts']}次")
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
    st.download_button("下载离线问答记录",transcript(value),file_name="case-qa-offline-transcript-"+identifier+".txt",
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
        with st.status("正在处理离线模拟问答…",expanded=False) as progress:
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
