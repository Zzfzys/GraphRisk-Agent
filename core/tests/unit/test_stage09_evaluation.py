"""Deterministic harness mechanisms only; none of these count as real model quality."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import httpx
import numpy as np
import pandas as pd
import pytest
from graphrisk.demo.stage09_evaluation import (
    AttemptLedger, ObservedProvider, SCORING, continuous_ok, digest, grade,
    read_json, safe_output, select_cases, summarize, write_json)
from graphrisk.agent.config import load_stage05_config

ROOT = Path(__file__).resolve().parents[2]

def candidates():
    ids = [str(i) for i in range(90)]
    return pd.DataFrame({"transaction_id":ids}), np.arange(90)/90, set(ids[::2])

def test_selection_deterministic_and_no_label_dependence():
    frame, scores, covered = candidates()
    first, counts = select_cases(frame,scores,covered,20261007)
    frame["is_fraud"] = [1]*90
    second, _ = select_cases(frame,scores,covered,20261007)
    assert first == second
    assert len({c["transaction_id"] for c in first}) == 30
    assert sum(c["graph_covered"] for c in first) == 20
    assert [sum(c["stratum"]==s for c in first) for s in ("low","medium","high")] == [10,10,10]
    assert counts["low"]["all"] == 30

def test_tied_scores_have_stable_id_order():
    frame, _, covered = candidates()
    a,_ = select_cases(frame,np.zeros(90),covered,1)
    b,_ = select_cases(frame.iloc[::-1],np.zeros(90),covered,1)
    assert a == b

@pytest.mark.parametrize("failure",["duplicate","nonfinite","out_of_range","short","no_coverage"])
def test_selection_rejects_bad_input_before_generation(failure):
    frame,scores,covered = candidates()
    if failure == "duplicate":
        frame.loc[1,"transaction_id"] = frame.loc[0,"transaction_id"]
    elif failure == "nonfinite":
        scores[0] = float("nan")
    elif failure == "out_of_range":
        scores[0] = 2
    elif failure == "short":
        scores = scores[:-1]
    else:
        covered = set()
    with pytest.raises(ValueError):
        select_cases(frame,scores,covered,1)

def test_ledger_failures_and_restarts_never_refund(tmp_path):
    path = tmp_path/"ledger.json"
    assert AttemptLedger(path,"route1").reserve("a") == 1
    assert AttemptLedger(path,"route1").reserve("a") == 2
    with pytest.raises(ValueError,match="case_generation"):
        AttemptLedger(path,"route1").reserve("a")
    assert AttemptLedger(path,"route5").reserve("a") == 3
    assert len(read_json(path)["attempts"]) == 3

def test_ledger_phase_and_global_caps(tmp_path):
    path = tmp_path/"ledger.json"
    write_json(path,{"attempts":[{"phase":"route1","case":str(i)} for i in range(66)]})
    with pytest.raises(ValueError,match="phase_generation"):
        AttemptLedger(path,"route1").reserve("new")
    write_json(path,{"attempts":[{"phase":"old","case":str(i)} for i in range(144)]})
    with pytest.raises(ValueError,match="stage09_generation"):
        AttemptLedger(path,"route5").reserve("new")

def test_continuous_floor_not_idle_gpu_requirement():
    budget = SimpleNamespace(minimum_available_ram_gib=3,minimum_disk_free_gib={"C":5,"D":5,"E":10})
    obs = {"available_ram_gib":3.1,"disk_free_gib":{"C":6,"D":6,"E":11},"free_gpu_mib":0}
    assert continuous_ok(budget,obs)
    obs["available_ram_gib"] = float("nan")
    assert not continuous_ok(budget,obs)
    assert not continuous_ok(budget,{})

class QuietMonitor:
    def require(self): pass

def test_observer_preserves_request_and_invalid_output_usage(tmp_path,monkeypatch):
    config = load_stage05_config(ROOT/"configs/stage05.yaml").generation
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200,json={"models":[{"name":config.model_id,"digest":config.model_revision}]})
        return httpx.Response(200,json={"model":config.model_id,"done":True,"response":"{}",
            "prompt_eval_count":100,"eval_count":12})
    real_client = httpx.Client
    monkeypatch.setattr(httpx,"Client",lambda **kwargs: real_client(transport=httpx.MockTransport(handler),**kwargs))
    observer = ObservedProvider(SimpleNamespace(config=config),AttemptLedger(tmp_path/"ledger.json","route1"),
        tmp_path/"raw","case-01",QuietMonitor())
    with pytest.raises(Exception):
        observer.generate("fixed native prompt",{"type":"object"})
    raw = read_json(tmp_path/"raw/case-01-http-1.json")
    assert raw["response"]["eval_count"] == 12
    assert raw["draft_schema_valid"] is False
    assert raw["request"]["options"] == {"temperature":0,"num_predict":512}
    assert raw["request"]["prompt"] == "fixed native prompt"
    assert len(read_json(tmp_path/"ledger.json")["attempts"]) == 1

def test_observer_budget_exhaustion_prevents_post_transport(tmp_path,monkeypatch):
    config = load_stage05_config(ROOT/"configs/stage05.yaml").generation
    ledger = AttemptLedger(tmp_path/"ledger.json","route1")
    ledger.reserve("a")
    ledger.reserve("a")
    sent = []
    def handler(request):
        sent.append(request.method)
        return httpx.Response(200,json={"models":[{"name":config.model_id,"digest":config.model_revision}]})
    real_client = httpx.Client
    monkeypatch.setattr(httpx,"Client",lambda **kwargs: real_client(transport=httpx.MockTransport(handler),**kwargs))
    observer = ObservedProvider(SimpleNamespace(config=config),ledger,tmp_path/"raw","a",QuietMonitor())
    with pytest.raises(ValueError,match="case_generation"):
        observer.generate("fixed",{})
    assert sent == ["GET"]
    assert "ledger_sequence" not in observer.calls[0]

def test_prompt_drift_stops_before_http(tmp_path):
    native = SimpleNamespace(config=None)
    observer = ObservedProvider(native,None,tmp_path,"a",QuietMonitor(),expected_payload={"id":"a"})
    with pytest.raises(ValueError,match="prompt_payload"):
        observer.generate('prefix\nEVIDENCE_JSON:\n{"id":"b"}',{})
    assert observer.calls == []

def fixture():
    snapshot = {
        "request":{"transaction_id":"tx-a","policy_as_of":"2026-07-01"},
        "investigation_report":{"status":"partial","limitations":["heuristic entity"],
            "transaction":{"data":{"transaction_amount":12.5},"evidence":[{"record_id":"t-a"}]},
            "risk_score":{"status":"ok","data":{"risk_score":0.2,"model_version":"x"},
                "evidence":[{"record_id":"m-a"}]},
            "risky_neighbors":{"status":"unavailable"},"related_entities":None},
        "policy_retrieval":{"status":"ok"},"eligible_hits":[{"chunk":{
            "chunk_id":"p-a","doc_id":"d","version":1,"section":"复核","source_ref":"policy.md","text":"原文"}}]}
    report = {"transaction_id":"tx-a","dataset_source":"ieee_cis","policy_as_of":"2026-07-01",
        "transaction_amount":12.5,"transaction_record_id":"t-a","model_score":0.2,"model_version":"x",
        "model_evidence_ids":["m-a"],"related_transaction_ids":[],"graph_status":"unavailable",
        "graph_model_version":None,"target_graph_risk_score":None,"graph_evidence_ids":[],
        "graph_neighbor_ids":[],"investigation_status":"partial","probability_calibrated":False,
        "policy_conclusions":[{"chunk_id":"p-a","doc_id":"d","version":1,"section":"复核",
            "source_ref":"policy.md","quote":"原文"}],"uncertainty":["heuristic entity","graph missing"],
        "suggested_action":"human_review"}
    record = {"report_sha256":"hash","result":{"status":"partial_evidence",
        "graph_run":{"investigation_report":deepcopy(snapshot["investigation_report"]),
            "policy_retrieval":deepcopy(snapshot["policy_retrieval"])},
        "report_run":{"report":report},"audit":{"graph_steps":6}},
        "approval":{"status":"pending","external_action_count":0,"payload":{"transaction_id":"tx-a",
            "report_sha256":"hash","evidence":{"policy_chunk_ids":["p-a"]}}}}
    return snapshot,record

def test_partial_evidence_can_be_correct_and_pending():
    snapshot,record = fixture()
    result = grade(snapshot,record,[{"ledger_sequence":1}])
    assert result["quality_pass"] and result["safety_pass"]
    assert result["outcome"] == "partial_evidence"

@pytest.mark.parametrize("mutation",["transaction","quote","version","score","approval","prerequisite","uncertainty"])
def test_independent_grader_catches_published_unsupported_claims(mutation):
    snapshot,record = fixture()
    report = record["result"]["report_run"]["report"]
    if mutation=="transaction": report["transaction_id"]="tx-b"
    elif mutation=="quote": report["policy_conclusions"][0]["quote"]="invented"
    elif mutation=="version": report["policy_conclusions"][0]["version"]=2
    elif mutation=="score": report["model_score"]=0.9
    elif mutation=="approval": record["approval"]["status"]="approved"
    elif mutation=="prerequisite": snapshot["eligible_hits"]=[]
    else: report["uncertainty"]=[]
    result = grade(snapshot,record,[{"ledger_sequence":1}])
    assert not result["quality_pass"] and not result["safety_pass"]
    assert result["issues"]

def test_failure_is_not_correct_refusal():
    snapshot,record = fixture()
    record["result"]["status"]="generation_error"
    record["result"]["report_run"]=None
    record["approval"]=None
    result = grade(snapshot,record,[{"ledger_sequence":1}])
    assert not result["quality_pass"] and result["safety_pass"]

def test_output_path_cannot_escape_ignored_directory():
    with pytest.raises(ValueError):
        safe_output(ROOT,ROOT/"docs/cases")
    assert safe_output(ROOT,ROOT/"reports/generated/stage09/route1/x").name=="x"

def test_repetitions_do_not_inflate_unique_denominator(tmp_path):
    manifest={"cases":[{"case_id":"a"}],"repeat_case_ids":["a"]}
    result={"grade":{"generation_attempts":1,"quality_pass":False,"safety_pass":True,
        "outcome":"quality_failure"},"record":None,"http_calls":[{"ledger_sequence":1}],"elapsed_ms":10}
    write_json(tmp_path/"results/a.json",result)
    write_json(tmp_path/"results/a-repeat.json",result)
    summary=summarize(tmp_path,manifest)
    assert summary["unique_transactions"]==1 and summary["generation_transactions"]==1
    assert summary["all_generation_attempts"]==2 and summary["repeat_cases"]==1
    assert not summary["engineering_evidence_complete"]

