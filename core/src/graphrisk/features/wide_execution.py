"""E-drive-only Route1 wrapper, resource evidence and read-only artifact checks."""
from __future__ import annotations

import ctypes
import json
import re
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from graphrisk.agent.stage09_preflight import _content_hash, _sha256, check_resources
from graphrisk.demo.stage09_evaluation import continuous_ok, read_json, resources, write_json
from graphrisk.features.wide_contract import FeatureContract
from graphrisk.features.wide_pipeline import audit_history, build_side_table, fit_fold_profiles, verify_transform_batches
from graphrisk.graph.stage09_enhancement import load_contract
from graphrisk.graph.stage09_enhancement_preflight import check_registration, verify_inventory
from graphrisk.graph.stage09_monitor import DiagnosisMonitor


def peak_working_set_mib():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
            (n, ctypes.c_size_t) for n in ("peak", "working", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1), ctypes.byref(counters), counters.cb):
        raise OSError("process peak working set measurement unavailable")
    return counters.peak / 2**20


class FeatureMonitor(DiagnosisMonitor):
    def __init__(self, budget, path, db, max_seconds):
        super().__init__(budget, path)
        self.db, self.started, self.max_seconds = db, time.perf_counter(), max_seconds

    def loop(self):
        while not self.stop.wait(2):
            self.sample(False)
            if self.violation.is_set() or time.perf_counter() - self.started >= self.max_seconds:
                self.violation.set()
                self.db.interrupt()
                return

    def require(self):
        super().require()
        if time.perf_counter() - self.started >= self.max_seconds:
            raise TimeoutError("Route1 pipeline deadline reached")

    def finish(self, thread):
        self.stop.set()
        thread.join(timeout=5)
        if thread.is_alive():
            raise ValueError("resource monitor did not stop")
        self.sample(False)
        self.require()


def execute(source: Path, root: Path, registration: Path, name: str):
    source, root, registration = source.resolve(), root.resolve(), registration.resolve()
    contract = load_contract(source / "configs/stage09_model_enhancement.json")
    spec = FeatureContract.model_validate_json((source / "configs/stage09_wide_features.json").read_text())
    if spec.profiles != ["core", "source_as_received"]:
        raise ValueError("two ordered frozen feature profiles required")
    if root.drive.upper() != "E:" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
        raise ValueError("safe unique name and E execution root required")
    check_registration(source, root, registration)
    frozen = read_json(registration / "freeze.json")
    if not any(i["root"] == "source" and i["path"] == "configs/stage09_wide_features.json" for i in frozen["inventory"]):
        raise ValueError("new feature contract/source must be registered before execution")
    artifact = (root / contract.artifact_root / name).resolve()
    report = (root / contract.output_root / name).resolve()
    if not artifact.is_relative_to(root / contract.artifact_root) or not report.is_relative_to(root / contract.output_root):
        raise ValueError("output escapes registered E root")
    if artifact.exists() or report.exists():
        raise ValueError("output exists; use check instead of overwriting")
    observation = resources(True)
    if check_resources(contract.resources, observation)["status"] != "passed":
        raise ValueError("Route1 resource start gate failed; no build started")
    artifact.mkdir(parents=True, exist_ok=False)
    report.mkdir(parents=True, exist_ok=False)
    write_json(report / "start-resource-gate.json", observation)
    started = time.perf_counter()
    scratch = artifact / "working.duckdb"
    db = duckdb.connect(str(scratch))
    db.execute("SET TimeZone='UTC'")
    db.execute(f"SET memory_limit='{spec.duckdb_memory_mib}MB'")
    db.execute(f"SET threads={spec.duckdb_threads}")
    db.execute(f"SET max_temp_directory_size='{spec.maximum_spill_gib}GiB'")
    db.execute("SET temp_directory=?", [str(artifact / "duckdb-temp")])
    monitor = FeatureMonitor(contract.resources, report / "resources.json", db, spec.maximum_pipeline_seconds)
    monitor.sample(False)
    thread = threading.Thread(target=monitor.loop, daemon=True)
    thread.start()
    success = False
    phase = "source_snapshot"
    try:
        snapshot = artifact / "source-snapshot"
        for item in frozen["inventory"]:
            if item["root"] == "source":
                target = snapshot / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / item["path"], target)
                if _sha256(target) != item["sha256"]:
                    raise ValueError("source changed while snapshotting")
        phase = "side_table"
        side = artifact / "features.parquet"
        summary = build_side_table(db, root / "data/raw/ieee-cis/train_transaction.csv",
            root / "data/raw/ieee-cis/train_identity.csv", root / "data/interim/ieee-cis/transactions.parquet", side, monitor.require)
        if summary["rows"] != frozen["data"]["canonical_count"] or summary["identity_rows"] != frozen["data"]["identity_count"]:
            raise ValueError("new side table differs from registered source cardinality")
        phase = "independent_history_check"
        write_json(artifact / "history-audit.json", audit_history(db, summary["rows"], monitor.require))
        phase = "train_only_preprocessing"
        train = frozen["data"]["windows"][0]["train"]
        states = fit_fold_profiles(db, summary["fields"], train, spec.profiles, artifact, monitor.require)
        phase = "batch_transform_check"
        batches = verify_transform_batches(db, side, states, artifact, train, monitor.require)
        phase = "input_and_budget_recheck"
        proof = check_registration(source, root, registration)
        monitor.require()
        outputs = {p.name: {"bytes": p.stat().st_size, "sha256": _sha256(p)} for p in artifact.iterdir() if p.is_file() and p.suffix in {".json", ".parquet"}}
        monitor.finish(thread)
        manifest = {"schema_version": "stage09-5b-feature-manifest-v1", "status": "passed",
            "at": datetime.now(UTC).isoformat(), "source_registration": str(registration),
            "source_registration_sha256": frozen["content_sha256"], "feature_contract": spec.model_dump(),
            "artifact_directory": str(artifact), "summary": summary, "preprocessors": states,
            "preprocessing_fit_scope": "fold-1-train-only", "fold_2_3_fit": "deferred_until_final_decision_freeze",
            "batch_transform_checks": batches, "registration_after": proof, "outputs": outputs,
            "scope": {"new_model_fits": 0, "generation_calls": 0, "paid_calls": 0,
                      "outer_label_reads": 0, "test_label_reads": 0, "legacy_pipeline_overwritten": False},
            "process_peak_working_set_mib": peak_working_set_mib(), "pipeline_seconds": time.perf_counter()-started}
        manifest["resource_evidence"] = {"bytes": monitor.path.stat().st_size, "sha256": _sha256(monitor.path)}
        manifest["content_sha256"] = _content_hash(manifest)
        write_json(report / "manifest.json", manifest)
        success = True
    except Exception as error:
        write_json(report / "failure.json", {"status": "failed", "phase": phase, "error_type": type(error).__name__,
            "safe_reason": str(error) if isinstance(error, (ValueError, TimeoutError)) else "data_engine_failure_see_private_debug",
            "pipeline_seconds": time.perf_counter()-started, "resource_floor_reached": monitor.violation.is_set(),
            "new_model_fits": 0, "generation_calls": 0, "paid_calls": 0})
        raise
    finally:
        monitor.stop.set()
        thread.join(timeout=5)
        if not success:
            monitor.sample(False)
        db.close()
        if success:
            # Only our new intermediate file, never raw data, old models or a directory.
            scratch.unlink()
    return check_outputs(root, report)


