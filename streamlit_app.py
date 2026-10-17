"""Single-page Stage 06 workbench. The FastAPI service owns all investigation state."""

from __future__ import annotations

import os
import json
import re
from datetime import date
from typing import Any

import streamlit as st

from graphrisk.demo.api_client import ApiReply, DemoApiError, Stage06ApiClient


RUN_ID_PATTERN = re.compile(r"^run-[A-Za-z0-9]{1,16}-[0-9a-f]{32}$")
ERROR_TEXT = {
    'prediction_out_of_coverage':'这笔交易没有合格独立分数。请查询评分覆盖；缺分不表示风险为零。',
    'coverage_artifact_unavailable':'覆盖产物不可用。请检查绑定文件并重新验证配置。',
    "transaction_not_found": "没有找到这笔交易。请核对交易 ID。",
    "invalid_request": "请求字段无效。请核对交易 ID、日期和关系上限。",
    "dataset_source_mismatch": "API 当前未配置 IEEE-CIS 数据源。",
    "generation_unavailable": "本地生成模型不可用，调查未形成可引用报告。",
    "policy_retrieval_unavailable": "政策检索不可用，无法形成可引用报告。",
    "investigation_unavailable": "调查依赖不可用，请查看 API 的就绪状态。",
    "deadline_exceeded": "调查超出时间上限；没有生成可信报告。",
    "budget_exhausted": "调查费用预算已达到停止阈值。",
    "run_instance_mismatch": "API 已重启；旧运行和审批检查点不可恢复，请重新调查。",
    "run_expired": "运行记录已过期，请重新调查。",
    "run_not_found": "没有找到该运行记录。",
    "approval_expired": "审批窗口已到期，不能再批准或拒绝。",
    "approval_conflict": "审批已由其他决定或备注终结，请刷新查看。",
    "approval_binding_mismatch": "审批绑定不匹配；请刷新当前报告。",
    "report_hash_mismatch": "冻结报告哈希不一致，不能审批。",
}


def _error_text(reply: ApiReply) -> str:
    code = str(reply.body.get("error_code") or "unknown_error")
    return f"{ERROR_TEXT.get(code, '调查 API 返回错误。')}（{code}）"


def _score(value: Any) -> str:
    return f"{value:.4f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "不可用"


