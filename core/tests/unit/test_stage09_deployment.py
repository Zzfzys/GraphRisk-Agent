
"""Model deployment and paired-replay guardrails, using small synthetic fixtures."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
import torch
import numpy as np
import pandas as pd
from graphrisk.graph.deployment import (bound_path, load_policy, bind_primary, DecisionGraphService)
from graphrisk.graph.service import GraphRiskService
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.schema import GraphTransaction, GraphFeatureEncoder
from graphrisk.graph.temporal_index import TemporalEntityIndex
from graphrisk.graph.train_graphsage import _sha256
from graphrisk.demo.stage09_evaluation import digest, write_json
from graphrisk.demo.stage09_replay import check_snapshot_changes, prompt_template_sha
from graphrisk.investigation.contracts import RiskyNeighborsData
from graphrisk.serving.investigation_service import RunVersions, Stage06Service, ServiceError
from graphrisk.serving.investigation_views import project_investigation
from graphrisk.agent.reporting import FakeReportProvider, Stage05ReportWorkflow
from test_stage05_reporting import graph_run
from test_stage06_service import FakeRunner, _result, REQUEST
from datetime import UTC,datetime,timedelta

@pytest.fixture
def files(tmp_path):
    folder=tmp_path/"artifacts";folder.mkdir()
    def put(name,payload):
        file=folder/name;write_json(file,payload);return {"path":f"artifacts/{name}","sha256":_sha256(file)}
    decision={"selected_by":"validation_only","promote_gnn":False,"winner":{"name":"candidate","variant":"full","cap":5}}
    decision["content_sha256"]=digest(decision)
    item=put("decision.json",decision)
    profile={"schema_version":"stage09-model-decision-v1","score_mode":"xgboost_only","gnn_role":"experimental",
        "decision_path":item["path"],"decision_sha256":decision["content_sha256"],"authority_files":[item]}
    profile["content_sha256"]=digest(profile)
    path=folder/"profile.json";write_json(path,profile)
    return tmp_path,path,profile,put

def test_policy_pinned_digest_and_authority_change_are_rejected(files):
    root,path,profile,_=files
    assert load_policy(root,path,profile["content_sha256"])==profile
    with pytest.raises(ValueError,match="digest"):load_policy(root,path,"0"*64)
    (root/"artifacts/decision.json").write_text("{}",encoding="utf-8")
    with pytest.raises(ValueError,match="artifact binding"):load_policy(root,path,profile["content_sha256"])

@pytest.mark.parametrize("relative",["../outside","configs/a.yaml",".git/config"])
def test_binding_cannot_escape_or_use_source_as_weights(tmp_path,relative):
    with pytest.raises(ValueError):bound_path(tmp_path,relative)

def test_primary_wrong_file_or_version_stops_before_serving(files):
    root,_,profile,put=files
    file=put("model.joblib",{"fixture":"weights"})
    profile.update(primary_files=[file],primary_model_version="xgb-1")
    tool=SimpleNamespace(startup_error=None,_predictor=SimpleNamespace(model_info=lambda:{"model_name":"xgboost","model_version":"xgb-1"}))
    assert bind_primary(root,profile,tool,root/"artifacts") is tool
    with pytest.raises(ValueError,match="configured primary"):bind_primary(root,profile,tool,root/"artifacts/other")
    profile["primary_model_version"]="xgb-2"
    with pytest.raises(ValueError,match="version/name"):bind_primary(root,profile,tool,root/"artifacts")

@pytest.fixture
def graph_deployment(tmp_path,monkeypatch):
    folder=tmp_path/"artifacts";folder.mkdir()
    rows=[GraphTransaction(k,datetime(2026,1,1,tzinfo=UTC)+timedelta(hours=h),
        "card","device","mail","address","W",(float(h),0.,1.,2.,None,None,None,0.,1.)) for k,h in (("train",0),("val",1),("test",2))]
    encoder=GraphFeatureEncoder.fit(rows,train_transaction_ids=("train",))
    index=TemporalEntityIndex(rows)
    store=SimpleNamespace(manifest={"content_sha256":"graph-sha","fanout":(5,5)},
        encoder=encoder,index=index,target_ids={"validation":("val",),"test":("test",)},
        ego_graph=lambda tid:index.build(tid,max_history_per_entity=5))
    torch.manual_seed(7);model=GraphSAGERiskModel(len(encoder.feature_names),8,.2).eval()
    def score(tid):
        with torch.no_grad():return float(torch.sigmoid(model(store.ego_graph(tid).to_pyg(encoder))).item())
    torch.save(model.state_dict(),folder/"model.pt")
    np.save(folder/"validation.npy",np.array([score("val")],dtype=np.float32))
    pd.DataFrame({"transaction_id":["test"],"candidate":[score("test")]}).to_parquet(folder/"test.parquet")
    spec={"variant":"full","cap":5}
    write_json(folder/"decision.json",{"winner":spec})
    write_json(folder/"metadata.json",{"spec":spec,"graph_manifest_sha256":"graph-sha",
        "feature_names":list(encoder.feature_names),"model_version":"sage-new","input_channels":len(encoder.feature_names),
        "hidden_channels":8,"dropout":.2,"model_sha256":_sha256(folder/"model.pt")})
    profile={"graph_files":[{"path":f"artifacts/{p.name}","sha256":_sha256(p)} for p in folder.iterdir()],
        "gnn_metadata_path":"artifacts/metadata.json","gnn_model_path":"artifacts/model.pt",
        "gnn_validation_path":"artifacts/validation.npy","gnn_test_path":"artifacts/test.parquet",
        "gnn_test_score_column":"candidate","decision_path":"artifacts/decision.json",
        "source_graph_sha256":"graph-sha","gnn_model_version":"sage-new"}
    def legacy_init(self,*a,**kw):
        self.startup_error=None;self.store=store;self.model=model;self._scores={"val":.1}
        self._tabular_scores={"val":.2};self._fusion_scores={"val":.15};self.fusion_alpha=.5
    monkeypatch.setattr(GraphRiskService,"__init__",legacy_init)
    return tmp_path,profile,store,score

def test_native_switch_uses_new_weights_and_removes_legacy_fusion(graph_deployment):
    root,profile,store,score=graph_deployment
    service=DecisionGraphService(None,comparison_dir=root,root=root,profile=profile)
    actual=service.predict("val")
    assert service.startup_error is None
    assert actual.graph_model_version=="sage-new" and actual.graph_risk_score==pytest.approx(score("val"))
    assert actual.score_role=="experimental" and actual.fusion_score is None and actual.controlled_xgboost_score is None
    assert service._scores["test"]==pytest.approx(score("test"))
    assert service._fusion_scores=={}

@pytest.mark.parametrize("corruption",["weights","encoder","prediction_order","comparison"])
def test_wrong_graph_binding_closes_graph_without_fabricating_score(graph_deployment,corruption):
    root,profile,store,_=graph_deployment
    if corruption=="weights":(root/"artifacts/model.pt").write_bytes(b"bad")
    elif corruption=="encoder":store.encoder=SimpleNamespace(feature_names=("wrong",))
    elif corruption=="prediction_order":
        pd.DataFrame({"transaction_id":["wrong"],"candidate":[.3]}).to_parquet(root/"artifacts/test.parquet")
        profile["graph_files"]=[{**v,"sha256":_sha256(root/v["path"])} for v in profile["graph_files"]]
    else:
        comparison=root/"artifacts/comparison.json";comparison.write_text("{}")
        profile["graph_files"].append({"path":"artifacts/comparison.json","sha256":"0"*64})
    service=DecisionGraphService(None,comparison_dir=root,root=root,profile=profile)
    assert service.startup_error=="graph_model_version_mismatch"
    assert service.model is None and not service._scores

def test_experimental_contract_forbids_fusion_and_legacy_wire_stays_stable():
    minimal={"transaction_id":"T","neighbors":[],"graph_model_version":"sage"}
    legacy=RiskyNeighborsData(**minimal)
    assert "score_role" not in legacy.model_dump()
    modern=RiskyNeighborsData(**minimal,score_role="experimental",target_graph_risk_score=.3)
    assert modern.model_dump()["score_role"]=="experimental"
    with pytest.raises(ValueError,match="forbids"):RiskyNeighborsData(**{**modern.model_dump(),"fusion_alpha":.5})

def snapshot():
    report={k:{} for k in ("transaction","historical_features","risk_score","related_entities")}
    report.update(status="completed",issues=[],unavailable_capabilities=[],risky_neighbors={"data":{"score":.1}},
        evidence_bindings=[],limitations=[])
    return {"request":{"transaction_id":"T"},"policy_retrieval":{},"eligible_hits":[],"legal_history_ids":["earlier"],
        "investigation_report":report,"expected_prompt_payload":{"transaction_id":"T","evidence_ids":["old"]}}

@pytest.mark.parametrize("area",["risk_score","request","eligible_hits","legal_history_ids"])
def test_paired_snapshot_rejects_nonmodel_changes(area):
    old=snapshot();new=deepcopy(old)
    if area=="risk_score":new["investigation_report"][area]={"data":{"score":.9}}
    else:new[area]=["changed"] if area!="request" else {"transaction_id":"OTHER"}
    with pytest.raises(ValueError):check_snapshot_changes(old,new)

def test_model_evidence_change_is_allowed_but_prompt_template_change_is_detectable():
    old=snapshot();new=deepcopy(old)
    new["investigation_report"]["risky_neighbors"]["data"]["score"]=.2
    new["expected_prompt_payload"]["evidence_ids"]=["new"]
    assert len(check_snapshot_changes(old,new))==2
    text='PROMPT = "You select source quotations for a fraud analyst. v1"'
    assert prompt_template_sha(text)!=prompt_template_sha(text.replace("v1","v2"))

def modern_result(graph_run,unavailable=False):
    graph=graph_run()
    report=graph.investigation_report
    report.model_decision_sha256="a"*64;report.score_mode="xgboost_only"
    data=report.risky_neighbors.data
    if unavailable:
        from graphrisk.investigation.contracts import FindRiskyNeighborsResult
        report.status="partial"
        from graphrisk.investigation.workflow import CapabilityGap
        from graphrisk.investigation.contracts import ToolError
        report.unavailable_capabilities=[CapabilityGap(tool="find_risky_neighbors",error=ToolError.from_code("graph_prediction_unavailable"))]
        report.risky_neighbors=FindRiskyNeighborsResult.failure(code="graph_prediction_unavailable",transaction_id="T3",dataset_source="synthetic")
        graph.status="partial_evidence"
    else:
        data.score_role="experimental";data.controlled_xgboost_score=None
        data.controlled_xgboost_model_version=None;data.fusion_score=None;data.fusion_alpha=None
    graph=graph_run(report)
    run=_result()
    run.graph_run=graph
    run.report_run=Stage05ReportWorkflow(FakeReportProvider()).run(graph)
    run.status=run.report_run.status
    versions=RunVersions(score_mode="xgboost_only",model_decision_sha256="a"*64,
        primary_model_version=report.risk_score.data.model_version,
        experimental_graph_model_version=data.graph_model_version,
        graph_comparison_run="demo",policy_index_version=graph.policy_retrieval.index_version,
        generation_model_id="fake")
    return run,versions

@pytest.mark.parametrize("tamper,code",[("decision","model_decision_binding_mismatch"),
    ("primary","primary_model_binding_mismatch"),("gnn","experimental_graph_binding_mismatch")])
def test_model_decision_mismatch_cannot_create_approval(graph_run,tamper,code):
    result,versions=modern_result(graph_run)
    if tamper=="decision":versions.model_decision_sha256="b"*64
    elif tamper=="primary":versions.primary_model_version="other"
    else:versions.experimental_graph_model_version="other"
    service=Stage06Service(dataset_source="synthetic",versions=versions,runner=FakeRunner(result))
    with pytest.raises(ServiceError,match=code):service.run(REQUEST)
    assert not service._records and service._inflight==0
    service.close()

@pytest.mark.parametrize("unavailable",[False,True])
def test_projection_keeps_primary_and_experimental_gap_and_approval(graph_run,unavailable):
    result,versions=modern_result(graph_run,unavailable)
    service=Stage06Service(dataset_source="synthetic",versions=versions,runner=FakeRunner(result))
    record=service.run(REQUEST);view=project_investigation(record)
    assert view.report.model.score_mode=="xgboost_only"
    assert view.report.graph.score_role=="experimental" and view.report.graph.fusion_score is None
    assert view.approval.status=="pending" and view.approval.external_action_count==0
    if unavailable:assert view.report.graph.target_graph_risk_score is None
    service.close()



def test_real_api_smoke_mechanism_uses_stored_report_and_no_new_model_call(graph_run,tmp_path):
    from graphrisk.demo.stage09_replay import api_smoke
    result,versions=modern_result(graph_run)
    runner=FakeRunner(result)
    service=Stage06Service(dataset_source="synthetic",versions=versions,runner=runner)
    record=service.run(REQUEST)
    output=api_smoke(service,record,tmp_path)
    assert output["passed"] and output["wrong_report_status"]==409
    assert runner.calls==1 and output["external_actions"]==0 and output["generation_calls_added"]==0
    assert record.approval.status=="pending"

@pytest.mark.parametrize("directory",["reports/generated/stage09/route1/old","reports/generated/stage09/route5","../outside"])
def test_replay_cli_output_is_confined_to_new_route5_run(tmp_path,directory):
    from scripts.replay_stage09_agent import safe_output
    with pytest.raises(ValueError):safe_output(tmp_path,Path(directory))



def test_registered_model_explanation_list_length_change_is_not_mistaken_for_data_drift():
    old=snapshot();new=deepcopy(old)
    new["investigation_report"]["limitations"]=["XGBoost primary; experimental GNN; no fusion."]
    new["investigation_report"]["evidence_bindings"]=[{"record_ids":["new-model"]}]
    changes=check_snapshot_changes(old,new)
    assert {v["path"] for v in changes}=={"/investigation_report/limitations","/investigation_report/evidence_bindings"}



def test_posthoc_draft_diagnostic_distinguishes_presence_from_correct_uncertainty(tmp_path):
    from scripts.check_stage09_replay import raw_draft_diagnostics
    before=tmp_path/"reports/generated/stage09/route1/20261007-baseline-v1"
    after=tmp_path/"after"
    manifest={"cases":[{"case_id":"case-01"}]}
    payload={"investigation_status":"completed","evidence_ids":["xgb","sage"]}
    for folder,ids in ((before,["xgb","sage"]),(after,["xgb"])):
        write_json(folder/"snapshots/case-01.json",{"expected_prompt_payload":payload})
        write_json(folder/"results/case-01.json",{"http_calls":[{"ledger_sequence":1,
            "response":{"response":json.dumps({"uncertainty":"partial_evidence","evidence_ids":ids})}}]})
    summary=raw_draft_diagnostics(tmp_path,after,manifest)
    assert summary["before_uncertainty_correct"]==summary["after_uncertainty_correct"]==0
    assert summary["before_evidence_ids_correct"]==1 and summary["after_evidence_ids_correct"]==0
    assert summary["human_review"]=="pending"

