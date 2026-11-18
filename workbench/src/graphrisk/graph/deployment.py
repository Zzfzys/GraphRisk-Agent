"""Pinned Stage09 model decision: XGBoost primary, experimental GNN, no fusion."""
from pathlib import Path
import json
import math
import numpy as np
import pandas as pd
import torch
from graphrisk.demo.stage09_evaluation import digest,read_json,write_json
from graphrisk.graph.service import GraphRiskService,GraphRiskPrediction
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.train_graphsage import _sha256

def bound_path(root,relative):
    file=(root/relative).resolve()
    if not file.is_relative_to(root.resolve()) or file.is_relative_to(root/".git"):
        raise ValueError("model policy path escapes workspace")
    if not (file.is_relative_to(root/"artifacts") or file.is_relative_to(root/"reports/generated")):
        raise ValueError("model policy input must be an artifact or generated evidence")
    return file

def verify_files(root,items):
    for item in items:
        file=bound_path(root,item["path"])
        if not file.is_file() or _sha256(file)!=item["sha256"]:
            raise ValueError("model decision artifact binding failed")
        
def load_policy(root,path,expected_sha256):
    profile=read_json(path)
    payload={k:v for k,v in profile.items() if k!="content_sha256"}
    if profile["content_sha256"]!=expected_sha256 or digest(payload)!=expected_sha256:
        raise ValueError("model decision profile digest mismatch")
    if profile["schema_version"]!="stage09-model-decision-v1" or profile["score_mode"]!="xgboost_only" or profile.get("gnn_role")!="experimental":
        raise ValueError("unsupported model decision mode")
    verify_files(root,profile["authority_files"])
    decision=read_json(bound_path(root,profile["decision_path"]))
    if decision["content_sha256"]!=profile["decision_sha256"] or digest({k:v for k,v in decision.items() if k!="content_sha256"})!=decision["content_sha256"]:
        raise ValueError("frozen validation decision differs")
    if decision["promote_gnn"] or decision["selected_by"]!="validation_only":
        raise ValueError("this profile only supports the frozen no-promotion decision")
    if decision["winner"]["variant"]!="full" or decision["winner"]["cap"]!=5:
        raise ValueError("experimental serving requires the registered full cap5 winner")
    return profile

def materialize(root,path):
    route4=root/"reports/generated/stage09/route4/20261007-diagnosis-v4"
    acceptance=read_json(route4/"acceptance.json")
    decision=read_json(route4/"decision.json")
    if acceptance["engineering_status"]!="done" or acceptance["decision_sha256"]!=decision["content_sha256"]:
        raise ValueError("route4 acceptance missing or decision mismatch")
    name=decision["winner"]["name"]
    folder=root/"artifacts/stage-09/route4/20261007-diagnosis-v4/runs"/name
    meta=read_json(folder/"metadata.json")
    def record(file):return {"path":file.relative_to(root).as_posix(),"sha256":_sha256(file)}
    champion=root/"artifacts/stage-02/ieee-cis/champion"
    old_comparison=root/"artifacts/stage-04/ieee-cis/comparisons/stage05-route8"
    profile={"schema_version":"stage09-model-decision-v1","score_mode":"xgboost_only",
        "decision_path":(route4/"decision.json").relative_to(root).as_posix(),
        "decision_sha256":decision["content_sha256"],"gnn_role":"experimental",
        "authority_files":[record(route4/n) for n in ("decision.json","registration.json")],
        "primary_files":[record(champion/n) for n in ("model.joblib","metadata.json")],
        "primary_model_version":read_json(champion/"metadata.json")["model_version"],
        "primary_model_name":"xgboost",
        "graph_files":[record(folder/n) for n in ("metadata.json","model.pt","validation-scores.npy")]+
            [record(route4/"fixed-test-predictions.parquet"),record(old_comparison/"metadata.json"),
             record(root/"artifacts/stage-04/ieee-cis/graph-manifest.json"),
             record(root/read_json(root/"artifacts/stage-04/ieee-cis/graph-manifest.json")["artifacts"]["encoder"]["path"])],
        "gnn_metadata_path":(folder/"metadata.json").relative_to(root).as_posix(),
        "gnn_model_path":(folder/"model.pt").relative_to(root).as_posix(),
        "gnn_validation_path":(folder/"validation-scores.npy").relative_to(root).as_posix(),
        "gnn_test_path":(route4/"fixed-test-predictions.parquet").relative_to(root).as_posix(),
        "gnn_test_score_column":name,"gnn_model_version":meta["model_version"],
        "source_graph_sha256":meta["graph_manifest_sha256"],"comparison_run":"stage05-route8"}
    profile["content_sha256"]=digest(profile)
    if path.exists() and read_json(path)!=profile:
        raise ValueError("existing deployment profile differs; use a new version")
    write_json(path,profile)
    return profile

