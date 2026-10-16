"""Resumable E-only tabular development, shared non-refundable fit ledger."""
import gc
import json
import re
import shutil
import threading
import time
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import xgboost as xgb

from graphrisk.agent.stage09_preflight import _content_hash, _sha256, check_resources
from graphrisk.demo.stage09_evaluation import continuous_ok, read_json, resources, write_json
from graphrisk.features.wide_execution import FeatureMonitor, check_outputs, peak_working_set_mib
from graphrisk.graph.stage09_enhancement import EnhancementLedger, load_contract
from graphrisk.graph.stage09_enhancement_preflight import budget_binding, check_registration, verify_inventory
from graphrisk.models.enhanced_contract import TabularSettings
from graphrisk.models.enhanced_data import batches, compact_view, prepare_legacy, targets
from graphrisk.models.enhanced_training import metrics, predict_xgb, quantile, select_candidate, train_xgb


def sealed(value):
    return {**value, "content_sha256": _content_hash(value)}


def checked_json(path):
    value = read_json(path)
    if _content_hash({k:v for k,v in value.items() if k != "content_sha256"}) != value.get("content_sha256"):
        raise ValueError("bound JSON content drift:"+path.name)
    return value


def binding(path):
    return {"bytes": path.stat().st_size, "sha256": _sha256(path)}


def open_db(root, side):
    db = duckdb.connect()
    db.execute("SET threads=1")
    db.execute("SET memory_limit='768MB'")
    db.execute("SET temp_directory=?", [str(root / "artifacts/tmp/5b-tabular-duckdb")])
    db.from_parquet(str(side)).create_view("wide")
    db.from_parquet(str(root / "data/interim/ieee-cis/features_all.parquet")).create_view("compact_source")
    db.from_parquet(str(root / "data/interim/ieee-cis/transactions.parquet")).create_view("canonical_source")
    compact_view(db)
    return db


def prepare(source, root, registration, features, name):
    source, root, registration, features = [p.resolve() for p in (source, root, registration, features)]
    if root.drive.upper() != "E:" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
        raise ValueError("E execution root and safe unique name required")
    contract = load_contract(source / "configs/stage09_model_enhancement.json")
    settings = TabularSettings.model_validate_json((source / "configs/stage09_tabular_baselines.json").read_text())
    check_registration(source, root, registration)
    frozen = checked_json(registration / "freeze.json")
    if not any(i["root"] == "source" and i["path"] == "configs/stage09_tabular_baselines.json" for i in frozen["inventory"]):
        raise ValueError("tabular appendix must be frozen before data/model work")
    feature_check = check_outputs(root, features)
    feature_manifest = checked_json(features / "manifest.json")
    artifact = (root / contract.artifact_root / name).resolve()
    report = (root / contract.output_root / name).resolve()
    if artifact.exists() or report.exists():
        raise ValueError("unique output required; resume/check existing run instead")
    observation = resources(True)
    if check_resources(contract.resources, observation)["status"] != "passed":
        raise ValueError("resource start gate failed; no label or fit access")
    artifact.mkdir(parents=True)
    report.mkdir(parents=True)
    write_json(report / "prepare-start-resources.json", observation)
    db = duckdb.connect()
    db.execute("SET threads=1")
    db.execute("SET memory_limit='256MB'")
    db.execute("SET temp_directory=?", [str(artifact / "duckdb-temp")])
    monitor = FeatureMonitor(contract.resources, report / "prepare-resources.json", db, settings.maximum_prepare_seconds)
    monitor.sample(False)
    thread = threading.Thread(target=monitor.loop, daemon=True)
    thread.start()
    started = time.perf_counter()
    try:
        for item in frozen["inventory"]:
            if item["root"] == "source":
                dst = artifact / "source-snapshot" / item["path"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / item["path"], dst)
                if _sha256(dst) != item["sha256"]:
                    raise ValueError("source drift during snapshot")
        window = frozen["data"]["windows"][0]
        for split, key in (("train", "train"), ("inner", "early_stop")):
            frame = targets(db, root / "data/interim/ieee-cis/transactions.parquet", window[key])
            frame.to_parquet(artifact / ("targets-"+split+".parquet"), index=False)
            monitor.require()
        legacy = prepare_legacy(db, root, artifact, window["train"], window["early_stop"], monitor.require)
        feature_artifact = Path(feature_manifest["artifact_directory"])
        for profile in ("core", "source_as_received"):
            filename = "fold-1-"+profile+".json"
            shutil.copyfile(feature_artifact / filename, artifact / filename)
        files = {p.name: binding(p) for p in artifact.iterdir() if p.is_file()}
        plan = {"schema_version": "stage09-5b-tabular-run-v1", "registration_directory": str(registration),
            "source_freeze_sha256": frozen["content_sha256"], "feature_directory": str(features),
            "feature_manifest_sha256": feature_manifest["content_sha256"], "side_path": str(feature_artifact / "features.parquet"),
            "artifact_directory": str(artifact), "settings": settings.model_dump(), "appendix_sha256": _content_hash(settings.model_dump()),
            "tasks": settings.tasks(contract), "windows": {"train": window["train"], "inner": window["early_stop"]},
            "prepared_files": files, "legacy": legacy, "feature_check": feature_check,
            "label_access": ["fold-1-train", "fold-1-early-stop"], "calibration_outer_test_access": False,
            "prepare_seconds": time.perf_counter()-started, "prepare_peak_working_set_mib": peak_working_set_mib()}
        check_registration(source, root, registration)
        monitor.finish(thread)
        plan["prepare_resource_binding"] = binding(monitor.path)
        write_json(report / "plan.json", sealed(plan))
    except Exception as error:
        write_json(report / "prepare-failure.json", {"error_type": type(error).__name__, "reason": str(error), "new_fits": 0})
        raise
    finally:
        monitor.stop.set()
        thread.join(timeout=5)
        db.close()
    return {"status": "prepared", "directory": str(report), "tasks": len(plan["tasks"]), "new_fits": 0}