def check_outputs(root: Path, directory: Path):
    root, directory = root.resolve(), directory.resolve()
    if not directory.is_relative_to(root / "reports/generated/stage09/route5b"):
        raise ValueError("feature report must stay under E 5B output root")
    manifest = read_json(directory / "manifest.json")
    if _content_hash({k: v for k, v in manifest.items() if k != "content_sha256"}) != manifest.get("content_sha256"):
        raise ValueError("feature manifest content drift")
    if manifest["status"] != "passed" or manifest["scope"] != {
        "new_model_fits": 0, "generation_calls": 0, "paid_calls": 0,
        "outer_label_reads": 0, "test_label_reads": 0, "legacy_pipeline_overwritten": False}:
        raise ValueError("feature execution scope/status mismatch")
    artifact = Path(manifest["artifact_directory"]).resolve()
    if not artifact.is_relative_to(root / "artifacts/stage-09/route5b"):
        raise ValueError("feature artifact path escape")
    for name, item in manifest["outputs"].items():
        path = (artifact / name).resolve()
        if not path.is_relative_to(artifact) or not path.is_file() or _sha256(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
            raise ValueError("feature output missing/changed:" + name)
    registration_path = Path(manifest["source_registration"]).resolve()
    if not registration_path.is_relative_to(root / "reports/generated/stage09/route5b"):
        raise ValueError("source registration path escape")
    registration = read_json(registration_path / "freeze.json")
    if _content_hash({k: v for k, v in registration.items() if k != "content_sha256"}) != registration.get("content_sha256"):
        raise ValueError("source registration content drift")
    if registration["content_sha256"] != manifest["source_registration_sha256"]:
        raise ValueError("source registration binding changed")
    verify_inventory({"source": artifact / "source-snapshot", "execution": root}, registration["inventory"])
    # Archive-based check remains usable after future source edits; budget stays live.
    contract = load_contract(artifact / "source-snapshot/configs/stage09_model_enhancement.json")
    resource_path = directory / "resources.json"
    resource_binding = manifest["resource_evidence"]
    if not resource_path.is_file() or _sha256(resource_path) != resource_binding["sha256"] or resource_path.stat().st_size != resource_binding["bytes"]:
        raise ValueError("resource evidence changed/missing")
    samples = read_json(resource_path)
    if not samples or any(not continuous_ok(contract.resources, sample) for sample in samples):
        raise ValueError("resource evidence failed continuous floor")
    from graphrisk.graph.stage09_enhancement_preflight import budget_binding
    if not (root / contract.artifact_root / "budget-binding.json").is_file():
        raise ValueError("shared budget binding missing; read-only check cannot recreate it")
    _, binding = budget_binding(root, contract, artifact / "unused-fit-ledger.json")
    return {"status": "passed", "rows": manifest["summary"]["rows"], "raw_fields": manifest["summary"]["raw_fields"],
        "feature_columns": len(manifest["summary"]["fields"]), "outputs_checked": len(manifest["outputs"]),
        "historical_input_files_checked": len(registration["inventory"]), "shared_ledger": binding["ledger_path"],
        "manifest_sha256": manifest["content_sha256"], "read_only": True}
