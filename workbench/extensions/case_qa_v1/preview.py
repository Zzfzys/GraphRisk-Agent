"""Runnable Step4 synthetic-only preview. Never creates a live Provider."""
from pathlib import Path
import json

import streamlit as st
from fastapi import FastAPI
from fastapi.testclient import TestClient

from extensions.case_qa_v1.api import router
from extensions.case_qa_v1.evidence_contract import adapt_evidence
from extensions.case_qa_v1.sessions import CaseQAService, SQLiteConversationStore
from extensions.case_qa_v1.workbench import QAClient, render_case_qa

st.set_page_config(page_title="本案问答 · 离线预览",page_icon=":material/chat:")


@st.cache_resource
def demo_client():
    # Synthetic signals illustrate UI states; they are not predictions or new bands.
    cases={}
    for number,score,level in [(1,.86,"high"),(2,.45,"medium"),(3,.12,"low")]:
        alias=str(number)*32
        fact={"fact_id":"F-"+"1"*24,"metric":"history_transaction_count","subject":"heuristic_identity_history",
              "unit":"transactions","window_seconds":3600,"temporal_scope":"strictly_before_target",
              "source_tool":"get_transaction_features","query_ref":"c"*64,"source_version":"d"*64,"model_version":None,
              "availability":"available","value":7,"missing_reason":None}
        signal={**fact,"fact_id":"F-"+"2"*24,"metric":"primary_model_score","subject":"target_transaction",
                "unit":"uncalibrated_score","window_seconds":None,"temporal_scope":"relative_day_end",
                "source_tool":"get_risk_score","query_ref":"e"*64,"source_version":"f"*64,
                "model_version":"synthetic-daily-demo","value":score}
        bundle={"schema_version":"investigation-facts-v1","case_alias":alias,"evidence_version":str(number)*64,
            "policy_as_of":"2026-11-16","facts":[fact,signal],"policies":[],"limitations":["synthetic_ui_demo_not_prediction"],
            "tool_outcomes":[{"tool":"get_transaction_features","query_ref":"c"*64,"status":"ok","error_code":None},
                             {"tool":"get_risk_score","query_ref":"e"*64,"status":"ok","error_code":None}],
            "scoring_context":{"relative_day":1,"batch_end_second":172800,"available_through_second":172800,
                "batch_id":"demo-day-1","revision":1,"sealed_content_sha256":"a"*64,"model_version":"synthetic-daily-demo",
                "model_sha256":"f"*64,"feature_protocol_sha256":"b"*64,"category_maps_sha256":"c"*64,"policy_sha256":"d"*64,
                "score":score,"risk_level":level,"calibrated_probability":False}}
        cases[str(number)]={"case_alias":alias,"binding":str(number)*64,"batch_revision":"demo-day-1-revision-1","evidence":adapt_evidence(bundle)}
    def resolve(transaction,batch):
        if batch not in (None,"demo-day-1"):
            raise ValueError("synthetic_batch_only")
        return cases[transaction]
    def check(alias,binding):
        return next(c for c in cases.values() if c["case_alias"]==alias and c["binding"]==binding)
    def mock(packet):
        evidence=packet["view"]["evidence"]
        return json.dumps({"version":"case-qa-answer-v1","case_alias":evidence["case_alias"],"evidence_version":evidence["evidence_version"],
            "fact_refs":["F1","F2"],"judgment":[{"text":"这是本地模拟回答，用于验证界面和上下文传递；尚未进行真实DeepSeek分析。","refs":["F1","F2"]}],
            "hypotheses":[{"text":"也可能是正常连续付款，尚待核实。","refs":["F1"]}],
            "gaps":[{"text":"需要核实交易用途和实际授权，再判断这些线索的含义。","refs":["F1"]}]},ensure_ascii=False)
    database=Path(__file__).resolve().parents[2]/"reports/case-qa-v1/preview/conversations.sqlite3"
    service=CaseQAService(SQLiteConversationStore(database),resolve,check,mock_provider=mock)
    app=FastAPI()
    app.include_router(router)
    app.state.case_qa_service=service
    return QAClient(TestClient(app))


# Tests inject a recording client before running this page; no preview DB is then created.
if "qa_client" not in st.session_state:
    st.session_state.qa_client=demo_client()
render_case_qa(st.session_state.qa_client)
