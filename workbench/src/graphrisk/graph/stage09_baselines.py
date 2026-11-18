"""Validation-only, common-root controls and temporal sampling measurements.

No test labels, hosted APIs, graph training, or baseline artifact mutation.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
import ctypes
import hashlib
import json
import threading
import time

import duckdb
import joblib
import numpy as np
import pandas as pd
import torch
from torch import nn
from xgboost import XGBClassifier
from xgboost.callback import TrainingCallback

from graphrisk.agent.stage09_preflight import build_freeze, check_freeze, check_resources, load_contract
from graphrisk.demo.stage09_evaluation import (Monitor, continuous_ok, digest, exclusive,
    read_json, resources, write_json)
from graphrisk.graph.build_artifacts import load_graph_artifacts, LoadedGraphArtifacts
from graphrisk.graph.config import load_graph_config
from graphrisk.graph.schema import GraphFeatureEncoder, GraphNode
from graphrisk.graph.train_graphsage import _load_labels, _seed_everything, _sha256, _verify_label_provenance
from graphrisk.training.config import load_training_config
from graphrisk.training.metrics import evaluate_binary_classifier
from graphrisk.models.tabular import FEATURE_COLUMNS

SPLITS = ("train", "validation")


def array_hash(array):
    value = np.ascontiguousarray(array)
    return hashlib.sha256(str(value.dtype).encode() + str(value.shape).encode() + value.tobytes()).hexdigest()


def root_matrix(store, ids):
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("root IDs must be nonempty and unique")
    matrix = np.asarray([store.encoder.transform_node(
        GraphNode("transaction", key, store.index.by_id[key])) for key in ids], dtype=np.float32)
    if not np.isfinite(matrix).all():
        raise ValueError("nonfinite root features")
    return matrix


def verify_encoder(store):
    expected = GraphFeatureEncoder.fit(
        (store.index.by_id[key] for key in store.target_ids["train"]),
        train_transaction_ids=store.target_ids["train"])
    if asdict(expected) != asdict(store.encoder):
        raise ValueError("encoder differs from train-only refit")
    return {"train_only_refit_equal": True, "feature_dimension": len(store.encoder.feature_names),
            "encoder_sha256": digest(asdict(expected))}


def process_rss_mib():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
            "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
            "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    current_process = ctypes.windll.kernel32.GetCurrentProcess
    current_process.restype = ctypes.c_void_p
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    get_memory.restype = ctypes.c_int
    if not get_memory(current_process(), ctypes.byref(counters), counters.cb):
        raise OSError("process working set measurement failed")
    return counters.WorkingSetSize / 2**20


def describe(values):
    array = np.asarray(values, dtype=float)
    return {"min": float(array.min()), "median": float(np.median(array)),
            "p95": float(np.percentile(array, 95)), "max": float(array.max()),
            "mean": float(array.mean())}


def degree_summary(histogram):
    # Repeated degree values kept as a bounded integer histogram, not millions of Python objects.
    count = sum(histogram.values())
    if not count:
        return {}
    ranks = (int((count - 1) * .5), int((count - 1) * .95))
    selected = []
    for rank in ranks:
        cumulative = 0
        for degree, number in sorted(histogram.items()):
            cumulative += number
            if cumulative > rank:
                selected.append(degree)
                break
    return {"nodes": count, "min": min(histogram), "median_lower_rank": selected[0],
            "p95_lower_rank": selected[1], "max": max(histogram),
            "mean": sum(k * v for k, v in histogram.items()) / count}


def sampling_rows(store, ids, cap, validation_start, guard=lambda: None):
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise ValueError("history cap must be a positive integer")
    rows, degree_counts = [], Counter()
    for position, key in enumerate(ids):
        if position % 1000 == 0:
            guard()
        target = store.index.by_id[key]
        graph = store.index.build(key, max_history_per_entity=cap)
        history = [store.index.by_id[item] for item in graph.selected_history_ids]
        if any(item.transaction_time >= target.transaction_time for item in history):
            raise ValueError("future or simultaneous history entered ego graph")
        entities = target.valid_entities()
        counts = [bisect_left(store.index.postings[entity], (target.transaction_time, ""))
                  for entity in entities]
        simultaneous = sum(
            bisect_right(store.index.postings[entity], (target.transaction_time, chr(0x10ffff)))
            - bisect_left(store.index.postings[entity], (target.transaction_time, ""))
            for entity in entities)
        degrees = np.zeros(len(graph.nodes), dtype=int)
        for a, b in graph.edges:
            degrees[a] += 1
            degrees[b] += 1
        degree_counts.update(int(v) for v in degrees)
        rows.append({"transaction_id": key, "history": len(history), "nodes": len(graph.nodes),
            "directed_pyg_edges": 2 * len(graph.edges), "root_degree": len(entities),
            "available_entity_incidences": sum(counts),
            "selected_entity_incidences": sum(min(cap, n) for n in counts),
            "truncated_entities": sum(n > cap for n in counts),
            "cold_start_entities": sum(n == 0 for n in counts),
            "missing_root_entities": 4 - len(entities), "simultaneous_incidences_excluded": simultaneous,
            "earlier_validation_context": sum(item.transaction_time >= validation_start for item in history),
            "missing_root_numeric": sum(v is None for v in target.numeric)})
    return pd.DataFrame(rows), degree_summary(degree_counts)


def summarize_sampling(frame):
    return {"targets": len(frame),
        **{name: describe(frame[name]) for name in (
            "history", "nodes", "directed_pyg_edges", "root_degree", "available_entity_incidences",
            "selected_entity_incidences", "missing_root_entities", "missing_root_numeric")},
        "zero_history_fraction": float((frame.history == 0).mean()),
        "zero_edges_fraction": float((frame.directed_pyg_edges == 0).mean()),
        "cold_start_target_fraction": float((frame.cold_start_entities > 0).mean()),
        "truncated_target_fraction": float((frame.truncated_entities > 0).mean()),
        "earlier_validation_context_targets": int((frame.earlier_validation_context > 0).sum()),
        "earlier_validation_context_occurrences": int(frame.earlier_validation_context.sum()),
        "simultaneous_entity_incidences_excluded": int(frame.simultaneous_incidences_excluded.sum()),
        "strict_time_violations": 0}


def create_larger_manifest(root, store, destination, fanout):
    destination = destination.resolve()
    if not destination.is_relative_to(root / "artifacts/stage-09/route3"):
        raise ValueError("larger graph must remain inside Stage09 route3 artifacts")
    if destination.exists():
        raise FileExistsError("preserve graph candidates; choose a fresh artifact directory")
    if tuple(fanout) != (10, 10):
        raise ValueError("unregistered larger sampling candidate")
    destination.mkdir(parents=True)
    manifest = deepcopy(store.manifest)
    parent = manifest.pop("content_sha256")
    manifest["fanout"] = list(fanout)
    manifest["parent_manifest_sha256"] = parent
    manifest["context_policy"] = "strict earlier unlabeled transactions; earlier validation/test events allowed at inference"
    manifest["sampling_rule"] = "all four valid root entity types; fanout[0]>=4 is a schema guard, not a sampled layer; per-entity strict earlier cap fanout[1]"
    manifest["ego_statistics"] = {}
    manifest["artifacts"].pop("ego_sample", None)
    manifest["statistics_status"] = "train/validation measured separately; test statistics not evaluated"
    for name in ("encoder", "train_ids", "validation_ids", "test_ids"):
        original = root / manifest["artifacts"][name]["path"]
        target = destination / original.name
        target.write_bytes(original.read_bytes())
        manifest["artifacts"][name] = {"path": target.relative_to(root).as_posix(), "sha256": _sha256(target)}
    # Large immutable label-free Parquet files are shared by content hash, not rebuilt or overwritten.
    manifest["content_sha256"] = digest(manifest)
    path = destination / "graph-manifest.json"
    write_json(path, manifest)
    return path


def load_plan(root):
    plan = read_json(root / "configs/stage09_root_baselines.json")
    if plan["splits"] != list(SPLITS) or plan["test_labels_allowed"] is not False:
        raise ValueError("only train/validation label access is allowed")
    if plan["seed"] != 20260921 or plan["per_model_attempt_cap"] != 1:
        raise ValueError("root baseline preregistration changed")
    if not 0 < plan["max_model_seconds"] <= 3600 or not 0 < plan["cpu_threads"] <= 4:
        raise ValueError("root training resource budget exceeds preregistered ceiling")
    if plan["mlp"]["max_epochs"] > 50 or plan["mlp"]["patience"] > 10:
        raise ValueError("root MLP training budget exceeds Stage09 contract")
    return plan


def prepare(root, directory):
    directory = directory.resolve()
    if not directory.is_relative_to(root / "reports/generated/stage09/route3"):
        raise ValueError("route3 output must remain under ignored project reports")
    if directory.exists():
        raise FileExistsError("preserve experiment evidence; choose a new directory")
    contract = load_contract(root / "configs/stage09_experiment.json")
    prior = read_json(root / "reports/generated/stage09/route2/20261007-audit-v1/inputs-after-audit.json")
    previous_check = check_freeze(root, prior)
    if previous_check["status"] != "passed":
        raise ValueError("route2 frozen inputs changed: " + str(previous_check["violations"]))
    plan = load_plan(root)
    directory.mkdir(parents=True)
    observed = resources(True)
    write_json(directory / "start-resources.json", observed)
    if check_resources(contract.resources, observed)["status"] != "passed":
        raise ValueError("resource start gate failed")
    config = load_graph_config(root / "configs/graph_ieee_cis.yaml")
    started = time.perf_counter()
    store = load_graph_artifacts(config.resolve_path(config.paths.graph_manifest), expected_dataset_source="ieee_cis")
    encoder_check = verify_encoder(store)
    train_ids, validation_ids = (store.target_ids[s] for s in SPLITS)
    train_times = [store.index.by_id[k].transaction_time for k in train_ids]
    valid_times = [store.index.by_id[k].transaction_time for k in validation_ids]
    if set(train_ids) & set(validation_ids) or max(train_times) >= min(valid_times):
        raise ValueError("target split overlap or non-strict train/validation boundary")
    baseline = load_training_config(config.resolve_path(config.paths.training_config))
    paths = {s: baseline.resolve_path(getattr(baseline.paths, s + "_features")) for s in SPLITS}
    _verify_label_provenance(config, paths)
    arrays, bindings = {}, {}
    for split in SPLITS:
        ids = store.target_ids[split]
        arrays[split + "_x"] = root_matrix(store, ids)
        arrays[split + "_y"] = _load_labels(paths[split], ids, split)
        if len(np.unique(arrays[split + "_y"])) != 2:
            raise ValueError("both train and validation need two label classes")
        write_json(directory / (split + "-ids.json"), list(ids))
        bindings[split] = {"targets": len(ids), "positive": int(arrays[split + "_y"].sum()),
            "ids_sha256": digest(list(ids)), "features_sha256": array_hash(arrays[split + "_x"]),
            "labels_sha256": array_hash(arrays[split + "_y"])}
    np.savez_compressed(directory / "root-matrices.npz", **arrays)
    candidate_path = create_larger_manifest(root, store,
        root / "artifacts/stage-09/route3" / directory.name / "history-cap-10", contract.graph.larger_fanout)
    candidate = read_json(candidate_path)
    sampling, resource_samples = {}, []
    def guard():
        observation = resources()
        resource_samples.append({**observation, "process_rss_mib": process_rss_mib()})
        write_json(directory / "sampling-resources.json", resource_samples)
        if not continuous_ok(contract.resources, observation):
            raise ValueError("continuous sampling resource floor reached")
    for cap in (5, 10):
        sampling[str(cap)] = {}
        for split in SPLITS:
            tick = time.perf_counter()
            frame, degrees = sampling_rows(store, store.target_ids[split], cap, min(valid_times), guard)
            if split == "train" and frame.earlier_validation_context.sum():
                raise ValueError("validation relationship entered training graph")
            frame.to_parquet(directory / f"sampling-cap{cap}-{split}.parquet", index=False)
            middle = len(frame) // 2
            sampling[str(cap)][split] = {"all": summarize_sampling(frame),
                "early_half": summarize_sampling(frame.iloc[:middle]),
                "late_half": summarize_sampling(frame.iloc[middle:]),
                "degree_distribution": degrees, "build_measurement_seconds": time.perf_counter() - tick}
    larger_store = LoadedGraphArtifacts(candidate, store.index, store.encoder, store.target_ids)
    # Verify the actual loader, including source/output hashes and copied targets/encoder.
    reloaded = load_graph_artifacts(candidate_path, expected_dataset_source="ieee_cis")
    if reloaded.target_ids != store.target_ids or asdict(reloaded.encoder) != asdict(store.encoder):
        raise ValueError("larger graph changed frozen targets or encoder")
    del reloaded
    for split in SPLITS:
        if array_hash(root_matrix(larger_store, store.target_ids[split])) != bindings[split]["features_sha256"]:
            raise ValueError("larger sampling changed root features")
    checkpoint = root / "artifacts/stage-04/ieee-cis/models/graphsage-v1/model.pt"
    comparison = root / "artifacts/stage-04/ieee-cis/comparisons/stage05-route8"
    metadata = read_json(comparison / "metadata.json")
    predictions = comparison / metadata["outputs"]["validation_predictions"]["path"]
    if metadata["graph_model_sha256"] != _sha256(checkpoint) or (
        metadata["graph_manifest_sha256"] != store.manifest["content_sha256"]
        or metadata["outputs"]["validation_predictions"]["sha256"] != _sha256(predictions)):
        raise ValueError("frozen graph validation scores have inconsistent provenance")
    registered = {"plan": plan, "bindings": bindings, "encoder": encoder_check,
        "feature_names": list(store.encoder.feature_names),
        "parent_graph_sha256": store.manifest["content_sha256"],
        "larger_manifest": str(candidate_path.relative_to(root)),
        "larger_graph_sha256": candidate["content_sha256"],
        "validation_prediction_path": str(predictions.relative_to(root)),
        "validation_prediction_sha256": _sha256(predictions),
        "checkpoint_sha256": _sha256(checkpoint),
        "root_matrix_file_sha256": _sha256(directory / "root-matrices.npz"),
        "context_policy": candidate["context_policy"], "labels_read": list(SPLITS),
        "sampling": sampling, "prepare_seconds": time.perf_counter() - started}
    registered["content_sha256"] = digest(registered)
    write_json(directory / "registration.json", registered)
    write_json(directory / "inputs.json", build_freeze(root, root / "configs/stage09_experiment.json"))
    print(json.dumps({"prepared": True, "targets": {s: bindings[s]["targets"] for s in SPLITS},
        "labels_read": list(SPLITS), "directory": str(directory)}, ensure_ascii=False), flush=True)


class RootMLP(nn.Module):
    def __init__(self, input_dim, hidden, dropout):
        super().__init__()
        self.network = nn.Sequential(nn.Linear(input_dim, hidden), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden, hidden), nn.ReLU(), nn.Dropout(dropout), nn.Linear(hidden, 1))

    def forward(self, roots):
        return self.network(roots).reshape(-1)


class DeadlineCallback(TrainingCallback):
    def __init__(self, guard):
        self.guard = guard

    def after_iteration(self, model, epoch, evals_log):
        self.guard()
        return False


def fit_mlp(train_x, train_y, valid_x, valid_y, plan, guard=lambda: None):
    _seed_everything(plan["seed"])
    torch.set_num_threads(plan["cpu_threads"])
    spec = plan["mlp"]
    model = RootMLP(train_x.shape[1], spec["hidden_channels"], spec["dropout"])
    optimizer = torch.optim.Adam(model.parameters(), lr=spec["learning_rate"])
    positive_weight = (len(train_y) - train_y.sum()) / train_y.sum()
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(float(positive_weight)))
    x, y, vx = (torch.from_numpy(np.asarray(v, dtype=np.float32)) for v in (train_x, train_y, valid_x))
    best, best_state, best_epoch, stale, history = -1., None, 0, 0, []
    for epoch in range(1, spec["max_epochs"] + 1):
        guard()
        model.train()
        positions = np.random.default_rng(plan["seed"] + epoch).permutation(len(train_y))
        total = 0.
        for start in range(0, len(positions), spec["batch_size"]):
            guard()
            selected = positions[start:start + spec["batch_size"]]
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(x[selected]), y[selected])
            loss.backward()
            optimizer.step()
            total += loss.detach().item() * len(selected)
        model.eval()
        with torch.no_grad():
            scores = torch.sigmoid(model(vx)).numpy()
        metrics = evaluate_binary_classifier(valid_y, scores, decision_threshold=.5)
        history.append({"epoch": epoch, "train_loss": total / len(train_y), **metrics})
        if metrics["pr_auc"] > best:
            best, best_epoch, stale = metrics["pr_auc"], epoch, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            stale += 1
        print(f"root-mlp epoch={epoch} loss={history[-1]['train_loss']:.5f} validation_AP={metrics['pr_auc']:.6f}", flush=True)
        if stale >= spec["patience"]:
            break
    guard()
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        final = torch.sigmoid(model(vx)).numpy()
    return model, final, {"history": history, "best_epoch": best_epoch, "best_validation_ap": best,
        "stopped_after_epoch": len(history), "positive_weight": float(positive_weight)}


def reserve_control(ledger, name, registration):
    if name not in ("xgboost", "mlp"):
        raise ValueError("unregistered root control")
    with exclusive(ledger.with_suffix(".lock")):
        value = read_json(ledger) if ledger.exists() else {"attempts": []}
        if any(a["model"] == name for a in value["attempts"]):
            raise ValueError("root control already attempted; no repeat training budget")
        value["attempts"].append({"model": name, "registration": registration,
            "state": "reserved_before_training", "at": datetime.now(UTC).isoformat()})
        write_json(ledger, value)


def finish_control(ledger, name, state, seconds):
    with exclusive(ledger.with_suffix(".lock")):
        value = read_json(ledger)
        entry = next(a for a in value["attempts"] if a["model"] == name)
        entry.update(state=state, wall_seconds=seconds)
        write_json(ledger, value)


def verify_registration(root, directory):
    if not directory.resolve().is_relative_to(root / "reports/generated/stage09/route3"):
        raise ValueError("training evidence must remain inside route3 report root")
    value = read_json(directory / "registration.json")
    if digest({k: v for k, v in value.items() if k != "content_sha256"}) != value["content_sha256"]:
        raise ValueError("registration content changed")
    if check_freeze(root, read_json(directory / "inputs.json"))["status"] != "passed":
        raise ValueError("registered source inputs changed")
    if value["plan"] != load_plan(root):
        raise ValueError("root training plan changed after registration")
    if _sha256(directory / "root-matrices.npz") != value["root_matrix_file_sha256"]:
        raise ValueError("root matrices changed")
    for split in SPLITS:
        if digest(read_json(directory / (split + "-ids.json"))) != value["bindings"][split]["ids_sha256"]:
            raise ValueError("registered target IDs changed")
    if _sha256(root / value["validation_prediction_path"]) != value["validation_prediction_sha256"]:
        raise ValueError("frozen graph scores changed")
    candidate = read_json(root / value["larger_manifest"])
    if (candidate["content_sha256"] != value["larger_graph_sha256"] or
        digest({k: v for k, v in candidate.items() if k != "content_sha256"}) != candidate["content_sha256"]):
        raise ValueError("registered larger graph manifest changed")
    return value


def align_scores(frame, ids):
    if frame.transaction_id.duplicated().any() or set(frame.transaction_id) != set(ids):
        raise ValueError("validation prediction IDs mismatch or duplicate")
    ordered = frame.set_index("transaction_id").loc[list(ids)]
    if not np.isfinite(ordered.graphsage_score).all() or not ordered.graphsage_score.between(0, 1).all():
        raise ValueError("invalid frozen validation scores")
    return ordered


def train_controls(root, directory):
    registration = verify_registration(root, directory)
    if (directory / "results.json").exists():
        raise FileExistsError("completed controls preserved; read results instead of retraining")
    plan = registration["plan"]
    contract = load_contract(root / "configs/stage09_experiment.json")
    matrix = np.load(directory / "root-matrices.npz", allow_pickle=False)
    tx, ty, vx, vy = (matrix[k] for k in ("train_x", "train_y", "validation_x", "validation_y"))
    for split in SPLITS:
        for suffix, binding in (("x", "features_sha256"), ("y", "labels_sha256")):
            if array_hash(matrix[split + "_" + suffix]) != registration["bindings"][split][binding]:
                raise ValueError("matrix content differs from registered binding")
    artifact = root / "artifacts/stage-09/route3" / directory.name / "root-controls"
    artifact.mkdir(parents=True, exist_ok=True)
    monitor = Monitor(contract.resources, directory / "training-resources.json")
    start_resources = monitor.sample(True)
    if check_resources(contract.resources, start_resources)["status"] != "passed":
        raise ValueError("resource start gate failed before training")
    thread = threading.Thread(target=monitor.loop, daemon=True)
    thread.start()
    ledger = root / "reports/generated/stage09/root-control-ledger.json"
    metrics, scores, training = {}, {}, {}
    try:
        for name in ("xgboost", "mlp"):
            monitor.require()
            reserve_control(ledger, name, registration["content_sha256"])
            started = time.perf_counter()
            def guard():
                monitor.require()
                if time.perf_counter() - started >= plan["max_model_seconds"]:
                    raise TimeoutError("root baseline wall-clock budget exhausted")
            try:
                if name == "xgboost":
                    model = XGBClassifier(**plan["xgboost"], random_state=plan["seed"],
                        scale_pos_weight=float((len(ty) - ty.sum()) / ty.sum()),
                        callbacks=[DeadlineCallback(guard)])
                    model.fit(tx, ty)
                    guard()
                    values = model.predict_proba(vx)[:, 1]
                    model.save_model(artifact / "xgboost.json")
                    detail = {"positive_weight": float((len(ty) - ty.sum()) / ty.sum()), "boosted_rounds": model.n_estimators}
                else:
                    model, values, detail = fit_mlp(tx, ty, vx, vy, plan, guard)
                    torch.save(model.state_dict(), artifact / "mlp.pt")
                guard()
                metrics[name] = evaluate_binary_classifier(vy, values, decision_threshold=.5)
                scores[name] = values
                training[name] = {**detail, "wall_seconds": time.perf_counter() - started, "device": "cpu"}
                write_json(artifact / (name + "-metadata.json"), {"registration_sha256": registration["content_sha256"],
                    "feature_names": registration["feature_names"], "plan": plan,
                    "model_sha256": _sha256(artifact / ("xgboost.json" if name == "xgboost" else "mlp.pt")),
                    "metrics": metrics[name], **training[name], "test_evaluated": False})
                write_json(directory / (name + "-validation.json"), {"metrics": metrics[name], "training": training[name]})
                np.save(directory / (name + "-validation-scores.npy"), values)
                finish_control(ledger, name, "completed", time.perf_counter() - started)
            except Exception as error:
                finish_control(ledger, name, "failed_no_refund", time.perf_counter() - started)
                write_json(directory / (name + "-failure.json"), {"type": type(error).__name__, "message": str(error)})
                raise
        ids = read_json(directory / "validation-ids.json")
        # Column projection does not read any test or validation label from the legacy prediction file.
        legacy = align_scores(pd.read_parquet(root / registration["validation_prediction_path"],
            columns=["transaction_id", "graphsage_score", "xgboost_score"]), ids)
        scores["graphsage_frozen"] = legacy.graphsage_score.to_numpy()
        scores["stage04_business_50k"] = legacy.xgboost_score.to_numpy()
        config = load_graph_config(root / "configs/graph_ieee_cis.yaml")
        tabular = load_training_config(config.resolve_path(config.paths.training_config))
        with duckdb.connect(":memory:") as connection:
            full_frame = connection.execute("SELECT transaction_id, " + ", ".join(FEATURE_COLUMNS) +
                " FROM read_parquet(?) ORDER BY transaction_time, transaction_id LIMIT ?",
                [str(tabular.resolve_path(tabular.paths.validation_features)), len(ids)]).fetchdf()
        if tuple(full_frame.transaction_id) != tuple(ids):
            raise ValueError("business baseline feature ID ordering changed")
        champion = joblib.load(tabular.resolve_path(tabular.paths.output_dir) / "champion/model.joblib")
        guard()
        scores["stage02_business_full_data"] = champion.predict_scores(full_frame)
        guard()
        for name in ("graphsage_frozen", "stage04_business_50k", "stage02_business_full_data"):
            metrics[name] = evaluate_binary_classifier(vy, scores[name], decision_threshold=.5)
        groups = {}
        sampled = pd.read_parquet(directory / "sampling-cap5-validation.parquet").set_index("transaction_id").loc[ids]
        masks = {"early_half": np.arange(len(ids)) < len(ids) // 2,
            "late_half": np.arange(len(ids)) >= len(ids) // 2,
            "with_history": sampled.history.to_numpy() > 0, "without_history": sampled.history.to_numpy() == 0}
        for group, mask in masks.items():
            groups[group] = {"targets": int(mask.sum()), "positives": int(vy[mask].sum()),
                "metrics": {name: evaluate_binary_classifier(vy[mask], value[mask], decision_threshold=.5)
                    for name, value in scores.items()} if mask.any() and len(np.unique(vy[mask])) == 2 else {}}
        pd.DataFrame({"transaction_id": ids, "is_fraud": vy.astype(int), **scores}).to_parquet(
            directory / "validation-predictions.parquet", index=False)
        result = {"registration_sha256": registration["content_sha256"], "validation_metrics": metrics,
            "groups": groups, "training": training, "test_evaluated": False,
            "new_gnn_runs": 0, "paid_calls": 0,
            "limitations": ["root features controlled, architectures differ", "old GNN patience20 versus MLP10",
                "old GNN scores reused; full forward replay and retrained sampling/ablations belong to route4",
                "Stage02 full-data business model is not a controlled causal comparison"]}
        write_json(directory / "results.json", result)
        print(json.dumps({"completed": True, "validation_metrics": metrics, "test_evaluated": False}), flush=True)
    finally:
        monitor.stop.set()
        thread.join(timeout=10)
        monitor.sample(True)

