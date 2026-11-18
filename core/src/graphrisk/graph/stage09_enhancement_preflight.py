"""Read-only input inspection and new E-drive-only 5B registration.

Training and generation are deliberately absent. Only newly named 5B evidence
directories may be written. Existing source/data/model evidence is hashed.
"""
from __future__ import annotations

import contextlib
import csv
import importlib.metadata
import io
import json
import os
import re
import runpy
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import duckdb

from graphrisk.agent.stage09_preflight import _content_hash, _sha256, build_freeze, check_resources
from graphrisk.demo.stage09_evaluation import resources, write_json
from graphrisk.graph.stage09_enhancement import EnhancementLedger, freeze_windows, load_contract


def input_path(root: Path, relative: str) -> Path:
    """Reject escape/symlink/secret paths before opening any file."""
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("input path escapes registered root")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("input path escapes registered root")
    parts = (*path.parts, *resolved.relative_to(root.resolve()).parts)
    if any(p.startswith(".env") or p in {".secrets", ".git"} or p.endswith((".pem", ".key")) for p in parts):
        raise ValueError("secret path is not a freeze input")
    return resolved


def inventory(roots: dict[str, Path], paths: dict[str, set[str]]) -> list[dict]:
    result = []
    for role in sorted(paths):
        for relative in sorted(paths[role]):
            path = input_path(roots[role], relative)
            result.append({"root": role, "path": relative, "bytes": path.stat().st_size,
                           "sha256": _sha256(path)})
    return result


def inspect_data(root: Path, contract):
    """Project only IDs/time. Neither new fraud counts nor outcome metrics are read."""
    canonical = input_path(root, "data/interim/ieee-cis/transactions.parquet")
    raw = input_path(root, "data/raw/ieee-cis/train_transaction.csv")
    identity = input_path(root, "data/raw/ieee-cis/train_identity.csv")
    with raw.open(encoding="utf-8", newline="") as stream:
        transaction_columns = next(csv.reader(stream))
    with identity.open(encoding="utf-8", newline="") as stream:
        identity_columns = next(csv.reader(stream))
    if not {"TransactionID", "TransactionDT", "isFraud"} <= set(transaction_columns):
        raise ValueError("raw IEEE transaction header missing required columns")
    if "TransactionID" not in identity_columns:
        raise ValueError("raw IEEE identity header missing TransactionID")
    with duckdb.connect() as db:
        db.execute("SET TimeZone='UTC'")
        db.execute("SET threads=2")
        db.execute("SET memory_limit='768MB'")
        # No spilling to an unregistered temp directory during the read-only gate.
        db.execute("SET max_temp_directory_size='0B'")
        events = db.execute("SELECT transaction_id, source_transaction_dt_seconds FROM read_parquet(?) ORDER BY source_transaction_dt_seconds, transaction_id", [str(canonical)]).fetchall()
        if len(events) != len({r[0] for r in events}):
            raise ValueError("canonical transaction IDs are not unique")
        event_map = dict(events)
        splits, development, seen = {}, [], set()
        prior_end = None
        for split in ("train", "validation", "test"):
            path = input_path(root, f"data/processed/ieee-cis/{split}_features.parquet")
            rows = db.execute("SELECT transaction_id, epoch(transaction_time)::BIGINT FROM read_parquet(?) ORDER BY transaction_time, transaction_id", [str(path)]).fetchall()
            ids = [r[0] for r in rows]
            if not rows or len(ids) != len(set(ids)) or seen.intersection(ids) or any(i not in event_map for i in ids):
                raise ValueError("split IDs empty, duplicate, overlapping or absent from canonical")
            if any(epoch != event_map[i] + 946684800 for i, epoch in rows):
                raise ValueError("split/canonical relative time mismatch")
            relative_rows = [(i, event_map[i]) for i in ids]
            if prior_end is not None and min(t for _, t in relative_rows) <= prior_end:
                raise ValueError("split time overlap or simultaneous timestamps across splits")
            prior_end = max(t for _, t in relative_rows)
            splits[split] = {"count": len(ids), "ids_sha256": _content_hash({"ids": ids}),
                "min_relative_seconds": min(t for _, t in relative_rows), "max_relative_seconds": prior_end}
            seen.update(ids)
            if split != "test":
                development.extend(relative_rows)
        if seen != set(event_map):
            raise ValueError("original splits do not exhaust canonical IDs")
        # Validate original CSV ID/time pairing, without projecting its labels.
        raw_rows = db.execute('SELECT "TransactionID", CAST("TransactionDT" AS BIGINT) FROM read_csv(?, header=true, all_varchar=true)', [str(raw)]).fetchall()
        if len(raw_rows) != len(events) or len({r[0] for r in raw_rows}) != len(raw_rows) or dict(raw_rows) != event_map:
            raise ValueError("raw/canonical ID or relative time mismatch")
        identity_rows = db.execute('SELECT "TransactionID" FROM read_csv(?, header=true, all_varchar=true)', [str(identity)]).fetchall()
        identity_ids = [r[0] for r in identity_rows]
        if len(identity_ids) != len(set(identity_ids)) or not set(identity_ids) <= seen:
            raise ValueError("identity IDs duplicate or outside transaction source")
    return {"canonical_count": len(events), "identity_count": len(identity_ids),
            "transaction_column_count": len(transaction_columns), "identity_column_count": len(identity_columns),
            "splits": splits, "development_count": len(development),
            "calendar": "synthetic 2000-01-01 UTC anchor; compare relative seconds",
            "outcome_columns_projected": [], "windows": freeze_windows(development, contract)}


