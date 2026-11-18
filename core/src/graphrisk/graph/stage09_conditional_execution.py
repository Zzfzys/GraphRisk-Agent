"""Five preregistered one-fold controls; no calibration/outer/test access."""
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

from graphrisk.agent.stage09_preflight import _content_hash, check_resources
from graphrisk.demo.stage09_evaluation import read_json, resources, write_json
from graphrisk.features.wide_execution import peak_working_set_mib
from graphrisk.graph.stage09_enhancement_preflight import check_registration, verify_inventory
from graphrisk.graph.stage09_expansion_execution import check as parent_check, verify_plan as parent_plan, idle_start
from graphrisk.graph.stage09_monitor import DiagnosisMonitor
from graphrisk.graph.stage09_packed import verify_cache
from graphrisk.graph.stage09_conditional_data import prepare_core, prepare_rewired, ConditionalGraphs
from graphrisk.graph.stage09_conditional_training import fit, select
from graphrisk.models.enhanced_execution import binding, checked_json, sealed, open_db, training_awake
from graphrisk.models.enhanced_training import metrics


def settings_for(source, contract):
    value = read_json(source / "configs/stage09_conditional_graph.json")
    grid = [("c1-no-edges", "no_edges", False, False, 800),
            ("c2-rewired", "rewired", False, False, 950),
            ("c3-no-history", "no_history_numeric", False, True, 900),
            ("c4-core-graph", "full", True, True, 1100),
            ("c5-core-no-edges", "no_edges", True, False, 600)]
    expected = {"schema_version": "stage09-5b-conditional-v1", "device": "cuda", "cpu_threads": 4,
        "batch_size": 256, "max_epochs": 25, "patience": 10, "train_probe_count": 10000,
        "seed": 20260928, "rewiring_seed": 20261009, "core_profile": "core",
        "core_clip_standard_deviations": 10., "maximum_prepare_seconds": 7200,
        "core_categorical_encoding": "low_card_one_hot_else_signed_hash8",
        "relationship_ap_margin": .005, "minimum_rewired_graph_fraction": .10,
        "tasks": [dict(zip(("id", "variant", "core", "eligible", "max_seconds"), t)) for t in grid],
        "selection": "eligible_inner_ap_then_seconds_then_registered_order",
        "fusion": "freeze_grid_now_select_on_calibration_in_route5",
        "heterogeneous_models": "deferred_limited_budget_and_no_confirmed_relation_gain",
        "label_access": ["fold-1-train", "fold-1-early-stop"]}
    if value != expected or value["patience"] != contract.training_rules.patience:
        raise ValueError("conditional appendix differs from preregistered finite experiment")
    return value


def remaining_seconds(ledger):
    state = ledger.read()
    limit = ledger.contract.budgets.gnn
    fits = [f for f in state["fits"] if f["family"] == "gnn"]
    spent = sum(f["seconds"] if f["state"] != "reserved" else f["cap_seconds"] for f in fits)
    reserved = (limit.lanes["confirmation"]-sum(f["lane"] == "confirmation" for f in fits))*limit.max_fit_seconds
    return limit.max_total_seconds-spent-reserved


def development_remaining(ledger):
    state = ledger.read()
    if state["decision_sha256"] or state["outer_access"] or state["test_access"] or any(f["state"] == "reserved" for f in state["fits"]):
        raise ValueError("candidate phase sealed or unresolved attempt")
    return remaining_seconds(ledger)