def verify_plan(root, report):
    root, report = root.resolve(), report.resolve()
    if root.drive.upper() != "E:":
        raise ValueError("tabular run/check must use the E execution root")
    if not report.is_relative_to(root / "reports/generated/stage09/route5b"):
        raise ValueError("report path escapes E run root")
    plan = checked_json(report / "plan.json")
    artifact = Path(plan["artifact_directory"]).resolve()
    if not artifact.is_relative_to(root / "artifacts/stage-09/route5b"):
        raise ValueError("artifact path escape")
    frozen = checked_json(Path(plan["registration_directory"]) / "freeze.json")
    if frozen["content_sha256"] != plan["source_freeze_sha256"]:
        raise ValueError("source freeze binding changed")
    verify_inventory({"source": artifact / "source-snapshot", "execution": root}, frozen["inventory"])
    for name, expected in plan["prepared_files"].items():
        path = (artifact / name).resolve()
        if not path.is_relative_to(artifact) or not path.is_file() or binding(path) != expected:
            raise ValueError("prepared input drift:"+name)
    if binding(report / "prepare-resources.json") != plan["prepare_resource_binding"]:
        raise ValueError("prepare resource evidence changed")
    if check_outputs(root, Path(plan["feature_directory"]))["manifest_sha256"] != plan["feature_manifest_sha256"]:
        raise ValueError("wide feature binding changed")
    contract = load_contract(artifact / "source-snapshot/configs/stage09_model_enhancement.json")
    settings = TabularSettings.model_validate(plan["settings"])
    if plan["tasks"] != settings.tasks(contract) or plan["appendix_sha256"] != _content_hash(settings.model_dump()):
        raise ValueError("task/config drift")
    if not (root / contract.artifact_root / "budget-binding.json").is_file():
        raise ValueError("shared budget binding missing; cannot recreate during verification")
    _, shared = budget_binding(root, contract, artifact / "unused-fit-ledger.json")
    ledger = EnhancementLedger(Path(shared["ledger_path"]), contract, shared["origin_freeze_sha256"])
    return plan, contract, settings, ledger


def recovery_attempt_name(task, report, entries, repair):
    original = report.name+"-"+task["id"]
    previous = next((f for f in entries if f["name"] == original), None)
    if previous and previous["state"] == "failed" and repair:
        if task["id"] != repair.get("allowed_failed_task") or task["id"] not in {"x1", "x3"} or previous["family"] != "xgboost" or previous["lane"] != "search":
            raise ValueError("runtime recovery only registered for the bound failed search task")
        return original+"-retry-1", original
    return original, None