def inspect_environment(source_root: Path, execution_root: Path):
    if sys.version_info[:2] != (3, 11) or not Path(sys.executable).resolve().is_relative_to(execution_root / ".venv"):
        raise ValueError("run with the E project Python 3.11 .venv")
    if tomllib.loads((source_root / "uv.lock").read_text()) != tomllib.loads((execution_root / "uv.lock").read_text()):
        raise ValueError("source/execution uv.lock semantic mismatch")
    if tomllib.loads((source_root / "pyproject.toml").read_text()) != tomllib.loads((execution_root / "pyproject.toml").read_text()):
        raise ValueError("source/execution pyproject semantic mismatch")
    caches = {}
    for key in ("TEMP", "TMP", "PIP_CACHE_DIR", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR"):
        path = Path(os.environ.get(key, "")).resolve()
        if not os.environ.get(key) or not path.is_relative_to(execution_root):
            raise ValueError(f"{key} must stay inside E project; dot-source enter_project_env.ps1")
        caches[key] = str(path)
    proof = subprocess.run(["uv", "sync", "--locked", "--offline", "--check", "--group", "demo"],
                           cwd=execution_root, capture_output=True, text=True, timeout=60)
    if proof.returncode:
        raise ValueError("E environment does not match uv.lock; sync explicitly before freezing")
    return {"python": sys.version, "executable": sys.executable, "caches": caches,
        "semantic_lock_match": True, "uv_sync_check_exit_code": proof.returncode,
        "uv_version": subprocess.check_output(["uv", "--version"], text=True).strip(),
        "versions": {name: importlib.metadata.version(name) for name in (
            "duckdb", "pydantic", "torch", "torch-geometric", "xgboost", "pytest", "langgraph", "streamlit")}}


def inspect_history(execution_root: Path):
    """Use the existing full regrader but suppress its one evidence rewrite."""
    checker = input_path(execution_root, "scripts/check_stage09_quality_docker_recheck.py")
    namespace = runpy.run_path(str(checker))
    output = io.StringIO()
    with patch.dict(namespace["main"].__globals__, {"write_json": lambda *_: None}), patch.object(sys, "argv", [str(checker)]), contextlib.redirect_stdout(output):
        namespace["main"]()
    records = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith("{")]
    if not records or not any(r.get("docker_acceptance_passed") for r in records):
        raise ValueError("historical Docker/5A acceptance recheck failed")
    return records


def inspect_active_models(root: Path):
    from graphrisk.agent.config import load_stage05_config
    from graphrisk.graph.deployment import load_policy, verify_files

    config = load_stage05_config(root / "configs/stage09_quality_4b.yaml")
    path = config.resolve_path(config.model_policy)
    policy = load_policy(root, path, config.model_policy_sha256)
    for section in ("graph_files", "primary_files"):
        verify_files(root, policy[section])
    metadata = json.loads(input_path(root, policy["gnn_metadata_path"]).read_text())
    if _sha256(input_path(root, policy["gnn_model_path"])) != metadata["model_sha256"] or metadata["input_channels"] != 28:
        raise ValueError("active experimental GraphSAGE weight/schema mismatch")
    inputs = {path.relative_to(root).as_posix(), "configs/stage09.yaml", "configs/stage09_quality.yaml", "configs/stage09_quality_4b.yaml"}
    for section in ("graph_files", "primary_files", "authority_files"):
        inputs.update(item["path"] for item in policy[section])
    return {"score_mode": policy["score_mode"], "gnn_role": policy["gnn_role"],
            "gnn_model_version": policy["gnn_model_version"], "primary_model_version": policy["primary_model_version"],
            "decision_sha256": policy["decision_sha256"], "deployment_sha256": policy["content_sha256"],
            "gnn_model_sha256": metadata["model_sha256"], "gnn_input_channels": metadata["input_channels"],
            "generation_model_id": config.generation.model_id, "generation_model_revision": config.generation.model_revision,
            "live_service_or_prediction_probe": "not_run_read_only_file_binding"}, inputs


