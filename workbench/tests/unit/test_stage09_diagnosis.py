"""Meaningful fixture tests for temporal packing, controlled ablations and budgets."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from pathlib import Path
from dataclasses import asdict
import numpy as np
import pytest
import torch
from torch_geometric.data import Batch
from graphrisk.demo.stage09_evaluation import read_json, write_json
from graphrisk.graph.stage09_packed import (stream_graphs, project_variant, build_cache,
    PackedGraphs, verify_cache, transform_edges)
from graphrisk.graph.schema import GraphTransaction, GraphFeatureEncoder
from graphrisk.graph.temporal_index import TemporalEntityIndex
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.stage09_budget import GraphBudgetLedger
from graphrisk.graph.stage09_diagnosis import fit_gnn, registered_runs, pick_winner, evaluate_decision
from graphrisk.agent.stage09_preflight import load_contract

def row(key, hour, card="card", device="device", product="C"):
    return GraphTransaction(key, datetime(2026,1,1,tzinfo=UTC)+timedelta(hours=hour),
        card, device, "email", "address", product, (float(hour+1),0.,1.,2.,None,None,None,0.,1.))

def fixtures():
    # A tie group larger than both caps: must retain lowest IDs, not last inserted IDs.
    return [row(f"a{i:02d}",0,card=f"c{i%3}") for i in range(15)] + [
        row("b",1),row("c",2),row("tie",2),row("d",3,device="unknown-x")]

def test_stream_matches_native_for_ties_cap_dedup_and_future_exclusion(monkeypatch):
    import graphrisk.graph.stage09_packed as module
    rows=fixtures()
    monkeypatch.setattr(module,"_iter_projected_rows",lambda path:iter(rows))
    ids={"train":("b",),"validation":("c","tie"),"test":("d",)}
    index=TemporalEntityIndex(rows)
    count=0
    for split,target,cap,graph in stream_graphs(Path("unused"),ids,(5,10)):
        expected=index.build(target.transaction_id,max_history_per_entity=cap)
        assert graph.content_sha256()==expected.content_sha256()
        assert all(n.transaction.transaction_time<target.transaction_time
                   for n in graph.nodes[1:] if n.transaction)
        count+=1
    assert count==8
    with pytest.raises(ValueError,match="IDs/order"):
        list(stream_graphs(Path("unused"),{"train":("absent",)},(5,)))

def test_packed_cache_batches_equal_native_and_detect_corruption(tmp_path,monkeypatch):
    import graphrisk.graph.stage09_packed as module
    rows=fixtures()
    ids={"train":("b",),"validation":("c","tie"),"test":("d",)}
    encoder=GraphFeatureEncoder.fit(rows,train_transaction_ids=("b",))
    monkeypatch.setattr(module,"load_manifest_inputs",
        lambda *a:({"content_sha256":"source","artifacts":{"projected_transactions":{"path":"unused"}}},encoder,ids))
    monkeypatch.setattr(module,"_iter_projected_rows",lambda path:iter(rows))
    monkeypatch.setattr(module,"future_entity_cutoffs",lambda *a:None)
    folder=tmp_path/"packed"
    manifest=build_cache(tmp_path,folder,Path("unused"),ids,(5,10),20261007)
    assert verify_cache(folder)==manifest
    index=TemporalEntityIndex(rows)
    torch.manual_seed(7)
    model=GraphSAGERiskModel(len(encoder.feature_names),8,.2).eval()
    for cap in (5,10):
        packed=PackedGraphs(folder,manifest,cap,"validation","full")
        actual=packed.batch([1,0])
        native=Batch.from_data_list([index.build(key,max_history_per_entity=cap).to_pyg(encoder)
            for key in ("tie","c")])
        assert torch.equal(actual.x,native.x)
        assert torch.equal(actual.edge_index,native.edge_index)
        assert torch.equal(actual.root_index,native.root_index)
        assert torch.equal(actual.batch,native.batch)
        assert torch.equal(model(actual),model(native))
        assert actual.y is None
    file=folder/"cap5-full-train"/"x.f32"
    file.write_bytes(file.read_bytes()+b"x")
    with pytest.raises(ValueError,match="bytes changed"):
        verify_cache(folder)

def test_rewire_preserves_root_degree_types_nodes_edges_and_changes_history():
    # Root0 connects card3 and device5; historical1/2 have distinct cards3/4.
    x=np.zeros((7,28),dtype=np.float32)
    x[:3,-5]=1
    x[3:5,-4]=1
    x[5:,-3]=1
    x[:3,:18]=np.arange(54).reshape(3,18)
    forward=np.array([[0,3],[0,5],[1,3],[1,6],[2,4],[2,5]],dtype=np.int64)
    edges=np.concatenate([forward,forward[:,::-1]])
    rewired=None
    for seed in range(20):
        xx,ee,changed=project_variant(x,edges,"rewired","fixture",seed)
        if changed:
            rewired=ee
            break
    assert rewired is not None and np.array_equal(xx,x)
    types=np.argmax(x[:,-5:],axis=1)
    assert np.array_equal(np.bincount(edges.ravel(),minlength=7),
                          np.bincount(rewired.ravel(),minlength=7))
    assert set(map(tuple,rewired[rewired[:,0]==0]))==set(map(tuple,edges[edges[:,0]==0]))
    for a,b in rewired:
        assert (types[a]==0)!=(types[b]==0)
    assert len(set(map(tuple,rewired)))==len(edges)
    assert set(map(tuple,rewired))!={tuple(e) for e in edges}
    np.testing.assert_array_equal(project_variant(x,edges,"rewired","fixture",seed)[1],rewired)

def test_ablation_masks_history_only_not_root_or_entity_type():
    x=np.zeros((5,28),dtype=np.float32)
    x[:3,-5]=1
    x[3,-4]=1
    x[4,-3]=1
    x[:3,:23]=2
    edge=np.array([[0,3],[3,0],[1,3],[3,1]],dtype=np.int64)
    xx,ee,_=project_variant(x,edge,"no_history_numeric","fixture",7)
    np.testing.assert_array_equal(xx[0],x[0])
    np.testing.assert_array_equal(xx[1:3,18:],x[1:3,18:])
    assert not xx[1:3,:18].any()
    np.testing.assert_array_equal(ee,edge)
    np.testing.assert_array_equal(xx[3:],x[3:])
    _,empty,_=project_variant(x,edge,"no_edges","fixture",7)
    assert empty.shape==(0,2)
    with pytest.raises(ValueError):
        project_variant(x,edge,"unknown","fixture",7)

def limits(runs=13,total=21600):
    return SimpleNamespace(max_new_gnn_runs=runs,max_run_seconds=3600,
        max_total_training_seconds=total,maximum_final_test_batches=1)

def test_budget_attempt_failed_not_refunded_deadline_and_global_stop(tmp_path):
    ledger=GraphBudgetLedger(tmp_path/"ledger.json",limits(runs=2,total=5))
    now=[0.]
    first=ledger.reserve("runs","first","reg",lambda:now[0])
    now[0]=2.
    first.finish("failed")
    with pytest.raises(ValueError,match="already"):
        ledger.reserve("runs","first","reg")
    second=ledger.reserve("runs","second","reg",lambda:now[0])
    assert second.cap==3.
    now[0]=5.
    with pytest.raises(TimeoutError):
        second.guard()
    second.finish("failed")
    with pytest.raises(ValueError,match="quota"):
        ledger.reserve("runs","third","reg")

def test_budget_crash_blocks_retries_and_final_test_seals_search(tmp_path):
    ledger=GraphBudgetLedger(tmp_path/"crash.json",limits())
    ledger.reserve("runs","orphan","reg")
    assert ledger.spent(ledger.read())==3600
    with pytest.raises(ValueError,match="unresolved"):
        ledger.reserve("runs","different","reg")
    with pytest.raises(ValueError,match="unresolved"):
        ledger.seal_test("decision")
    clean=GraphBudgetLedger(tmp_path/"clean.json",limits())
    attempt=clean.reserve("work","build","reg")
    attempt.finish("completed")
    clean.seal_test("decision")
    with pytest.raises(ValueError,match="quota"):
        clean.seal_test("same-decision")
    with pytest.raises(ValueError,match="sealed"):
        clean.reserve("runs","late-search","reg")

def test_registration_reuses_exact_full_candidate_and_caps_real_runs():
    contract=load_contract(Path("configs/stage09_experiment.json"))
    runs=registered_runs(contract)
    assert len(runs)==10 and len(runs)+2<=13
    assert len({r["name"] for r in runs})==10
    assert sum(r["cap"]==10 for r in runs)==1
    assert all(r["seed"]==20260921 for r in runs)

def test_only_validation_eligible_candidates_and_three_seed_stability():
    runs=[{"name":"full","eligible":True},{"name":"no-edges","eligible":False}]
    values={"full":{"validation_metrics":{"pr_auc":.2}},"no-edges":{"validation_metrics":{"pr_auc":.9}}}
    assert pick_winner(values,runs)["name"]=="full"
    results=lambda v:[{"validation_metrics":{"pr_auc":p}} for p in v]
    assert evaluate_decision(results([.23,.24,.25]),.21,.005)["promote_gnn"]
    assert not evaluate_decision(results([.23,.24,.20]),.21,.005)["promote_gnn"]
    assert not evaluate_decision(results([.23,.24]),.21,.005)["promote_gnn"]

def test_gnn_training_restores_best_weights_and_uses_no_test(monkeypatch,tmp_path):
    import graphrisk.graph.stage09_diagnosis as module
    from torch_geometric.data import Data
    graph=Data(x=torch.tensor([[0.,1.],[1.,0.]]),edge_index=torch.tensor([[0,1],[1,0]]),
               root_index=torch.tensor([0]))
    class Tiny:
        dimension=2
        ids=("low","high")
        def batch(self,positions):
            graphs=[]
            for i in positions:
                sample=graph.clone()
                sample.x=sample.x+float(i)
                graphs.append(sample)
            return Batch.from_data_list(graphs)
    original=module.scores_for
    observed=[]
    def score(*a,**kw):
        v=original(*a,**kw)
        observed.append(v.copy())
        return v
    monkeypatch.setattr(module,"scores_for",score)
    original_metric=module.evaluate_binary_classifier
    seq=iter([.4,.8,.3,.2,.8])
    def metrics(*a,**kw):
        m=original_metric(*a,**kw)
        m["pr_auc"]=next(seq)
        return m
    monkeypatch.setattr(module,"evaluate_binary_classifier",metrics)
    y=np.array([0.,1.],dtype=np.float32)
    spec={"name":"tiny","seed":7,"hidden_channels":4,"dropout":.2,"learning_rate":.01}
    plan={"device":"cpu","cpu_threads":1,"max_epochs":8,"batch_size":2,"patience":2}
    result=fit_gnn(Tiny(),Tiny(),y,y,spec,plan,tmp_path)
    assert result["best_epoch"]==2 and result["stopped_after_epoch"]==4
    np.testing.assert_array_equal(observed[1],observed[-1])
    state=torch.load(tmp_path/"model.pt",weights_only=True,map_location="cpu")
    assert state.keys()==GraphSAGERiskModel(2,4,.2).state_dict().keys()

@pytest.mark.parametrize("caps",[(),(0,),(True,)])
def test_stream_invalid_history_caps_rejected(caps,monkeypatch):
    import graphrisk.graph.stage09_packed as module
    monkeypatch.setattr(module,"_iter_projected_rows",lambda path:iter([]))
    with pytest.raises(ValueError,match="positive integers"):
        list(stream_graphs(Path("unused"),{},caps))

def test_work_build_time_charged_against_training_total(tmp_path):
    ledger=GraphBudgetLedger(tmp_path/"ledger.json",limits(total=5))
    now=[0.]
    work=ledger.reserve("work","construction","reg",lambda:now[0])
    now[0]=4.
    work.finish("completed")
    trial=ledger.reserve("runs","model","reg",lambda:now[0])
    assert trial.cap==1.
    now[0]=5.
    with pytest.raises(TimeoutError):
        trial.guard()
    trial.finish("failed")
    with pytest.raises(ValueError,match="cumulative"):
        ledger.reserve("runs","another","reg")

def test_final_review_failure_still_seals_budget(tmp_path):
    ledger=GraphBudgetLedger(tmp_path/"ledger.json",limits())
    ledger.seal_test("validation-decision")
    ledger.finish_test("failed_no_refund",1.)
    assert ledger.read()["test_batches"][0]["state"]=="failed_no_refund"
    with pytest.raises(ValueError,match="quota"):
        ledger.seal_test("new-decision")
    with pytest.raises(ValueError,match="sealed"):
        ledger.reserve("runs","new-search","reg")

def test_gnn_deadline_stops_before_any_batch_or_weights(tmp_path):
    class NotReadable:
        dimension=2
        ids=("a","b")
        def batch(self,positions):
            pytest.fail("deadline must stop before dataset/model step")
    spec={"name":"tiny","seed":7,"hidden_channels":4,"dropout":.2,"learning_rate":.01}
    plan={"device":"cpu","cpu_threads":1,"max_epochs":2,"batch_size":2,"patience":1}
    def expired():
        raise TimeoutError("expired")
    labels=np.array([0.,1.],dtype=np.float32)
    with pytest.raises(TimeoutError):
        fit_gnn(NotReadable(),NotReadable(),labels,labels,spec,plan,tmp_path,expired)
    assert not (tmp_path/"model.pt").exists()

def test_pruned_postings_are_exact_and_do_not_use_future_values(monkeypatch):
    import graphrisk.graph.stage09_packed as module
    rows=fixtures()
    monkeypatch.setattr(module,"_iter_projected_rows",lambda path:iter(rows))
    ids={"train":("b",),"validation":("c","tie"),"test":("d",)}
    cutoffs={}
    for r in rows:
        if r.transaction_id in {k for v in ids.values() for k in v}:
            for entity in r.valid_entities():
                cutoffs[entity]=max(cutoffs.get(entity,r.transaction_time),r.transaction_time)
    full=list(stream_graphs(Path("unused"),ids,(5,10)))
    pruned=list(stream_graphs(Path("unused"),ids,(5,10),entity_cutoffs=cutoffs))
    assert [v[3].content_sha256() for v in full]==[v[3].content_sha256() for v in pruned]
    # Future target metadata only decides storage retention, not feature values or earlier histories.
    assert all(any(n.key==("transaction",v[1].transaction_id) for n in v[3].nodes) for v in pruned)

def test_future_entity_cutoffs_queries_no_labels_and_excludes_unknown(tmp_path):
    import pandas as pd
    from graphrisk.graph.stage09_packed import future_entity_cutoffs
    path=tmp_path/"roots.parquet"
    pd.DataFrame({"transaction_id":["wanted","future-unused"],
        "transaction_time":[datetime(2026,1,1,tzinfo=UTC),datetime(2026,2,1,tzinfo=UTC)],
        "card_id":["c","unused"],"device_id":["unknown-d","other"],
        "email_domain":["e","other"],"address_id":["a","other"]}).to_parquet(path,index=False)
    cutoffs=future_entity_cutoffs(path,{"train":("wanted",)})
    assert set(cutoffs)=={("card","c"),("email_domain","e"),("address","a")}


def test_continuous_monitor_uses_ram_disk_without_gpu_driver(tmp_path,monkeypatch):
    import graphrisk.graph.stage09_monitor as module
    calls=[]
    budget=SimpleNamespace(minimum_available_ram_gib=3,minimum_disk_free_gib={"E":10},minimum_free_gpu_mib=4096)
    mon=module.DiagnosisMonitor(budget,tmp_path/"resources.json")
    def read(gpu):
        calls.append(gpu)
        return {"available_ram_gib":4.,"disk_free_gib":{"E":20.}}
    monkeypatch.setattr(module,"resources",read)
    mon.sample(False)
    mon.require()
    assert calls==[False]

def test_monitor_missing_measurement_and_real_ram_floor_fail_closed(tmp_path,monkeypatch):
    import graphrisk.graph.stage09_monitor as module
    budget=SimpleNamespace(minimum_available_ram_gib=3,minimum_disk_free_gib={"E":10},minimum_free_gpu_mib=4096)
    mon=module.DiagnosisMonitor(budget,tmp_path/"resources.json")
    monkeypatch.setattr(module,"resources",lambda gpu:{"available_ram_gib":2.99,"disk_free_gib":{"E":20.}})
    mon.sample(False)
    with pytest.raises(ValueError):
        mon.require()
    failed=module.DiagnosisMonitor(budget,tmp_path/"failure.json")
    def unavailable(gpu):
        raise OSError("probe not available")
    monkeypatch.setattr(module,"resources",unavailable)
    failed.sample(True)
    assert failed.samples[0]["error_type"]=="OSError" and failed.samples[0]["gpu_probe"]
    with pytest.raises(ValueError):
        failed.require()


def test_gpu_timeout_retries_bounded_and_requires_fresh_success(tmp_path,monkeypatch):
    import subprocess
    import graphrisk.graph.stage09_monitor as module
    budget=SimpleNamespace(minimum_available_ram_gib=3,minimum_disk_free_gib={"E":10},minimum_free_gpu_mib=4096)
    mon=module.DiagnosisMonitor(budget,tmp_path/"resources.json")
    calls=[]
    def probe(gpu):
        calls.append(gpu)
        if len(calls)==1:
            raise subprocess.TimeoutExpired("nvidia-smi",5)
        return {"available_ram_gib":4.,"disk_free_gib":{"E":20.},"free_gpu_mib":5000,"gpu_utilization_percent":0}
    monkeypatch.setattr(module,"resources",probe)
    result=mon.sample(True)
    mon.require()
    assert result["gpu_probe_attempt"]==2 and calls==[True,True]
    assert mon.samples[0]["bounded_retry_pending"]
    exhausted=module.DiagnosisMonitor(budget,tmp_path/"exhausted.json")
    monkeypatch.setattr(module,"resources",lambda gpu:(_ for _ in ()).throw(subprocess.TimeoutExpired("nvidia-smi",5)))
    exhausted.sample(True)
    assert len(exhausted.samples)==3
    with pytest.raises(ValueError):
        exhausted.require()