def register_recovery(root, report, plan, registration):
    registration = registration.resolve()
    artifact = Path(plan["artifact_directory"])
    source = Path(__file__).resolve().parents[3]
    check_registration(source, root, registration)
    fresh = checked_json(registration / "freeze.json")
    original = checked_json(Path(plan["registration_directory"]) / "freeze.json")
    old_files = {(i["root"],i["path"]):i["sha256"] for i in original["inventory"]}
    allowed = {"src/graphrisk/models/enhanced_execution.py", "scripts/run_stage09_enhanced_baselines.py",
               "tests/unit/test_stage09_tabular_recovery.py", "docs/data/stage-09-enhanced-baselines.md"}
    changed = [i for i in fresh["inventory"] if old_files.get((i["root"],i["path"])) != i["sha256"]]
    if not changed or any(i["root"] != "source" or i["path"] not in allowed for i in changed):
        raise ValueError("recovery revision changes model/data contract beyond registered runtime repair")
    ledger_failure = read_json(report / "x1-failure.json")
    if ledger_failure["error_type"] != "OutOfMemoryException" or (artifact / "x1.json").exists():
        raise ValueError("recovery requires pre-model DuckDB memory failure")
    path = report / "runtime-recovery.json"
    payload = {"original_plan_sha256": plan["content_sha256"], "registration_directory": str(registration),
        "freeze_sha256": fresh["content_sha256"], "changed_source_paths": [i["path"] for i in changed],
        "duckdb_memory_mib": 768, "model_parameters_changed": False, "data_or_label_windows_changed": False,
        "allowed_failed_task": "x1", "maximum_retries": 1}
    if path.exists():
        if checked_json(path) != sealed(payload):
            raise ValueError("recovery registration drift")
    else:
        for item in fresh["inventory"]:
            if item["root"] == "source":
                dst = artifact / "recovery-source-snapshot" / item["path"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / item["path"],dst)
                if _sha256(dst) != item["sha256"]:
                    raise ValueError("recovery source snapshot changed")
        write_json(path,sealed(payload))
    return checked_json(path)


def validate_host_pause(failure, evidence, parent, model_present):
    if model_present or parent["state"] != "failed" or parent["family"] != "xgboost" or parent["lane"] != "search" or parent["registration"].get("candidate_id") != "x3":
        raise ValueError("host recovery requires failed x3 without saved model")
    if failure["error_type"] != "TimeoutError" or failure["attempt"] != parent["name"] or failure["seconds"] <= parent["cap_seconds"]:
        raise ValueError("host recovery requires the bound deadline failure")
    if evidence.get("provider") != "Microsoft-Windows-Power-Troubleshooter" or evidence.get("event_id") != 1:
        raise ValueError("Windows sleep/resume evidence required")
    try:
        start = datetime.fromisoformat(parent["at"])
        sleep = datetime.fromisoformat(evidence["sleep_time_utc"].replace("Z", "+00:00"))
        wake = datetime.fromisoformat(evidence["wake_time_utc"].replace("Z", "+00:00"))
        duration = (wake-sleep).total_seconds()
        elapsed = (wake-start).total_seconds()
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("valid timezone-aware sleep/resume timestamps required") from error
    if not start < sleep < wake or duration <= parent["cap_seconds"] or abs(duration-evidence["sleep_seconds"]) > 1 or abs(elapsed-failure["seconds"]) > 120:
        raise ValueError("sleep event must explain this fit deadline, not an unrelated pause")


