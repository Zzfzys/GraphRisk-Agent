"""Frozen E-only expansion experiments with the existing non-refundable ledger."""
import gc
import json
import re
import shutil
import threading
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import torch

from graphrisk.agent.stage09_preflight import _content_hash, check_resources
from graphrisk.demo.stage09_evaluation import read_json, resources, write_json
from graphrisk.features.wide_execution import FeatureMonitor, peak_working_set_mib
from graphrisk.graph.stage09_enhancement import EnhancementLedger, load_contract
from graphrisk.graph.stage09_enhancement_preflight import budget_binding, check_registration, verify_inventory
from graphrisk.graph.stage09_expansion_data import project_source, fit_encoders, build_scale_caches, BoundedPackedGraphs
from graphrisk.graph.stage09_expansion_training import train, choose
from graphrisk.graph.stage09_monitor import DiagnosisMonitor
from graphrisk.graph.stage09_packed import PackedGraphs, verify_cache
from graphrisk.models.enhanced_execution import binding, checked_json, sealed, training_awake, verify_plan as tabular_plan
from graphrisk.models.enhanced_training import metrics


def settings_for(source, contract):
    settings = read_json(source / "configs/stage09_graph_expansion.json")
    expected = {"schema_version":"stage09-5b-graph-expansion-v1","device":"cuda","cpu_threads":4,"batch_size":256,
        "history_per_entity":5,"max_epochs":50,"patience":10,"scale_seed":20260921,"search_seed":20260928,
        "train_probe_count":10000,"maximum_prepare_seconds":7200,
        "scale_selection":"earliest_prefix_move_cut_left_over_equal_times","encoder_fit":"each_scale_training_roots_only",
        "selection":"inner_ap_then_seconds_then_registered_order",
        "conditional_trigger":"best_current_inner_ap_below_selected_wide_xgboost",
        "label_access":["fold-1-train","fold-1-early-stop"]}
    if settings != expected or settings["max_epochs"] != contract.training_rules.max_epochs:
        raise ValueError("graph expansion appendix differs from registered finite experiment")
    return settings


def tasks_for(contract, settings):
    base = contract.gnn_candidates[0].model_dump()
    return [{"id":"scale-"+str(size),"lane":"scale","scale":str(size),"scale_target":size,
             "parameters":base,"seed":settings["scale_seed"]} for size in contract.gnn_scale_targets] + [
            {"id":c.id,"lane":"search","scale":"full_allowed_train","candidate_id":c.id,
             "parameters":c.model_dump(),"seed":settings["search_seed"]} for c in contract.gnn_candidates]


def idle_start(monitor,budget,sleep=time.sleep):
    """At most five probes; wait only for GPU cooldown, never relax any floor."""
    for position in range(5):
        observed = monitor.sample(True)
        check = check_resources(budget,observed)
        if check["status"] == "passed":
            return observed
        if check["violations"] != ["resource_limit:gpu_utilization_percent"] or position == 4:
            raise ValueError("graph start resource floor; no new attempt reserved")
        sleep(2)
    raise AssertionError("bounded idle probes exhausted")


