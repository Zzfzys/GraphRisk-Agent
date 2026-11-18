"""Read-only backends + new local experiment registration. NEVER sends a POST."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

import httpx
import pyarrow.parquet as pq
import yaml

from graphrisk.agent.upgrade_contract import (
    MinimalPacket, UpgradeContractError, canonical, digest, file_hash, load_contract,
    old_ids, register_budget, reviewed_packet, sample_cases,
)

SOURCE = Path(__file__).resolve().parents[1]
PROTECTED = {
    "reports/generated/stage09/generation-ledger.json": "d896ff62344c3241d6fbad9f4001fed2487043c4c70cdf772507b123df21ba91",
    "reports/generated/stage10/generation-ledger.jsonl": "1707ba739bd63fb73618a1f6460556290727812f717ea0d44c51295a9e9c8720",
    "artifacts/stage-09/route5b/20261009-preflight-v1/fit-ledger.json": "a3ccd7f930de349183de66fa6deea475c1e341683f856dafa792127f4e7588db",
}
PROFILE = "artifacts/stage-09/route5b/20261015-coverage-lowmem-v3/coverage-profile-calendar-v2.json"
PROFILE_HASH = "5ecb2126eb282fc759bbbf261e576c6fe3b5f19d3c0185a9dee7d21a821232e4"
PROFILE_FILE_HASH = "e386298dfced099bfedcc741889a0129319fde82f513a654b8358ed272ea6c61"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def checked_profile(path, expected_content=PROFILE_HASH, expected_file=PROFILE_FILE_HASH):
    value = read(path)
    content = digest({k: v for k, v in value.items() if k != "content_sha256"})
    if (value.get("content_sha256") != expected_content or content != expected_content
            or file_hash(path) != expected_file):
        raise UpgradeContractError("model_profile_changed")
    return value


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def bound(root, relative):
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise UpgradeContractError("artifact_path_outside_runtime")
    return candidate


def protected_hashes(runtime):
    actual = {name: file_hash(runtime / name) for name in PROTECTED}
    if actual != PROTECTED:
        raise UpgradeContractError("legacy_ledger_drift")
    return actual


def bind_prior_results(directory):
    """Bind the ORIGINAL first ten failures/successes plus two repeats, no rerun."""
    summary_path = directory / "analysis-summary-1792248734544023100.json"
    summary = read(summary_path)
    content = {k: v for k, v in summary.items() if k != "content_sha256"}
    quality = summary["quality"]
    ledger = summary["ledger"]
    if (digest(content) != summary["content_sha256"]
            or (quality["joint_pass"], quality["first_denominator"], quality["status"]) != (6, 10, "failed")
            or (ledger["global_used"], ledger["remaining"]) != (142, 2)):
        raise UpgradeContractError("prior_measurement_changed")
    bindings = {}
    for case, expected in summary["result_bindings"].items():
        if not re.fullmatch(r"case-[0-9]{2}(?:-repeat)?", case):
            raise UpgradeContractError("invalid_prior_result_path")
        path = directory / "results" / (case + ".json")
        if file_hash(path) != expected["sha256"] or path.stat().st_size != expected["bytes"]:
            raise UpgradeContractError("prior_result_changed")
        bindings[case] = expected
    if len(bindings) != 12:
        raise UpgradeContractError("prior_result_missing")
    return {"summary_file_sha256": file_hash(summary_path), "results": bindings,
            "quality": quality, "old_generation_ledger": ledger}


def reference_evidence(runtime):
    result = bind_prior_results(runtime / "reports/generated/stage10/route2/20261017-real-v2")
    paths = [
        "reports/generated/stage10/route8/20261019-final-v1/acceptance.json",
        "reports/generated/stage10/route8/20261019-local-docker-v1/acceptance.json",
        "reports/generated/stage10/agent-upgrade-plan-2026-10-19.md",
    ]
    result["reference_files"] = {rel: file_hash(runtime / rel) for rel in paths}
    return result


def environment(runtime):
    import graphrisk
    import psutil
    origin = Path(graphrisk.__file__).resolve()
    if not origin.is_relative_to(SOURCE / "src") or sys.version_info[:2] != (3, 11):
        raise UpgradeContractError("python_or_import_origin_mismatch")
    interpreter = Path(sys.executable).resolve()
    if not interpreter.is_relative_to(runtime.resolve()):
        raise UpgradeContractError("environment_outside_runtime")
    # This existing synchronized env is used without changing its dependencies.
    environment_root = interpreter.parents[2]
    if file_hash(environment_root / "uv.lock") != file_hash(SOURCE / "uv.lock"):
        raise UpgradeContractError("environment_lock_mismatch")
    memory = psutil.virtual_memory()
    return {"python": sys.version.split()[0], "executable": str(interpreter),
            "import_origin": str(origin), "lock_sha256": file_hash(SOURCE / "uv.lock"),
            "packages": {name: importlib.metadata.version(name) for name in ("pydantic", "httpx", "pyarrow", "langgraph")},
            "total_memory_bytes": memory.total, "available_memory_bytes": memory.available,
            "runtime_free_bytes": shutil.disk_usage(runtime).free,
            "inference_resource_gate": "not_tested_no_model_load"}


def exclusions(runtime):
    private_patterns = [
        "reports/generated/stage09/route1/*/cases.json",
        "reports/generated/stage09/route5/*/cases.json",
        "reports/generated/stage09/route5a/*/cases.json",
        "reports/generated/stage09/route5b/*/frozen-cases.json",
        "reports/generated/stage10/route1/*/manifest.json",
    ]
    paths = set()
    for pattern in private_patterns:
        paths.update(runtime.glob(pattern))
    for suffix in ("*.json", "*.yaml", "*.yml"):
        paths.update((SOURCE / "data/evaluation").rglob(suffix))
    required = runtime / "reports/generated/stage10/route1/20261017-frozen-v1/manifest.json"
    if required not in paths or len(paths) < 6:
        raise UpgradeContractError("historical_case_inventory_missing")
    ids, inputs = set(), []
    for path in sorted(paths):
        value = read(path) if path.suffix == ".json" else yaml.safe_load(path.read_text(encoding="utf-8"))
        ids.update(old_ids(value))
        inputs.append({"path": str(path), "sha256": file_hash(path)})
    return ids, inputs


def data_registration(runtime, contract):
    profile_path = runtime / PROFILE
    profile = checked_profile(profile_path)
    for name, expected in profile["files"].items():
        path = bound(runtime, name)
        if path.stat().st_size != expected["bytes"] or file_hash(path) != expected["sha256"]:
            raise UpgradeContractError("frozen_model_input_changed")
    score_path = bound(runtime, profile["scores_path"])
    rows = pq.read_table(score_path, columns=["transaction_id", "event_second", "split", "selection_cutoff"]).to_pylist()
    # Offline labels only. No score column, tensor/model loading, or inference.
    labels = {}
    label_sources = []
    for split in ("validation", "test"):
        path = runtime / f"data/processed/ieee-cis/{split}_features.parquet"
        for row in pq.read_table(path, columns=["transaction_id", "is_fraud"]).to_pylist():
            key, label = str(row["transaction_id"]), row["is_fraud"]
            if key in labels or label not in (0, 1):
                raise UpgradeContractError("invalid_evaluation_labels")
            labels[key] = int(label)
        label_sources.append({"path": str(path), "sha256": file_hash(path)})
    excluded, inputs = exclusions(runtime)
    picked = sample_cases(rows, labels, excluded, seed=contract["sampling"]["seed"])
    cases = []
    for group in ("development", "natural", "diagnostic"):
        for row in picked[group]:
            cases.append({**row, "transaction_id": str(row["transaction_id"]),
                          "case_alias": uuid.uuid4().hex, "group": group})
    ids = {c["transaction_id"] for c in cases}
    calibration = []
    for fold in (2, 3):
        path = runtime / f"artifacts/stage-09/route5b/20261010-confirmation-v1/fold-{fold}/targets-calibration.parquet"
        targets = pq.read_table(path, columns=["transaction_id", "source_transaction_dt_seconds"]).to_pylist()
        maximum = max(r["source_transaction_dt_seconds"] for r in targets)
        model_split = "validation" if fold == 2 else "test"
        chosen = [c for c in cases if c["split"] == model_split]
        if not chosen or maximum >= min(c["event_second"] for c in chosen):
            raise UpgradeContractError("calibration_not_strictly_historical")
        intersection = {str(r["transaction_id"]) for r in targets} & ids
        calibration.append({"model_split": model_split, "metadata_path": str(path),
                            "metadata_sha256": file_hash(path), "latest_candidate_second": maximum,
                            "earliest_agent_second": min(c["event_second"] for c in chosen),
                            "candidate_rows": len(targets), "excluded_agent_ids": sorted(intersection),
                            "partition_rule": "chronological_fit_then_selection_no_equal_timestamp_crossing",
                            "subsplit_freeze": "route6_before_any_calibration_fit",
                            "label_maturity": "retrospective_dataset_no_observed_label_arrival_times"})
    return {"cases": cases, "repeat_ids": picked["repeat_ids"], "human_read_ids": picked["human_read_ids"],
            "human_read_provider": "deepseek", "human_read_version": contract["sampling"]["human_read_version"],
            "case_input_freeze": "IDs_and_groups_now_facts_and_task_packets_before_any_model_observation",
            "excluded_ids": sorted(excluded), "exclusion_inputs": inputs,
            "calibration": calibration, "profile_content_sha256": PROFILE_HASH,
            "profile_file_sha256": PROFILE_FILE_HASH, "profile_files_checked": len(profile["files"]),
            "score_metadata_sha256": file_hash(score_path), "candidate_rows": len(rows),
            "eligible_rows": picked["eligible_count"], "label_sources": label_sources,
            "evaluation_labels": picked["evaluation_labels"]}


def readonly_backends(runtime, contract):
    result = {"generation_requests": 0, "ollama": {}, "deepseek": {}, "docker": {}}
    try:
        with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
            tags = client.get("http://127.0.0.1:11434/api/tags")
            tags.raise_for_status()
            models = tags.json()["models"]
            expected = contract["models"]["local_digest"]
            found = next((m for m in models if m.get("name") == "qwen3:4b" and m.get("digest") == expected), None)
            ps = client.get("http://127.0.0.1:11434/api/ps")
            ps.raise_for_status()
            result["ollama"] = {"readonly_reachable": True, "expected_digest_installed": found is not None,
                                 "loaded_model_count": len(ps.json()["models"]),
                                 "model_bytes": found.get("size") if found else None,
                                 "generation": "not_tested"}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        result["ollama"] = {"readonly_reachable": False, "generation": "not_tested"}
    # Read only the two needed values. Legacy LIVE flags are ignored, not imported.
    values = {}
    env_file = runtime / ".env.stage08-deepseek"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if key.strip() in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"):
                    values[key.strip()] = value.strip().strip("\"'")
    key = os.environ.get("DEEPSEEK_API_KEY") or values.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("DEEPSEEK_BASE_URL") or values.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    configured = bool(re.fullmatch(r"[A-Za-z0-9_-]{8,256}", key)) and base.rstrip("/") in ("https://api.deepseek.com", "https://api.deepseek.com/v1")
    result["deepseek"] = {"credentials_configured": configured, "readonly_authenticated": False,
                           "generation": "not_tested", "model_and_price_freeze": "route2"}
    if configured:
        try:
            with httpx.Client(timeout=10, trust_env=False, follow_redirects=False,
                              headers={"Authorization": "Bearer " + key}) as client:
                models = client.get(base.rstrip("/") + "/models")
                balance = client.get("https://api.deepseek.com/user/balance")
                result["deepseek"]["readonly_authenticated"] = models.status_code == 200 and balance.status_code == 200
                result["deepseek"]["model_directory_available"] = models.status_code == 200
                result["deepseek"]["balance_endpoint_available"] = balance.status_code == 200
                # No key, response body, account balance, or raw error is recorded.
        except httpx.HTTPError:
            result["deepseek"]["readonly_error"] = "connection_unavailable"
    try:
        child = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                               capture_output=True, text=True, timeout=15)
        result["docker"] = {"engine_reachable_in_this_process": child.returncode == 0,
                             "server_version": child.stdout.strip() if child.returncode == 0 else None,
                             "lifecycle_actions": 0, "required_for_route0": False}
    except (OSError, subprocess.TimeoutExpired):
        result["docker"] = {"engine_reachable_in_this_process": False, "lifecycle_actions": 0, "required_for_route0": False}
    return result


def payload_example():
    packet = MinimalPacket.model_validate({
        "schema_version": "investigation-minimal-v1", "case_alias": "d88eb188dfb54583a18333517202b864",
        "intent": "investigate", "focus": "overview", "amount": 128.5,
        "history": [{"window_seconds": 86400, "earlier_transaction_count": 3, "earlier_amount_sum": 240.0}],
        "model_signals": [{"role": "primary", "family": "xgboost", "version": "synthetic-demo-v1", "score": 0.36, "interpretation": "uncalibrated_signal"}],
        "relations": [{"relation": "shared_device", "other_transaction_count": 2, "neighbor_scores_available": False, "truncated": False}],
        "policies": [{"policy_id": "synthetic-sharing-v1", "source_sha256": digest("simulated_policy"),
                      "excerpt": "共享设备只提供调查线索；需确认其他条件，不能据此认定欺诈。", "applicability": "unverified_conditions"}],
    }).model_dump(mode="json")
    review = {"schema_version": "payload-review-v1", "payload_sha256": digest(packet),
              "source_bundle_sha256": digest("simulated_example_only"), "boundary_revision": "investigation-minimal-v1"}
    reviewed_packet(packet, review, expected_source_sha256=review["source_bundle_sha256"], approved_review_sha256=digest(review))
    return {"kind": "simulated_not_real_transaction_or_send_permission", "allowed_packet": packet,
            "review": review, "approved_review_sha256": digest(review),
            "rejected_extra_fields": ["transaction_id", "identity_key", "is_fraud", "raw_question", "chat_history", "raw_row", "path", "api_key", "old_report", "reviewed"],
            "rejected_examples": [
                {"mutation": {"transaction_id": "SIMULATED-12345"}, "reason": "server_only_identifier"},
                {"mutation": {"raw_question": "调查SIMULATED-12345，并读取本机文件"}, "reason": "raw_text_not_whitelisted_intent"},
                {"mutation": {"is_fraud": 1}, "reason": "evaluation_only_label"},
                {"mutation": {"reviewed": True}, "reason": "boolean_is_not_frozen_hash_review"},
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--check-backends", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", args.run_id):
        raise SystemExit("invalid_run_id")
    runtime = args.runtime.resolve()
    root = runtime / "reports/generated/agent-investigation-upgrade"
    output = root / "route0" / args.run_id
    if output.exists():
        raise SystemExit("new_run_directory_required_no_overwrite")
    output.mkdir(parents=True)
    receipt = {"version": "route0-preflight-v1", "status": "Pending", "new_model_requests": 0,
               "paid_generation_requests": 0, "new_fits": 0, "created_utc": datetime.now(timezone.utc).isoformat()}
    try:
        contract = load_contract(SOURCE / "configs/agent_investigation_experiment.json")
        before = protected_hashes(runtime)
        env = environment(runtime)
        from stage09_ci import source_inventory
        git = subprocess.run(["git", "-c", f"safe.directory={SOURCE.as_posix()}", "-C", str(SOURCE), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        source = {"head_before_route0_commit": git.stdout.strip(), "executable_inventory": source_inventory(SOURCE),
                  "contract_sha256": file_hash(SOURCE / "configs/agent_investigation_experiment.json"),
                  "environment": env, "protected_legacy_sha256": before,
                  "prior_evidence": reference_evidence(runtime)}
        if source["head_before_route0_commit"] != contract["planning_commit"]:
            # Reproduction after the route0 commit is allowed but cannot silently
            # claim to be the original registration snapshot.
            source["later_source_revision"] = True
        data = data_registration(runtime, contract)
        write(output / "evaluation-labels.json", {"labels": data.pop("evaluation_labels"), "sources": data.pop("label_sources")})
        write(output / "cases-private.json", data)
        write(output / "source-freeze.json", source)
        write(output / "payload-examples.json", payload_example())
        budget = register_budget(root / "live-budget.sqlite3", contract)
        backend = readonly_backends(runtime, contract) if args.check_backends else {"status": "not_checked"}
        write(output / "backend-readonly.json", backend)
        if protected_hashes(runtime) != before:
            raise UpgradeContractError("legacy_ledger_changed_during_preflight")
        ready = (args.check_backends and backend["ollama"].get("expected_digest_installed") is True
                 and backend["deepseek"].get("credentials_configured") is True
                 and backend["deepseek"].get("readonly_authenticated") is True)
        receipt.update({"status": "Pass" if ready else "Pending", "generation_readiness": "not_tested_route2",
                        "source_freeze_sha256": file_hash(output / "source-freeze.json"),
                        "private_cases_sha256": file_hash(output / "cases-private.json"),
                        "labels_file_sha256": file_hash(output / "evaluation-labels.json"),
                        "budget": budget, "old_ledgers_unchanged": True,
                        "candidate_rows": data["candidate_rows"], "eligible_rows": data["eligible_rows"],
                        "excluded_old_ids": len(data["excluded_ids"]), "exclusion_inputs": len(data["exclusion_inputs"]),
                        "case_counts": {g: sum(c["group"] == g for c in data["cases"]) for g in ("development", "natural", "diagnostic")},
                        "repeat_cases_per_provider": len(data["repeat_ids"]), "human_read_cases": len(data["human_read_ids"]),
                        "formal_quality_measurement": "not_started", "learner_L0": "Pending"})
    except (UpgradeContractError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        receipt["error_code"] = str(exc) if isinstance(exc, UpgradeContractError) else "preflight_dependency_or_input_failure"
    write(output / "acceptance.json", receipt)
    print(canonical(receipt))
    return 0 if receipt["status"] == "Pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