def register_host_resume(root, report, plan, registration, ledger):
    source = Path(__file__).resolve().parents[3]
    check_registration(source, root, registration)
    fresh = checked_json(registration / "freeze.json")
    runtime = checked_json(report / "runtime-recovery.json")
    previous = checked_json(Path(runtime["registration_directory"]) / "freeze.json")
    old_files = {(i["root"], i["path"]): i["sha256"] for i in previous["inventory"]}
    allowed = {"src/graphrisk/models/enhanced_execution.py", "scripts/run_stage09_enhanced_baselines.py",
               "tests/unit/test_stage09_tabular_recovery.py", "docs/data/stage-09-enhanced-baselines.md"}
    changed = [i for i in fresh["inventory"] if old_files.get((i["root"], i["path"])) != i["sha256"]]
    if not changed or any(i["root"] != "source" or i["path"] not in allowed for i in changed):
        raise ValueError("host recovery cannot change model/data contract")
    parent = next(f for f in ledger.read()["fits"] if f["name"] == report.name+"-x3")
    evidence_path = report / "host-sleep-evidence.json"
    failure_path = report / "x3-failure.json"
    artifact = Path(plan["artifact_directory"])
    validate_host_pause(read_json(failure_path), read_json(evidence_path), parent, (artifact / "x3.json").exists())
    path = report / "host-resume-recovery.json"
    payload = {"original_plan_sha256": plan["content_sha256"], "registration_directory": str(registration.resolve()),
        "freeze_sha256": fresh["content_sha256"], "changed_source_paths": [i["path"] for i in changed],
        "parent_runtime_recovery_sha256": runtime["content_sha256"], "evidence_binding": binding(evidence_path),
        "failure_binding": binding(failure_path), "model_parameters_changed": False, "data_or_label_windows_changed": False,
        "allowed_failed_task": "x3", "maximum_retries": 1, "wake_lock": "process_scoped_system_required_only"}
    if path.exists():
        if checked_json(path) != sealed(payload):
            raise ValueError("host recovery registration drift")
    else:
        for item in fresh["inventory"]:
            if item["root"] == "source":
                dst = artifact / "host-resume-source-snapshot" / item["path"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / item["path"], dst)
                if _sha256(dst) != item["sha256"]:
                    raise ValueError("host recovery source changed")
        write_json(path, sealed(payload))
    return checked_json(path)


@contextmanager
def training_awake(set_execution_state=None):
    if set_execution_state is None and sys.platform == "win32":
        import ctypes
        set_execution_state = ctypes.windll.kernel32.SetThreadExecutionState
    if set_execution_state is None:
        yield
        return
    if not set_execution_state(0x80000001):
        raise OSError("Windows training wake lock could not be acquired")
    try:
        yield
    finally:
        set_execution_state(0x80000000)


def run(root, report, recovery_registration=None, interruption_registration=None):
    if (report / "closure.json").exists():
        return check(root, report)
    with training_awake():
        return _run(root, report, recovery_registration, interruption_registration)