def prepare(source, root, registration, tabular, name):
    source,root,registration,tabular = [p.resolve() for p in (source,root,registration,tabular)]
    if root.drive.upper() != "E:" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}",name):
        raise ValueError("E root and safe unique experiment name required")
    contract = load_contract(source / "configs/stage09_model_enhancement.json")
    settings = settings_for(source,contract)
    check_registration(source,root,registration)
    frozen = checked_json(registration / "freeze.json")
    if not any(i["root"] == "source" and i["path"] == "configs/stage09_graph_expansion.json" for i in frozen["inventory"]):
        raise ValueError("graph expansion appendix must be frozen before work")
    baseline,_,_,ledger = tabular_plan(root,tabular)
    if not (tabular / "closure.json").exists():
        raise ValueError("Route2 completed closure required")
    # Bind the completed baseline result, not just its mutable directory name.
    baseline_closure = checked_json(tabular / "closure.json")
    choice = checked_json(tabular / "baseline-choice.json")
    state = ledger.read()
    if state["decision_sha256"] or state["outer_access"] or state["test_access"] or any(f["state"] == "reserved" for f in state["fits"]):
        raise ValueError("candidate phase already sealed or unresolved fit")
    observation = resources(True)
    if check_resources(contract.resources,observation)["status"] != "passed" or not torch.cuda.is_available():
        raise ValueError("CUDA/resource start gate failed; no preparation or fit")
    artifact,report = root / contract.artifact_root / name, root / contract.output_root / name
    if artifact.exists() or report.exists():
        raise ValueError("fresh output required; resume/check existing experiment")
    artifact.mkdir(parents=True)
    report.mkdir(parents=True)
    write_json(report / "prepare-start-resources.json",observation)
    db = duckdb.connect()
    db.execute("SET threads=1")
    db.execute("SET memory_limit='768MB'")
    db.execute("SET temp_directory=?",[str(artifact / "duckdb-temp")])
    monitor = FeatureMonitor(contract.resources,report / "prepare-resources.json",db,settings["maximum_prepare_seconds"])
    monitor.sample(False)
    thread = threading.Thread(target=monitor.loop,daemon=True)
    thread.start()
    started = time.perf_counter()
    try:
        with training_awake():
            for item in frozen["inventory"]:
                if item["root"] == "source":
                    path = artifact / "source-snapshot" / item["path"]
                    path.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(source / item["path"],path)
            original = Path(baseline["artifact_directory"])
            for split in ("train","inner"):
                shutil.copyfile(original / ("targets-"+split+".parquet"),artifact / ("targets-"+split+".parquet"))
            train_targets = pd.read_parquet(artifact / "targets-train.parquet")
            inner_targets = pd.read_parquet(artifact / "targets-inner.parquet")
            projected = artifact / "projected.parquet"
            project_source(db,root,projected,baseline["windows"]["inner"]["max_relative_seconds"])
            monitor.require()
            encoders,counts = fit_encoders(db,projected,train_targets,contract.gnn_scale_targets)
            old_encoder = read_json(original / "root-encoder.json")
            from dataclasses import asdict
            full = json.loads(json.dumps(asdict(encoders["full_allowed_train"])))
            if full != old_encoder["encoder"]:
                raise ValueError("full-scale train-only encoder differs from Route2 matched control")
            roots = {"train":np.load(original / "root-train.npy",mmap_mode="r"),
                     "validation":np.load(original / "root-inner.npy",mmap_mode="r")}
            caches = build_scale_caches(projected,artifact / "caches",train_targets,inner_targets,encoders,counts,monitor.require,roots)
            del roots
            scales = {}
            for scale,count in counts.items():
                frame = train_targets.iloc[:count]
                end = int(frame.source_transaction_dt_seconds.iloc[-1])
                scales[scale] = {"train_count":count,"positive_count":int(frame.is_fraud.sum()),
                    "min_relative_seconds":int(frame.source_transaction_dt_seconds.iloc[0]),"max_relative_seconds":end,
                    "gap_to_inner_seconds":int(inner_targets.source_transaction_dt_seconds.iloc[0])-end,
                    "ids_sha256":_content_hash({"ids":frame.transaction_id.tolist()}),"cache_sha256":caches[scale]}
            monitor.finish(thread)
            check_registration(source,root,registration)
            plan = {"schema_version":"stage09-5b-graph-expansion-run-v1","registration_directory":str(registration),
                "source_freeze_sha256":frozen["content_sha256"],"artifact_directory":str(artifact),
                "tabular_directory":str(tabular),"tabular_plan_sha256":baseline["content_sha256"],
                "tabular_closure_sha256":baseline_closure["content_sha256"],"baseline_choice_sha256":choice["content_sha256"],
                "settings":settings,"tasks":tasks_for(contract,settings),"windows":baseline["windows"],"scales":scales,
                "prepared_files":{p.name:binding(p) for p in artifact.iterdir() if p.is_file()},
                "prepare_resource_binding":binding(monitor.path),"prepare_seconds":time.perf_counter()-started,
                "prepare_peak_working_set_mib":peak_working_set_mib(),"new_fits_during_prepare":0,
                "label_access":["fold-1-train","fold-1-early-stop"],"calibration_outer_test_access":False}
            write_json(report / "plan.json",sealed(plan))
    except Exception as error:
        write_json(report / "prepare-failure.json",{"error_type":type(error).__name__,"reason":str(error),"new_fits":0})
        raise
    finally:
        monitor.stop.set()
        thread.join(timeout=5)
        db.close()
    return {"status":"prepared","directory":str(report),"scales":scales,"planned_real_fits":9}


