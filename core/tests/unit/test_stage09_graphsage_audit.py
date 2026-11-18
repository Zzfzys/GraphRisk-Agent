"""Stage09 route2: causal propagation and contract regressions, synthetic fixtures only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import numpy as np
import pytest
import torch
from torch_geometric.data import Batch, Data
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.schema import GraphFeatureEncoder, GraphTransaction, TemporalEgoBuilder
from graphrisk.graph.temporal_index import TemporalEntityIndex
from graphrisk.graph.train_graphsage import _batches, _evaluate
from graphrisk.models.tabular import NUMERIC_FEATURES

def chain(values=(1.,0.,3.,0.,5.)):
    forward=[(0,1),(1,2),(2,3),(3,4)]
    return Data(x=torch.tensor(values).reshape(-1,1),
        edge_index=torch.tensor(forward+[(b,a) for a,b in forward],dtype=torch.long).t().contiguous(),
        root_index=torch.tensor([0],dtype=torch.long))

def controlled_model():
    model=GraphSAGERiskModel(1,1,0).eval()
    with torch.no_grad():
        for conv in (model.conv1,model.conv2):
            conv.lin_l.weight.fill_(1)
            conv.lin_l.bias.zero_()
            conv.lin_r.weight.fill_(1)
        model.classifier.weight.fill_(1)
        model.classifier.bias.zero_()
    return model

def test_hand_calculated_two_hop_history_and_four_hop_exclusion():
    model=controlled_model()
    original=chain()
    closer=chain((1.,0.,5.,0.,5.))
    distant=chain((1.,0.,3.,0.,25.))
    assert model(original).item()==pytest.approx(3.)
    assert model(closer).item()==pytest.approx(4.)
    assert model(distant).item()==pytest.approx(3.)

def test_autograd_follows_legal_receptive_field():
    model=controlled_model()
    graph=chain()
    graph.x.requires_grad_(True)
    model(graph).sum().backward()
    assert graph.x.grad[2,0].item()==pytest.approx(.5)
    assert graph.x.grad[4,0].item()==pytest.approx(0.)
    for layer in (model.conv1,model.conv2,model.classifier):
        parameters=list(layer.parameters())
        assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in parameters)
        assert any(p.grad.abs().sum()>0 for p in parameters)

def test_batch_disjointness_under_other_graph_history_perturbation():
    model=controlled_model()
    first,second=chain(),chain((2.,0.,4.,0.,6.))
    batch=Batch.from_data_list([first,second])
    assert batch.root_index.tolist()==batch.ptr[:-1].tolist()==[0,5]
    assert torch.allclose(model(batch),torch.cat([model(first),model(second)]))
    changed=Batch.from_data_list([first,chain((2.,0.,100.,0.,6.))])
    assert model(changed)[0].item()==model(batch)[0].item()
    assert model(changed)[1].item()!=model(batch)[1].item()

@pytest.mark.parametrize("roots",[[0,1],[5,0],[0,0]])
def test_batch_roots_must_belong_to_corresponding_graph(roots):
    batch=Batch.from_data_list([chain(),chain()])
    batch.root_index=torch.tensor(roots,dtype=torch.long)
    with pytest.raises(ValueError,match="root.*corresponding graph"):
        controlled_model()(batch)

def test_batch_rejects_cross_graph_message_edge():
    batch=Batch.from_data_list([chain(),chain()])
    batch.edge_index=torch.cat([batch.edge_index,torch.tensor([[2],[5]])],dim=1)
    with pytest.raises(ValueError,match="cross-graph"):
        controlled_model()(batch)

def test_single_graph_requires_one_root():
    graph=chain()
    graph.root_index=torch.tensor([0,2])
    with pytest.raises(ValueError,match="one root"):
        controlled_model()(graph)

@pytest.mark.parametrize("bad_edge",[
    torch.tensor([[0],[-1]],dtype=torch.long),
    torch.tensor([[0],[5]],dtype=torch.long),
    torch.tensor([[0.],[1.]],dtype=torch.float32)])
def test_bad_edge_indices_fail_with_actionable_validation(bad_edge):
    graph=chain()
    graph.edge_index=bad_edge
    with pytest.raises(ValueError,match="edge_index"):
        controlled_model()(graph)

def test_optimizer_updates_each_layer_and_history_only_fixture_overfits():
    torch.manual_seed(17)
    model=GraphSAGERiskModel(1,8,0)
    a,b=chain((1.,0.,1.,0.,5.)),chain((1.,0.,7.,0.,5.))
    batch=Batch.from_data_list([a,b])
    labels=torch.tensor([0.,1.])
    criterion=torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor(1.))
    optimizer=torch.optim.Adam(model.parameters(),lr=.03)
    before=deepcopy(model.state_dict())
    model.train()
    first_loss=criterion(model(batch),labels).item()
    first_grad={}
    for iteration in range(160):
        optimizer.zero_grad(set_to_none=True)
        loss=criterion(model(batch),labels)
        loss.backward()
        if iteration==0:
            first_grad={name:float(p.grad.abs().sum()) for name,p in model.named_parameters()}
        optimizer.step()
    model.eval()
    with torch.no_grad():
        final_loss=criterion(model(batch),labels).item()
        scores=torch.sigmoid(model(batch))
    assert final_loss < .08 and final_loss < first_loss*.15
    assert scores[0]<.1 and scores[1]>.9
    for prefix in ("conv1.","conv2.","classifier."):
        assert any(not torch.equal(before[n],p) for n,p in model.state_dict().items() if n.startswith(prefix))
        assert any(v>0 for n,v in first_grad.items() if n.startswith(prefix))
    assert a.y is None and b.y is None

def test_native_batches_shuffle_targets_and_labels_together_without_graph_labels():
    ids=tuple(str(i) for i in range(7))
    graphs={key:chain((float(key)+1,0.,3.,0.,5.)) for key in ids}
    class Ego:
        def __init__(self,data): self.data=data
        def to_pyg(self,encoder): return self.data
    store=SimpleNamespace(target_ids={"train":ids},encoder=None,
        ego_graph=lambda key:Ego(graphs[key]))
    labels=np.arange(7,dtype=np.float32)
    seen=[]
    for batch,y in _batches(store,"train",labels,batch_size=3,shuffle_seed=23):
        assert batch.y is None
        assert torch.equal(batch.root_index,batch.ptr[:-1])
        assert torch.allclose(batch.x[batch.root_index,0]-1,y)
        seen+=y.tolist()
    assert sorted(seen)==list(range(7))
    for batch,y in _batches(store,"train",labels,batch_size=3,shuffle_seed=None):
        assert torch.allclose(batch.x[batch.root_index,0]-1,y)

def test_native_evaluation_disables_dropout_and_gradients_and_uses_sigmoid():
    ids=("a","b")
    class Ego:
        def __init__(self,data): self.data=data
        def to_pyg(self,encoder): return self.data
    graphs={"a":chain(),"b":chain((1.,0.,5.,0.,5.))}
    store=SimpleNamespace(target_ids={"validation":ids},encoder=None,ego_graph=lambda key:Ego(graphs[key]))
    model=controlled_model()
    model.dropout=torch.nn.Dropout(.5)
    model.train()
    flags=[]
    handle=model.register_forward_pre_hook(lambda module,args:flags.append((module.training,torch.is_grad_enabled())))
    metrics,scores=_evaluate(model,store,"validation",np.array([0.,1.]),
        batch_size=2,device=torch.device("cpu"))
    handle.remove()
    assert flags==[(False,False)]
    assert scores==pytest.approx(torch.sigmoid(torch.tensor([3.,4.])).numpy())
    assert metrics["pr_auc"]==pytest.approx(1.)

def feature_row(key,minute,count=0):
    row={name:0. for name in NUMERIC_FEATURES}
    row.update(transaction_id=key,transaction_time=datetime(2000,1,1,tzinfo=timezone.utc)+timedelta(minutes=minute),
        transaction_amount=10.+minute,tx_count_last_1h=count,tx_count_last_24h=count,tx_count_last_7d=count,
        card_id="card",device_id="unknown-device-"+key,email_domain="unknown-email-"+key,
        address_id="unknown-address-"+key,product_code="W")
    return row

def test_history_node_keeps_own_feature_cutoff_not_target_statistics():
    raw=[feature_row("old",0,0),feature_row("history",10,1),feature_row("target",20,2)]
    rows=[GraphTransaction.from_feature_row(row) for row in raw]
    encoder=GraphFeatureEncoder.fit(rows,train_transaction_ids=("old","history"))
    for builder in (TemporalEgoBuilder(rows),TemporalEntityIndex(rows[::-1])):
        graph=builder.build("target",max_history_per_entity=5)
        node=next(n for n in graph.nodes if n.node_id=="history" and n.node_type=="transaction")
        assert node.transaction.numeric[NUMERIC_FEATURES.index("tx_count_last_24h")]==1
        assert encoder.transform_node(node)==encoder.transform_node(
            TemporalEgoBuilder(rows).build("history",max_history_per_entity=5).nodes[0])
    changed_raw=deepcopy(raw)
    changed_raw[2]["tx_count_last_24h"]=999
    changed=[GraphTransaction.from_feature_row(row) for row in changed_raw]
    node=next(n for n in TemporalEntityIndex(changed).build("target",max_history_per_entity=5).nodes
        if n.node_id=="history" and n.node_type=="transaction")
    assert node.transaction.numeric[NUMERIC_FEATURES.index("tx_count_last_24h")]==1


def test_native_early_stopping_restores_best_clone_and_never_reads_test_labels(tmp_path,monkeypatch):
    import importlib
    native=importlib.import_module("graphrisk.graph.train_graphsage")
    manifest={"project_name":"audit-fixture","model_version":"tiny-v1","fanout":[4,2],
        "random_seed":7,"feature_names":["toy"],"content_sha256":"fixture-manifest","dataset_source":"synthetic"}
    class Ego:
        def __init__(self,data): self.data=data
        def to_pyg(self,encoder): return self.data
    graphs={"a":chain(),"b":chain((1.,0.,5.,0.,5.))}
    store=SimpleNamespace(manifest=manifest,encoder=SimpleNamespace(feature_names=("toy",)),
        target_ids={s:("a","b") for s in ("train","validation","test")},
        ego_graph=lambda key:Ego(graphs[key]))
    config=SimpleNamespace(project_name="audit-fixture",dataset_source="synthetic",
        paths=SimpleNamespace(artifact_dir="artifacts/fixture",graph_manifest="graph.json",training_config="train.yaml"),
        sampling=SimpleNamespace(fanout=(4,2),max_train_transactions=2,max_validation_transactions=2,max_test_transactions=2),
        training=SimpleNamespace(model_version="tiny-v1",random_seed=7,device="cpu",hidden_channels=4,
            dropout=.3,learning_rate=.02,batch_size=2,max_epochs=8,early_stopping_patience=2),
        resolve_path=lambda p:tmp_path/p)
    baseline=SimpleNamespace(paths=SimpleNamespace(**{f"{s}_features":f"{s}.parquet" for s in ("train","validation","test")}),
        resolve_path=lambda p:tmp_path/p)
    monkeypatch.setattr(native,"load_graph_config",lambda _:config)
    monkeypatch.setattr(native,"load_training_config",lambda _:baseline)
    monkeypatch.setattr(native,"load_graph_artifacts",lambda *a,**k:store)
    provenance=[]
    monkeypatch.setattr(native,"_verify_label_provenance",lambda conf,paths:provenance.append(tuple(paths)))
    loaded_splits=[]
    def labels(path,ids,split):
        assert split!="test"
        loaded_splits.append(split)
        return np.array([0.,1.],dtype=np.float32)
    monkeypatch.setattr(native,"_load_labels",labels)
    states=[]
    metrics=(.4,.8,.3,.2)
    def scripted_evaluate(model,store,split,labels,**kwargs):
        assert split=="validation"
        model.eval()
        state={n:v.detach().clone() for n,v in model.state_dict().items()}
        if len(states)<4:
            score=metrics[len(states)]
            states.append(state)
        else:
            assert all(torch.equal(v,states[1][n]) for n,v in state.items())
            assert any(not torch.equal(states[3][n],states[1][n]) for n in state)
            score=.8
        return {"pr_auc":score,"roc_auc":.8},np.array([.2,.8])
    monkeypatch.setattr(native,"_evaluate",scripted_evaluate)
    result=native.train_graphsage(tmp_path/"unused.yaml",evaluate_test=False,run_name="audit-tiny")
    saved=torch.load(result.model_path,weights_only=True,map_location="cpu")
    assert all(torch.equal(v,states[1][n]) for n,v in saved.items())
    assert result.best_epoch==2 and result.test_pr_auc is None
    import json
    metadata=json.loads(result.metadata_path.read_text())
    history=json.loads(result.history_path.read_text())
    assert len(history)==4 and metadata["stopped_after_epoch"]==4
    assert metadata["positive_weight"]==1
    assert loaded_splits==["train","validation"] and provenance==[("train","validation")]
    assert metadata["test_evaluated"] is False and metadata["test_metrics"] is None


@pytest.mark.parametrize("corrupt",[None,"count","mean","deviation"])
def test_independent_history_oracle_detects_target_cutoff_contamination(tmp_path,corrupt):
    import pandas as pd
    from graphrisk.graph.stage09_audit import feature_cutoff_probe
    base=datetime(2000,1,1,tzinfo=timezone.utc)
    source=pd.DataFrame([
        {"transaction_id":key,"identity_key":"same","transaction_time":base+timedelta(minutes=minute),
         "transaction_amount":amount}
        for key,minute,amount in (("old",0,10.),("history",10,20.),("target",20,30.))])
    source["transaction_time"]=source["transaction_time"].dt.tz_localize(None)
    source.to_parquet(tmp_path/"canonical.parquet",index=False)
    row={"transaction_id":"history","identity_key":"same","transaction_time":base.replace(tzinfo=None)+timedelta(minutes=10),
        "transaction_amount":20.,"tx_count_last_1h":1,"tx_count_last_24h":1,"tx_count_last_7d":1,
        "amount_mean_last_24h":10.,"amount_std_last_24h":0.,"amount_deviation_last_24h":10.,
        "transaction_hour":0,"transaction_day_of_week":6,
        "is_fraud":"unused non-label sentinel"}
    if corrupt=="count": row["tx_count_last_24h"]=2
    elif corrupt=="mean": row["amount_mean_last_24h"]=15
    elif corrupt=="deviation": row["amount_deviation_last_24h"]=5
    pd.DataFrame([row]).to_parquet(tmp_path/"features.parquet",index=False)
    conf=SimpleNamespace(paths=SimpleNamespace(interim_features="features.parquet",
        interim_transactions="canonical.parquet"),resolve_path=lambda p:tmp_path/p)
    if corrupt is None:
        result=feature_cutoff_probe(conf,["history"])
        assert result["nodes_checked"]==1 and result["maximum_absolute_difference"]==0
        assert result["labels_read"] is False
    else:
        with pytest.raises(ValueError,match="own-time canonical history"):
            feature_cutoff_probe(conf,["history"])

