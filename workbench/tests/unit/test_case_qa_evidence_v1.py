"""Step3 schema/provenance/controlled-error checks; all data are synthetic."""
from base64 import b64decode
from copy import deepcopy
import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from extensions.case_qa_v1.api import router
from extensions.case_qa_v1.evidence_contract import (
    ANSWER_SCHEMA, ContractError, MAX_RAW_BYTES, adapt_evidence, check_answer, raw_receipt, sha,
)
from extensions.case_qa_v1.sessions import SessionError
from test_case_qa_sessions_v1 import Harness, VERSION, case, expect, source_bundle


def output(evidence, **changes):
    return {"version":ANSWER_SCHEMA,"case_alias":evidence["case_alias"],"evidence_version":evidence["evidence_version"],
            "fact_refs":[],"judgment":[],"hypotheses":[],"gaps":[],**changes}


def check(value, evidence=None):
    evidence=evidence or case()["evidence"]
    return check_answer(json.dumps(value,ensure_ascii=False),evidence)


def provider(h, raw):
    def mock(packet):
        h.calls.append(deepcopy(packet))
        return raw(packet) if callable(raw) else raw
    h.service.mock_provider=mock


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path/"qa.sqlite3")


def daily_bundle(*, available=True):
    bundle=source_bundle()
    fact=deepcopy(bundle["facts"][0])
    fact.update({"fact_id":"F-"+"2"*24,"metric":"primary_model_score","subject":"target_transaction",
                 "unit":"uncalibrated_score","window_seconds":None,"temporal_scope":"relative_day_end",
                 "source_tool":"get_risk_score","query_ref":"e"*64,"source_version":"f"*64,"model_version":"daily-test-model",
                 "availability":"available" if available else "not_measured","value":.86 if available else None,
                 "missing_reason":None if available else "not_requested"})
    bundle["facts"].append(fact)
    bundle["tool_outcomes"].append({"tool":"get_risk_score","query_ref":"e"*64,"status":"ok" if available else "not_requested","error_code":None})
    bundle["scoring_context"]={"contract_version":"daily-scoring-context-v1","temporal_scope":"relative_day_end",
        "relative_day":1,"batch_end_second":172800,"available_through_second":172800,"batch_id":"synthetic-day-1","revision":1,
        "sealed_content_sha256":"a"*64,"model_version":"daily-test-model","model_sha256":"f"*64,
        "feature_protocol_sha256":"b"*64,"category_maps_sha256":"c"*64,"policy_sha256":"d"*64,
        "score":.86,"risk_level":"high","calibrated_probability":False}
    return bundle


def test_program_fact_projection_preserves_value_units_scope_and_source():
    evidence=case()["evidence"]
    result=check(output(evidence,fact_refs=["F1"]))
    assert result["status"]=="answered"
    card=result["answer_payload"]["facts"][0]
    assert (card["value"],card["unit"],card["window_seconds"],card["temporal_scope"])==(7,"transactions",3600,"strictly_before_target")
    assert card["source_tool"]=="get_transaction_features" and card["source_version"]=="d"*64
    assert "数据事实" in result["answer"] and "7" in result["answer"]
    assert card==next(c for c in evidence["cards"] if c["ref"]=="F1")
    assert result["answer_payload"]["acceptance"]=="contract_checked_not_semantic_or_human_acceptance"


@pytest.mark.parametrize("text",[
    "近期活动值得关注，但还不能据此认定欺诈。", "这个关联需要结合正常使用习惯解释。",
    "不能确认这些记录属于同一客户。", "不能说已确认欺诈团伙。", "本轮未查询新的资料。",
    "尚未冻结账户，也不代表没有反证。", "此前1小时有7笔交易，但单凭频次不足以下结论。",
])
def test_varied_explanations_and_negated_assertions_are_allowed(text):
    evidence=case()["evidence"]
    result=check(output(evidence,judgment=[{"text":text,"refs":["F1"]}]))
    assert result["status"]=="answered",result
    assert text in result["answer"] and "模型解释" in result["answer"]