def _run(root, report, recovery_registration=None, interruption_registration=None):
    root, report = root.resolve(), report.resolve()
    plan, contract, settings, ledger = verify_plan(root, report)
    if (report / "closure.json").exists():
        return check(root, report)
    if ledger.read()["decision_sha256"] or ledger.read()["outer_access"] or ledger.read()["test_access"]:
        raise ValueError("development sealed; cannot resume searches")
    repair = register_recovery(root,report,plan,recovery_registration) if recovery_registration else checked_json(report / "runtime-recovery.json") if (report / "runtime-recovery.json").exists() else None
    interruption = register_host_resume(root, report, plan, interruption_registration, ledger) if interruption_registration else checked_json(report / "host-resume-recovery.json") if (report / "host-resume-recovery.json").exists() else None
    if recovery_registration is None and interruption_registration is None:
        registered = Path(interruption["registration_directory"]) if interruption else Path(repair["registration_directory"]) if repair else Path(plan["registration_directory"])
        check_registration(Path(__file__).resolve().parents[3],root,registered)
    artifact, side = Path(plan["artifact_directory"]), Path(plan["side_path"])
    train_frame, inner_frame = [pd.read_parquet(artifact / ("targets-"+split+".parquet")) for split in ("train", "inner")]
    db = open_db(root, side)
    observation = resources(True)
    if check_resources(contract.resources, observation)["status"] != "passed":
        db.close()
        raise ValueError("training resource start gate failed; no fit reserved")
    resource_name = "run-resources-"+str(time.time_ns())+".json"
    monitor = FeatureMonitor(contract.resources, report / resource_name, db, 8*3600)
    monitor.sample(False)
    thread = threading.Thread(target=monitor.loop, daemon=True)
    thread.start()
    matrices, previous_profile = None, None
    try:
        for task in plan["tasks"]:
            task_id = task["id"]
            task_repair = interruption if task_id == "x3" else repair
            attempt_name, retry_of = recovery_attempt_name(task,report,ledger.read()["fits"],task_repair)
            result_path = report / (task_id+".json")
            if result_path.exists():
                result = checked_json(result_path)
                prior = next((f for f in ledger.read()["fits"] if f["name"] == result.get("attempt_name", attempt_name)), None)
                if not prior or prior["state"] != "completed" or result["status"] != "completed":
                    raise ValueError("incomplete/failed fit requires explicit audit; no silent retrain")
                continue
            if any(f["name"] == attempt_name for f in ledger.read()["fits"]):
                raise ValueError("attempt already consumed without result; audit before recovery")
            monitor.require()
            task_binding = _content_hash({"plan": plan["content_sha256"], "task": task})
            registration = {"input_sha256": task_binding, "fold": "fold-1", "seed": settings.seed,
                "appendix_sha256": plan["appendix_sha256"], "candidate_id": task_id, "control_id": task["profile"]}
            if retry_of:
                registration["retry_of"] = retry_of
            attempt = ledger.reserve(task["family"], "failure" if retry_of else task["lane"], attempt_name, registration)
            def guard():
                monitor.require()
                attempt.guard()
            print(json.dumps({"task": task_id, "state": "reserved", "profile": task["profile"], "family": task["family"]}), flush=True)
            started = time.perf_counter()
            try:
                factory = lambda split: lambda: batches(db, root, artifact, side, task["profile"], train_frame if split == "train" else inner_frame, split, guard)
                if task["family"] == "xgboost":
                    if task["profile"] != previous_profile:
                        matrices = None
                        gc.collect()
                        dtrain = quantile(factory("train"), len(train_frame), settings, guard)
                        dinner = quantile(factory("inner"), len(inner_frame), settings, guard, dtrain)
                        matrices = (dtrain, dinner)
                        previous_profile = task["profile"]
                    model, detail = train_xgb(task, settings, *matrices, guard)
                    model_path = artifact / (task_id+".json")
                    model.save_model(model_path)
                    scores = predict_xgb(model, factory("inner"), guard)
                    loaded = xgb.Booster()
                    loaded.load_model(model_path)
                    replay = predict_xgb(loaded, factory("inner"), guard)
                    del model, loaded
                else:
                    matrices = None
                    previous_profile = None
                    gc.collect()
                    import torch
                    from graphrisk.graph.stage09_baselines import fit_mlp, RootMLP
                    tx, vx = [np.load(artifact / ("root-"+s+".npy")) for s in ("train", "inner")]
                    model, scores, detail = fit_mlp(tx, train_frame.is_fraud.to_numpy(dtype=np.float32), vx, inner_frame.is_fraud.to_numpy(dtype=np.float32), {"seed": settings.seed, "cpu_threads": settings.threads, "mlp": settings.mlp.model_dump()}, guard)
                    model_path = artifact / (task_id+".pt")
                    torch.save({"state_dict": model.state_dict(), "input_sha256": task_binding, "parameters": settings.mlp.model_dump()}, model_path)
                    checkpoint = torch.load(model_path, map_location="cpu", weights_only=True)
                    loaded = RootMLP(28, settings.mlp.hidden_channels, settings.mlp.dropout)
                    loaded.load_state_dict(checkpoint["state_dict"])
                    loaded.eval()
                    with torch.no_grad():
                        replay = torch.sigmoid(loaded(torch.from_numpy(vx))).numpy()
                    del tx, vx, model, loaded
                delta = float(np.max(np.abs(scores-replay)))
                if delta > settings.prediction_tolerance:
                    raise ValueError("saved model reload predictions differ")
                guard()
                result_metrics = metrics(inner_frame.transaction_id.tolist(), inner_frame.is_fraud.to_numpy(), scores)
                prediction_path = artifact / (task_id+"-predictions.parquet")
                pd.DataFrame({"transaction_id": inner_frame.transaction_id, "score": scores}).to_parquet(prediction_path, index=False)
                seconds = time.perf_counter()-started
                result = {"status": "completed", "task": task, "attempt_name": attempt_name, "input_sha256": task_binding, "plan_sha256": plan["content_sha256"],
                    "metrics": result_metrics, "fit_seconds": seconds, "reload_max_absolute_delta": delta,
                    "model_path": model_path.name, "model_sha256": _sha256(model_path),
                    "prediction_file": prediction_path.name, "prediction_binding": binding(prediction_path), "training": detail}
                guard()
                write_json(result_path, sealed(result))
                attempt.finish("completed")
                print(json.dumps({"task": task_id, "state": "completed", "seconds": seconds, "metrics": result_metrics}), flush=True)
            except Exception as error:
                if next(f for f in ledger.read()["fits"] if f["name"] == attempt_name)["state"] == "reserved":
                    attempt.finish("failed")
                filename = task_id+"-retry-1-failure.json" if retry_of else task_id+"-failure.json"
                write_json(report / filename, {"error_type": type(error).__name__, "reason": str(error), "attempt": attempt_name, "seconds": time.perf_counter()-started})
                raise
        monitor.finish(thread)
        results = {t["id"]: checked_json(report / (t["id"]+".json")) for t in plan["tasks"]}
        choice = select_candidate({c.id: results[c.id] for c in contract.xgboost_candidates}, [c.id for c in contract.xgboost_candidates])
        write_json(report / "baseline-choice.json", choice)
        outputs = {p.name: binding(p) for p in artifact.iterdir() if p.is_file()}
        reports = {p.name: binding(p) for p in report.iterdir() if p.is_file()}
        attempts = [f for f in ledger.read()["fits"] if f["name"].startswith(report.name+"-")]
        closure = {"status": "passed", "plan_sha256": plan["content_sha256"], "artifact_files": outputs, "report_files": reports,
            "choice_sha256": choice["content_sha256"], "process_peak_working_set_mib": peak_working_set_mib(),
            "new_real_xgboost_fits": sum(f["family"]=="xgboost" for f in attempts), "new_real_mlp_fits": sum(f["family"]=="mlp" for f in attempts), "failed_attempts": sum(f["state"]=="failed" for f in attempts), "new_gnn_fits": 0, "new_generation_calls": 0, "paid_calls": 0,
            "outer_quality": "not_evaluated", "service_switched": False}
        write_json(report / "closure.json", sealed(closure))
    finally:
        monitor.stop.set()
        thread.join(timeout=5)
        db.close()
    return check(root, report)