def _show_report(record: dict[str, Any]) -> None:
    report = record.get("report")
    status = record.get("status")
    st.header("调查结果", icon=":material/fact_check:")
    if status == "verified":
        st.info("报告引文来源已核验；政策适用性和风险结论仍须人工判断。")
    elif status == "partial_evidence":
        st.warning("调查只获得部分证据；图或其他证据缺口不能被解释为低风险。")
    elif status == "insufficient_evidence":
        st.warning("证据不足，未形成可供审批的政策结论。")
    else:
        st.error("调查未形成可靠报告。")
    st.caption(f"运行 ID：{record['run_id']} · 状态：{status} · 数据源：IEEE-CIS")
    if report is None:
        return

    transaction = report["transaction"]
    with st.container(border=True):
        st.subheader("交易与历史", icon=":material/receipt_long:")
        a, b, c = st.columns(3)
        a.metric("交易 ID", transaction["transaction_id"])
        b.metric("金额", f"{transaction['amount']:,.2f}")
        c.metric("产品", transaction.get("product_code") or "未提供")
        history = report.get("history")
        if history and history.get("status") == "ok":
            st.caption("严格历史窗口交易数（不包含当前交易及同时间事件）")
            h1, h2, h3 = st.columns(3)
            h1.metric("近 1 小时", history.get("tx_count_last_1h") or 0)
            h2.metric("近 24 小时", history.get("tx_count_last_24h") or 0)
            h3.metric("近 7 天", history.get("tx_count_last_7d") or 0)
        elif history:
            st.warning(f"历史特征不可用：{history.get('error_code') or history.get('status')}")

    with st.container(border=True):
        st.subheader("模型与图证据", icon=":material/account_tree:")
        model = report["model"]
        graph = report["graph"]
        decision_mode = model.get("score_mode") == "xgboost_only"
        if decision_mode:
            m1, m2 = st.columns(2)
            m1.metric("XGBoost 主风险分数", _score(model.get("risk_score")))
            m2.metric("GraphSAGE 实验性分数", _score(graph.get("target_graph_risk_score")))
            st.caption("主评分仅使用 XGBoost；GraphSAGE 保留为实验性图信号，不参与融合评分。")
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("XGBoost 风险分数", _score(model.get("risk_score")))
            m2.metric("GraphSAGE 目标分数", _score(graph.get("target_graph_risk_score")))
            m3.metric("融合分数", _score(graph.get("fusion_score")))
        st.caption("全部分数未经过业务概率校准；高分不等于已证实欺诈，低分不等于安全。")
        st.caption(f"模型：{model.get('model_version') or '不可用'} · 图：{graph.get('graph_model_version') or '不可用'}")
        if model.get("status") != "ok":
            st.warning(f"风险模型证据不可用：{model.get('error_code') or model.get('status')}")
        if graph.get("status") != "ok":
            st.warning(f"图证据不可用：{graph.get('error_code') or graph.get('status')}。不能据此断言没有关联。")
        elif not graph.get("neighbors"):
            st.info("图查询成功，但当前受控历史窗口内没有返回风险邻居。")
        else:
            st.caption("历史关系线索：时间差均早于目标交易；共享实体是启发式关系，不是已验证客户身份。缺少独立分数的关系不会填成0或视作低风险。")
            st.dataframe([{
                "历史交易": item["transaction_id"],
                "图分数（未校准）": round(item["graph_risk_score"], 4) if item.get("graph_risk_score") is not None else "无独立分数",
                "证据类型": "仅关系" if item.get("evidence_role")=="relation_only" else "有分数的关系",
                "早于目标（秒）": item["seconds_before_target"],
                "共享实体类型": "、".join(item["shared_entity_types"]),
            } for item in graph["neighbors"]], hide_index=True)

    analysis = report.get("analysis")
    if analysis:
        with st.container(border=True):
            st.subheader("已核验的线索与信息缺口", icon=":material/checklist:")
            st.caption("模型负责受限判断，程序核对工具事实并生成以下文字；这不是自由文本欺诈结论。")
            for statement in analysis["statements"]:
                st.text(statement)
            for gap in analysis["gaps"]:
                st.text("待核实：" + gap)
            with st.expander("线索出处"):
                for ref, records in analysis["source_records"].items():
                    st.text(ref + "：" + ("、".join(records) if records else "工具不可用，无可引用记录"))

    with st.container(border=True):
        st.subheader("政策出处与不确定性", icon=":material/article:")
        st.caption(f"模拟政策 · 适用日期：{report['policy_as_of']} · 检索状态：{report.get('policy_retrieval_status') or '不可用'}")
        citations = report.get("policy_citations") or []
        if not citations:
            st.warning("没有可引用的适用政策片段；不能补造政策条款。")
        for item in citations:
            with st.expander(f"{item.get('title') or item['doc_id']} · V{item['version']} · {item['section']}"):
                st.text(item["quote"])
                st.caption(f"出处：{item['source_ref']} · chunk：{item['chunk_id']} · 类型：{item.get('source_type') or '未标注'}")
        st.caption("引文来源核验只证明引用与版本匹配，不证明政策已自动适用于本交易。")
        for item in report.get("uncertainty") or []:
            st.text(f"• {item}")

    with st.expander("运行审计与版本"):
        audit = record["audit"]
        versions = record["versions"]
        st.text(f"调查 ID：{audit.get('investigation_id') or '无'}")
        st.text(f"耗时：{audit['elapsed_ms']:.0f} ms · 检索尝试：{audit['policy_attempts']} · 生成尝试：{audit['generation_attempts']} · 重试：{audit['retries']}")
        trace = audit.get("graph_node_trace") or []
        if trace:
            st.text("受控工作流步骤：" + " → ".join(trace))
        for event in audit.get("tool_events") or []:
            st.text(f"工具 {event['tool']}：{event['status']}" + (f"（{event['error_code']}）" if event.get("error_code") else ""))
        st.text(f"图比较：{versions['graph_comparison_run']} · 政策索引：{versions['policy_index_version']}")
        st.text(f"本地模型：{versions['generation_model_id']} · digest：{versions.get('generation_model_revision') or '未提供'}")
        st.text(f"Token：{audit.get('prompt_tokens') or 0} / {audit.get('output_tokens') or 0} · 费用估计：{audit.get('run_cost_cny') or 0:.4f} 元")