def test_hypothesis_and_gap_are_visible_categories_not_facts():
    evidence=case()["evidence"]
    value=output(evidence,hypotheses=[{"text":"也可能是多人共用终端，尚待核实。","refs":["F1"]}],
                 gaps=[{"text":"需要核实设备是否长期共享，以及这会怎样影响对关联的解释。","refs":["T1"]}])
    result=check(value)
    assert result["status"]=="answered"
    assert result["answer_payload"]["facts"]==[]
    assert "未核实假设" in result["answer"] and "尚未执行" in result["answer"]


@pytest.mark.parametrize("changes",[
    {"fact_refs":[{"ref":"F1","value":9}]}, {"fact_refs":[{"ref":"F1","text":"已确认欺诈"}]},
    {"fact_refs":["F99"]}, {"fact_refs":["T1"]}, {"case_alias":"2"*32,"fact_refs":["F1"]},
    {"evidence_version":"b"*64,"fact_refs":["F1"]}, {"fact_refs":["other-case/F1"]},
    {"fact_refs":["F1","F1"]}, {"fact_refs":["F1"],"tools_executed":["freeze_account"]},
    {"judgment":[{"text":"text","refs":["history-turn-1"]}]},
    {"judgment":[{"text":"text","refs":["F1"],"source":"data_fact"}]},
])
def test_wrong_case_refs_fact_overrides_and_fake_tool_fields_rejected(changes):
    evidence=case()["evidence"]
    result=check(output(evidence,**changes))
    assert result["status"]=="invalid_response" and result["answer"] is None
    assert result["answer_payload"] is None


@pytest.mark.parametrize("text",[
    "此前1小时有9笔交易。", "已确认同一客户实施了盗刷。", "这是已证实的欺诈团伙。",
    "本轮已查询新的外部资料。", "我已经冻结账户。", "欺诈概率为86%。", "没有任何反证。",
])
def test_controlled_wrong_numbers_unverified_facts_and_fabricated_actions_need_review(text):
    evidence=case()["evidence"]
    result=check(output(evidence,judgment=[{"text":text,"refs":["F1"]}]))
    assert result["status"]=="review_required" and result["answer"] is None
    assert result["issues"] and result["answer_payload"] is None


def test_asserted_fact_inside_hypothesis_also_needs_review():
    evidence=case()["evidence"]
    result=check(output(evidence,hypotheses=[{"text":"已确认同一客户。","refs":["F1"]}]))
    assert result["status"]=="review_required"


def test_day_end_signal_and_priority_never_become_verified_probability():
    evidence=adapt_evidence(daily_bundle())
    result=check(output(evidence,fact_refs=["F2"]),evidence)
    signal=result["answer_payload"]["facts"][0]
    assert signal["kind"]=="model_signal" and signal["risk_priority"]=="high"
    assert signal["value"]==.86
    assert "日终" in result["answer"] and "不是已验证的欺诈概率" in result["answer"]
    assert "不能称为交易发生时的即时评分" in result["answer"]


def test_unrequested_context_score_is_not_exposed_as_acquired_signal():
    evidence=adapt_evidence(daily_bundle(available=False))
    signal=next(c for c in evidence["cards"] if c["ref"]=="F2")
    assert signal["value"] is None and "risk_priority" not in signal
    assert "0.86" not in json.dumps(evidence,ensure_ascii=False)
    result=check(output(evidence,fact_refs=["F2"]),evidence)
    assert "没有取得" in result["answer"] and "不能当成0" in result["answer"]
    assert result["answer_payload"]["facts"][0]["value"] is None


def test_missing_is_not_zero_and_explicit_nonzero_caution_is_allowed():
    bundle=source_bundle()
    bundle["facts"][0].update({"availability":"not_measured","value":None,"missing_reason":"not_requested"})
    bundle["tool_outcomes"][0]["status"]="not_requested"
    evidence=adapt_evidence(bundle)
    bad=check(output(evidence,judgment=[{"text":"设备关联为0笔。","refs":["F1"]}]),evidence)
    good=check(output(evidence,gaps=[{"text":"尚未取得这项数据，不能当成0。","refs":["F1"]}]),evidence)
    assert bad["status"]=="review_required" and good["status"]=="answered"


def test_available_fact_with_failed_tool_is_rejected():
    bundle=source_bundle()
    bundle["tool_outcomes"][0]["status"]="unavailable"
    with pytest.raises(ContractError):
        adapt_evidence(bundle)


