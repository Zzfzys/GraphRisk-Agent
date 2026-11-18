"""CPU-only correctness audit of the frozen checkpoint. No training or model APIs."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import math
import time
from pathlib import Path

import duckdb
import torch
from torch_geometric.data import Batch, Data
from graphrisk.agent.stage09_preflight import build_freeze, check_freeze, check_resources, load_contract
from graphrisk.demo.stage09_evaluation import digest, read_json, resources, write_json
from graphrisk.graph.build_artifacts import load_graph_artifacts
from graphrisk.graph.config import load_graph_config
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.train_graphsage import load_graphsage_checkpoint, _sha256
from graphrisk.config import load_config
from graphrisk.models.tabular import NUMERIC_FEATURES

def propagation_probe():
    model=GraphSAGERiskModel(1,1,0).eval()
    with torch.no_grad():
        for conv in (model.conv1,model.conv2):
            conv.lin_l.weight.fill_(1)
            conv.lin_l.bias.zero_()
            conv.lin_r.weight.fill_(1)
        model.classifier.weight.fill_(1)
        model.classifier.bias.zero_()
    edges=torch.tensor([[0,1,1,2,2,3,3,4],[1,0,2,1,3,2,4,3]],dtype=torch.long)
    def graph(values):
        return Data(x=torch.tensor(values).reshape(-1,1),edge_index=edges.clone(),
            root_index=torch.tensor([0],dtype=torch.long))
    original=graph([1.,0.,3.,0.,5.])
    original.x.requires_grad_(True)
    value=model(original)
    value.sum().backward()
    changed=model(graph([1.,0.,5.,0.,5.])).item()
    distant=model(graph([1.,0.,3.,0.,25.])).item()
    result={"nodes":["root T0","entity E0","history T1","entity E1","far T2"],
        "x":[1,0,3,0,5],"neighbor_and_self_weights":1,"bias":0,
        "hand_expected_root_logit":3,"actual_root_logit":value.item(),
        "history_plus_two_logit":changed,"far_plus_twenty_logit":distant,
        "dlogit_dhistory":original.x.grad[2,0].item(),"dlogit_dfar":original.x.grad[4,0].item()}
    assert result["actual_root_logit"]==3 and changed==4 and distant==3
    assert result["dlogit_dhistory"]==.5 and result["dlogit_dfar"]==0
    return result

def feature_cutoff_probe(data_config, ids):
    """Independent canonical self-join; each focal historical node uses its own time."""
    feature=data_config.resolve_path(data_config.paths.interim_features)
    canonical=data_config.resolve_path(data_config.paths.interim_transactions)
    query="""
        WITH focal AS (
          SELECT transaction_id,identity_key,transaction_time,transaction_amount
          FROM read_parquet(?) WHERE transaction_id IN (SELECT unnest(?))
        ), joined AS (
          SELECT f.transaction_id,f.transaction_time,f.transaction_amount,
            COUNT(c.transaction_id) FILTER(WHERE c.transaction_time >= f.transaction_time-INTERVAL '1 hour') AS n1,
            COUNT(c.transaction_id) FILTER(WHERE c.transaction_time >= f.transaction_time-INTERVAL '24 hours') AS n24,
            COUNT(c.transaction_id) AS n7,
            AVG(c.transaction_amount) FILTER(WHERE c.transaction_time >= f.transaction_time-INTERVAL '24 hours') AS mean24,
            STDDEV_POP(c.transaction_amount) FILTER(WHERE c.transaction_time >= f.transaction_time-INTERVAL '24 hours') AS std24
          FROM focal f LEFT JOIN read_parquet(?) c
            ON c.identity_key=f.identity_key AND c.transaction_time<f.transaction_time
            AND c.transaction_time>=f.transaction_time-INTERVAL '7 days'
          GROUP BY f.transaction_id,f.transaction_time,f.transaction_amount
        )
        SELECT transaction_id,n1,n24,n7,mean24,std24,transaction_amount-mean24,
          EXTRACT('hour' FROM transaction_time),EXTRACT('dow' FROM transaction_time)
        FROM joined ORDER BY transaction_id
    """
    with duckdb.connect(":memory:") as connection:
        calculated=connection.execute(query,[str(feature),list(ids),str(canonical)]).fetchall()
        stored=connection.execute(
            "SELECT transaction_id, "+", ".join(NUMERIC_FEATURES[1:])+
            " FROM read_parquet(?) WHERE transaction_id IN (SELECT unnest(?)) ORDER BY transaction_id",
            [str(feature),list(ids)]).fetchall()
    if len(calculated)!=len(ids) or len(stored)!=len(ids):
        raise ValueError("historical node features absent")
    worst=0.
    for actual,expected in zip(stored,calculated,strict=True):
        assert actual[0]==expected[0]
        for a,e in zip(actual[1:],expected[1:],strict=True):
            if a is None or e is None:
                if a is not None or e is not None:
                    raise ValueError("historical feature null mismatch")
            else:
                if not math.isclose(a,e,rel_tol=1e-8,abs_tol=1e-7):
                    raise ValueError("historical node feature differs from own-time canonical history")
                worst=max(worst,abs(a-e))
    return {"nodes_checked":len(ids),"numeric_statistics_per_node":8,
        "labels_read":False,"oracle":"independent strict canonical earlier-event self-join",
        "maximum_absolute_difference":worst}

def audit(root, directory, baseline_directory, before_source):
    if not directory.resolve().is_relative_to(root/"reports/generated/stage09/route2"):
        raise ValueError("audit outputs must remain inside ignored Stage09 route2")
    if (directory/"audit.json").exists():
        raise ValueError("audit already exists; preserve it and use a new output directory")
    contract=load_contract(root/"configs/stage09_experiment.json")
    baseline=read_json(baseline_directory/"inputs.json")
    case_manifest=read_json(baseline_directory/"cases.json")
    if digest({k:v for k,v in case_manifest.items() if k!="content_sha256"})!=case_manifest["content_sha256"]:
        raise ValueError("baseline case manifest changed")
    if case_manifest["input_freeze"]!=baseline["content_sha256"]:
        raise ValueError("baseline inventory binding changed")
    old_entry=next(a for a in baseline["artifacts"] if a["path"]=="src/graphrisk/graph/model.py")
    if _sha256(before_source)!=old_entry["sha256"]:
        raise ValueError("saved pre-fix model source does not match real baseline")
    drift=check_freeze(root,baseline)
    if drift["violations"]!=["artifact_changed:src/graphrisk/graph/model.py"]:
        raise ValueError("unexpected baseline input drift; only documented graph validator change allowed")
    start=resources(True)
    if check_resources(contract.resources,start)["status"]!="passed":
        raise ValueError("resource start gate failed")
    directory.mkdir(parents=True,exist_ok=True)
    write_json(directory/"start-resources.json",start)
    began=time.perf_counter()
    torch.set_num_threads(1)
    config=load_graph_config(root/"configs/graph_ieee_cis.yaml")
    store=load_graph_artifacts(config.resolve_path(config.paths.graph_manifest),expected_dataset_source="ieee_cis")
    comparison_dir=config.resolve_path(config.paths.artifact_dir)/"comparisons/stage05-route8"
    comparison=read_json(comparison_dir/"metadata.json")
    checkpoint=Path(comparison["graph_model_path"])
    if not checkpoint.is_absolute() or not checkpoint.resolve().is_relative_to(config.resolve_path(config.paths.artifact_dir)):
        raise ValueError("checkpoint path is not inside frozen artifact root")
    if _sha256(checkpoint)!=comparison["graph_model_sha256"]:
        raise ValueError("checkpoint and comparison hash mismatch")
    model=load_graphsage_checkpoint(checkpoint,checkpoint.with_name("metadata.json"),store,
        device="cpu",expected_model_version=config.training.model_version)
    spec=importlib.util.spec_from_file_location("stage09_saved_graphsage",before_source)
    previous=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    old=previous.GraphSAGERiskModel(model.input_channels,model.hidden_channels,model.dropout_probability)
    old.load_state_dict(model.state_dict(),strict=True)
    old.eval()
    prediction_info=comparison["outputs"]["validation_predictions"]
    prediction_path=comparison_dir/prediction_info["path"]
    if _sha256(prediction_path)!=prediction_info["sha256"]:
        raise ValueError("validation prediction artifact mismatch")
    with duckdb.connect(":memory:") as connection:
        prediction_rows=connection.execute(
            "SELECT transaction_id,graphsage_score FROM read_parquet(?)",[str(prediction_path)]).fetchall()
    saved_scores=dict(prediction_rows)
    selected=[c for c in case_manifest["cases"] if c["graph_covered"]]
    if len(selected)!=20 or len({c["transaction_id"] for c in selected})!=20:
        raise ValueError("expected twenty distinct frozen validation graph-covered cases")
    examples=[]
    graphs=[]
    feature_ids=set()
    maximums={"old_new_logit":0.,"saved_score":0.,"batch_single_logit":0.}
    for case in selected:
        cid=case["transaction_id"]
        if cid not in store.target_ids["validation"]:
            raise ValueError("audit target not in validation graph coverage")
        ego=store.ego_graph(cid)
        target=ego.nodes[0].transaction
        history=[node.transaction for node in ego.nodes if node.transaction is not None and node.node_id!=cid]
        assert all(h.transaction_time<target.transaction_time for h in history)
        feature_ids.update(h.transaction_id for h in history)
        graph=ego.to_pyg(store.encoder)
        assert graph.root_index.tolist()==[0] and graph.y is None and torch.isfinite(graph.x).all()
        graphs.append(graph)
        with torch.no_grad():
            new_logit=model(graph).item()
            old_logit=old(graph).item()
        score=float(torch.sigmoid(torch.tensor(new_logit)).item())
        old_delta=abs(old_logit-new_logit)
        saved_delta=abs(score-saved_scores[cid])
        if old_delta>1e-7 or saved_delta>1e-5:
            raise ValueError("validator changed valid prediction or frozen score no longer reproduces")
        maximums["old_new_logit"]=max(maximums["old_new_logit"],old_delta)
        maximums["saved_score"]=max(maximums["saved_score"],saved_delta)
        examples.append({"case_id":case["case_id"],"transaction_id":cid,
            "history_nodes":len(history),"nodes":graph.num_nodes,"directed_edges":graph.num_edges,
            "new_logit":new_logit,"old_logit":old_logit,"stored_score":saved_scores[cid],
            "score":score,"difference":saved_delta})
    batch=Batch.from_data_list(graphs)
    assert torch.equal(batch.root_index,batch.ptr[:-1])
    assert torch.equal(batch.batch[batch.edge_index[0]],batch.batch[batch.edge_index[1]])
    with torch.no_grad():
        batch_values=model(batch)
    maximums["batch_single_logit"]=max(abs(a.item()-b["new_logit"]) for a,b in zip(batch_values,examples,strict=True))
    if maximums["batch_single_logit"]>1e-5:
        raise ValueError("real batched roots do not match singleton predictions")
    cutoff=feature_cutoff_probe(load_config(config.resolve_path(config.paths.data_config)),sorted(feature_ids))
    result={"status":"passed","model_source_before_sha256":old_entry["sha256"],
        "model_source_after_sha256":_sha256(root/"src/graphrisk/graph/model.py"),
        "code_change":"validation only: root ownership, cross-graph messages, single root, edge range/type",
        "baseline_drift":drift,"checkpoint_sha256":_sha256(checkpoint),
        "graph_manifest_content_sha256":store.manifest["content_sha256"],
        "real_validation_cases":len(examples),"cases":examples,"maximum_differences":maximums,
        "propagation_probe":propagation_probe(),"history_feature_cutoffs":cutoff,
        "end_resources":resources(True),"elapsed_seconds":time.perf_counter()-began,
        "real_training_runs":0,"generation_calls":0,"paid_calls":0,"test_labels_read":False,
        "fixture_note":"controlled graph is causal correctness, not IEEE fraud accuracy"}
    current=build_freeze(root,root/"configs/stage09_experiment.json")
    write_json(directory/"inputs-after-audit.json",current)
    result["input_freeze_after_sha256"]=current["content_sha256"]
    write_json(directory/"audit.json",result)
    print(json.dumps({k:v for k,v in result.items() if k not in ("cases","end_resources")},ensure_ascii=False,indent=2))