def _show_approval(client: Stage06ApiClient, record: dict[str, Any]) -> None:
    st.header("人工审批", icon=":material/approval:")
    approval = record.get("approval")
    if not approval:
        st.info("本次调查没有可审批报告。")
        return
    try:
        reply = client.get_approval(record["run_id"])
    except DemoApiError as error:
        st.error(str(error))
        return
    if not reply.ok:
        st.error(_error_text(reply))
        return
    approval = reply.body
    st.caption("复核员 ID 仅供本地演示，未经过身份认证；审批不会执行封禁、拒付或通知。")
    st.caption(f"审批窗口至：{approval['expires_at']} · 外部处置次数：{approval['external_action_count']}")
    st.text("冻结报告 SHA-256")
    st.code(approval["report_sha256"], language=None)
    if approval["status"] == "pending":
        with st.form("approval_form"):
            reviewer_id = st.text_input("复核员 ID（演示字段）", key="reviewer_id", max_chars=128)
            note = st.text_area("备注（最多 500 字）", key="approval_note", max_chars=500)
            approve = st.form_submit_button("批准复核建议", icon=":material/check_circle:")
            reject = st.form_submit_button("拒绝复核建议", icon=":material/block:")
        if approve or reject:
            if not reviewer_id.strip():
                st.error("请填写复核员 ID。此字段仍不代表已认证身份。")
            else:
                try:
                    result = client.decide(
                        record["run_id"], session_id=approval["session_id"],
                        transaction_id=record["request"]["transaction_id"],
                        report_sha256=approval["report_sha256"],
                        decision="approve" if approve else "reject",
                        reviewer_id=reviewer_id.strip(), note=note,
                    )
                except DemoApiError as error:
                    st.error(str(error))
                else:
                    if result.ok:
                        approval = result.body
                    else:
                        st.error(_error_text(result))
    if approval["status"] == "approved":
        st.info("已人工批准复核建议；没有执行任何自动处置。")
    elif approval["status"] == "rejected":
        st.warning("已人工拒绝复核建议；没有执行任何自动处置。")
    elif approval["status"] == "expired":
        st.error("审批窗口已到期。请重新发起调查以取得新的待审报告。")
    if approval["status"] in ("approved", "rejected"):
        st.text(f"复核员（未认证）：{approval.get('reviewer_id') or '未提供'}")
        if approval.get("note"):
            st.text(f"备注：{approval['note']}")
    st.caption(f"审批状态：{approval['status']} · 审计事件：{len(approval.get('audit') or [])} · 外部处置：0")


st.set_page_config(page_title="GraphRisk-Agent 调查工作台", page_icon=":material/account_tree:", layout="wide")
st.title("GraphRisk-Agent", icon=":material/account_tree:")
st.caption("欺诈调查工作台 · IEEE-CIS 本地演示 · 模拟政策 · 分数未校准 · 无自动处置 · 复核员身份未认证")
if os.getenv("GRAPHRISK_HISTORY_REPLAY") == "1":
    st.info("历史真实报告回放：本次不运行风险模型或大模型；审批仅操作新的隔离演示记录。")
client = Stage06ApiClient(os.getenv("GRAPHRISK_STAGE06_API_URL", "http://127.0.0.1:8000"))
st.session_state.setdefault("run_id", None)
url_run_id = st.query_params.get("run_id")
if url_run_id and url_run_id != st.session_state["run_id"]:
    st.session_state["run_id"] = url_run_id