def test_policy_conditions_projected_as_guidance_not_case_facts():
    bundle=source_bundle()
    bundle["policies"]=[{"policy_ref":"synthetic-policy-1","doc_id":"public-fixture","section":"test-section",
        "line_start":1,"line_end":2,"content_sha256":"c"*64,"source_type":"synthetic_policy",
        "text":"共享字段可作为核实线索。","applicability":"unverified_conditions","conditions":[],"conflict_state":"not_observed",
        "policy_id":"fixture-policy","chunk_id":"fixture-chunk","version":1,"license":"synthetic-test",
        "effective_from":"2026-01-01","effective_to":None,"purpose":"evidence_guidance","conflicts_with":[],"content_trust":"untrusted_policy_data"}]
    evidence=adapt_evidence(bundle)
    result=check(output(evidence,judgment=[{"text":"资料建议先核实共享关系，并不证明已满足条件。","refs":["P1"]}]),evidence)
    assert result["status"]=="answered"
    citation=result["answer_payload"]["citations"][0]
    assert citation["kind"]=="policy" and citation["applicability"]=="unverified_conditions"
    assert "命中不表示" in citation["notice"]
    assert check(output(evidence,fact_refs=["P1"]),evidence)["status"]=="invalid_response"


def test_adapter_preserves_version_but_separately_hashes_projection_and_bundle():
    bundle=source_bundle()
    evidence=adapt_evidence(bundle)
    assert evidence["evidence_version"]==bundle["evidence_version"]
    assert evidence["source_evidence_sha256"]==sha(bundle)
    assert evidence["view_sha256"]==sha({k:v for k,v in evidence.items() if k!="view_sha256"})
    original=deepcopy(evidence)
    bundle["facts"][0]["value"]=99
    assert evidence==original


@pytest.mark.parametrize("raw",["not JSON", "", "[]",'{"version":1,"version":2}',"["*1200+"0"+"]"*1200, 'NaN'])
def test_malformed_response_is_not_published(raw):
    result=check_answer(raw,case()["evidence"])
    assert result["status"]=="invalid_response" and result["answer"] is None


def test_default_api_hides_raw_but_separate_readonly_endpoint_preserves_it(h):
    raw='{"unmatched":"private synthetic model output"'
    provider(h,raw)
    session=h.create()
    result,request=h.follow(session)
    assert result["turn"]["result"]["status"]=="invalid_response"
    assert result["turn"]["result"]["model_attempts"]==1
    assert "raw_response" not in result["turn"]["result"]
    assert raw not in json.dumps(h.service.get(session["conversation_id"]))
    app=FastAPI()
    app.include_router(router)
    app.state.case_qa_service=h.service
    with TestClient(app) as client:
        base=f"/api/v1/case-qa/conversations/{session['conversation_id']}"
        response=client.get(base+f"/turns/{request['message_id']}/raw")
        assert response.status_code==200 and response.json()["source"]=="unreviewed_mock_output"
        receipt=response.json()["raw_response"]
        assert receipt["complete"] and b64decode(receipt["content_base64"]).decode()==raw
        assert receipt["sha256"]==hashlib.sha256(raw.encode()).hexdigest()
        assert client.get(base+"/turns/"+"f"*32+"/raw").status_code==404
        assert client.get(base+"/turns/bad/raw").status_code==422
    assert len(h.calls)==1
    h.restart()
    assert h.service.follow(session["conversation_id"],request)["replayed"]
    assert len(h.calls)==1
    assert h.service.raw(session["conversation_id"],request["message_id"])["raw_response"]==receipt


def test_review_and_invalid_outputs_stay_out_of_history(h):
    session=h.create()
    evidence=h.cases["1"]["evidence"]
    for raw in ['broken JSON',json.dumps(output(evidence,judgment=[{"text":"有9笔交易。","refs":["F1"]}]))]:
        provider(h,raw)
        result,_=h.follow(session)
        assert result["turn"]["result"]["answer"] is None
    provider(h,json.dumps(output(evidence,judgment=[{"text":"尚需核实。","refs":["F1"]}])))
    result,_=h.follow(session)
    assert result["turn"]["result"]["status"]=="answered"
    assert h.calls[-1]["view"]["dialogue_background"]==[]
    assert "fact_refs" in h.calls[-1]["system_prompt"]
    assert len(h.calls)==3


