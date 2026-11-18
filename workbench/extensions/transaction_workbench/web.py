"""Risk and evidence → report → questions, always for one selected transaction."""
from uuid import uuid4

import httpx
import streamlit as st

from extensions.case_qa_compact_v1.workbench import QAClient, download_record
from extensions.case_qa_live_v1.workbench import BASE, ClientError, _error
from extensions.case_qa_live_v3.workbench import _turn


@st.cache_resource
def client():
    return httpx.Client(base_url="http://127.0.0.1:8003",timeout=360,trust_env=False)


@st.cache_data(ttl=60,max_entries=1,show_spinner=False)
def transactions():
    return client().get("/api/v1/workbench/transactions").raise_for_status().json()


def load(transaction):
    response = client().get("/api/v1/workbench/transactions/"+transaction)
    response.raise_for_status()
    value = response.json()
    if value["transaction_id"] != transaction:
        raise ValueError("transaction_mismatch")
    return value


st.set_page_config(page_title="GraphRisk · 交易调查",page_icon=":material/account_tree:",layout="wide")
st.title("交易调查")
st.caption("选择一笔交易，查看风险、证据和报告，再直接向 DeepSeek 提问。")
state = st.session_state
state.setdefault("transaction_pending",None)
try:
    catalog = transactions()
except (httpx.HTTPError,ValueError):
    st.error("交易服务暂不可用，请检查本地8002和8003服务。当前没有调用模型。")
    st.stop()
rows = {r["transaction_id"]:r for r in catalog["results"]}
if not rows:
    st.info("当前没有已封批交易。")
    st.stop()
levels = {"high":"高","medium":"中","low":"低"}
default = list(rows).index("3558671") if "3558671" in rows else 0
with st.form("select_transaction"):
    transaction = st.selectbox("交易编号",list(rows),index=default,key="transaction",bind="query-params",
        format_func=lambda tx:tx+" · "+levels[rows[tx]["risk_level"]]+"调查优先级")
    opened = st.form_submit_button("查看这笔交易",disabled=bool(state.transaction_pending))
if opened or state.get("selected_transaction") != transaction or "transaction_view" not in state:
    try:
        with st.spinner("读取本地交易证据与已存报告…"):
            state.transaction_view = load(transaction)
            state.selected_transaction = transaction
    except (httpx.HTTPError,ValueError):
        state.pop("transaction_view",None)
        st.error("这笔交易的调查资料暂不可用，请检查封批结果与本地调查服务。没有发送至 DeepSeek。")
        st.stop()
view = state.transaction_view
row = rows[transaction]
case = view["case"]
st.subheader("风险与证据")
cols = st.columns(3)
cols[0].metric("日终调查优先级",levels[row["risk_level"]])
cols[1].metric("交易金额（数据集单位）",str(row["transaction_amount"]))
cols[2].metric("模型原始分数",f"{row['score']:.6f}")
st.caption(f"相对日 {catalog['relative_day']} · 封批修订 {catalog['revision']} · 日终 XGBoost 216+49。"
           "分数不是欺诈概率；日终评分在交易发生时未必可用。")
facts = [c for c in case["evidence"]["cards"] if c["kind"] == "data_fact" and c.get("availability") == "available"]
for card in facts[:4]:
    st.text(card["text"])
with st.expander("查看完整证据与来源"):
    for card in case["evidence"]["cards"]:
        st.text(card["ref"]+" · "+card["text"])
    st.caption("共享字段是启发式关联，不证明同一客户或欺诈团伙。")

st.subheader("调查报告")
st.caption({"deepseek_saved_report":"DeepSeek · 已保存的研判报告",
            "deepseek_saved_draft":"DeepSeek · 已保存的首稿（用户认可版本，原自动检查未全部通过）",
            "deepseek_saved_draft_compatible":"DeepSeek · 本笔交易的历史首稿；已核对事实一致，原自动检查结果保留"}.get(view["report_source"],
           "程序整理 · 已封批证据报告；此处尚无通过核验的 DeepSeek 报告"))
