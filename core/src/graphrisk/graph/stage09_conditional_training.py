"""The same two-layer mean model, with explicit conditional input dimensions."""
import random
import math
import time

import numpy as np
import torch

from graphrisk.agent.stage09_preflight import _content_hash
from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.stage09_diagnosis import scores_for
from graphrisk.graph.stage09_expansion_training import Probe, positive_weight, probe_positions
from graphrisk.graph.train_graphsage import _seed_everything
from graphrisk.models.enhanced_training import metrics


def fit(train, inner, y_train, y_inner, task, settings, directory, guard=lambda: None):
    if train.dimension != inner.dimension or train.dimension < 28 or len(y_train) != len(train.ids) or len(y_inner) != len(inner.ids):
        raise ValueError("conditional dimensions/labels must align")
    positive_weight(y_inner, "train_ratio")
    weight = positive_weight(y_train, task["parameters"]["positive_weight"])
    _seed_everything(task["seed"])
    torch.set_num_threads(settings["cpu_threads"])
    device = torch.device(settings["device"])
    params = task["parameters"]
    model = GraphSAGERiskModel(train.dimension, params["hidden_channels"], params["dropout"]).to(device)
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=params["learning_rate"])
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor(weight, device=device))
    positions = probe_positions(len(train.ids), settings["train_probe_count"])
    probe = Probe(train, positions)
    positive_weight(y_train[positions], "train_ratio")
    history, best, best_epoch, state, stale = [], -1., 0, None, 0
    started = time.perf_counter()
    for epoch in range(1, settings["max_epochs"]+1):
        guard()
        model.train()
        order = list(range(len(train.ids)))
        random.Random(task["seed"]+epoch).shuffle(order)
        total = 0.
        for begin in range(0, len(order), settings["batch_size"]):
            guard()
            selected = order[begin:begin+settings["batch_size"]]
            batch = train.batch(selected).to(device)
            labels = torch.tensor(y_train[selected], device=device, dtype=torch.float32)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(batch), labels)
            loss_value = float(loss.detach().cpu())
            if not math.isfinite(loss_value):
                raise ValueError("nonfinite conditional loss")
            loss.backward()
            finite = [torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None]
            if finite and not torch.stack(finite).all():
                raise ValueError("nonfinite conditional gradient")
            optimizer.step()
            total += loss_value*len(selected)
        values = scores_for(model, inner, settings["batch_size"], device, guard)
        observed = metrics(inner.ids, y_inner, values)
        train_values = scores_for(model, probe, settings["batch_size"], device, guard)
        train_metrics = metrics(probe.ids, y_train[positions], train_values)
        entry = {"epoch": epoch, "train_weighted_loss": total/len(train.ids),
                 "train_probe_ap": train_metrics["ap"], "train_probe_roc_auc": train_metrics["roc_auc"],
                 "inner_ap": observed["ap"], "inner_roc_auc": observed["roc_auc"],
                 "elapsed_seconds": time.perf_counter()-started}
        history.append(entry)
        write_json(directory / "training-history.json", history)
        print(f"{task['id']} epoch={epoch} loss={entry['train_weighted_loss']:.5f} train-AP={train_metrics['ap']:.6f} inner-AP={observed['ap']:.6f} ROC={observed['roc_auc']:.6f}", flush=True)
        if observed["ap"] > best:
            best, best_epoch, stale = observed["ap"], epoch, 0
            state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            torch.save(state, directory / "best-progress.pt")
        else:
            stale += 1
        if stale >= settings["patience"]:
            break
    guard()
    model.load_state_dict(state)
    values = scores_for(model, inner, settings["batch_size"], device, guard)
    final = metrics(inner.ids, y_inner, values)
    torch.save(state, directory / "model.pt")
    restored = GraphSAGERiskModel(train.dimension, params["hidden_channels"], params["dropout"]).to(device)
    restored.load_state_dict(torch.load(directory / "model.pt", map_location=device, weights_only=True))
    repeated = scores_for(restored, inner, settings["batch_size"], device, guard)
    delta = float(np.max(np.abs(values-repeated)))
    if delta > 1e-6 or abs(final["ap"]-best) > 1e-12:
        raise ValueError("conditional best checkpoint/reload mismatch")
    np.save(directory / "inner-scores.npy", values)
    return {"metrics": final, "best_epoch": best_epoch, "epochs_observed": len(history),
            "positive_weight": weight, "input_dimension": train.dimension,
            "parameter_count": sum(p.numel() for p in model.parameters()),
            "probe_count": len(positions), "probe_ids_sha256": _content_hash({"ids": list(probe.ids)}),
            "reload_rows": len(values), "reload_max_abs_delta": delta,
            "cuda_peak_allocated_mib": torch.cuda.max_memory_allocated(device)/2**20 if device.type == "cuda" else None}


def select(reference, results, tasks):
    if any(t["id"] not in results for t in tasks):
        raise ValueError("all five registered controls must complete before selection")
    items = [("g3", reference)] + [(t["id"], results[t["id"]]) for t in tasks if t["eligible"]]
    return min(enumerate(items), key=lambda p: (-p[1][1]["metrics"]["ap"],
                                              p[1][1]["fit_seconds"], p[0]))[1][0]
