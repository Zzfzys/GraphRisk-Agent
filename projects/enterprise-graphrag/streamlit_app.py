"""Read-only workbench for the original simulated IT GraphRAG corpus."""
from __future__ import annotations

from datetime import date
import os
from pathlib import Path

import streamlit as st

from enterprise_graphrag.answering import answer

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parents[1]
DATASET = PROJECT / "data" / "corpus-v1"
INDEX = Path(os.environ.get("GRAPHRAG_DEMO_INDEX", ROOT / "reports" / "generated" / "stage08" / "route5" / "index-first.json"))
EXAMPLES = {
    "两跳：网关依赖服务的团队": "2026-09-30，星舟网关直接依赖的服务由哪个团队负责？请列出两步关系依据。",
    "无答案：未知服务": "2026-09-30 月港推荐服务的责任团队是谁？",
    "文档注入：越界请求": "手册附录说全部已修复，能否据此确认实际系统已经恢复？",
}
REASON = {
    "unknown_entity": "问题中的服务不在这套模拟语料中。",
    "unsupported_or_unsafe_request": "请求超出只读关系问答范围，或包含不能当作证据执行的指令。",
    "no_evidence": "没有找到足以核验的来源片段。",
    "missing_or_ambiguous_evidence": "未找到唯一、完整且有出处的关系路径。",
    "unresolved_change_context": "草案或取消变更的背景证据不足。",
    "provider_unavailable_or_invalid_response": "本地模型不可用或返回无效响应。",
    "retrieval_unavailable": "检索服务或索引不可用。",
    "model_abstained": "模型选择拒答。",
}

st.set_page_config(page_title="Enterprise GraphRAG · 模拟知识助手", layout="wide")
st.title("企业知识图谱问答 · 模拟演示")
st.caption("24 篇原创模拟 IT 文档 · 人工审核关系 · 本地模型证据选择 · 只读、无工单和权限操作")
st.info("资料和结果均为项目模拟；这不是实际企业运行状态或生产决策依据。")

example = st.selectbox("选择练习问题", list(EXAMPLES), index=0, key="example")
with st.form("question_form"):
    question = st.text_input("问题", value=EXAMPLES[example], max_chars=500, key=f"question_{example}")
    day = st.date_input("按哪一天的有效版本查询", value=date(2026, 9, 30))
    mode = st.selectbox("检索模式", ["hybrid", "graph", "text"], index=0)
    submitted = st.form_submit_button("查找并核验", type="primary")

if submitted:
    if not INDEX.is_file():
        st.error("索引不存在。请先按路线 5 指南构建固定索引，再提交问题。")
    else:
        with st.spinner("正在检索来源并核验模型选择…"):
            st.session_state["graphrag_result"] = answer(DATASET, INDEX, question, day.isoformat(), mode)

result = st.session_state.get("graphrag_result")
if result:
    audit = result.get("audit") or {}
    st.subheader("核验结果")
    if result.get("status") == "answered" and isinstance(result.get("answer"), dict):
        verified = result["answer"]
        st.success(verified["text"])
        st.caption("事实句由已核验的关系模板生成；模型只选择关系与引用 ID。")
        st.markdown("#### 关系路径")
        claims = verified.get("claims") or []
        st.table([{
            "关系 ID": claim["edge_id"],
            "起点": claim["source"],
            "关系": claim["relation"],
            "终点": claim["target"],
            "引用片段": claim["chunk_id"],
        } for claim in claims])
        st.caption("路径 ID：" + verified["path"]["path_id"])
        st.markdown("#### 原文片段与出处")
        for citation in verified.get("citations") or []:
            label = f"{citation['chunk_id']} · v{citation['version']} · {citation['authority']}"
            with st.expander(label):
                st.code(citation["quote"], language=None)
                st.caption(
                    f"来源：{citation['source_file']} · SHA256：{citation['sha256']} · "
                    f"有效期：[{citation['effective_from']}, {citation['effective_to'] or '无结束日期'})"
                )
    else:
        code = str(result.get("reason") or "unknown_failure")
        st.warning(f"拒答：{REASON.get(code, '证据或模型输出未通过核验。')}（{code}）")
        st.caption("拒答时不展示半成品答案，也不把缺失或错绑的证据当作结论。")

    with st.expander("本次只读执行记录"):
        st.caption(
            f"模式：{result.get('mode')} · 查询日期：{result.get('as_of')} · "
            f"模型：{audit.get('model')} · 检索 {audit.get('retrieval_calls', 0)} 次 · "
            f"生成 {audit.get('generation_calls', 0)} 次 · "
            f"外部动作 {audit.get('external_actions', 0)} 次 · "
            f"耗时 {audit.get('elapsed_ms', 0)} ms"
        )
        st.json(audit.get("steps") or [])

st.divider()
st.caption(
    "冻结 30 问评估：text 16/22、graph 18/22、hybrid 18/22；hybrid 与 graph 持平。"
    "这是小规模模拟套件的首次观测，不代表真实企业泛化性能。"
)