def verify_plan(root,report):
    root,report = root.resolve(),report.resolve()
    if root.drive.upper() != "E:" or not report.is_relative_to(root / "reports/generated/stage09/route5b"):
        raise ValueError("graph expansion must remain in registered E root")
    plan = checked_json(report / "plan.json")
    artifact = Path(plan["artifact_directory"])
    if not artifact.resolve().is_relative_to(root / "artifacts/stage-09/route5b"):
        raise ValueError("graph artifact path escape")
    frozen = checked_json(Path(plan["registration_directory"]) / "freeze.json")
    if frozen["content_sha256"] != plan["source_freeze_sha256"]:
        raise ValueError("graph source freeze drift")
    verify_inventory({"source":artifact / "source-snapshot","execution":root},frozen["inventory"])
    contract = load_contract(artifact / "source-snapshot/configs/stage09_model_enhancement.json")
    settings = settings_for(artifact / "source-snapshot",contract)
    if plan["settings"] != settings or plan["tasks"] != tasks_for(contract,settings):
        raise ValueError("graph task/config drift")
    for name,item in plan["prepared_files"].items():
        path = (artifact / name).resolve()
        if not path.is_relative_to(artifact) or binding(path) != item:
            raise ValueError("graph input bytes changed:"+name)
    if binding(report / "prepare-resources.json") != plan["prepare_resource_binding"]:
        raise ValueError("graph prepare resource evidence drift")
    baseline,_,_,ledger = tabular_plan(root,Path(plan["tabular_directory"]))
    if baseline["content_sha256"] != plan["tabular_plan_sha256"] or checked_json(Path(plan["tabular_directory"]) / "closure.json")["content_sha256"] != plan["tabular_closure_sha256"] or checked_json(Path(plan["tabular_directory"]) / "baseline-choice.json")["content_sha256"] != plan["baseline_choice_sha256"]:
        raise ValueError("matched baseline binding drift")
    manifests = {}
    for scale,details in plan["scales"].items():
        manifest = verify_cache(artifact / "caches" / scale)
        if manifest["content_sha256"] != details["cache_sha256"] or manifest["encoder_fit_ids_sha256"] != details["ids_sha256"]:
            raise ValueError("graph cache/encoder window drift")
        manifests[scale] = manifest
    return plan,contract,ledger,manifests


def register_memory_recovery(root,report,plan,registration):
    """One frozen runtime-only repair; preserve failed fit and every input/parameter."""
    source = Path(__file__).resolve().parents[3]
    registration = registration.resolve()
    check_registration(source,root,registration)
    fresh = checked_json(registration / "freeze.json")
    original = checked_json(Path(plan["registration_directory"]) / "freeze.json")
    old = {(i["root"],i["path"]):i["sha256"] for i in original["inventory"]}
    allowed = {"src/graphrisk/graph/stage09_expansion_data.py","src/graphrisk/graph/stage09_expansion_execution.py",
               "scripts/run_stage09_graph_expansion.py","tests/integration/test_stage09_expansion_data.py",
               "docs/data/stage-09-graph-expansion.md","tests/unit/test_stage09_graph_expansion.py"}
    changed = [i for i in fresh["inventory"] if old.get((i["root"],i["path"])) != i["sha256"]]
    if not changed or any(i["root"] != "source" or i["path"] not in allowed for i in changed):
        raise ValueError("memory recovery changes model/data contract beyond bounded mapping repair")
    failure = read_json(report / "scale-150000-failure.json")
    if failure["reason"] != "resource_floor_reached; stop before next request/case" or (report / "scale-150000.json").exists():
        raise ValueError("memory recovery requires bound failed 150k fit")
    evidence = read_json(report / "scale-150000-resources.json")
    if not any(r.get("available_ram_gib",99) < 3 for r in evidence):
        raise ValueError("failed fit lacks RAM-floor evidence")
    path = report / "runtime-recovery.json"
    artifact = Path(plan["artifact_directory"])
    payload = {"plan_sha256":plan["content_sha256"],"registration_directory":str(registration),
        "source_freeze_sha256":fresh["content_sha256"],"allowed_failed_task":"scale-150000","maximum_retries":1,
        "reader":"batch_scoped_mapping_owned_pyg_tensors","model_parameters_changed":False,"label_windows_changed":False,
        "cache_bytes_changed":False,"changed_source_paths":[i["path"] for i in changed]}
    if path.exists():
        if checked_json(path) != sealed(payload):
            raise ValueError("memory recovery registration already bound differently")
        return
    for item in fresh["inventory"]:
        if item["root"] == "source":
            destination = artifact / "runtime-source-snapshot" / item["path"]
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source / item["path"],destination)
    verify_inventory({"source":artifact / "runtime-source-snapshot","execution":root},fresh["inventory"])
    write_json(path,sealed(payload))