st.text(view["report_text"])
st.download_button("下载本笔报告",view["report_text"],file_name="transaction-"+transaction+".txt",mime="text/plain",on_click="ignore")

st.subheader("问问 DeepSeek")
qa = QAClient(client())
value = view.get("conversation")
identifier = value["conversation_id"] if value else None
if st.button("刷新问答记录",help="只读取已保存的结果，不会重发问题或调用模型"):
    try:
        state.transaction_view = load(transaction)
        st.rerun()
    except (httpx.HTTPError,ValueError):
        st.error("暂时无法读取结果；原问题不会重新发送。")
if value:
    for turn in value["turns"]:
        _turn(qa,identifier,turn)
    pending = state.transaction_pending
    if pending and pending.get("conversation_id") == identifier:
        saved = next((t for t in value["turns"] if t["message_id"] == pending["message_id"]),None)
        if saved and saved["result"]["status"] != "in_flight":
            state.transaction_pending = None
    with st.expander("问答记录与原始资料"):
        st.download_button("下载本笔问答",download_record(value),file_name="qa-"+transaction+".json",mime="application/json",on_click="ignore")
        st.caption("单会话保留最多12条消息，模型只带入最近3轮已通过问答；较早记录仍保存。")
usage = value["usage"] if value else view["usage"]
st.caption(f"网页问答累计 {usage['used_requests']} / 100 次 · 估算费用 ¥{usage['batch_cost_estimate_cny']} / ¥5.00"
           f" · 剩余 {usage['batch_remaining_calls']} 次 / ¥{usage['remaining_cny']}（跨会话共享）")
st.caption("主动提问才发送本案脱敏证据及最近3轮已通过问答；数据事实、模型解释与待核实假设分别展示。")
reason = (value.get("send_block_reason") if value else None) or usage.get("block_reason")
if reason == "session_message_limit" or (value and value.get("requires_new_conversation")):
    if st.button("新建同案会话",disabled=bool(state.transaction_pending)):
        try:
            state.transaction_view["conversation"] = qa.call("POST",BASE,{"transaction_id":transaction,"batch_id":catalog["batch_id"]},uuid4().hex)
            st.rerun()
        except ClientError as error:
            _error(error)
elif reason:
    if reason == "qa_pricing_refresh_required":
        st.warning("价格核验已过期，更新官方核价后可继续；已保存记录仍可查看。")
    else:
        _error(ClientError(reason))
if state.transaction_pending:
    st.warning("上个问题的结果待确认，请刷新问答记录；不会自动重新发送。")
with st.container():
    message = st.chat_input("直接询问这笔交易，例如：哪些证据值得关注？有哪些正常解释？",key="transaction_chat",
        max_chars=2000,disabled=bool(reason or state.transaction_pending),submit_mode="disable")
if message:
    pending = {"conversation_id":identifier,"message_id":uuid4().hex}
    state.transaction_pending = pending
    with st.status("DeepSeek 正在结合本笔交易证据回答…",expanded=False) as progress:
        try:
            if not identifier:
                value = qa.call("POST",BASE,{"transaction_id":transaction,"batch_id":catalog["batch_id"]},uuid4().hex)
                identifier = value["conversation_id"]
                pending["conversation_id"] = identifier
            body = {"message_id":pending["message_id"],"parent_turn_id":value["turns"][-1]["message_id"] if value["turns"] else None,
                    "evidence_version":value["evidence_version"],"message":message}
            qa.call("POST",BASE+"/"+identifier+"/turns",body)
            state.transaction_pending = None
            progress.update(label="本轮已结束",state="complete")
        except ClientError as error:
            if not error.uncertain:
                state.transaction_pending = None
            _error(error)
            progress.update(label="结果待确认" if error.uncertain else "请求未受理",state="error")
        try:
            state.transaction_view = load(transaction)
        except (httpx.HTTPError,ValueError):
            st.warning("本轮结果尚未读取，请使用刷新问答记录；不要重复发送。")
    st.rerun()