with st.form("investigation_form"):
    transaction_id = st.text_input("IEEE-CIS 交易 ID", value="3488959", max_chars=128)
    policy_as_of = st.date_input("政策适用日期", value=date(2026, 7, 1))
    relation_limit = st.number_input("图邻居上限", min_value=1, max_value=50, value=3, step=1)
    submitted = st.form_submit_button("开始新调查", type="primary", icon=":material/search:")
    coverage_submitted = st.form_submit_button('查询评分覆盖')

if coverage_submitted:
    try:
        reply=client.coverage(transaction_id.strip())
        if reply.ok and reply.body.get('status')=='covered':
            st.success('这笔历史交易有合格的独立评分和时点图索引。')
            st.caption('历史留出回放 · GraphSAGE仅作实验信号 · 分数未校准')
            st.json(reply.body,expanded=False)
        elif reply.ok:
            st.warning('这笔交易不在独立评分覆盖范围；这不表示没有关系或风险为零。')
        else:
            st.warning('评分覆盖不可用；请确认API已显式选择路线6覆盖配置，且产物完整。')
    except DemoApiError as error:
        st.error(str(error))

with st.expander('新交易试算（只读，不创建调查或审批）'):
    st.caption('需启用路线6覆盖配置。使用该配置提供的事件JSON；历史统计由服务计算。重复ID、标签、客户端历史统计会被拒绝。')
    with st.form('prospective_form'):
        event_json=st.text_area('新事件 JSON',value='{}',height=160,max_chars=100000)
        event_submitted=st.form_submit_button('试算新交易')
    if event_submitted:
        try:
            event=json.loads(event_json)
            if not isinstance(event,dict):raise ValueError('event must be an object')
            reply=client.prospective(event)
            if reply.ok:
                a,b=st.columns(2)
                a.metric('新事件XGBoost信号',_score(reply.body.get('risk_score')))
                b.metric('实验性图信号',_score(reply.body.get('graph_risk_score')))
                st.caption('prospective_replay · 未写入历史 · 无新交易质量结论 · 不自动处置')
                st.json(reply.body,expanded=False)
            else:st.warning('未生成预测：'+str(reply.body.get('error_code','unknown_error')))
        except (ValueError,DemoApiError):
            st.error('事件JSON无效或API不可用。请核对配置与输入。')

if submitted:
    if not transaction_id.strip():
        st.error("请输入交易 ID。")
    else:
        progress = st.status("正在运行受控调查…", expanded=False)
        try:
            created = client.create_investigation(transaction_id.strip(), policy_as_of.isoformat(), int(relation_limit))
        except DemoApiError as error:
            progress.update(label="调查 API 不可用", state="error")
            st.error(str(error))
        else:
            new_run_id = created.body.get("run_id")
            if isinstance(new_run_id, str) and RUN_ID_PATTERN.fullmatch(new_run_id):
                st.session_state["run_id"] = new_run_id
                st.query_params["run_id"] = new_run_id
            if created.ok:
                progress.update(label="调查已完成", state="complete")
            else:
                progress.update(label="调查未形成可靠报告", state="error")
                st.error(_error_text(created))

with st.container(horizontal=True):
    if st.button("刷新当前状态", icon=":material/refresh:"):
        pass  # The rerun below reads the same run_id from FastAPI.
    if st.button("清除当前运行", icon=":material/close:"):
        st.session_state["run_id"] = None
        st.query_params.clear()

run_id = st.session_state["run_id"]
if run_id is None:
    st.info("提交交易后可查看调查证据与人工审批。页面交互不会自动重跑调查。")
elif not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
    st.error("运行 ID 格式无效。请清除当前运行并重新调查。")
else:
    try:
        current = client.get_investigation(run_id)
    except DemoApiError as error:
        st.error(str(error))
    else:
        if not current.ok:
            st.error(_error_text(current))
            st.caption(f"运行 ID：{run_id}")
        else:
            _show_report(current.body)
            _show_approval(client, current.body)