def bind_primary(root,profile,risk_tool,model_dir):
    verify_files(root,profile["primary_files"])
    if risk_tool.startup_error is not None or risk_tool._predictor is None:
        raise ValueError("primary risk model unavailable")
    expected=next(item["path"] for item in profile["primary_files"] if item["path"].endswith("/model.joblib"))
    if (model_dir/"model.joblib").resolve()!=bound_path(root,expected):
        raise ValueError("configured primary artifact differs from decision")
    info=risk_tool._predictor.model_info()
    if info["model_version"]!=profile["primary_model_version"] or info["model_name"]!="xgboost":
        raise ValueError("primary model version/name differs")
    return risk_tool

class DecisionGraphService(GraphRiskService):
    """Reuse verified temporal schema; serve winner scores without legacy fusion."""
    def __init__(self,config,*,comparison_dir,root,profile):
        self.profile=profile
        super().__init__(config,comparison_dir=comparison_dir)
        if self.startup_error is not None:return
        try:
            verify_files(root,profile["graph_files"])
            meta=read_json(bound_path(root,profile["gnn_metadata_path"]))
            decision=read_json(bound_path(root,profile["decision_path"]))
            assert self.store is not None
            if (meta["spec"]!=decision["winner"] or meta["graph_manifest_sha256"]!=self.store.manifest["content_sha256"]
                or meta["graph_manifest_sha256"]!=profile["source_graph_sha256"]
                or tuple(meta["feature_names"])!=self.store.encoder.feature_names
                or meta["model_version"]!=profile["gnn_model_version"]):
                raise ValueError("experimental model/schema/encoder mismatch")
            self.model=GraphSAGERiskModel(meta["input_channels"],meta["hidden_channels"],meta["dropout"])
            model_path=bound_path(root,profile["gnn_model_path"])
            if _sha256(model_path)!=meta["model_sha256"]:raise ValueError("experimental weight mismatch")
            self.model.load_state_dict(torch.load(model_path,weights_only=True,map_location="cpu"),strict=True)
            self.model.eval()
            val=np.load(bound_path(root,profile["gnn_validation_path"]),allow_pickle=False)
            test=pd.read_parquet(bound_path(root,profile["gnn_test_path"]),
                columns=["transaction_id",profile["gnn_test_score_column"]])
            if val.shape!=(len(self.store.target_ids["validation"]),) or tuple(test.transaction_id)!=self.store.target_ids["test"]:
                raise ValueError("experimental score target IDs/order differ")
            values=np.concatenate([val,test[profile["gnn_test_score_column"]].to_numpy()])
            if not np.isfinite(values).all() or ((values<0)|(values>1)).any():
                raise ValueError("experimental scores must be finite in [0,1]")
            ids=(*self.store.target_ids["validation"],*self.store.target_ids["test"])
            self._scores=dict(zip(ids,map(float,values),strict=True))
            self.model_version=profile["gnn_model_version"]
            self._tabular_scores,self._fusion_scores={},{}
            self.fusion_alpha=None
        except Exception:
            self.startup_error="graph_model_version_mismatch"
            self.model=None
            self._scores={}
            
    def predict(self,transaction_id):
        store,_=self._covered_target(transaction_id)
        assert self.model is not None
        with torch.no_grad():
            score=float(torch.sigmoid(self.model(store.ego_graph(transaction_id).to_pyg(store.encoder))).item())
        if not math.isfinite(score) or abs(score-self._scores[transaction_id])>1e-5:
            raise ValueError("experimental inference differs from frozen score")
        return GraphRiskPrediction(transaction_id,score,self.model_version,None,None,None,None,
            score_role="experimental")

