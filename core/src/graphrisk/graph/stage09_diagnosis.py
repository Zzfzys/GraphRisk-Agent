"""Preregistered, validation-only GraphSAGE diagnosis followed by one sealed review."""
from __future__ import annotations
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
import gc
import json
import random
import threading
import time
import joblib
import numpy as np
import pandas as pd
import torch
from torch import nn
from graphrisk.agent.stage09_preflight import build_freeze, check_freeze, check_resources, load_contract
from graphrisk.demo.stage09_evaluation import (digest, read_json, write_json, resources, Monitor, exclusive)
from graphrisk.graph.stage09_packed import (PackedGraphs, build_cache, verify_cache, load_manifest_inputs)
from graphrisk.graph.stage09_budget import GraphBudgetLedger
from graphrisk.graph.stage09_monitor import DiagnosisMonitor
from graphrisk.graph.stage09_baselines import verify_registration, array_hash
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.train_graphsage import _seed_everything, _sha256, _load_labels, _verify_label_provenance
from graphrisk.graph.config import load_graph_config
from graphrisk.training.config import load_training_config
from graphrisk.training.metrics import evaluate_binary_classifier
from graphrisk.models.tabular import FEATURE_COLUMNS

ROUTE3 = Path("reports/generated/stage09/route3/20261007-baselines-v1")
VARIANT_VERSION = "temporal-type-rewire-v1"
DEFAULT_DIR = "20261007-diagnosis-v1"


def registered_runs(contract):
    base = {"learning_rate": .001, "dropout": .2, "hidden_channels": 64,
        "seed": contract.graph.baseline_seed, "cap": 5, "variant": "full"}
    runs = [{"name": "full-base", **base, "eligible": True}]
    for variant in ("no_edges", "rewired", "no_history_numeric"):
        runs.append({"name": variant.replace("_", "-"), **base, "variant": variant, "eligible": False})
    runs.append({"name": "larger-history", **base, "cap": 10, "eligible": True})
    for i, candidate in enumerate(contract.graph.candidates):
        params = candidate.model_dump() if hasattr(candidate, "model_dump") else dict(candidate)
        if i == 0 and all(base[k] == v for k, v in params.items()):
            continue  # Same exact run reused, not counted twice.
        runs.append({"name": f"candidate-{i}", **base, **params, "eligible": True})
    return runs


def scores_for(model, dataset, batch_size, device, guard=lambda: None):
    model.eval()
    values = []
    with torch.no_grad():
        for start in range(0, len(dataset.ids), batch_size):
            guard()
            batch = dataset.batch(range(start, min(start + batch_size, len(dataset.ids))))
            values.append(torch.sigmoid(model(batch.to(device))).cpu().numpy())
    return np.concatenate(values)


def fit_gnn(train, validation, train_labels, validation_labels, spec, plan, directory, guard=lambda: None):
    _seed_everything(spec["seed"])
    torch.set_num_threads(plan["cpu_threads"])
    device = torch.device(plan["device"])
    model = GraphSAGERiskModel(train.dimension, spec["hidden_channels"], spec["dropout"]).to(device)
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=spec["learning_rate"])
    pos_weight = (len(train_labels) - train_labels.sum()) / train_labels.sum()
    if not np.isfinite(pos_weight) or pos_weight <= 0 or len(np.unique(validation_labels)) != 2:
        raise ValueError("training and validation require both classes")
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(float(pos_weight), device=device))
    best_ap, best_epoch, best_state, stale, history = -1., 0, None, 0, []
    directory.mkdir(parents=True, exist_ok=True)
    for epoch in range(1, plan["max_epochs"] + 1):
        guard()
        model.train()
        positions = list(range(len(train.ids)))
        random.Random(spec["seed"] + epoch).shuffle(positions)
        total = 0.
        for start in range(0, len(positions), plan["batch_size"]):
            guard()
            chosen = positions[start:start + plan["batch_size"]]
            batch = train.batch(chosen).to(device)
            labels = torch.tensor(train_labels[chosen], dtype=torch.float32, device=device)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(batch), labels)
            if not torch.isfinite(loss):
                raise ValueError("nonfinite graph training loss")
            loss.backward()
            optimizer.step()
            total += loss.detach().cpu().item() * len(chosen)
        predicted = scores_for(model, validation, plan["batch_size"], device, guard)
        metric = evaluate_binary_classifier(validation_labels, predicted, decision_threshold=.5)
        entry = {"epoch": epoch, "train_loss": total / len(train.ids),
            "validation_pr_auc": metric["pr_auc"], "validation_roc_auc": metric["roc_auc"]}
        history.append(entry)
        write_json(directory / "training-history.json", history)
        print(f"{spec['name']} epoch={epoch} loss={entry['train_loss']:.5f} AP={metric['pr_auc']:.6f} ROC={metric['roc_auc']:.6f}", flush=True)
        if metric["pr_auc"] > best_ap:
            best_ap, best_epoch, stale = metric["pr_auc"], epoch, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            torch.save(best_state, directory / "best-progress.pt")
        else:
            stale += 1
        if stale >= plan["patience"]:
            break
    guard()
    model.load_state_dict(best_state)
    predicted = scores_for(model, validation, plan["batch_size"], device, guard)
    metric = evaluate_binary_classifier(validation_labels, predicted, decision_threshold=.5)
    torch.save(best_state, directory / "model.pt")
    np.save(directory / "validation-scores.npy", predicted)
    return {"validation_metrics": metric, "best_epoch": best_epoch, "stopped_after_epoch": len(history),
        "positive_weight": float(pos_weight), "parameter_count": sum(p.numel() for p in model.parameters()),
        "cuda_peak_allocated_mib": torch.cuda.max_memory_allocated(device)/2**20 if device.type == "cuda" else None}


