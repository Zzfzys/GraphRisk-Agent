"""Synthetic-only quality preview, with a dedicated new conversation database."""
from copy import deepcopy
import json
from pathlib import Path

import streamlit as st
from fastapi import FastAPI
from fastapi.testclient import TestClient

from extensions.case_qa_v1.api import router
from extensions.case_qa_v1.evidence_contract import ANSWER_SCHEMA, adapt_evidence
from extensions.case_qa_quality_v1.service import QualityConversationStore, QualityService
from extensions.case_qa_quality_v1.workbench import QAClient, render_case_qa


def build_fixture_service(path):
    """No ledger, credentials or network: only fixed synthetic evidence/answers."""
    cases = {}
    for number,score,level in ((1,.860123456789,"high"), (2,.450123456789,"medium"), (3,.120123456789,"low")):
        alias = str(number)*32
        fact = {"fact_id":"F-"+"1"*24, "metric":"history_transaction_count", "subject":"heuristic_identity_history",
                "unit":"transactions", "window_seconds":3600, "temporal_scope":"strictly_before_target",
                "source_tool":"get_transaction_features", "query_ref":"c"*64, "source_version":"d"*64,
                "model_version":None, "availability":"available", "value":7, "missing_reason":None}
        signal = {**fact, "fact_id":"F-"+"2"*24, "metric":"primary_model_score", "subject":"target_transaction",
                  "unit":"uncalibrated_score", "window_seconds":None, "temporal_scope":"relative_day_end",
                  "source_tool":"get_risk_score", "query_ref":"e"*64, "source_version":"f"*64,
                  "model_version":"synthetic-quality-demo", "value":score}
        bundle = {"schema_version":"investigation-facts-v1", "case_alias":alias, "evidence_version":str(number)*64,
                  "policy_as_of":"2026-11-17", "facts":[fact,signal], "policies":[],
                  "limitations":["synthetic_ui_demo_not_prediction"],
                  "tool_outcomes":[{"tool":"get_transaction_features", "query_ref":"c"*64, "status":"ok", "error_code":None},
                                   {"tool":"get_risk_score", "query_ref":"e"*64, "status":"ok", "error_code":None}],
                  "scoring_context":{"contract_version":"daily-scoring-context-v1", "relative_day":1,
                      "batch_end_second":172800, "available_through_second":172800, "batch_id":"demo-day-1", "revision":1,
                      "sealed_content_sha256":"a"*64, "model_version":"synthetic-quality-demo", "model_sha256":"f"*64,
                      "feature_protocol_sha256":"b"*64, "category_maps_sha256":"c"*64, "policy_sha256":"d"*64,
                      "score":score, "risk_level":level, "calibrated_probability":False}}
        cases[str(number)] = {"case_alias":alias, "binding":str(number)*64, "batch_revision":"demo-day-1-revision-1",
                              "evidence":adapt_evidence(bundle)}
    def resolve(transaction, batch):
        if batch not in (None, "demo-day-1"):
            raise ValueError("synthetic_batch_only")
        return deepcopy(cases[transaction])
    def check(alias, binding):
        return next(deepcopy(c) for c in cases.values() if c["case_alias"] == alias and c["binding"] == binding)
    def mock(packet):
        # Fixed demo fixtures illustrate sparse answers; this is not production intent routing.
        evidence = packet["view"]["evidence"]
        answer = {"version":ANSWER_SCHEMA, "case_alias":evidence["case_alias"], "evidence_version":evidence["evidence_version"],
                  "fact_refs":[], "judgment":[], "hypotheses":[], "gaps":[]}
        question = packet["view"]["question"]
        if question == "这些线索有没有正常解释？":
            answer["hypotheses"] = [{"text":"也可能是正常连续付款；这只是待核实的解释。", "refs":["F1"]}]
        elif question == "现有资料还缺什么，接下来优先核实什么？":
            answer["gaps"] = [{"text":"建议先核实支付授权与交易用途；这些核实尚未执行。", "refs":[]}]
        else:
            answer["fact_refs"] = ["F1", "F2"]
            answer["judgment"] = [{"text":"近期频次与日终模型信号提示应先核实，不能直接认定欺诈。", "refs":["F1","F2"]}]
            answer["gaps"] = [{"text":"建议先核实支付授权与交易用途；这些核实尚未执行。", "refs":[]}]
        return json.dumps(answer, ensure_ascii=False)
    return QualityService(QualityConversationStore(path), resolve, check, mock_provider=mock)


@st.cache_resource
def demo_client():
    path = Path(__file__).resolve().parents[2]/"reports/case-qa-quality-v1/preview/conversations.sqlite3"
    app = FastAPI()
    app.include_router(router)
    app.state.case_qa_service = build_fixture_service(path)
    return QAClient(TestClient(app))


def main():
    st.set_page_config(page_title="本案问答 · 质量离线预览", page_icon=":material/chat:")
    if "qa_client" not in st.session_state:
        st.session_state.qa_client = demo_client()
    render_case_qa(st.session_state.qa_client)


if __name__ == "__main__":
    main()