def verify_inventory(roots: dict[str, Path], items: list[dict]):
    if not items or len({(i["root"], i["path"]) for i in items}) != len(items):
        raise ValueError("empty or duplicate inventory")
    for item in items:
        if item["root"] not in roots:
            raise ValueError("unknown freeze root")
        path = input_path(roots[item["root"]], item["path"])
        if not path.is_file() or path.stat().st_size != item["bytes"] or _sha256(path) != item["sha256"]:
            raise ValueError("input drift:" + item["root"] + ":" + item["path"])


def budget_binding(execution_root: Path, contract, proposed: Path):
    """Every source revision shares the FIRST ledger; never allocate a new quota."""
    artifact_root = (execution_root / contract.artifact_root).resolve()
    pointer = artifact_root / "budget-binding.json"
    contract_hash = _content_hash(contract.model_dump())
    if pointer.exists():
        binding = json.loads(pointer.read_text(encoding="utf-8"))
        payload = {k: v for k, v in binding.items() if k != "content_sha256"}
        if _content_hash(payload) != binding.get("content_sha256") or binding["contract_sha256"] != contract_hash:
            raise ValueError("shared budget binding/contract drift; never reset quota")
        ledger_path = Path(binding["ledger_path"]).resolve()
        if not ledger_path.is_relative_to(artifact_root) or ledger_path.name != "fit-ledger.json":
            raise ValueError("shared ledger escapes artifact root")
        EnhancementLedger(ledger_path, contract, binding["origin_freeze_sha256"]).read()
        return pointer, binding
    previous = list(artifact_root.glob("*/fit-ledger.json"))
    if len(previous) > 1:
        raise ValueError("multiple unbound ledgers; audit before registering another version")
    if previous:
        ledger_path = previous[0].resolve()
        original = json.loads((execution_root / contract.output_root / ledger_path.parent.name / "freeze.json").read_text())
        payload = {k: v for k, v in original.items() if k != "content_sha256"}
        if _content_hash(payload) != original.get("content_sha256") or original["contract"] != contract.model_dump():
            raise ValueError("original budget contract/hash drift")
        origin = original["content_sha256"]
        EnhancementLedger(ledger_path, contract, origin).read()
    else:
        ledger_path, origin = proposed, None
    return pointer, {"schema_version": "stage09-5b-shared-budget-v1", "contract_sha256": contract_hash,
                     "ledger_path": str(ledger_path), "origin_freeze_sha256": origin}


def check_registration(source_root: Path, execution_root: Path, directory: Path):
    directory = directory.resolve()
    if not directory.is_relative_to(execution_root / "reports/generated/stage09/route5b"):
        raise ValueError("check directory must be under registered E output root")
    frozen = json.loads((directory / "freeze.json").read_text(encoding="utf-8"))
    payload = {k: v for k, v in frozen.items() if k != "content_sha256"}
    if _content_hash(payload) != frozen.get("content_sha256"):
        raise ValueError("freeze content drift")
    if frozen["roots"] != {"source": str(source_root.resolve()), "execution": str(execution_root.resolve())}:
        raise ValueError("root binding mismatch")
    contract = load_contract(source_root / "configs/stage09_model_enhancement.json")
    if contract.model_dump() != frozen["contract"]:
        raise ValueError("contract drift")
    verify_inventory({"source": source_root, "execution": execution_root}, frozen["inventory"])
    ledger_path = Path(frozen["ledger_path"]).resolve()
    pointer, binding = budget_binding(execution_root, contract, ledger_path)
    if not pointer.exists() or ledger_path != Path(binding["ledger_path"]):
        raise ValueError("shared budget pointer missing/mismatched")
    origin = frozen.get("ledger_origin_freeze_sha256") or frozen["content_sha256"]
    if origin != binding["origin_freeze_sha256"]:
        raise ValueError("shared budget origin drift")
    ledger = EnhancementLedger(ledger_path, contract, origin).read()
    return {"status": "passed", "files_checked": len(frozen["inventory"]),
        "freeze_sha256": frozen["content_sha256"], "fits_used": len(ledger["fits"]),
        "decision_frozen": bool(ledger["decision_sha256"]), "outer_access": len(ledger["outer_access"]),
        "test_access": len(ledger["test_access"]), "read_only": True}