def reference_files(artifact, reference):
    directory = (artifact / reference.get("model_directory", "g3")).resolve()
    if not directory.is_relative_to(artifact.resolve()):
        raise ValueError("reference model path escape")
    for name, item in reference["files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or binding(path) != item:
            raise ValueError("reference checkpoint/history bytes drift")


def reference_prefix(reference, history, maximum):
    prefix = [h for h in history if h["epoch"] <= maximum]
    if not prefix or reference["best_epoch"] > maximum or max(h["inner_ap"] for h in prefix) != reference["metrics"]["ap"]:
        raise ValueError("saved reference is not best within matching epoch prefix")
    return {"prefix_epochs": maximum, "saved_best_epoch": reference["best_epoch"],
            "prefix_best_ap": max(h["inner_ap"] for h in prefix),
            "source_epochs_observed": reference["epochs_observed"], "new_reference_fit": False}


def prepare(source, root, registration, parent, name):
    source, root, registration, parent = [p.resolve() for p in (source, root, registration, parent)]
    if root.drive.upper() != "E:" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
        raise ValueError("E root and safe unique experiment name required")
    parent_check(root, parent)
    previous, contract, ledger, manifests = parent_plan(root, parent)
    check_registration(source, root, registration)
    frozen = checked_json(registration / "freeze.json")
    for path in ("configs/stage09_conditional_graph.json", "docs/data/stage-09-conditional-graph.md"):
        if not any(i["root"] == "source" and i["path"] == path for i in frozen["inventory"]):
            raise ValueError("conditional config and explanation must be frozen before fitting")
    settings = settings_for(source, contract)
    if not checked_json(parent / "candidate-choice.json")["conditional_v2_triggered"]:
        raise ValueError("conditional enhancement was not triggered")
    remaining = development_remaining(ledger)
    deadlines = sum(t["max_seconds"] for t in settings["tasks"])
    if remaining < deadlines:
        raise ValueError("registered five-fit deadlines exceed development reserve")
    artifact, report = root / contract.artifact_root / name, root / contract.output_root / name
    if artifact.exists() or report.exists():
        raise ValueError("fresh output required; resume existing plan instead")
    observed = resources(True)
    if check_resources(contract.resources, observed)["status"] != "passed" or not torch.cuda.is_available():
        raise ValueError("CUDA/resource preparation gate failed")
    artifact.mkdir(parents=True)
    report.mkdir(parents=True)
    write_json(report / "prepare-start-resources.json", observed)
    monitor = DiagnosisMonitor(contract.resources, report / "prepare-resources.json")
    monitor.sample(False)
    thread = threading.Thread(target=monitor.loop, daemon=True)
    thread.start()
    started = time.perf_counter()
    def guard():
        monitor.require()
        if time.perf_counter()-started >= settings["maximum_prepare_seconds"]:
            raise ValueError("conditional preparation deadline exceeded")
    try:
        with training_awake():
            folder = Path(previous["artifact_directory"]) / "caches/full_allowed_train"
            manifest = manifests["full_allowed_train"]
            reference = checked_json(parent / "g3.json")
            prefix = reference_prefix(reference, read_json(Path(previous["artifact_directory"]) / "g3/training-history.json"), settings["max_epochs"])
            train = pd.read_parquet(Path(previous["artifact_directory"]) / "targets-train.parquet")
            inner = pd.read_parquet(Path(previous["artifact_directory"]) / "targets-inner.parquet")
            base = checked_json(Path(previous["tabular_directory"]) / "plan.json")
            tabular = Path(base["artifact_directory"])
            db = open_db(root, Path(base["side_path"]))
            try:
                core = prepare_core(db, root, tabular, Path(base["side_path"]), train, inner, artifact,
                                    settings["core_clip_standard_deviations"], guard)
            finally:
                db.close()
            print("train-only core matrices prepared", flush=True)
            audit = prepare_rewired(folder, manifest, artifact / "rewired", settings["rewiring_seed"], guard)
            tasks = [{**t, "seed": settings["seed"], "parameters": reference["task"]["parameters"]} for t in settings["tasks"]]
            schema = {"schema_version": "graphsage-conditional-core-root-v1", "base_dimension": 28,
                      "enhanced_dimension": 28+len(core["columns"]),
                      "appended_columns": ["root_core_scaled__"+c for c in core["columns"]],
                      "append_scope": "root-only; historical/entity extras are zero",
                      "categorical_encoding": settings["core_categorical_encoding"],
                      "normalization": "train-only population moments; clip standardized values at +/-10",
                      "preprocessor": binding(tabular / "fold-1-core.json"), "labels_used": False,
                      "future_fold_rule": "refit core vocabulary and normalization on that fold train only"}
            write_json(artifact / "input-schema.json", sealed(schema))
            for item in frozen["inventory"]:
                if item["root"] == "source":
                    path = artifact / "source-snapshot" / item["path"]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source / item["path"], path)
            verify_inventory({"source": artifact / "source-snapshot", "execution": root}, frozen["inventory"])
            monitor.stop.set()
            thread.join(timeout=5)
            if thread.is_alive():
                raise ValueError("conditional prepare monitor did not stop")
            monitor.sample(True)
            guard()
            plan = {"schema_version": "stage09-5b-conditional-run-v1", "artifact_directory": str(artifact),
                "registration_directory": str(registration), "source_freeze_sha256": frozen["content_sha256"],
                "parent_directory": str(parent), "parent_plan_sha256": previous["content_sha256"],
                "parent_closure_sha256": checked_json(parent / "closure.json")["content_sha256"],
                "cache_directory": str(folder), "cache_sha256": manifest["content_sha256"],
                "reference_sha256": reference["content_sha256"], "reference_prefix": prefix,
                "initial_development_remaining_seconds": remaining, "registered_deadline_sum": deadlines,
                "settings": settings, "tasks": tasks, "input_schema": schema, "relation_audit": audit,
                "prepared_files": {p.relative_to(artifact).as_posix(): binding(p) for p in artifact.rglob("*")
                                   if p.is_file() and "source-snapshot" not in p.parts},
                "prepare_resource_binding": binding(monitor.path), "prepare_seconds": time.perf_counter()-started,
                "label_access": settings["label_access"], "outer_test_access": False, "new_fits_during_prepare": 0}
            write_json(report / "plan.json", sealed(plan))
    except BaseException as error:
        write_json(report / "prepare-failure.json", {"error_type": type(error).__name__, "reason": str(error), "new_fits": 0})
        raise
    finally:
        monitor.stop.set()
        thread.join(timeout=5)
    return {"status": "prepared", "directory": str(report), "input_dimension": schema["enhanced_dimension"],
            "planned_real_fits": 5, "relation_audit": audit, "reference_prefix": prefix}


