"""Resumable frozen confirmation; scores precede the single outer-label release."""
import gc
import json
import re
import shutil
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import xgboost as xgb

from graphrisk.agent.stage09_preflight import _content_hash, check_resources
from graphrisk.demo.stage09_evaluation import exclusive, read_json, resources, write_json
from graphrisk.features.wide_execution import FeatureMonitor, check_outputs, peak_working_set_mib
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.stage09_conditional_data import ConditionalGraphs
from graphrisk.graph.stage09_conditional_execution import check as conditional_check
from graphrisk.graph.stage09_conditional_training import fit
from graphrisk.graph.stage09_confirmation_data import prepare_fold_data, prepare_prediction_data, targets
from graphrisk.graph.stage09_confirmation_metrics import choose_alpha, evaluate_folds
from graphrisk.graph.stage09_diagnosis import scores_for
from graphrisk.graph.stage09_enhancement import EnhancementLedger, load_contract
from graphrisk.graph.stage09_enhancement_preflight import budget_binding, check_registration, verify_inventory
from graphrisk.graph.stage09_expansion_execution import idle_start
from graphrisk.graph.stage09_packed import verify_cache
from graphrisk.models.enhanced_contract import TabularSettings
from graphrisk.models.enhanced_data import batches
from graphrisk.models.enhanced_execution import binding, checked_json, open_db, sealed, training_awake
from graphrisk.models.enhanced_training import metrics, predict_xgb, quantile, train_xgb


def settings_for(source):
    value = read_json(source / "configs/stage09_confirmation.json")
    expected = {"schema_version": "stage09-5b-confirmation-v1", "gnn_candidate": "c4-core-graph",
        "xgboost_candidate": "x3", "device": "cuda", "cpu_threads": 4, "batch_size": 256,
        "max_epochs": 25, "patience": 10, "train_probe_count": 10000, "history_per_entity": 5,
        "core_clip": 10., "fusion_selection": "one_alpha_per_fold_mean_three_seed_calibration_AP_tie_larger_xgb",
        "confirmation": "nine_new_gnn_three_new_xgb_fits_no_development_search",
        "outer_release": "all_fits_and_alphas_sealed_before_single_label_batch",
        "bootstrap_iterations": 1000, "bootstrap_seed": 20261009, "bootstrap_interval": .95,
        "stability": "paired_block_AP_and_relative_recall_CI_lower_above_zero",
        "historical_test": "fold3_fixed_models_and_alpha_one_descriptive_batch_not_blind",
        "maximum_prepare_seconds": 7200, "maximum_evaluation_seconds": 7200,
        "new_generation_calls": 0, "paid_calls": 0}
    if value != expected:
        raise ValueError("confirmation appendix differs from fixed recipe")
    return value