def test_checker_failure_retains_raw_once_without_publishing(h,monkeypatch):
    import extensions.case_qa_v1.sessions as sessions
    raw=json.dumps(output(h.cases["1"]["evidence"],fact_refs=["F1"]))
    provider(h,raw)
    def fail(*args):
        raise RuntimeError("private checker detail")
    monkeypatch.setattr(sessions,"check_answer",fail)
    session=h.create()
    result,request=h.follow(session)
    assert result["turn"]["result"]["issues"]==["checker_failed"]
    assert result["turn"]["result"]["answer"] is None
    assert "private checker detail" not in json.dumps(result)
    assert h.service.follow(session["conversation_id"],request)["replayed"]
    receipt=h.service.raw(session["conversation_id"],request["message_id"])["raw_response"]
    assert b64decode(receipt["content_base64"]).decode()==raw
    assert len(h.calls)==1


def test_raw_retention_cap_is_explicit_and_pauses_new_slots(h):
    raw="x"*(MAX_RAW_BYTES+1)
    provider(h,raw)
    session=h.create()
    result,request=h.follow(session)
    assert result["turn"]["result"]["issues"]==["raw_retention_incomplete"]
    receipt=h.service.raw(session["conversation_id"],request["message_id"])["raw_response"]
    assert not receipt["complete"] and receipt["byte_length"]==MAX_RAW_BYTES+1
    assert len(b64decode(receipt["content_base64"]))==MAX_RAW_BYTES
    assert receipt["sha256"]==hashlib.sha256(raw.encode()).hexdigest()
    other=h.create("2","other_case")
    expect("raw_retention_incomplete",lambda:h.follow(other))
    assert h.service.follow(session["conversation_id"],request)["replayed"]
    assert len(h.calls)==1


def test_receipt_lossless_for_exact_cap_and_invalid_unicode():
    raw="x"*MAX_RAW_BYTES
    receipt=raw_receipt(raw)
    assert receipt["complete"] and b64decode(receipt["content_base64"]).decode()==raw
    raw="\ud800"
    receipt=raw_receipt(raw)
    assert receipt["complete"] and b64decode(receipt["content_base64"]).decode("utf-8","surrogatepass")==raw


def test_short_answers_need_not_fill_all_categories():
    evidence=case()["evidence"]
    result=check(output(evidence,gaps=[{"text":"现有资料不足以回答，需要核实设备实际使用者。","refs":[]}]))
    assert result["status"]=="answered"
    assert len(result["answer"])<80



@pytest.mark.parametrize("field,value",[("subject","heuristic_identity_history"),("unit","transactions"),
    ("source_tool","get_transaction_features"),("window_seconds",3600)])
def test_daily_schema_rejects_score_slot_changes(field,value):
    bundle=daily_bundle()
    bundle["facts"][1][field]=value
    if field=="source_tool":
        bundle["facts"][1]["query_ref"]="c"*64
    with pytest.raises(ContractError):
        adapt_evidence(bundle)


def test_daily_closed_boundary_and_non_score_day_end_rejected():
    bundle=daily_bundle()
    bundle["scoring_context"]["batch_end_second"]=172799
    with pytest.raises(ContractError):
        adapt_evidence(bundle)
    bundle=daily_bundle()
    bundle["facts"][0]["temporal_scope"]="relative_day_end"
    with pytest.raises(ContractError):
        adapt_evidence(bundle)


def test_raw_checkpoint_is_durable_before_checker_runs(h,monkeypatch):
    import extensions.case_qa_v1.sessions as sessions
    evidence=h.cases["1"]["evidence"]
    raw=json.dumps(output(evidence,fact_refs=["F1"]))
    provider(h,raw)
    session=h.create()
    real_check=sessions.check_answer
    def checker(value,view):
        # Another store instance sees a committed raw receipt, before final status.
        recovered=Harness(h.path)
        saved=recovered.service.raw(session["conversation_id"],"0"*31+"1")
        assert saved["status"]=="in_flight"
        assert b64decode(saved["raw_response"]["content_base64"]).decode()==raw
        return real_check(value,view)
    monkeypatch.setattr(sessions,"check_answer",checker)
    result,_=h.follow(session)
    assert result["turn"]["result"]["status"]=="answered"
    assert len(h.calls)==1