def runtime_recovery(root,report,plan):
    if not (report / "runtime-recovery.json").exists():
        return None
    repair = checked_json(report / "runtime-recovery.json")
    frozen = checked_json(Path(repair["registration_directory"]) / "freeze.json")
    if repair["plan_sha256"] != plan["content_sha256"] or frozen["content_sha256"] != repair["source_freeze_sha256"]:
        raise ValueError("memory recovery source/input drift")
    verify_inventory({"source":Path(plan["artifact_directory"]) / "runtime-source-snapshot","execution":root},frozen["inventory"])
    return repair


def register_resource_recovery(root,report,plan,registration):
    """One same-parameter retry after the externally observed g2 RAM stop."""
    source = Path(__file__).resolve().parents[3]
    registration = registration.resolve()
    check_registration(source,root,registration)
    previous = runtime_recovery(root,report,plan)
    if previous is None:
        raise ValueError("bounded reader repair must already be registered")
    old = checked_json(Path(previous["registration_directory"]) / "freeze.json")
    fresh = checked_json(registration / "freeze.json")
    hashes = {(i["root"],i["path"]):i["sha256"] for i in old["inventory"]}
    changed = [i for i in fresh["inventory"] if hashes.get((i["root"],i["path"])) != i["sha256"]]
    allowed = {"src/graphrisk/graph/stage09_expansion_execution.py","scripts/run_stage09_graph_expansion.py",
               "tests/unit/test_stage09_graph_expansion.py","docs/data/stage-09-graph-expansion.md"}
    if not changed or any(i["root"] != "source" or i["path"] not in allowed for i in changed):
        raise ValueError("g2 recovery changes model, reader or data contract")
    failure = read_json(report / "g2-failure.json")
    if failure["reason"] != "resource_floor_reached; stop before next request/case" or (report / "g2.json").exists():
        raise ValueError("g2 recovery requires the bound incomplete RAM failure")
    evidence = read_json(report / "g2-resources.json")
    if not any(r.get("available_ram_gib",99) < 3 for r in evidence):
        raise ValueError("g2 failure lacks RAM-floor evidence")
    path = report / "resource-recovery.json"
    payload = {"plan_sha256":plan["content_sha256"],"registration_directory":str(registration),
        "source_freeze_sha256":fresh["content_sha256"],"runtime_recovery_sha256":previous["content_sha256"],
        "allowed_failed_task":"g2","maximum_retries":1,"model_parameters_changed":False,
        "label_windows_changed":False,"cache_bytes_changed":False,"reader_changed":False,
        "changed_source_paths":[i["path"] for i in changed]}
    if path.exists():
        if checked_json(path) != sealed(payload):
            raise ValueError("g2 resource recovery already bound differently")
        return
    artifact = Path(plan["artifact_directory"])
    for item in fresh["inventory"]:
        if item["root"] == "source":
            target = artifact / "resource-source-snapshot" / item["path"]
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source / item["path"],target)
    verify_inventory({"source":artifact / "resource-source-snapshot","execution":root},fresh["inventory"])
    write_json(path,sealed(payload))


def resource_recovery(root,report,plan):
    if not (report / "resource-recovery.json").exists():
        return None
    repair = checked_json(report / "resource-recovery.json")
    frozen = checked_json(Path(repair["registration_directory"]) / "freeze.json")
    if (repair["plan_sha256"] != plan["content_sha256"] or frozen["content_sha256"] != repair["source_freeze_sha256"]
            or checked_json(report / "runtime-recovery.json")["content_sha256"] != repair["runtime_recovery_sha256"]):
        raise ValueError("g2 resource recovery source/input drift")
    verify_inventory({"source":Path(plan["artifact_directory"]) / "resource-source-snapshot","execution":root},frozen["inventory"])
    return repair


def retry_binding(task,previous,repairs):
    if previous is None:
        return None
    repair = next((r for r in repairs if r and r["allowed_failed_task"] == task["id"]),None)
    if not repair or previous["state"] != "failed":
        raise ValueError("graph task already attempted without completed result; audit failed/abandoned fit")
    return repair