def verify(root, report):
    root, report = root.resolve(), report.resolve()
    if root.drive.upper() != "E:" or not report.is_relative_to(root / "reports/generated/stage09/route5b"):
        raise ValueError("conditional output must remain in registered E root")
    plan = checked_json(report / "plan.json")
    artifact = Path(plan["artifact_directory"]).resolve()
    if not artifact.is_relative_to(root / "artifacts/stage-09/route5b"):
        raise ValueError("conditional artifact path escape")
    parent = Path(plan["parent_directory"])
    previous, contract, ledger, manifests = parent_plan(root, parent)
    if previous["content_sha256"] != plan["parent_plan_sha256"] or checked_json(parent / "closure.json")["content_sha256"] != plan["parent_closure_sha256"]:
        raise ValueError("conditional parent binding drift")
    frozen = checked_json(Path(plan["registration_directory"]) / "freeze.json")
    if frozen["content_sha256"] != plan["source_freeze_sha256"]:
        raise ValueError("conditional source freeze drift")
    verify_inventory({"source": artifact / "source-snapshot", "execution": root}, frozen["inventory"])
    settings = settings_for(artifact / "source-snapshot", contract)
    reference = checked_json(parent / "g3.json")
    reference_files(Path(previous["artifact_directory"]), reference)
    tasks = [{**t, "seed": settings["seed"], "parameters": reference["task"]["parameters"]} for t in settings["tasks"]]
    if settings != plan["settings"] or tasks != plan["tasks"] or reference["content_sha256"] != plan["reference_sha256"]:
        raise ValueError("conditional task/reference drift")
    for name, item in plan["prepared_files"].items():
        path = (artifact / name).resolve()
        if not path.is_relative_to(artifact) or binding(path) != item:
            raise ValueError("conditional prepared input drift:"+name)
    if binding(report / "prepare-resources.json") != plan["prepare_resource_binding"]:
        raise ValueError("conditional preparation resource evidence drift")
    manifest = verify_cache(Path(plan["cache_directory"]))
    if manifest["content_sha256"] != plan["cache_sha256"] or manifest != manifests["full_allowed_train"]:
        raise ValueError("conditional cache drift")
    recovery(root, report, plan)
    return plan, contract, ledger, manifest, reference