def register(source_root: Path, execution_root: Path, name: str):
    source_root, execution_root = source_root.resolve(), execution_root.resolve()
    contract_path = source_root / "configs/stage09_model_enhancement.json"
    contract = load_contract(contract_path)
    if execution_root.drive.upper() != "E:":
        raise ValueError("5B execution/artifacts must remain on E drive")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
        raise ValueError("safe unique run directory name required")
    output = (execution_root / contract.output_root / name).resolve()
    artifact = (execution_root / contract.artifact_root / name).resolve()
    if not output.is_relative_to(execution_root / contract.output_root) or not artifact.is_relative_to(execution_root / contract.artifact_root):
        raise ValueError("output path escape")
    if output.exists() or artifact.exists():
        raise ValueError("run directory already exists; check it instead of overwriting")
    environment = inspect_environment(source_root, execution_root)
    history = inspect_history(execution_root)
    active, active_inputs = inspect_active_models(execution_root)
    old = build_freeze(execution_root, execution_root / "configs/stage09_experiment.json")
    data = inspect_data(execution_root, contract)
    observations = resources(include_gpu=True)
    gate = check_resources(contract.resources, observations)
    paths = {"execution": {a["path"] for a in old["artifacts"]}, "source": set()}
    paths["execution"].update(active_inputs)
    for folder, patterns in (("src/graphrisk", ["*.py"]), ("api", ["*.py"]),
                             ("scripts", ["*.py", "*.ps1"]), ("tests", ["*.py"])):
        for pattern in patterns:
            paths["source"].update(p.relative_to(source_root).as_posix() for p in (source_root / folder).rglob(pattern))
    paths["source"].update({"uv.lock", "pyproject.toml", "conftest.py", "configs/stage09_model_enhancement.json",
        "docs/data/stage-09-model-enhancement-contract.md", "configs/stage09.yaml", "configs/stage09_quality.yaml", "configs/stage09_quality_4b.yaml"})
    for relative in ("configs/stage09_wide_features.json", "docs/data/stage-09-model-enhancement-features.md",
                     "configs/stage09_tabular_baselines.json", "docs/data/stage-09-enhanced-baselines.md",
                 "configs/stage09_graph_expansion.json", "docs/data/stage-09-graph-expansion.md",
                 "configs/stage09_conditional_graph.json", "docs/data/stage-09-conditional-graph.md",
                 "configs/stage09_confirmation.json", "docs/data/stage-09-confirmation.md"):
        if (source_root / relative).is_file():
            paths["source"].add(relative)
    paths["execution"].update({"data/raw/ieee-cis/train_transaction.csv", "data/raw/ieee-cis/train_identity.csv",
        "reports/generated/stage09/gnn-budget-ledger.json", "reports/generated/stage09/generation-ledger.json",
        "reports/generated/stage09/root-control-ledger.json",
        "reports/generated/stage09/route5a/20261008-quality-v2/acceptance.json",
        "reports/generated/stage09/route5a/docker-recheck-20261008/acceptance.json"})
    docker = json.loads((execution_root / "reports/generated/stage09/route5a/docker-recheck-20261008/acceptance.json").read_text())
    paths["execution"].update(i["path"] for i in docker["closure_files"])
    from graphrisk.demo.stage09_evaluation import exclusive
    with exclusive(execution_root / contract.artifact_root / "registration.lock"):
        pointer, binding = budget_binding(execution_root, contract, artifact / "fit-ledger.json")
        frozen = {"schema_version": "stage09-5b-freeze-v1", "at": datetime.now(UTC).isoformat(),
            "roots": {"source": str(source_root), "execution": str(execution_root)},
            "contract": contract.model_dump(), "environment": environment,
            "historical_recheck": history, "historical_service_bindings": old["bindings"],
            "active_model_file_bindings": active,
            "data": data, "resource_observation": observations, "training_resource_gate": gate,
            "inventory": inventory({"source": source_root, "execution": execution_root}, paths),
            "ledger_path": binding["ledger_path"], "ledger_origin_freeze_sha256": binding["origin_freeze_sha256"],
            "scope": {"new_fits": 0, "new_generation_calls": 0, "new_paid_calls": 0,
                      "model_quality": "not_measured", "personal_learning": "pending",
                      "continuous_peak_resource_readiness": "not_measured"}}
        frozen["content_sha256"] = _content_hash(frozen)
        output.mkdir(parents=True, exist_ok=False)
        artifact.mkdir(parents=True, exist_ok=False)
        write_json(output / "freeze.json", frozen)
        if binding["origin_freeze_sha256"] is None:
            binding["origin_freeze_sha256"] = frozen["content_sha256"]
            EnhancementLedger(Path(binding["ledger_path"]), contract, frozen["content_sha256"]).initialize()
        if not pointer.exists():
            write_json(pointer, {**binding, "content_sha256": _content_hash(binding)})
    proof = check_registration(source_root, execution_root, output)
    write_json(output / "registration-check.json", proof)
    return {**proof, "directory": str(output), "training_resource_gate": gate,
            "new_generation_calls": 0, "new_paid_calls": 0}