def run(root,report,recovery_registration=None,resource_registration=None):
    plan,contract,ledger,manifests = verify_plan(root,report)
    artifact = Path(plan["artifact_directory"])
    # A new code revision cannot silently resume an existing training plan.
    if recovery_registration:
        register_memory_recovery(root,report,plan,recovery_registration)
    repair = runtime_recovery(root,report,plan)
    if resource_registration:
        register_resource_recovery(root,report,plan,resource_registration)
    extra = resource_recovery(root,report,plan)
    current = extra or repair
    check_registration(Path(__file__).resolve().parents[3],root,Path(current["registration_directory"] if current else plan["registration_directory"]))
    if (report / "closure.json").exists():
        return check(root,report)
    targets_train = pd.read_parquet(artifact / "targets-train.parquet")
    targets_inner = pd.read_parquet(artifact / "targets-inner.parquet")
    results = {}
    for task in plan["tasks"]:
        output = report / (task["id"]+".json")
        if output.exists():
            result = checked_json(output)
            entry = next((f for f in ledger.read()["fits"] if f["name"] == result["attempt_name"]),None)
            if result["plan_sha256"] != plan["content_sha256"] or result["task"] != task or not entry or entry["state"] != "completed":
                raise ValueError("completed graph result/ledger binding drift")
            for name,item in result["files"].items():
                model_directory = artifact / result.get("model_directory",task["id"])
                path = (model_directory / name).resolve()
                if not path.is_relative_to(model_directory) or binding(path) != item:
                    raise ValueError("completed graph output bytes drift")
            if binding(report / result.get("resource_file",task["id"]+"-resources.json")) != result["resources"]:
                raise ValueError("completed graph resource evidence drift")
            results[task["id"]] = result
            continue
        attempt_name = report.name+"-"+task["id"]
        previous = next((f for f in ledger.read()["fits"] if f["name"] == attempt_name),None)
        retry = retry_binding(task,previous,[repair,extra])
        parent_name = attempt_name
        if retry:
            attempt_name += "-retry-1"
        if any(f["name"] == attempt_name for f in ledger.read()["fits"]):
            raise ValueError("graph recovery already attempted without completed result; audit before further work")
        model_name = task["id"]+("-retry-1" if retry else "")
        resource_file = model_name+"-resources.json"
        monitor = DiagnosisMonitor(contract.resources,report / resource_file)
        initial = idle_start(monitor,contract.resources)
        directory = artifact / model_name
        directory.mkdir(exist_ok=False)
        registration = {"fold":"fold-1","seed":task["seed"],"input_sha256":plan["content_sha256"],
                        "appendix_sha256":_content_hash(plan["settings"])}
        registration.update({k:task[k] for k in ("candidate_id","scale_target") if k in task})
        if retry:
            registration["retry_of"] = parent_name
        attempt = ledger.reserve("gnn","failure" if retry else task["lane"],attempt_name,registration)
        thread = threading.Thread(target=monitor.loop,daemon=True)
        thread.start()
        def guard():
            monitor.require()
            attempt.guard()
        started = time.perf_counter()
        try:
            with training_awake():
                manifest = manifests[task["scale"]]
                folder = artifact / "caches" / task["scale"]
                reader = BoundedPackedGraphs if repair else PackedGraphs
                train_data = reader(folder,manifest,5,"train","full")
                inner_data = reader(folder,manifest,5,"validation","full")
                count = plan["scales"][task["scale"]]["train_count"]
                if train_data.ids != tuple(targets_train.transaction_id.iloc[:count]) or inner_data.ids != tuple(targets_inner.transaction_id):
                    raise ValueError("graph/target labels misaligned")
                result = train(train_data,inner_data,targets_train.is_fraud.to_numpy()[:count],targets_inner.is_fraud.to_numpy(),task,plan["settings"],directory,guard)
                del train_data,inner_data
                gc.collect()
                torch.cuda.empty_cache()
                monitor.stop.set()
                thread.join(timeout=5)
                if thread.is_alive():
                    raise ValueError("graph resource monitor did not stop")
                monitor.sample(True)
                guard()
                elapsed = time.perf_counter()-started
                result.update({"plan_sha256":plan["content_sha256"],"task":task,"attempt_name":attempt_name,
                    "fit_seconds":elapsed,"model_directory":model_name,"runtime_recovery_sha256":repair["content_sha256"] if repair else None,
                    "resource_recovery_sha256":extra["content_sha256"] if extra else None,
                    "peak_working_set_mib":peak_working_set_mib(),
                    "files":{p.name:binding(p) for p in directory.iterdir() if p.is_file()},"resources":binding(monitor.path),"resource_file":resource_file})
                attempt.finish("completed")
                write_json(output,sealed(result))
                results[task["id"]] = result
                print(json.dumps({"completed":task["id"],"metrics":result["metrics"],"fit_seconds":elapsed},ensure_ascii=False),flush=True)
        except Exception as error:
            attempt.finish("failed")
            write_json(report / (model_name+"-failure.json"),{"error_type":type(error).__name__,"reason":str(error),"attempt_name":attempt_name,
                "elapsed_seconds":time.perf_counter()-started,"partial_directory":str(directory)})
            raise
        finally:
            monitor.stop.set()
            thread.join(timeout=5)
            gc.collect()
            torch.cuda.empty_cache()
    selected = choose(results,plan["tasks"])
    baseline_choice = checked_json(Path(plan["tabular_directory"]) / "baseline-choice.json")
    # Read the already-authorized inner metrics; do not fit fusion or read calibration labels.
    baseline = checked_json(Path(plan["tabular_directory"]) / (baseline_choice["candidate"]+".json"))
    threshold = baseline["metrics"]["ap"]
    all_best = max(result["metrics"]["ap"] for result in results.values())
    decision = {"plan_sha256":plan["content_sha256"],"selected_current_candidate":selected,
                "selected_inner_metrics":results[selected]["metrics"],"strong_baseline_inner_ap":threshold,
                "best_any_current_inner_ap":all_best,"conditional_v2_triggered":all_best < threshold,
                "trigger_evidence":"inner AP only; no fusion claim or outer/test access",
                "final_model_decision":False,"default_service_changed":False}
    write_json(report / "candidate-choice.json",sealed(decision))
    # Bind completed run evidence; source snapshot is verified by its original inventory.
    files = {}
    for base in (artifact,report):
        for path in base.rglob("*"):
            if path.is_file() and not {"source-snapshot","runtime-source-snapshot","resource-source-snapshot"}.intersection(path.parts) and path.name != "closure.json" and "duckdb-temp" not in path.parts:
                files[path.relative_to(root).as_posix()] = binding(path)
    closure = {"plan_sha256":plan["content_sha256"],"files":files,"engineering_status":"done",
               "completed_real_fits":len(results),"outer_test_access":False,"new_generation_calls":0,"new_paid_calls":0}
    write_json(report / "closure.json",sealed(closure))
    return check(root,report)