def register_recovery(root, report, plan, registration):
    """One runtime-only retry after the registered c5 deadline; no refunds."""
    source = Path(__file__).resolve().parents[3]
    check_registration(source, root, registration)
    fresh = checked_json(registration / "freeze.json")
    old = checked_json(Path(plan["registration_directory"]) / "freeze.json")
    before = {(i["root"], i["path"]): i["sha256"] for i in old["inventory"]}
    changed = [i for i in fresh["inventory"] if before.get((i["root"], i["path"])) != i["sha256"]]
    allowed = {"src/graphrisk/graph/stage09_conditional_data.py", "src/graphrisk/graph/stage09_conditional_execution.py",
               "src/graphrisk/graph/stage09_conditional_training.py",
               "scripts/run_stage09_conditional_graph.py", "tests/unit/test_stage09_conditional_graph.py",
               "tests/integration/test_stage09_conditional_data.py", "docs/data/stage-09-conditional-graph.md"}
    if not changed or any(i["root"] != "source" or i["path"] not in allowed for i in changed):
        raise ValueError("conditional recovery changes model/config/data beyond runtime repair")
    # All feature transforms outside the reader must remain byte-equivalent ASTs.
    import ast
    artifact = Path(plan["artifact_directory"])
    data_file = Path("src/graphrisk/graph/stage09_conditional_data.py")
    def functions(path):
        return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(path.read_text(encoding="utf-8")).body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if functions(source / data_file) != functions(artifact / "source-snapshot" / data_file):
        raise ValueError("conditional recovery changed frozen feature transforms")
    failure = read_json(report / "c5-core-no-edges-failure.json")
    if failure["error_type"] != "TimeoutError" or failure["reason"] != "5B fit wall-clock deadline reached" or (report / "c5-core-no-edges.json").exists():
        raise ValueError("runtime repair requires the bound incomplete c5 deadline")
    payload = {"plan_sha256": plan["content_sha256"], "registration_directory": str(registration),
               "source_freeze_sha256": fresh["content_sha256"], "allowed_failed_task": "c5-core-no-edges", "maximum_retries": 1,
               "changed_source_paths": [i["path"] for i in changed], "model_parameters_changed": False,
               "feature_bytes_changed": False, "labels_windows_changed": False, "deadline_changed": False,
               "reader": "bounded_core_mapping_single_numpy_allocation_skip_unused_edges"}
    if (report / "runtime-recovery.json").exists():
        if checked_json(report / "runtime-recovery.json") != sealed(payload):
            raise ValueError("conditional recovery already bound differently")
        return
    for item in fresh["inventory"]:
        if item["root"] == "source":
            path = artifact / "runtime-source-snapshot" / item["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / item["path"], path)
    verify_inventory({"source": artifact / "runtime-source-snapshot", "execution": root}, fresh["inventory"])
    write_json(report / "runtime-recovery.json", sealed(payload))


def recovery(root, report, plan):
    if not (report / "runtime-recovery.json").exists():
        return None
    item = checked_json(report / "runtime-recovery.json")
    frozen = checked_json(Path(item["registration_directory"]) / "freeze.json")
    if item["plan_sha256"] != plan["content_sha256"] or frozen["content_sha256"] != item["source_freeze_sha256"]:
        raise ValueError("conditional recovery source/input drift")
    verify_inventory({"source": Path(plan["artifact_directory"]) / "runtime-source-snapshot", "execution": root}, frozen["inventory"])
    return item


def prefix_equivalence(original, repeated):
    fields = ("train_weighted_loss", "train_probe_ap", "train_probe_roc_auc", "inner_ap", "inner_roc_auc")
    if not original or len(repeated) < len(original) or [h["epoch"] for h in repeated[:len(original)]] != [h["epoch"] for h in original]:
        raise ValueError("runtime recovery lacks full original epoch prefix")
    delta = {k: max(abs(a[k]-b[k]) for a, b in zip(original, repeated)) for k in fields}
    if any(v != 0 for v in delta.values()):
        raise ValueError("runtime recovery training prefix changed")
    return {"epochs": len(original), "metric_max_abs_deltas": delta, "elapsed_seconds_compared": False}


def decision(plan, reference, results):
    selected = select(reference, results, plan["tasks"])
    item = reference if selected == "g3" else results[selected]
    ap = reference["metrics"]["ap"]
    delta = {"base_minus_no_edges_ap": ap-results["c1-no-edges"]["metrics"]["ap"],
             "base_minus_rewired_ap": ap-results["c2-rewired"]["metrics"]["ap"],
             "core_graph_minus_core_no_edges_ap": results["c4-core-graph"]["metrics"]["ap"]-results["c5-core-no-edges"]["metrics"]["ap"]}
    fraction = plan["relation_audit"]["validation"]["changed_graph_fraction"]
    rule = delta["base_minus_no_edges_ap"] >= .005 and delta["base_minus_rewired_ap"] >= .005 and fraction >= .10
    return {"selected_current_candidate": selected, "selected_inner_metrics": item["metrics"],
        "selected_recipe": item["task"], "selected_input_schema_sha256": _content_hash(plan["input_schema"]) if item.get("input_dimension", 28) > 28 else plan["cache_sha256"],
        "relation_deltas": delta, "rewired_inner_graph_fraction": fraction,
        "exploratory_relationship_rule_met": rule, "confirmed_relation_gain": False,
        "heterogeneous_models": "Deferred: limited budget and no multi-fold confirmed relation gain",
        "fusion": {"xgboost_weight_grid": [0., .25, .5, .75, 1.], "selection": "calibration AP; tie prefer larger XGBoost weight",
                   "selection_stage": "Route5", "calibration_fitted_here": False},
        "confirmation_training": {"max_epochs": 25, "patience": 10, "future_fold_core_refit": True},
        "final_model_decision": False, "default_service_changed": False, "outer_test_access": False,
        "new_generation_calls": 0, "new_paid_calls": 0}


def result_check(root, report, artifact, plan, task, ledger):
    result = checked_json(report / (task["id"]+".json"))
    entry = next((f for f in ledger.read()["fits"] if f["name"] == result["attempt_name"]), None)
    if result["task"] != task or result["plan_sha256"] != plan["content_sha256"] or not entry or entry["state"] != "completed":
        raise ValueError("conditional completed result/ledger drift")
    model_name = result.get("model_directory", task["id"])
    if model_name not in {task["id"], task["id"]+"-retry-1"}:
        raise ValueError("conditional result model directory mismatch")
    for name, item in result["files"].items():
        path = (artifact / model_name / name).resolve()
        if not path.is_relative_to(artifact / model_name) or binding(path) != item:
            raise ValueError("conditional model bytes drift")
    if binding(report / result.get("resource_file", task["id"]+"-resources.json")) != result["resources"]:
        raise ValueError("conditional fit resource evidence drift")
    return result


def run(root, report, recovery_registration=None):
    plan, contract, ledger, manifest, reference = verify(root, report)
    if recovery_registration:
        register_recovery(root, report, plan, recovery_registration.resolve())
    repair = recovery(root, report, plan)
    check_registration(Path(__file__).resolve().parents[3], root, Path(repair["registration_directory"] if repair else plan["registration_directory"]))
    if (report / "closure.json").exists():
        return check(root, report)
    artifact = Path(plan["artifact_directory"])
    previous = checked_json(Path(plan["parent_directory"]) / "plan.json")
    original = Path(previous["artifact_directory"])
    train_targets, inner_targets = [pd.read_parquet(original / ("targets-"+s+".parquet")) for s in ("train", "inner")]
    results = {}
    for task in plan["tasks"]:
        output = report / (task["id"]+".json")
        if output.exists():
            results[task["id"]] = result_check(root, report, artifact, plan, task, ledger)
            continue
        attempt_name = report.name+"-"+task["id"]
        parent_name, model_name = attempt_name, task["id"]
        previous_attempt = next((f for f in ledger.read()["fits"] if f["name"] == attempt_name), None)
        retry = previous_attempt is not None
        if retry:
            if not repair or task["id"] != repair["allowed_failed_task"] or previous_attempt["state"] != "failed":
                raise ValueError("conditional attempt incomplete; audit before registering recovery")
            attempt_name += "-retry-1"
            model_name += "-retry-1"
            if any(f["name"] == attempt_name for f in ledger.read()["fits"]):
                raise ValueError("conditional recovery already attempted; no extra retries")
        needed = sum(t["max_seconds"] for t in plan["tasks"] if not (report / (t["id"]+".json")).exists())
        if development_remaining(ledger) < needed:
            raise ValueError("pending registered deadlines exceed protected development budget")
        resource_file = model_name+"-resources.json"
        monitor = DiagnosisMonitor(contract.resources, report / resource_file)
        idle_start(monitor, contract.resources)
        directory = artifact / model_name
        directory.mkdir(exist_ok=False)
        registration = {"fold": "fold-1", "seed": task["seed"], "input_sha256": plan["content_sha256"],
                        "appendix_sha256": _content_hash(plan["settings"]), "max_fit_seconds": task["max_seconds"]}
        if retry:
            registration["retry_of"] = parent_name
        attempt = ledger.reserve("gnn", "failure" if retry else "conditional", attempt_name, registration)
        thread = threading.Thread(target=monitor.loop, daemon=True)
        thread.start()
        def guard():
            monitor.require()
            attempt.guard()
        started = time.perf_counter()
        readers = []
        try:
            with training_awake():
                for split, label in (("train", "train"), ("validation", "inner")):
                    readers.append(ConditionalGraphs(Path(plan["cache_directory"]), manifest, split, task["variant"],
                        artifact / "rewired" / (split+"-edges.i64") if task["variant"] == "rewired" else None,
                        artifact / ("core-"+label+".npy") if task["core"] else None))
                if readers[0].ids != tuple(train_targets.transaction_id) or readers[1].ids != tuple(inner_targets.transaction_id):
                    raise ValueError("conditional targets misaligned")
                result = fit(*readers, train_targets.is_fraud.to_numpy(), inner_targets.is_fraud.to_numpy(), task, plan["settings"], directory, guard)
                if retry:
                    proof = prefix_equivalence(read_json(artifact / task["id"] / "training-history.json"), read_json(directory / "training-history.json"))
                    write_json(report / "recovery-prefix.json", sealed({"plan_sha256": plan["content_sha256"], **proof}))
                for reader in readers:
                    reader.close()
                readers.clear()
                gc.collect()
                torch.cuda.empty_cache()
                monitor.stop.set()
                thread.join(timeout=5)
                if thread.is_alive():
                    raise ValueError("conditional fit monitor did not stop")
                monitor.sample(True)
                guard()
                result.update({"plan_sha256": plan["content_sha256"], "task": task, "attempt_name": attempt_name,
                    "fit_seconds": time.perf_counter()-started, "peak_working_set_mib": peak_working_set_mib(),
                    "model_directory": model_name, "resource_file": resource_file, "runtime_recovery_sha256": repair["content_sha256"] if retry else None,
                    "files": {p.name: binding(p) for p in directory.iterdir() if p.is_file()}, "resources": binding(monitor.path)})
                attempt.finish("completed")
                write_json(output, sealed(result))
                results[task["id"]] = result
                print(json.dumps({"completed": task["id"], "metrics": result["metrics"], "seconds": result["fit_seconds"]}), flush=True)
        except BaseException as error:
            entry = next(f for f in ledger.read()["fits"] if f["name"] == attempt_name)
            if entry["state"] == "reserved":
                attempt.finish("failed")
            write_json(report / (model_name+"-failure.json"), {"error_type": type(error).__name__, "reason": str(error),
                "attempt_name": attempt_name, "partial_directory": str(directory), "elapsed_seconds": time.perf_counter()-started,
                "charged_state": next(f for f in ledger.read()["fits"] if f["name"] == attempt_name)["state"]})
            raise
        finally:
            for reader in readers:
                reader.close()
            monitor.stop.set()
            thread.join(timeout=5)
            gc.collect()
            torch.cuda.empty_cache()
    write_json(report / "candidate-choice.json", sealed({"plan_sha256": plan["content_sha256"], **decision(plan, reference, results)}))
    files = {p.relative_to(root).as_posix(): binding(p) for base in (artifact, report) for p in base.rglob("*")
             if p.is_file() and not {"source-snapshot", "runtime-source-snapshot"}.intersection(p.parts) and p.name != "closure.json"}
    write_json(report / "closure.json", sealed({"plan_sha256": plan["content_sha256"], "files": files,
        "engineering_status": "done", "completed_real_fits": 5, "outer_test_access": False,
        "new_generation_calls": 0, "new_paid_calls": 0}))
    return check(root, report)


def check(root, report):
    plan, contract, ledger, manifest, reference = verify(root, report)
    closure = checked_json(report / "closure.json")
    if closure["plan_sha256"] != plan["content_sha256"] or closure["completed_real_fits"] != 5:
        raise ValueError("incomplete conditional closure")
    for name, item in closure["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or binding(path) != item:
            raise ValueError("conditional closure bytes drift:"+name)
    previous = checked_json(Path(plan["parent_directory"]) / "plan.json")
    targets = pd.read_parquet(Path(previous["artifact_directory"]) / "targets-inner.parquet")
    artifact = Path(plan["artifact_directory"])
    results = {}
    for task in plan["tasks"]:
        result = result_check(root, report, artifact, plan, task, ledger)
        values = np.load(artifact / result.get("model_directory", task["id"]) / "inner-scores.npy", allow_pickle=False)
        if metrics(targets.transaction_id.tolist(), targets.is_fraud.to_numpy(), values) != result["metrics"]:
            raise ValueError("conditional independent metrics mismatch")
        if result["reload_rows"] != len(targets) or result["reload_max_abs_delta"] > 1e-6:
            raise ValueError("conditional checkpoint reload incomplete")
        results[task["id"]] = result
    chosen = checked_json(report / "candidate-choice.json")
    expected = sealed({"plan_sha256": plan["content_sha256"], **decision(plan, reference, results)})
    if chosen != expected:
        raise ValueError("conditional selection/fusion rule drift")
    return {"status": "passed", "completed_real_fits": 5, "selected_current_candidate": chosen["selected_current_candidate"],
            "selected_inner_metrics": chosen["selected_inner_metrics"], "relation_deltas": chosen["relation_deltas"],
            "development_remaining_seconds": remaining_seconds(ledger), "outer_test_access": False,
            "default_service_changed": False, "source_freeze_sha256": plan["source_freeze_sha256"]}