def check(root, report):
    root, report = root.resolve(), report.resolve()
    plan, contract, settings, ledger = verify_plan(root, report)
    closure = checked_json(report / "closure.json")
    if closure["plan_sha256"] != plan["content_sha256"] or closure["status"] != "passed":
        raise ValueError("closure mismatch")
    artifact = Path(plan["artifact_directory"])
    if (report / "runtime-recovery.json").exists():
        repair = checked_json(report / "runtime-recovery.json")
        fresh = checked_json(Path(repair["registration_directory"]) / "freeze.json")
        if repair["original_plan_sha256"] != plan["content_sha256"] or fresh["content_sha256"] != repair["freeze_sha256"]:
            raise ValueError("recovery closure binding changed")
        verify_inventory({"source": artifact / "recovery-source-snapshot", "execution": root},fresh["inventory"])
    if (report / "host-resume-recovery.json").exists():
        interruption = checked_json(report / "host-resume-recovery.json")
        fresh = checked_json(Path(interruption["registration_directory"]) / "freeze.json")
        if interruption["original_plan_sha256"] != plan["content_sha256"] or fresh["content_sha256"] != interruption["freeze_sha256"] or interruption["parent_runtime_recovery_sha256"] != repair["content_sha256"]:
            raise ValueError("host recovery closure binding changed")
        for filename, key in (("host-sleep-evidence.json", "evidence_binding"), ("x3-failure.json", "failure_binding")):
            if binding(report / filename) != interruption[key]:
                raise ValueError("host recovery evidence changed")
        verify_inventory({"source": artifact / "host-resume-source-snapshot", "execution": root}, fresh["inventory"])
    for base, items in ((artifact, closure["artifact_files"]), (report, closure["report_files"])):
        for name, expected in items.items():
            path = (base / name).resolve()
            if not path.is_relative_to(base) or not path.is_file() or binding(path) != expected:
                raise ValueError("closure file drift:"+name)
    for path in report.glob("*resources*.json"):
        observations = read_json(path)
        if path.name.endswith("start-resources.json"):
            continue
        if not observations or any(not continuous_ok(contract.resources, s) for s in observations):
            raise ValueError("failed resource observations")
    value = ledger.read()
    for task in plan["tasks"]:
        result = checked_json(report / (task["id"]+".json"))
        entry = next((f for f in value["fits"] if f["name"] == result.get("attempt_name",report.name+"-"+task["id"])), None)
        if not entry or entry["state"] != "completed":
            raise ValueError("fit completion ledger mismatch")
    choice = checked_json(report / "baseline-choice.json")
    if choice["content_sha256"] != closure["choice_sha256"]:
        raise ValueError("choice drift")
    return {"status": "passed", "baseline_candidate": choice["candidate"], "tasks": len(plan["tasks"]),
        "shared_fits_used": len(value["fits"]), "outer_access": len(value["outer_access"]), "test_access": len(value["test_access"]),
        "decision_frozen": bool(value["decision_sha256"]), "read_only": True, "closure_sha256": closure["content_sha256"]}