def check(root,report):
    plan,contract,ledger,manifests = verify_plan(root,report)
    runtime_recovery(root,report,plan)
    resource_recovery(root,report,plan)
    closure = checked_json(report / "closure.json")
    if closure["plan_sha256"] != plan["content_sha256"] or closure["completed_real_fits"] != len(plan["tasks"]):
        raise ValueError("incomplete graph closure")
    for name,item in closure["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or binding(path) != item:
            raise ValueError("graph closure bytes drift:"+name)
    artifact = Path(plan["artifact_directory"])
    targets = pd.read_parquet(artifact / "targets-inner.parquet")
    results,entries = {},ledger.read()["fits"]
    for task in plan["tasks"]:
        result = checked_json(report / (task["id"]+".json"))
        entry = next((f for f in entries if f["name"] == result["attempt_name"]),None)
        if result["plan_sha256"] != plan["content_sha256"] or result["task"] != task or not entry or entry["state"] != "completed":
            raise ValueError("graph result/ledger drift")
        scores = np.load(artifact / result.get("model_directory",task["id"]) / "inner-scores.npy",allow_pickle=False)
        recomputed = metrics(targets.transaction_id.tolist(),targets.is_fraud.to_numpy(),scores)
        if recomputed != result["metrics"] or result["reload_rows"] != len(targets) or result["reload_max_abs_delta"] > 1e-6:
            raise ValueError("independent inner metric/reload check failed")
        results[task["id"]] = result
    winner = choose(results,plan["tasks"])
    choice = checked_json(report / "candidate-choice.json")
    if choice["selected_current_candidate"] != winner or choice["selected_inner_metrics"] != results[winner]["metrics"]:
        raise ValueError("graph candidate selection drift")
    state = ledger.read()
    if state["outer_access"] or state["test_access"] or state["decision_sha256"]:
        raise ValueError("unexpected final decision or outer/test access in development route")
    return {"status":"passed","completed_real_fits":len(results),"selected_candidate":winner,
            "selected_inner_metrics":results[winner]["metrics"],"conditional_v2_triggered":choice["conditional_v2_triggered"],
            "shared_fit_count":len(state["fits"]),"gnn_seconds":sum(f["seconds"] for f in state["fits"] if f["family"] == "gnn"),
            "outer_access_count":0,"test_access_count":0,"default_service_changed":False}