def prepare(root, directory, reuse_cache=None):
    if directory.exists():
        raise FileExistsError("diagnosis already registered; verify/resume without overwriting")
    if not directory.resolve().is_relative_to(root / "reports/generated/stage09/route4"):
        raise ValueError("diagnosis reports must remain inside Stage09 route4")
    contract = load_contract(root / "configs/stage09_experiment.json")
    route3 = read_json(root / ROUTE3 / "acceptance.json")
    before = read_json(root / ROUTE3 / "inputs-after-check.json")
    if route3["engineering_status"] != "done" or check_freeze(root, before)["status"] != "passed":
        raise ValueError("route3 input/acceptance gate failed")
    binding = verify_registration(root, root / ROUTE3)
    plan = {"schema_version": "stage09-gnn-diagnosis-v1", "device": "cuda" if torch.cuda.is_available() else "cpu", "cpu_threads": 4,
        "torch_version": torch.__version__,
        "max_epochs": contract.graph.max_epochs, "patience": contract.graph.patience, "batch_size": 256,
        "rewiring_seed": contract.selection_seed, "variant_version": VARIANT_VERSION,
        "runs": registered_runs(contract), "confirmation_seeds": list(contract.graph.confirmation_seeds[1:]),
        "selection": "highest validation AP, stable preregistration order for ties; eligible variants only",
        "promotion_rule": "3-seed mean AP >= strongest business reference AP + 0.005 and every seed exceeds reference",
        "promotion_min_ap": .005, "blind_test": False, "test_before_decision_allowed": False,
        "source_binding": binding["content_sha256"], "prior_input_sha256": before["content_sha256"],
        "source_manifest_path": "artifacts/stage-04/ieee-cis/graph-manifest.json",
        "larger_manifest_path": binding["larger_manifest"]}
    if len(plan["runs"]) + 2 > contract.graph.max_new_gnn_runs:
        raise ValueError("preregistered training count exceeds budget")
    cache_path = (reuse_cache.resolve() if reuse_cache else
        root / "artifacts/stage-09/route4" / directory.name / "packed")
    if not cache_path.is_relative_to(root / "artifacts/stage-09/route4"):
        raise ValueError("cache must remain inside Stage09 route4")
    plan["cache_path"] = cache_path.relative_to(root).as_posix()
    plan["reused_cache_sha256"] = None
    if reuse_cache:
        cached = verify_cache(cache_path)
        if cached["source_manifest_sha256"] != binding["parent_graph_sha256"] or cached["rewiring_seed"] != plan["rewiring_seed"]:
            raise ValueError("reused cache provenance mismatch")
        plan["reused_cache_sha256"] = cached["content_sha256"]
    plan["content_sha256"] = digest(plan)
    directory.mkdir(parents=True)
    write_json(directory / "registration.json", plan)
    write_json(directory / "inputs.json", build_freeze(root, root / "configs/stage09_experiment.json"))
    write_json(directory / "initial-resources.json", resources(True))
    print(json.dumps({"registered": True, "planned_unique_runs": len(plan["runs"]) + 2,
        "root_controls_retrained": False, "directory": str(directory)}, ensure_ascii=False), flush=True)