def freeze(source, root, registration, conditional, tabular, name):
    source, root, registration, conditional, tabular = [p.resolve() for p in (source, root, registration, conditional, tabular)]
    if root.drive.upper() != "E:" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,50}", name):
        raise ValueError("safe unique E-drive confirmation name required")
    check_registration(source, root, registration)
    frozen = checked_json(registration / "freeze.json")
    settings = settings_for(source)
    contract = load_contract(source / "configs/stage09_model_enhancement.json")
    for relative in ("configs/stage09_confirmation.json", "docs/data/stage-09-confirmation.md"):
        if not any(i["root"] == "source" and i["path"] == relative for i in frozen["inventory"]):
            raise ValueError("confirmation config and learning contract must be registered")
    conditional_check(root, conditional)
    chosen = checked_json(conditional / "candidate-choice.json")
    from graphrisk.models.enhanced_execution import check as tabular_check
    tabular_check(root, tabular)
    baseline = checked_json(tabular / "baseline-choice.json")
    prior = checked_json(tabular / "plan.json")
    if chosen["selected_current_candidate"] != settings["gnn_candidate"] or baseline["candidate"] != settings["xgboost_candidate"]:
        raise ValueError("candidate differs from registered parent choice")
    if not chosen["selected_recipe"]["core"] or chosen["selected_recipe"]["variant"] != "full":
        raise ValueError("confirmation requires frozen core full-graph recipe")
    check_outputs(root, Path(prior["feature_directory"]))
    artifact, report = root / contract.artifact_root / name, root / contract.output_root / name
    if artifact.exists() or report.exists():
        raise ValueError("fresh confirmation plan required; never overwrite a frozen decision")
    _, shared = budget_binding(root, contract, artifact / "unused-fit-ledger.json")
    ledger = EnhancementLedger(Path(shared["ledger_path"]), contract, shared["origin_freeze_sha256"])
    state = ledger.read()
    if state["decision_sha256"] or state["outer_access"] or state["test_access"] or any(f["state"] == "reserved" for f in state["fits"]):
        raise ValueError("decision already frozen or unresolved attempt; no second confirmation search")
    artifact.mkdir(parents=True)
    report.mkdir(parents=True)
    for item in frozen["inventory"]:
        if item["root"] == "source":
            destination = artifact / "source-snapshot" / item["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / item["path"], destination)
    verify_inventory({"source": artifact / "source-snapshot", "execution": root}, frozen["inventory"])
    plan = sealed({"schema_version": "stage09-5b-confirmation-plan-v1", "registration_directory": str(registration),
        "source_freeze_sha256": frozen["content_sha256"], "artifact_directory": str(artifact),
        "conditional_directory": str(conditional), "conditional_choice_sha256": chosen["content_sha256"],
        "tabular_directory": str(tabular), "baseline_choice_sha256": baseline["content_sha256"],
        "side_path": prior["side_path"], "feature_directory": prior["feature_directory"],
        "feature_manifest_sha256": prior["feature_manifest_sha256"], "settings": settings,
        "graph_parameters": chosen["selected_recipe"]["parameters"],
        "xgboost_parameters": next(c.model_dump() for c in contract.xgboost_candidates if c.id == baseline["candidate"]),
        "windows": frozen["data"]["windows"], "historical_test_window": frozen["data"]["splits"]["test"],
        "seeds": contract.confirmation_seeds, "fusion_grid": contract.training_rules.fusion_alphas_xgboost_weight,
        "ledger_path": shared["ledger_path"], "ledger_origin": shared["origin_freeze_sha256"],
        "decision_kind": "algorithm_and_calibration_rule_before_confirmation_not_quality_verdict",
        "reuse_old_fits": False, "new_generation_calls": 0, "paid_calls": 0})
    write_json(report / "plan.json", plan)
    ledger.freeze_decision(plan["content_sha256"])
    print(json.dumps({"status": "decision_frozen", "directory": str(report), "decision_sha256": plan["content_sha256"],
        "gnn_fits_planned": 9, "xgboost_fits_planned": 3, "outer_access": 0}), flush=True)
    return plan


def context(root, report, active=False):
    root, report = root.resolve(), report.resolve()
    if root.drive.upper() != "E:" or not report.is_relative_to(root / "reports/generated/stage09/route5b"):
        raise ValueError("confirmation run must remain inside E output root")
    plan = checked_json(report / "plan.json")
    artifact = Path(plan["artifact_directory"]).resolve()
    if not artifact.is_relative_to(root / "artifacts/stage-09/route5b"):
        raise ValueError("confirmation artifact path escape")
    frozen = checked_json(Path(plan["registration_directory"]) / "freeze.json")
    if frozen["content_sha256"] != plan["source_freeze_sha256"]:
        raise ValueError("confirmation source binding drift")
    verify_inventory({"source": artifact / "source-snapshot", "execution": root}, frozen["inventory"])
    if active:
        check_registration(Path(__file__).resolve().parents[3], root, Path(plan["registration_directory"]))
    contract = load_contract(artifact / "source-snapshot/configs/stage09_model_enhancement.json")
    settings = settings_for(artifact / "source-snapshot")
    if plan["settings"] != settings or plan["windows"] != frozen["data"]["windows"] or plan["seeds"] != contract.confirmation_seeds or plan["fusion_grid"] != contract.training_rules.fusion_alphas_xgboost_weight:
        raise ValueError("confirmation schema/seed/window/alpha drift")
    if checked_json(Path(plan["conditional_directory"]) / "candidate-choice.json")["content_sha256"] != plan["conditional_choice_sha256"] or checked_json(Path(plan["tabular_directory"]) / "baseline-choice.json")["content_sha256"] != plan["baseline_choice_sha256"]:
        raise ValueError("parent candidate binding drift")
    _, shared = budget_binding(root, contract, Path(plan["ledger_path"]))
    if shared["ledger_path"] != plan["ledger_path"] or shared["origin_freeze_sha256"] != plan["ledger_origin"]:
        raise ValueError("confirmation shared ledger drift")
    ledger = EnhancementLedger(Path(plan["ledger_path"]), contract, plan["ledger_origin"])
    if ledger.read()["decision_sha256"] != plan["content_sha256"]:
        raise ValueError("confirmation decision is not the ledger's frozen decision")
    return plan, contract, ledger, artifact


def fold_inputs(directory):
    value = checked_json(directory / "prepared.json")
    for name, expected in value["files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or binding(path) != expected:
            raise ValueError("confirmation prepared file drift:"+name)
    manifest = verify_cache(directory / "cache")
    if manifest["content_sha256"] != value["cache_sha256"]:
        raise ValueError("confirmation cache binding drift")
    return value, manifest


def prepare(root, report):
    plan, contract, ledger, artifact = context(root, report, True)
    if ledger.read()["outer_access"] or ledger.read()["test_access"]:
        raise ValueError("cannot prepare/rebuild inputs after result release")
    feature = checked_json(Path(plan["feature_directory"]) / "manifest.json")
    if feature["content_sha256"] != plan["feature_manifest_sha256"]:
        raise ValueError("side feature manifest drift")
    fields = read_json(Path(feature["artifact_directory"]) / "field-catalog.json")
    for window in plan["windows"]:
        directory = artifact / window["name"]
        if (directory / "prepared.json").exists():
            fold_inputs(directory)
            continue
        if directory.exists():
            raise ValueError("incomplete preparation preserved; explicit audit required")
        if check_resources(contract.resources, resources(True))["status"] != "passed":
            raise ValueError("confirmation preparation resource gate; no new fit")
        directory.mkdir()
        db = open_db(root, Path(plan["side_path"]))
        db.execute("SET memory_limit='256MB'")
        monitor = FeatureMonitor(contract.resources, report / (window["name"]+"-prepare-resources.json"), db, plan["settings"]["maximum_prepare_seconds"])
        monitor.sample(False)
        thread = threading.Thread(target=monitor.loop, daemon=True)
        thread.start()
        started = time.perf_counter()
        try:
            with training_awake():
                value = prepare_fold_data(db, root, Path(plan["side_path"]), directory, window, fields, monitor.require)
                monitor.finish(thread)
                value.update(decision_sha256=plan["content_sha256"], prepare_seconds=time.perf_counter()-started,
                    prepare_peak_working_set_mib=peak_working_set_mib(), resources=binding(monitor.path))
                write_json(directory / "prepared.json", sealed(value))
                print(json.dumps({"fold": window["name"], "status": "prepared", "dimension": value["input_dimension"],
                    "seconds": value["prepare_seconds"], "rows": {k: v["count"] for k,v in window.items() if isinstance(v, dict)}}), flush=True)
        except Exception as error:
            write_json(report / (window["name"]+"-prepare-failure.json"), {"error_type": type(error).__name__, "reason": str(error), "new_fits": 0})
            raise
        finally:
            monitor.stop.set()
            thread.join(timeout=5)
            db.close()
        gc.collect()
    return {"status": "prepared", "folds": 3, "decision_sha256": plan["content_sha256"]}


def validate_result(directory, result, ledger):
    entry = next((f for f in ledger.read()["fits"] if f["name"] == result["attempt_name"]), None)
    if (entry is None or entry["state"] != "completed" or entry["lane"] != "confirmation"
            or entry["family"] != result["family"] or entry["registration"]["fold"] != result["fold"]
            or entry["registration"]["seed"] != result["seed"]
            or entry["registration"]["input_sha256"] != result["input_sha256"]):
        raise ValueError("confirmation fit is not completed in shared ledger")
    for name, expected in result["files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or binding(path) != expected:
            raise ValueError("confirmation model/prediction file drift:"+name)


def train(root, report):
    plan, contract, ledger, artifact = context(root, report, True)
    if ledger.read()["outer_access"] or ledger.read()["test_access"]:
        raise ValueError("result release seals further model fits")
    settings = TabularSettings.model_validate_json((artifact / "source-snapshot/configs/stage09_tabular_baselines.json").read_text())
    with training_awake():
        for window in plan["windows"]:
            folder = artifact / window["name"]
            prepared, manifest = fold_inputs(folder)
            if prepared["decision_sha256"] != plan["content_sha256"] or prepared["window"] != window:
                raise ValueError("fold inputs not bound to frozen decision")
            frames = {s: pd.read_parquet(folder / ("targets-"+s+".parquet")) for s in ("train", "inner", "calibration", "outer")}
            tasks = [("x3", "xgboost", settings.seed)] + [("gnn-"+str(seed), "gnn", seed) for seed in plan["seeds"]]
            db = open_db(root, Path(plan["side_path"]))
            monitor = FeatureMonitor(contract.resources, report / (window["name"]+"-train-resources-"+str(time.time_ns())+".json"), db, 12*3600)
            monitor.sample(False)
            thread = threading.Thread(target=monitor.loop, daemon=True)
            thread.start()
            try:
                for task_id, family, seed in tasks:
                    result_path = report / (window["name"]+"-"+task_id+".json")
                    directory = folder / task_id
                    if result_path.exists():
                        result = checked_json(result_path)
                        validate_result(directory, result, ledger)
                        continue
                    attempt_name = report.name+"-"+window["name"]+"-"+task_id
                    if directory.exists() or any(f["name"] == attempt_name for f in ledger.read()["fits"]):
                        raise ValueError("consumed/incomplete fit; no silent repeat or budget refund")
                    gc.collect()
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    idle_start(monitor, contract.resources)
                    monitor.require()
                    directory.mkdir()
                    input_hash = _content_hash({"decision": plan["content_sha256"], "prepared": prepared["content_sha256"], "task": task_id, "seed": seed})
                    attempt = ledger.reserve(family, "confirmation", attempt_name,
                        {"fold": window["name"], "seed": seed, "input_sha256": input_hash, "candidate_id": plan["settings"]["xgboost_candidate"] if family == "xgboost" else plan["settings"]["gnn_candidate"]})
                    def guard():
                        monitor.require()
                        attempt.guard()
                    started = time.perf_counter()
                    readers = []
                    print(json.dumps({"fold": window["name"], "task": task_id, "state": "reserved", "cap_seconds": attempt.cap}), flush=True)
                    try:
                        if family == "xgboost":
                            factory = lambda split: lambda: batches(db, root, folder, Path(plan["side_path"]), "source_as_received",
                                frames[split] if split in ("train", "inner") else frames[split].assign(is_fraud=0), split, guard)
                            tx = quantile(factory("train"), len(frames["train"]), settings, guard)
                            vx = quantile(factory("inner"), len(frames["inner"]), settings, guard, tx)
                            model, detail = train_xgb({"parameters": plan["xgboost_parameters"]}, settings, tx, vx, guard)
                            del tx, vx
                            model.save_model(directory / "model.json")
                            restored = xgb.Booster()
                            restored.load_model(directory / "model.json")
                            inner = predict_xgb(model, factory("inner"), guard)
                            repeated = predict_xgb(restored, factory("inner"), guard)
                            delta = float(np.max(np.abs(inner-repeated)))
                            if delta > 1e-6:
                                raise ValueError("confirmation XGBoost reload mismatch")
                            np.save(directory / "inner-scores.npy", inner)
                            for split in ("calibration", "outer"):
                                np.save(directory / (split+"-scores.npy"), predict_xgb(restored, factory(split), guard))
                            detail.update(metrics=metrics(frames["inner"].transaction_id.tolist(), frames["inner"].is_fraud.to_numpy(), inner),
                                          reload_rows=len(inner), reload_max_abs_delta=delta)
                            del model, restored
                        else:
                            for split in ("train", "inner"):
                                readers.append(ConditionalGraphs(folder / "cache", manifest, "validation" if split == "inner" else split,
                                    "full", core=folder / ("core-"+split+".npy")))
                            task = {"id": window["name"]+"-"+task_id, "seed": seed, "parameters": plan["graph_parameters"]}
                            detail = fit(*readers, frames["train"].is_fraud.to_numpy(dtype=np.float32),
                                frames["inner"].is_fraud.to_numpy(dtype=np.float32), task, plan["settings"], directory, guard)
                            model = GraphSAGERiskModel(prepared["input_dimension"], plan["graph_parameters"]["hidden_channels"], plan["graph_parameters"]["dropout"]).cuda()
                            model.load_state_dict(torch.load(directory / "model.pt", map_location="cuda", weights_only=True))
                            for split in ("calibration", "outer"):
                                reader = ConditionalGraphs(folder / "cache", manifest, split, "full", core=folder / ("core-"+split+".npy"))
                                readers.append(reader)
                                np.save(directory / (split+"-scores.npy"), scores_for(model, reader, 256, torch.device("cuda"), guard))
                            del model
                        guard()
                        value = sealed({"status": "completed", "decision_sha256": plan["content_sha256"], "attempt_name": attempt_name,
                            "input_sha256": input_hash, "fold": window["name"], "seed": seed, "family": family,
                            "detail": detail, "fit_seconds": time.perf_counter()-started,
                            "files": {p.name: binding(p) for p in directory.iterdir() if p.is_file()},
                            "outer_labels_read": False, "resources_file": monitor.path.name})
                        write_json(result_path, value)
                        attempt.finish("completed")
                        print(json.dumps({"fold": window["name"], "task": task_id, "state": "completed", "seconds": value["fit_seconds"], "inner_metrics": detail["metrics"]}), flush=True)
                    except Exception as error:
                        if next(f for f in ledger.read()["fits"] if f["name"] == attempt_name)["state"] == "reserved":
                            attempt.finish("failed")
                        write_json(report / (window["name"]+"-"+task_id+"-failure.json"),
                            {"error_type": type(error).__name__, "reason": str(error), "attempt": attempt_name, "seconds": time.perf_counter()-started})
                        raise
                    finally:
                        for reader in readers:
                            reader.close()
                        gc.collect()
                        torch.cuda.empty_cache()
                monitor.finish(thread)
            finally:
                monitor.stop.set()
                thread.join(timeout=5)
                db.close()
    return {"status": "confirmation_fits_completed", "gnn": 9, "xgboost": 3, "outer_labels_read": False}


def completed_grid(plan, report, artifact, ledger):
    bound = {}
    for window in plan["windows"]:
        for task in ["x3", *["gnn-"+str(seed) for seed in plan["seeds"]]]:
            path = report / (window["name"]+"-"+task+".json")
            result = checked_json(path)
            expected_family = "xgboost" if task == "x3" else "gnn"
            expected_seed = 20260921 if task == "x3" else int(task.removeprefix("gnn-"))
            if result["decision_sha256"] != plan["content_sha256"] or result["fold"] != window["name"] or result["family"] != expected_family or result["seed"] != expected_seed:
                raise ValueError("confirmation result decision/fold mismatch")
            validate_result(artifact / window["name"] / task, result, ledger)
            bound[path.name] = result["content_sha256"]
    return bound


def calibrate(root, report):
    plan, contract, ledger, artifact = context(root, report, True)
    if ledger.read()["outer_access"] or ledger.read()["test_access"]:
        raise ValueError("fusion selection forbidden after outer/test release")
    result_binding = completed_grid(plan, report, artifact, ledger)
    path = report / "fusion-choice.json"
    if path.exists():
        value = checked_json(path)
        if value["result_bindings"] != result_binding or value["decision_sha256"] != plan["content_sha256"]:
            raise ValueError("sealed fusion result drift")
        return value
    db = open_db(root, Path(plan["side_path"]))
    try:
        values = {}
        for window in plan["windows"]:
            frame = targets(db, root / "data/interim/ieee-cis/transactions.parquet", window["calibration"])
            folder = artifact / window["name"]
            base = np.load(folder / "x3/calibration-scores.npy", allow_pickle=False)
            graphs = [np.load(folder / ("gnn-"+str(seed)) / "calibration-scores.npy", allow_pickle=False) for seed in plan["seeds"]]
            values[window["name"]] = choose_alpha(frame.transaction_id.tolist(), frame.is_fraud.to_numpy(), base, graphs, plan["fusion_grid"])
        value = sealed({"decision_sha256": plan["content_sha256"], "result_bindings": result_binding, "folds": values,
                        "outer_labels_read": False, "calibration_rule": plan["settings"]["fusion_selection"]})
        write_json(path, value)
        print(json.dumps({"status": "fusion_frozen", "alphas": {k:v["alpha"] for k,v in values.items()}, "outer_access": 0}), flush=True)
        return value
    finally:
        db.close()


def outer_labels(db, root, plan, report, ledger):
    path = report / "outer-labels.parquet"
    marker = report / "outer-label-binding.json"
    state = ledger.read()
    if state["outer_access"]:
        if not marker.exists():
            raise ValueError("outer label release incomplete; no repeat source-label batch")
        release = checked_json(report / "outer-release-inputs.json")
        if release["decision_sha256"] != plan["content_sha256"] or release["fusion_choice"] != binding(report / "fusion-choice.json"):
            raise ValueError("fusion changed after outer release")
        expected = checked_json(marker)
        if expected["decision_sha256"] != plan["content_sha256"] or binding(path) != expected["labels_file"]:
            raise ValueError("released outer labels changed")
        return pd.read_parquet(path)
    write_json(report / "outer-release-inputs.json", sealed({"decision_sha256": plan["content_sha256"],
        "fusion_choice": binding(report / "fusion-choice.json")}))
    ledger.open_results("outer")  # persisted BEFORE projecting any outer label
    windows = [w["outer"] for w in plan["windows"]]
    predicate = " OR ".join(["source_transaction_dt_seconds BETWEEN ? AND ?"]*len(windows))
    params = [str(root / "data/interim/ieee-cis/transactions.parquet")]
    params += [v for w in windows for v in (w["min_relative_seconds"], w["max_relative_seconds"])]
    frame = db.execute("SELECT transaction_id,source_transaction_dt_seconds,is_fraud FROM read_parquet(?) WHERE "+predicate+" ORDER BY source_transaction_dt_seconds,transaction_id", params).fetchdf()
    for window in windows:
        chosen = frame[frame.source_transaction_dt_seconds.between(window["min_relative_seconds"], window["max_relative_seconds"])]
        if len(chosen) != window["count"] or _content_hash({"ids": chosen.transaction_id.tolist()}) != window["ids_sha256"]:
            raise ValueError("released outer label ID alignment failure")
    frame.to_parquet(path, index=False)
    write_json(marker, sealed({"decision_sha256": plan["content_sha256"], "labels_file": binding(path), "rows": len(frame)}))
    return frame


def evaluate(root, report):
    plan, contract, ledger, artifact = context(root, report, True)
    bound = completed_grid(plan, report, artifact, ledger)
    fusion = checked_json(report / "fusion-choice.json")
    if fusion["result_bindings"] != bound or fusion["decision_sha256"] != plan["content_sha256"]:
        raise ValueError("all completed fits and frozen calibration weights required")
    path = report / "outer-evaluation.json"
    if path.exists():
        return checked_json(path)
    db = open_db(root, Path(plan["side_path"]))
    monitor = FeatureMonitor(contract.resources, report / ("outer-evaluation-resources-"+str(time.time_ns())+".json"), db, plan["settings"]["maximum_evaluation_seconds"])
    monitor.sample(False)
    thread = threading.Thread(target=monitor.loop, daemon=True)
    thread.start()
    try:
        with training_awake():
            labels = outer_labels(db, root, plan, report, ledger)
            folds = []
            for window in plan["windows"]:
                spec = window["outer"]
                frame = labels[labels.source_transaction_dt_seconds.between(spec["min_relative_seconds"], spec["max_relative_seconds"])]
                folder = artifact / window["name"]
                folds.append({"name": window["name"], "ids": frame.transaction_id.tolist(), "seconds": frame.source_transaction_dt_seconds.to_numpy(),
                    "labels": frame.is_fraud.to_numpy(), "baseline": np.load(folder / "x3/outer-scores.npy", allow_pickle=False),
                    "graphs": [np.load(folder / ("gnn-"+str(seed)) / "outer-scores.npy", allow_pickle=False) for seed in plan["seeds"]],
                    "alpha": fusion["folds"][window["name"]]["alpha"]})
            value, draws = evaluate_folds(folds, contract.metrics, plan["settings"]["bootstrap_iterations"],
                                         plan["settings"]["bootstrap_seed"], monitor.require)
            np.save(report / "paired-bootstrap.npy", draws)
            monitor.finish(thread)
            value.update(decision_sha256=plan["content_sha256"], fusion_choice_sha256=fusion["content_sha256"],
                result_bindings=bound, bootstrap_file=binding(report / "paired-bootstrap.npy"), resources_file=monitor.path.name)
            write_json(path, sealed(value))
            print(json.dumps({"status": "outer_evaluated", "summary": value["summary"], "gates": value["gates"], "promoted": value["promoted"]}), flush=True)
            return checked_json(path)
    finally:
        monitor.stop.set()
        thread.join(timeout=5)
        db.close()


def historical_test(root, report):
    plan, contract, ledger, artifact = context(root, report, True)
    completed_grid(plan, report, artifact, ledger)
    outer = checked_json(report / "outer-evaluation.json")
    fusion = checked_json(report / "fusion-choice.json")
    if outer["decision_sha256"] != plan["content_sha256"] or outer["fusion_choice_sha256"] != fusion["content_sha256"]:
        raise ValueError("historical test requires sealed outer decision and fusion")
    result_path = report / "historical-test.json"
    if result_path.exists():
        return checked_json(result_path)
    trained, directory = artifact / "fold-3", artifact / "historical-test"
    db = open_db(root, Path(plan["side_path"]))
    monitor = FeatureMonitor(contract.resources, report / ("historical-test-resources-"+str(time.time_ns())+".json"), db, 7200)
    monitor.sample(False)
    thread = threading.Thread(target=monitor.loop, daemon=True)
    thread.start()
    try:
        with training_awake():
            prepared_path = directory / "prepared.json"
            if not prepared_path.exists():
                if directory.exists():
                    raise ValueError("partial historical-test preparation preserved; audit required")
                directory.mkdir()
                value = prepare_prediction_data(db, root, Path(plan["side_path"]), directory, trained,
                                                 plan["historical_test_window"], monitor.require)
                value.update(decision_sha256=plan["content_sha256"])
                write_json(prepared_path, sealed(value))
            prepared, manifest = fold_inputs(directory)
            if prepared["decision_sha256"] != plan["content_sha256"]:
                raise ValueError("historical-test data decision mismatch")
            frame = pd.read_parquet(directory / "targets-test.parquet")
            scores_path = directory / "prediction-bindings.json"
            if not scores_path.exists():
                if ledger.read()["test_access"]:
                    raise ValueError("cannot rebuild predictions after test label access")
                model = xgb.Booster()
                model.load_model(trained / "x3/model.json")
                factory = lambda: batches(db, root, trained, Path(plan["side_path"]), "source_as_received",
                                          frame.assign(is_fraud=0), "test", monitor.require)
                np.save(directory / "x3-scores.npy", predict_xgb(model, factory, monitor.require))
                del model
                reader = ConditionalGraphs(directory / "cache", manifest, "test", "full", core=directory / "core-test.npy")
                try:
                    for seed in plan["seeds"]:
                        idle_start(monitor, contract.resources)
                        model = GraphSAGERiskModel(reader.dimension, plan["graph_parameters"]["hidden_channels"], plan["graph_parameters"]["dropout"]).cuda()
                        model.load_state_dict(torch.load(trained / ("gnn-"+str(seed)) / "model.pt", map_location="cuda", weights_only=True))
                        np.save(directory / ("gnn-"+str(seed)+"-scores.npy"),
                                scores_for(model, reader, 256, torch.device("cuda"), monitor.require))
                        del model
                        gc.collect()
                        torch.cuda.empty_cache()
                finally:
                    reader.close()
                write_json(scores_path, sealed({"decision_sha256": plan["content_sha256"],
                    "files": {p.name: binding(p) for p in directory.glob("*-scores.npy")}, "test_labels_read": False}))
            predictions = checked_json(scores_path)
            for name, expected in predictions["files"].items():
                if binding(directory / name) != expected:
                    raise ValueError("historical test scores drift")
            labels_path, marker = report / "test-labels.parquet", report / "test-label-binding.json"
            if ledger.read()["test_access"]:
                if not marker.exists() or binding(labels_path) != checked_json(marker)["labels_file"]:
                    raise ValueError("incomplete/changed historical test label batch; no source reread")
                labelled = pd.read_parquet(labels_path)
            else:
                ledger.open_results("test")
                labelled = targets(db, root / "data/interim/ieee-cis/transactions.parquet", plan["historical_test_window"])
                labelled.to_parquet(labels_path, index=False)
                write_json(marker, sealed({"decision_sha256": plan["content_sha256"], "labels_file": binding(labels_path)}))
            if labelled.transaction_id.tolist() != frame.transaction_id.tolist():
                raise ValueError("historical test prediction/label alignment mismatch")
            ids, labels = labelled.transaction_id.tolist(), labelled.is_fraud.to_numpy()
            baseline = np.load(directory / "x3-scores.npy", allow_pickle=False)
            graphs = [np.load(directory / ("gnn-"+str(seed)+"-scores.npy"), allow_pickle=False) for seed in plan["seeds"]]
            alpha = fusion["folds"]["fold-3"]["alpha"]
            value = {"decision_sha256": plan["content_sha256"], "outer_evaluation_sha256": outer["content_sha256"],
                "rows": len(ids), "alpha": alpha, "baseline": metrics(ids, labels, baseline),
                "graph_seeds": [metrics(ids, labels, g) for g in graphs],
                "fusion_seeds": [metrics(ids, labels, alpha*baseline+(1-alpha)*g) for g in graphs],
                "fixed_graph_ensemble": metrics(ids, labels, np.mean(graphs, axis=0)),
                "descriptive_only": True, "historically_observed_not_blind": True,
                "model_or_alpha_reselected": False, "predictions_sha256": predictions["content_sha256"]}
            monitor.finish(thread)
            write_json(result_path, sealed(value))
            print(json.dumps({"status": "historical_test_reviewed", "rows": len(ids), "baseline": value["baseline"],
                              "fusion_seed_ap": [v["ap"] for v in value["fusion_seeds"]], "descriptive_only": True}), flush=True)
            return checked_json(result_path)
    except Exception as error:
        write_json(report / ("historical-test-failure-"+str(time.time_ns())+".json"),
                   {"error_type": type(error).__name__, "reason": str(error), "new_fits": 0})
        raise
    finally:
        monitor.stop.set()
        thread.join(timeout=5)
        db.close()


def complete(root, report):
    plan, contract, ledger, artifact = context(root, report, True)
    bound = completed_grid(plan, report, artifact, ledger)
    outer = checked_json(report / "outer-evaluation.json")
    reviewed = checked_json(report / "historical-test.json")
    if reviewed["outer_evaluation_sha256"] != outer["content_sha256"] or outer["result_bindings"] != bound:
        raise ValueError("final decision outer/test bindings disagree")
    # Test metrics never enter the quality gate or choose a model/alpha.
    decision = sealed({"recipe_decision_sha256": plan["content_sha256"], "outer_evaluation_sha256": outer["content_sha256"],
        "promoted": outer["promoted"], "gates": outer["gates"], "summary": outer["summary"],
        "primary_model_candidate": "enhanced_xgboost_x3", "primary_quality_eligible": outer["gates"]["baseline_quality"],
        "graph_role": "eligible_incremental_fusion" if outer["promoted"] else "withdraw_from_main_scoring_keep_experimental_and_relations",
        "deployment_fold": "fold-3", "deployment_seeds": plan["seeds"],
        "probabilities_calibrated": False, "anonymous_supplier_timing_verified": False,
        "service_changed_here": False, "coverage_stage": "Route6_pending", "subgroup_promotion": False,
        "historical_test_used_for_selection": False, "personal_learning": "Pending"})
    path = report / "model-decision.json"
    if path.exists() and checked_json(path) != decision:
        raise ValueError("final quality verdict already exists with different content")
    write_json(path, decision)
    closure = sealed({"status": "passed", "decision_sha256": plan["content_sha256"],
        "quality_decision_sha256": decision["content_sha256"],
        "model_results": bound, "fold_preparations": {w["name"]: checked_json(artifact / w["name"] / "prepared.json")["content_sha256"] for w in plan["windows"]},
        "report_files": {p.name: binding(p) for p in report.iterdir() if p.is_file() and p.name not in {"closure.json", "execution.lock"}},
        "historical_test_preparation": checked_json(artifact / "historical-test/prepared.json")["content_sha256"],
        "historical_test_prediction_files": checked_json(artifact / "historical-test/prediction-bindings.json"),
        "ledger_at_acceptance": read_json(ledger.path)["content_sha256"], "new_gnn_fits": 9, "new_xgboost_fits": 3,
        "generation_calls": 0, "paid_calls": 0, "scope": "engineering_complete_quality_gate_separate"})
    write_json(report / "closure.json", closure)
    return check(root, report)


def check(root, report):
    plan, contract, ledger, artifact = context(root, report)
    closure = checked_json(report / "closure.json")
    for name, expected in closure["report_files"].items():
        path = (report / name).resolve()
        if not path.is_relative_to(report.resolve()) or binding(path) != expected:
            raise ValueError("confirmation report file drift:"+name)
    for window in plan["windows"]:
        prepared, _ = fold_inputs(artifact / window["name"])
        if prepared["content_sha256"] != closure["fold_preparations"][window["name"]]:
            raise ValueError("confirmation prepared closure drift")
    if completed_grid(plan, report, artifact, ledger) != closure["model_results"]:
        raise ValueError("confirmation fit closure drift")
    prepared, _ = fold_inputs(artifact / "historical-test")
    if prepared["content_sha256"] != closure["historical_test_preparation"]:
        raise ValueError("historical-test preparation closure drift")
    predictions = checked_json(artifact / "historical-test/prediction-bindings.json")
    if predictions != closure["historical_test_prediction_files"]:
        raise ValueError("historical-test predictions closure drift")
    for name, expected in predictions["files"].items():
        path = (artifact / "historical-test" / name).resolve()
        if not path.is_relative_to((artifact / "historical-test").resolve()) or binding(path) != expected:
            raise ValueError("historical-test prediction bytes drift")
    decision = checked_json(report / "model-decision.json")
    if decision["recipe_decision_sha256"] != plan["content_sha256"] or decision["content_sha256"] != closure["quality_decision_sha256"]:
        raise ValueError("confirmation final quality decision drift")
    state = ledger.read()
    if len(state["outer_access"]) != 1 or len(state["test_access"]) != 1:
        raise ValueError("one outer and one historical-test label release required")
    return {"status": "passed", "gnn_confirmation_fits": 9, "xgboost_confirmation_fits": 3,
        "promoted": decision["promoted"], "summary": decision["summary"],
        "outer_access": len(state["outer_access"]), "test_access": len(state["test_access"]),
        "new_generation_calls": 0, "paid_calls": 0, "default_service_changed": False}
