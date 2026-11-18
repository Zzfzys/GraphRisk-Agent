"""Current mean GraphSAGE only; finite candidates and restored-best diagnostics."""
import math
import random
import time

import numpy as np
import torch
from torch import nn

from graphrisk.agent.stage09_preflight import _content_hash
from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.stage09_diagnosis import scores_for
from graphrisk.graph.train_graphsage import _seed_everything
from graphrisk.models.enhanced_training import metrics


def positive_weight(labels, rule):
    labels = np.asarray(labels)
    if labels.ndim != 1 or not np.isin(labels,[0,1]).all() or len(np.unique(labels)) != 2:
        raise ValueError("training requires both binary classes")
    ratio = float((len(labels)-labels.sum())/labels.sum())
    if rule == "train_ratio":
        return ratio
    if rule == "sqrt_train_ratio":
        return math.sqrt(ratio)
    raise ValueError("unregistered positive-weight rule")


def probe_positions(count, maximum):
    if count < 2 or maximum < 2:
        raise ValueError("diagnostic probe needs at least two roots")
    return np.linspace(0,count-1,min(count,maximum),dtype=np.int64)


class Probe:
    def __init__(self, dataset, positions):
        self.dataset, self.positions = dataset, positions
        self.ids = tuple(dataset.ids[i] for i in positions)
    def batch(self, positions):
        return self.dataset.batch(self.positions[list(positions)])


def train(train, inner, y_train, y_inner, task, settings, directory, guard=lambda: None):
    if train.dimension != 28 or inner.dimension != 28 or len(y_train) != len(train.ids) or len(y_inner) != len(inner.ids):
        raise ValueError("aligned 28-dimensional roots required")
    # Validate both-class inner data before constructing the model.
    positive_weight(y_inner,"train_ratio")
    weight = positive_weight(y_train,task["parameters"]["positive_weight"])
    _seed_everything(task["seed"])
    torch.set_num_threads(settings["cpu_threads"])
    device = torch.device(settings["device"])
    params = task["parameters"]
    model = GraphSAGERiskModel(28,params["hidden_channels"],params["dropout"]).to(device)
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    optimizer = torch.optim.Adam(model.parameters(),lr=params["learning_rate"])
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(weight,device=device))
    positions = probe_positions(len(train.ids),settings["train_probe_count"])
    probe = Probe(train,positions)
    positive_weight(y_train[positions],"train_ratio")
    history, best, best_epoch, state, stale = [], -1., 0, None, 0
    started = time.perf_counter()
    for epoch in range(1,settings["max_epochs"]+1):
        guard()
        model.train()
        order = list(range(len(train.ids)))
        random.Random(task["seed"]+epoch).shuffle(order)
        total = 0.
        for begin in range(0,len(order),settings["batch_size"]):
            guard()
            selected = order[begin:begin+settings["batch_size"]]
            batch = train.batch(selected).to(device)
            labels = torch.tensor(y_train[selected],device=device,dtype=torch.float32)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(batch),labels)
            if not torch.isfinite(loss):
                raise ValueError("nonfinite GraphSAGE loss")
            loss.backward()
            if any(p.grad is not None and not torch.isfinite(p.grad).all() for p in model.parameters()):
                raise ValueError("nonfinite GraphSAGE gradient")
            optimizer.step()
            total += float(loss.detach().cpu())*len(selected)
        values = scores_for(model,inner,settings["batch_size"],device,guard)
        result = metrics(inner.ids,y_inner,values)
        train_values = scores_for(model,probe,settings["batch_size"],device,guard)
        train_metrics = metrics(probe.ids,y_train[positions],train_values)
        entry = {"epoch": epoch,"train_weighted_loss": total/len(train.ids),"train_probe_ap":train_metrics["ap"],
                 "train_probe_roc_auc": train_metrics["roc_auc"],"inner_ap":result["ap"],"inner_roc_auc":result["roc_auc"],
                 "elapsed_seconds":time.perf_counter()-started}
        history.append(entry)
        write_json(directory / "training-history.json",history)
        print(f"{task['id']} epoch={epoch} loss={entry['train_weighted_loss']:.5f} train-probe-AP={train_metrics['ap']:.6f} inner-AP={result['ap']:.6f} ROC={result['roc_auc']:.6f}",flush=True)
        if result["ap"] > best:
            best,best_epoch,stale = result["ap"],epoch,0
            state = {k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
            torch.save(state,directory / "best-progress.pt")
        else:
            stale += 1
        if stale >= settings["patience"]:
            break
    guard()
    model.load_state_dict(state)
    values = scores_for(model,inner,settings["batch_size"],device,guard)
    final = metrics(inner.ids,y_inner,values)
    torch.save(state,directory / "model.pt")
    restored = GraphSAGERiskModel(28,params["hidden_channels"],params["dropout"]).to(device)
    restored.load_state_dict(torch.load(directory / "model.pt",map_location=device,weights_only=True))
    reload_values = scores_for(restored,inner,settings["batch_size"],device,guard)
    delta = float(np.max(np.abs(values-reload_values)))
    if delta > 1e-6 or abs(final["ap"]-best) > 1e-12:
        raise ValueError("best-checkpoint or independent weight reload mismatch")
    np.save(directory / "inner-scores.npy",values)
    return {"metrics":final,"best_epoch":best_epoch,"epochs_observed":len(history),"positive_weight":weight,
            "probe_count":len(positions),"probe_ids_sha256":_content_hash({"ids":list(probe.ids)}),
            "parameter_count":sum(p.numel() for p in model.parameters()),"reload_rows":len(values),"reload_max_abs_delta":delta,
            "cuda_peak_allocated_mib":torch.cuda.max_memory_allocated(device)/2**20 if device.type == "cuda" else None}


def choose(results, tasks):
    ids = [t["id"] for t in tasks if t["lane"] == "search"]
    if any(key not in results for key in ids):
        raise ValueError("all six registered candidates must complete before selection")
    winner = min(enumerate(ids),key=lambda pair:(-results[pair[1]]["metrics"]["ap"],results[pair[1]]["fit_seconds"],pair[0]))[1]
    return winner