def check_registration(root, directory):
    plan = read_json(directory / "registration.json")
    if digest({k: v for k, v in plan.items() if k != "content_sha256"}) != plan["content_sha256"]:
        raise ValueError("diagnosis registration changed")
    check = check_freeze(root, read_json(directory / "inputs.json"))
    if check["status"] != "passed":
        raise ValueError("registered diagnosis code or inputs drifted: " + str(check["violations"]))
    if verify_registration(root, root / ROUTE3)["content_sha256"] != plan["source_binding"]:
        raise ValueError("root controls changed")
    return plan


def open_data(root, directory, plan, cache_manifest, spec, split):
    cache = root / plan["cache_path"]
    return PackedGraphs(cache, cache_manifest, spec["cap"], split, spec["variant"])


def summarize_groups(labels, values):
    masks = {"early_half": np.arange(len(labels)) < len(labels) // 2,
             "late_half": np.arange(len(labels)) >= len(labels) // 2}
    return {name: {"targets": int(mask.sum()), "positives": int(labels[mask].sum()),
        "metrics": evaluate_binary_classifier(labels[mask], values[mask], decision_threshold=.5)}
        for name, mask in masks.items()}


def pick_winner(results, runs):
    eligible = [run for run in runs if run["eligible"] and run["name"] in results]
    if not eligible:
        raise ValueError("no completed eligible validation candidate")
    return max(eligible, key=lambda run: results[run["name"]]["validation_metrics"]["pr_auc"])


def evaluate_decision(seed_results, reference, minimum):
    values = [r["validation_metrics"]["pr_auc"] for r in seed_results]
    mean = float(np.mean(values))
    promote = len(values) == 3 and mean >= reference + minimum and min(values) > reference
    return {"promote_gnn": promote, "seed_validation_ap": values, "mean_validation_ap": mean,
        "std_validation_ap_population": float(np.std(values)), "minimum_validation_ap": min(values),
        "business_reference_ap": reference, "required_mean_gain": minimum,
        "reason": "validation gain stable over preregistered seeds" if promote else "gain insufficient or not stable; retain business XGBoost default"}


def run(root, directory):
    plan = check_registration(root, directory)
    contract = load_contract(root / "configs/stage09_experiment.json")
    artifact = root / "artifacts/stage-09/route4" / directory.name
    artifact.mkdir(parents=True, exist_ok=True)
    ledger = GraphBudgetLedger(root / "reports/generated/stage09/gnn-budget-ledger.json", contract.graph)
    monitor = DiagnosisMonitor(contract.resources, directory / "resources.json")
    initial = monitor.sample(True)
    if check_resources(contract.resources, initial)["status"] != "passed":
        raise ValueError("initial resources below frozen floor; no GNN attempt reserved")
    thread = threading.Thread(target=monitor.loop, daemon=True)
    thread.start()
    try:
        cache_path = root / plan["cache_path"]
        if not (cache_path / "manifest.json").exists():
            attempt = ledger.reserve("work", "packed-graph-build:" + plan["content_sha256"][:12], plan["content_sha256"])
            def cache_guard():
                monitor.require()
                attempt.guard()
            try:
                manifest, encoder, ids = load_manifest_inputs(root, root / plan["source_manifest_path"])
                # The source is label-free; test graph construction itself is not label access.
                cache_manifest = build_cache(root, cache_path, root / plan["source_manifest_path"], ids,
                    (5, 10), plan["rewiring_seed"], cache_guard)
                attempt.finish("completed")
            except Exception:
                attempt.finish("failed")
                raise
        cache_manifest = verify_cache(cache_path)
        if plan["reused_cache_sha256"] and cache_manifest["content_sha256"] != plan["reused_cache_sha256"]:
            raise ValueError("reused cache changed")
        matrix = np.load(root / ROUTE3 / "root-matrices.npz", allow_pickle=False)
        labels = {"train": matrix["train_y"], "validation": matrix["validation_y"]}
        binding = read_json(root / ROUTE3 / "registration.json")
        for split in ("train", "validation"):
            if array_hash(labels[split]) != binding["bindings"][split]["labels_sha256"]:
                raise ValueError("frozen diagnosis labels changed")
            for cap in (5, 10):
                data = open_data(root, directory, plan, cache_manifest,
                    {"cap": cap, "variant": "full"}, split)
                packed_roots = np.asarray(data.x[data.no[:-1]])
                if array_hash(packed_roots) != binding["bindings"][split]["features_sha256"]:
                    raise ValueError("packed root features differ from root controls")
                del data, packed_roots
        if not (directory / "checkpoint-replay.json").exists():
            attempt = ledger.reserve("work", "frozen-checkpoint-validation-replay:" + plan["content_sha256"][:12], plan["content_sha256"])
            def replay_guard():
                monitor.require()
                attempt.guard()
            try:
                torch.set_num_threads(plan["cpu_threads"])
                old = read_json(root / "artifacts/stage-04/ieee-cis/models/graphsage-v1/metadata.json")
                model = GraphSAGERiskModel(len(cache_manifest["feature_names"]), old["hidden_channels"], old["dropout"])
                model.load_state_dict(torch.load(root / "artifacts/stage-04/ieee-cis/models/graphsage-v1/model.pt",
                    weights_only=True, map_location="cpu"), strict=True)
                valid = open_data(root, directory, plan, cache_manifest, {"cap": 5, "variant": "full"}, "validation")
                values = scores_for(model, valid, plan["batch_size"], torch.device("cpu"), replay_guard)
                old_values = pd.read_parquet(root / ROUTE3 / "validation-predictions.parquet").graphsage_frozen.to_numpy()
                delta = float(np.max(np.abs(values - old_values)))
                if delta > 1e-6:
                    raise ValueError("full frozen checkpoint replay exceeds score tolerance")
                result = {"targets": len(values), "maximum_score_difference": delta,
                    "validation_metrics": evaluate_binary_classifier(labels["validation"], values, decision_threshold=.5),
                    "test_evaluated": False, "model_sha256": old["model_sha256"], "packed_manifest_sha256": cache_manifest["content_sha256"]}
                np.save(directory / "checkpoint-replay-scores.npy", values)
                write_json(directory / "checkpoint-replay.json", result)
                attempt.finish("completed")
                del model, valid
            except Exception:
                attempt.finish("failed")
                raise
        results = {}
        def train_one(spec):
            folder = artifact / "runs" / spec["name"]
            result_path = folder / "metadata.json"
            if result_path.exists():
                saved = read_json(result_path)
                if saved["spec"] != spec or saved["registration_sha256"] != plan["content_sha256"]:
                    raise ValueError("completed candidate registration drift")
                if _sha256(folder / "model.pt") != saved["model_sha256"] or _sha256(folder / "validation-scores.npy") != saved["prediction_sha256"]:
                    raise ValueError("candidate output changed")
                return saved
            monitor.sample(True)
            monitor.require()
            attempt = ledger.reserve("runs", spec["name"], plan["content_sha256"])
            def guard():
                monitor.require()
                attempt.guard()
            try:
                tick = time.perf_counter()
                train = open_data(root, directory, plan, cache_manifest, spec, "train")
                valid = open_data(root, directory, plan, cache_manifest, spec, "validation")
                saved = fit_gnn(train, valid, labels["train"], labels["validation"], spec, plan, folder, guard)
                values = np.load(folder / "validation-scores.npy", allow_pickle=False)
                saved.update(spec=spec, registration_sha256=plan["content_sha256"],
                    packed_manifest_sha256=cache_manifest["content_sha256"],
                    graph_manifest_sha256=(binding["parent_graph_sha256"] if spec["cap"] == 5
                        else binding["larger_graph_sha256"]) if spec["variant"] == "full"
                        else digest({"parent": binding["parent_graph_sha256"], "variant": spec["variant"], "cache": cache_manifest["content_sha256"]}),
                    model_name="graphsage", model_version="graphsage-stage09-" + spec["name"],
                    feature_names=cache_manifest["feature_names"], input_channels=len(cache_manifest["feature_names"]),
                    hidden_channels=spec["hidden_channels"], dropout=spec["dropout"], dataset_source="ieee_cis",
                    model_sha256=_sha256(folder / "model.pt"), prediction_sha256=_sha256(folder / "validation-scores.npy"),
                    history_sha256=_sha256(folder / "training-history.json"),
                    groups=summarize_groups(labels["validation"], values),
                    training_seconds=time.perf_counter() - tick, device=plan["device"], test_evaluated=False,
                    random_seed=spec["seed"], learning_rate=spec["learning_rate"], batch_size=plan["batch_size"],
                    max_epochs=plan["max_epochs"], early_stopping_patience=plan["patience"],
                    selected_by="validation_pr_auc", test_metrics=None,
                    graph_manifest_path=str(root/plan["source_manifest_path"]) if spec["cap"]==5
                        else str(root/plan["larger_manifest_path"]))
                guard()
                write_json(result_path, saved)
                attempt.finish("completed")
                del train, valid
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                return saved
            except Exception as error:
                attempt.finish("failed")
                write_json(directory / (spec["name"] + "-failure.json"), {"type": type(error).__name__,
                    "message": str(error), "spec": spec, "test_evaluated": False})
                raise
        for spec in plan["runs"]:
            results[spec["name"]] = train_one(spec)
            write_json(directory / "completed-runs.json", results)
        winner = pick_winner(results, plan["runs"])
        confirmations = []
        for seed in plan["confirmation_seeds"]:
            spec = {**winner, "name": f"confirm-{seed}", "seed": seed}
            saved = train_one(spec)
            results[spec["name"]] = saved
            confirmations.append(spec["name"])
            write_json(directory / "completed-runs.json", results)
        baseline = read_json(root / ROUTE3 / "results.json")["validation_metrics"]
        reference = max(baseline["stage04_business_50k"]["pr_auc"], baseline["stage02_business_full_data"]["pr_auc"])
        decision = evaluate_decision([results[name] for name in [winner["name"], *confirmations]],
                                    reference, plan["promotion_min_ap"])
        decision.update(winner=winner, confirmation_names=confirmations,
            registration_sha256=plan["content_sha256"], selected_by="validation_only",
            default_recommendation="GNN candidate" if decision["promote_gnn"] else "existing Stage02 XGBoost champion + graph evidence + experimental GNN",
            service_changed=False, test_seen_before_selection=False, new_training_runs=len(ledger.read()["runs"]))
        decision["content_sha256"] = digest(decision)
        if (directory / "decision.json").exists() and read_json(directory / "decision.json") != decision:
            raise ValueError("decision already frozen; preserve it")
        monitor.require()
        write_json(directory / "decision.json", decision)
        print(json.dumps({"diagnosis_completed": True, "decision": decision}, ensure_ascii=False), flush=True)
    except Exception as error:
        write_json(directory / "interruption.json", {"at": datetime.now(UTC).isoformat(),
            "type": type(error).__name__, "reason": str(error), "engineering_status": "incomplete",
            "ledger": ledger.read(), "test_evaluated": False})
        raise
    finally:
        monitor.stop.set()
        thread.join(timeout=10)
        monitor.sample(True)


def final_review(root, directory):
    """Sealed, bounded review of only the fixed historically observed 10k IDs."""
    plan = check_registration(root, directory)
    contract = load_contract(root / "configs/stage09_experiment.json")
    decision = read_json(directory / "decision.json")
    if digest({k:v for k,v in decision.items() if k!="content_sha256"}) != decision["content_sha256"]:
        raise ValueError("validation decision hash changed")
    if (directory / "fixed-test-review.json").exists():
        raise FileExistsError("sealed review already complete; read results without new label access")
    artifact = root / "artifacts/stage-09/route4" / directory.name
    cache_manifest = verify_cache(root / plan["cache_path"])
    config = load_graph_config(root / "configs/graph_ieee_cis.yaml")
    training = load_training_config(config.resolve_path(config.paths.training_config))
    ledger = GraphBudgetLedger(root / "reports/generated/stage09/gnn-budget-ledger.json", contract.graph)
    monitor = DiagnosisMonitor(contract.resources, directory / "final-review-resources.json")
    observed = monitor.sample(True)
    if check_resources(contract.resources, observed)["status"] != "passed":
        raise ValueError("final review resource gate failed before label read")
    thread = threading.Thread(target=monitor.loop, daemon=True)
    thread.start()
    started = time.perf_counter()
    def guard():
        monitor.require()
        if time.perf_counter() - started >= contract.graph.max_run_seconds:
            raise TimeoutError("fixed review wall-clock deadline reached")
    sealed = False
    try:
        ids = tuple(cache_manifest["target_ids"]["test"])
        for name in [decision["winner"]["name"], *decision["confirmation_names"]]:
            item = read_json(artifact / "runs" / name / "metadata.json")
            if _sha256(artifact / "runs" / name / "model.pt") != item["model_sha256"]:
                raise ValueError("selected checkpoint changed before label access")
        ledger.seal_test(decision["content_sha256"])
        sealed = True
        test_path = training.resolve_path(training.paths.test_features)
        from graphrisk.config import load_config
        import duckdb
        data_config = load_config(config.resolve_path(config.paths.data_config))
        guard()
        # Restrict semantic label/provenance access to the fixed cohort, never the full test period.
        with duckdb.connect(":memory:") as connection:
            rows = connection.execute("""
                SELECT s.transaction_id,s.is_fraud,c.is_fraud,f.is_fraud
                FROM (SELECT transaction_id,transaction_time,is_fraud FROM read_parquet(?)
                      WHERE transaction_id IN (SELECT unnest(?))) s
                LEFT JOIN read_parquet(?) c USING(transaction_id)
                LEFT JOIN read_parquet(?) f USING(transaction_id)
                ORDER BY s.transaction_time,s.transaction_id
            """, [str(test_path),list(ids),str(data_config.resolve_path(data_config.paths.interim_transactions)),
                str(data_config.resolve_path(data_config.paths.interim_features))]).fetchall()
        if tuple(r[0] for r in rows) != ids or any(r[1] not in (0,1) or r[1]!=r[2] or r[1]!=r[3] for r in rows):
            raise ValueError("fixed test label provenance/IDs invalid")
        labels = np.asarray([r[1] for r in rows],dtype=np.float32)
        torch.set_num_threads(plan["cpu_threads"])
        values, metrics = {}, {}
        for name in [decision["winner"]["name"], *decision["confirmation_names"]]:
            guard()
            saved = read_json(artifact / "runs" / name / "metadata.json")
            spec = saved["spec"]
            model = GraphSAGERiskModel(len(cache_manifest["feature_names"]), spec["hidden_channels"], spec["dropout"])
            weights = artifact / "runs" / name / "model.pt"
            model.load_state_dict(torch.load(weights,weights_only=True,map_location="cpu"),strict=True)
            packed = open_data(root,directory,plan,cache_manifest,spec,"test")
            values[name] = scores_for(model,packed,plan["batch_size"],torch.device("cpu"),guard)
            metrics[name] = evaluate_binary_classifier(labels,values[name],decision_threshold=.5)
            del model,packed
        legacy_path = root / "artifacts/stage-04/ieee-cis/comparisons/stage05-route8/test-predictions.parquet"
        legacy = pd.read_parquet(legacy_path,columns=["transaction_id","graphsage_score","xgboost_score"])
        if tuple(legacy.transaction_id)!=ids:
            raise ValueError("historical test prediction IDs disagree")
        values["old_graphsage"] = legacy.graphsage_score.to_numpy()
        values["business_xgboost_50k"] = legacy.xgboost_score.to_numpy()
        guard()
        with duckdb.connect(":memory:") as connection:
            frame = connection.execute("SELECT transaction_id, " + ", ".join(FEATURE_COLUMNS) +
                " FROM read_parquet(?) WHERE transaction_id IN (SELECT unnest(?)) ORDER BY transaction_time,transaction_id",
                [str(test_path),list(ids)]).fetchdf()
        if tuple(frame.transaction_id)!=ids:
            raise ValueError("business fixed test feature IDs disagree")
        champion = joblib.load(training.resolve_path(training.paths.output_dir) / "champion/model.joblib")
        values["stage02_champion"] = champion.predict_scores(frame)
        guard()
        for name in ("old_graphsage","business_xgboost_50k","stage02_champion"):
            metrics[name] = evaluate_binary_classifier(labels,values[name],decision_threshold=.5)
        pd.DataFrame({"transaction_id":ids,"is_fraud":labels.astype(int),**values}).to_parquet(
            directory / "fixed-test-predictions.parquet",index=False)
        result = {"decision_sha256":decision["content_sha256"],"status":"historically_observed_not_blind",
            "test_targets":len(ids),"positives":int(labels.sum()),"metrics":metrics,
            "new_model_mean_ap":float(np.mean([metrics[n]["pr_auc"] for n in
                [decision["winner"]["name"],*decision["confirmation_names"]]])),
            "selection_changed_after_test":False,"test_label_read_batches":1,
            "review_seconds":time.perf_counter()-started}
        guard()
        write_json(directory / "fixed-test-review.json",result)
        ledger.finish_test("completed",time.perf_counter()-started)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    except Exception as error:
        if sealed:
            ledger.finish_test("failed_no_refund",time.perf_counter()-started)
        write_json(directory / "fixed-review-failure.json",{"type":type(error).__name__,"reason":str(error),
            "sealed":sealed,"selection_changed":False})
        raise
    finally:
        monitor.stop.set()
        thread.join(timeout=10)
        monitor.sample(True)

