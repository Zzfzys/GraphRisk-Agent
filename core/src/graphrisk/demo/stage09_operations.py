"""Original Stage09 route7: read-only metrics/drift and owned database recovery."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import re
from pathlib import Path

from graphrisk.graph.coverage_resources import CoverageMonitor
from graphrisk.monitoring.operations import error_family, latency_summary, project_run, summarize_events
from graphrisk.monitoring.drift import freeze_reference, compare_population

SOURCE = Path(__file__).resolve().parents[3]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def file_binding(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def inside(root, relative):
    candidate = (root/relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("operational input must stay inside the selected runtime root")
    return candidate


def pin_inputs(root, output, paths):
    bindings = {p.relative_to(root).as_posix(): file_binding(p) for p in sorted(set(paths))}
    write_json(output/"input-bindings.json", bindings)
    return bindings


def require_inputs_unchanged(root, bindings):
    for name, expected in bindings.items():
        if file_binding(inside(root, name)) != expected:
            raise ValueError("frozen operational input changed")


def metrics(root, output, config, require):
    from graphrisk.serving.investigation_service import InvestigationRecord
    outputs, pairings, input_paths = {}, {}, []
    for relative in config["cohorts"].values():
        directory = inside(root, relative)
        manifest = read_json(directory/"cases.json")
        input_paths.extend(directory/name for name in ("cases.json", "summary.json", "acceptance.json"))
        for group, ids in (("main", [c["case_id"] for c in manifest["cases"]]), ("repeat", manifest["repeat_case_ids"])):
            for case_id in ids:
                if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", case_id):
                    raise ValueError("historical case ID must be a bounded opaque file identifier")
                input_paths.append(directory/"results"/(case_id+("-repeat" if group == "repeat" else "")+".json"))
    inputs = pin_inputs(root, output, input_paths)
    for name, relative in config["cohorts"].items():
        directory = inside(root, relative)
        manifest = read_json(directory/"cases.json")
        old_summary = read_json(directory/"summary.json")
        input_paths += [directory/"cases.json", directory/"summary.json", directory/"acceptance.json"]
        cases = manifest["cases"]
        if len(cases) != config["cohort_main_cases"]:
            raise ValueError("historical cohort no longer has the declared 30-case denominator")
        by_transaction, groups = {}, {}
        for group, ids in (("main", [c["case_id"] for c in cases]), ("repeat", manifest["repeat_case_ids"])):
            events, rows, calls = [], [], []
            quality, outcomes, issues, resources = [], Counter(), Counter(), Counter()
            for case_id in ids:
                require()
                suffix = "-repeat" if group == "repeat" else ""
                path = directory/"results"/(case_id+suffix+".json")
                input_paths.append(path)
                value = read_json(path)
                if value["case_id"] not in {case_id, case_id+suffix}:
                    raise ValueError("historical result is bound to another case")
                rows.append(value["elapsed_ms"])
                grade = value["grade"]
                quality.append(bool(grade["quality_pass"]))
                outcome = grade["outcome"]
                if outcome not in {"verified", "partial_evidence", "quality_failure", "correct_refusal", "failure", "execution_error", "insufficient_evidence"}:
                    outcome = "other_saved_outcome"
                outcomes[outcome] += 1
                for issue in grade.get("issues", []):
                    issues[error_family("generation_error", issue)] += 1
                resources["resource_stop" if value.get("error_type") == "MemoryError" else "other_error" if value.get("error_type") else "no_error"] += 1
                raw = value.get("record")
                status = raw["status"] if raw else "execution_error"
                if raw:
                    record = InvestigationRecord.model_validate(raw)
                    events.extend(project_run(record))
                if group == "main":
                    key = str(value["transaction_id"])
                    if key in by_transaction:
                        raise ValueError("main cohort contains a repeated transaction")
                    by_transaction[key] = status
                calls.extend(c for c in value.get("http_calls", []) if "ledger_sequence" in c)
            summary = summarize_events(events)
            responses = [c["response"] for c in calls if isinstance(c.get("response"), dict)]
            load_ms = [x["load_duration"]/1e6 for x in responses if isinstance(x.get("load_duration"), (int, float)) and not isinstance(x["load_duration"], bool)]
            tokens = {}
            for field in ("prompt_eval_count", "eval_count"):
                observed = [x[field] for x in responses if isinstance(x.get(field), int) and not isinstance(x[field], bool) and x[field] >= 0]
                tokens[field] = {"observed_responses": len(observed), "total_observed": sum(observed)}
            groups[group] = {"case_count": len(ids), "records_missing": len(ids)-summary["run_count"],
                "unique_main_not_claimed_for_repeats": group == "repeat", "operation_summary": summary,
                "case_end_to_end_latency": latency_summary(rows), "saved_quality_pass": sum(quality),
                "saved_outcomes": dict(outcomes), "saved_issue_families": dict(issues), "resource_outcomes": dict(resources),
                "actual_generation_send_attempts": len(calls), "response_token_observations": tokens,
                "ollama_reported_load_time": latency_summary(load_ms), "load_time_is_not_cold_warm_attestation": True,
                "schema_invalid_returned_responses": sum(bool(c.get("response")) and c.get("draft_schema_valid") is False for c in calls)}
            if group == "main":
                if summary["statuses"] != old_summary["first_statuses"] or sum(quality) != old_summary["first_quality_pass"]:
                    raise ValueError("historical result aggregation no longer agrees with its saved summary")
            write_json(output/f"{name}-{group}-safe-events.json", [e.model_dump(mode="json") for e in events])
        outputs[name] = groups
        pairings[name] = by_transaction
    before, after = pairings["route1"], pairings["route5"]
    if set(before) != set(after):
        raise ValueError("baseline/replay populations do not match")
    require_inputs_unchanged(root, inputs)
    return {"status": "passed", "cohorts": outputs,
            "paired_status_transitions": dict(Counter(before[key]+"->"+after[key] for key in before)),
            "paired_unique_transactions": len(before), "causal_quality_improvement_claimed": False,
            "labels_read": False, "new_real_model_calls": 0, "new_training_runs": 0}


def drift(root, output, config, require):
    import numpy as np
    import pandas as pd
    spec = config["drift"]
    directory = inside(root, spec["directory"])
    targets = directory/"targets-test.parquet"
    projected = directory/"projected.parquet"
    scores_path = directory/spec["score_file"]
    binding_path = directory/"prediction-bindings.json"
    inputs = pin_inputs(root, output, [targets, projected, scores_path, binding_path])
    binding = read_json(binding_path)
    if (binding["decision_sha256"] != spec["model_decision_sha256"]
        or inputs[scores_path.relative_to(root).as_posix()]["sha256"] != spec["score_sha256"]
        or binding["files"][spec["score_file"]] != file_binding(scores_path)):
        raise ValueError("historical score/decision binding changed")
    require()
    target = pd.read_parquet(targets, columns=["transaction_id", "source_transaction_dt_seconds"])
    times = target["source_transaction_dt_seconds"].to_numpy()
    if target["transaction_id"].duplicated().any() or not np.isfinite(times).all() or not target["source_transaction_dt_seconds"].is_monotonic_increasing:
        raise ValueError("historical score targets must be unique and chronological")
    fields = spec["feature_columns"]
    data = pd.read_parquet(projected, columns=["transaction_id", *fields])
    data = target.merge(data, on="transaction_id", how="left", sort=False, validate="one_to_one", indicator=True)
    if not (data["_merge"] == "both").all():
        raise ValueError("historical monitoring feature row is missing")
    values = np.load(scores_path, mmap_mode="r", allow_pickle=False)
    if values.shape != (len(target),):
        raise ValueError("score array and frozen target order differ")
    data["risk_score"] = values
    boundary = float(times[len(times)//2])
    earlier = times < boundary
    later = times >= boundary
    if not earlier.any() or not later.any() or not float(times[earlier].max()) < float(times[later].min()):
        raise ValueError("reference and current populations must have a strict time boundary")
    score_binding = hashlib.sha256((file_binding(binding_path)["sha256"]+spec["score_sha256"]).encode()).hexdigest()
    reference = {}
    for field in [*fields, "risk_score"]:
        reference[field] = freeze_reference(data.loc[earlier, field].tolist(), feature=field,
            model_binding=score_binding if field == "risk_score" else None, bins=spec["quantile_bins"],
            smoothing=spec["smoothing_count"], warning=spec["warning_psi"], alert=spec["alert_psi"])
    write_json(output/"reference.json", reference)  # Freeze before any current-population PSI.
    reference_hash = file_binding(output/"reference.json")
    reference = read_json(output/"reference.json")
    actual, synthetic = [], []
    for field, frozen in reference.items():
        require()
        current = data.loc[later, field].tolist()
        key = score_binding if field == "risk_score" else None
        actual.append(compare_population(frozen, current, model_binding=key))
        changed = list(current)
        if field == "transaction_amount":
            finite = data.loc[earlier, field].dropna()
            shift = max(float(finite.max()-finite.min()), 1)*spec["synthetic_amount_shift_reference_ranges"]
            changed = [v+shift if v is not None else None for v in changed]
        for i in range(int(len(changed)*spec["synthetic_missing_fraction"])):
            changed[i] = None
        synthetic.append(compare_population(frozen, changed, model_binding=key, population_kind="synthetic_shift"))
    if not any(v["status"] == "alert" for v in synthetic):
        raise ValueError("declared synthetic shift did not exercise an alert")
    if file_binding(output/"reference.json") != reference_hash:
        raise ValueError("reference was changed during comparison")
    require_inputs_unchanged(root, inputs)
    return {"status": "passed", "population": spec["population"], "reference_rows": int(earlier.sum()),
            "current_rows": int(later.sum()), "reference_time_max": float(times[earlier].max()),
            "current_time_min": float(times[later].min()), "reference_before_comparison": True,
            "reference_file": reference_hash, "actual_historical_drift": actual, "synthetic_drift": synthetic,
            "labels_read": False, "new_predictions": 0, "new_training_runs": 0,
            "model_quality_or_production_drift_claimed": False}


def verify_recovery_evidence(root, path, result):
    from graphrisk.monitoring.recovery import require_restored_equal
    required = {
        "real_database_disconnect_and_restart", "corrupt_backup_rejected_before_restore",
        "logical_restore_to_another_new_database", "policy_vector_rows_and_limited_role_restore",
        "create_idempotency_restored_without_generation",
        "pending_checkpoint_continues_and_terminal_decisions_stay_final",
        "target_mutations_do_not_change_source_database", "reserved_uncertain_and_settled_costs_preserved",
        "model_connect_failure_is_bounded", "model_transport_timeout_is_bounded",
        "total_deadline_stops_extra_attempts", "expired_policy_stops_generation",
        "stale_retrieval_version_rejected", "policy_injection_cannot_approve_auto_block",
        "wrong_graph_version_fails_closed_after_valid_control",
    }
    probes = result["probes"]
    if {p["name"] for p in probes} != required or len(probes) != len(required) or any(p["status"] != "passed" for p in probes):
        raise ValueError("required recovery/fault evidence is missing")
    comparison = require_restored_equal(read_json(path/"source-logical.json"), read_json(path/"restored-logical.json"))
    if comparison != result["logical_comparison"] or comparison["tables"] != 12:
        raise ValueError("recovery manifests do not match their acceptance binding")
    backup = result["backup"]
    archive = inside(root, backup["path"])
    if not archive.is_relative_to(root/"artifacts/stage-09/route7") or file_binding(archive) != {k: backup[k] for k in ("bytes", "sha256")}:
        raise ValueError("private backup is missing or changed")
    with archive.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise ValueError("private backup is not a PostgreSQL custom archive")
    cleanup = read_json(path/"sandbox-cleanup.json")
    boundary = read_json(path/"boundary-scan.json")
    if (not result["restore_verified"] or not result["source_and_restore_are_distinct_databases"]
        or not result["sandbox_removed"] or not cleanup["container_removed"]
        or not cleanup["password_file_removed"] or cleanup["old_containers_or_volumes_touched"]
        or boundary != result["boundary_scan"] or boundary["matches"] != 0 or boundary["checked_canaries"] < 6
        or result["workers"] != 1 or result["reviewer_authenticated"]
        or any(result[k] != 0 for k in ("real_model_calls", "real_dataset_fits", "paid_calls", "external_action_count"))):
        raise ValueError("recovery boundaries or cleanup are not verified")


def check(root, output, config, paths):
    results = {name: read_json(path/"result.json") for name, path in paths.items()}
    if any(v["status"] != "passed" for v in results.values()):
        raise ValueError("every operational acceptance phase must actually pass")
    for path in paths.values():
        if read_json(path/"contract-binding.json") != file_binding(SOURCE/"configs/stage09_operations.json"):
            raise ValueError("operational phases used another contract")
        if read_json(path/"resources.json")["status"] != "passed":
            raise ValueError("an operational phase violated its resource contract")
        require_inputs_unchanged(root, read_json(path/"input-bindings.json"))
    if (results["metrics"]["paired_unique_transactions"] != config["cohort_main_cases"]
        or results["drift"]["labels_read"] or not results["recovery"]["restore_verified"]
        or any(v["status"] != "passed" for v in results["recovery"]["probes"])):
        raise ValueError("incomplete route7 evidence")
    verify_recovery_evidence(root, paths["recovery"], results["recovery"])
    return {"status": "passed", "phases": results, "personal_learning": "Pending",
            "real_generation_calls": 0, "real_dataset_fits": 0, "paid_calls": 0,
            "production_readiness_claimed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("metrics", "drift", "recovery", "check"))
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--metrics-dir", type=Path)
    parser.add_argument("--drift-dir", type=Path)
    parser.add_argument("--recovery-dir", type=Path)
    args = parser.parse_args()
    root, output = args.runtime_root.resolve(), args.output_dir.resolve()
    if not output.is_relative_to(root/"reports/generated/stage09/route7"):
        parser.error("output-dir must be a new directory inside runtime reports/generated/stage09/route7")
    output.mkdir(parents=True, exist_ok=False)
    contract = SOURCE/"configs/stage09_operations.json"
    config = read_json(contract)
    if config["schema_version"] != "stage09-operations-contract-v1" or any(config[k] for k in ("real_training_allowed", "real_generation_allowed", "paid_calls_allowed")):
        raise ValueError("route7 contract forbids real training, generation and paid calls")
    write_json(output/"contract-binding.json", file_binding(contract))
    try:
        with CoverageMonitor(output/"resources.json", maximum_rss_mib=config["resources"]["max_process_rss_mib"], maximum_seconds=600) as monitor:
            if args.action in ("metrics", "drift"):
                result = {"metrics": metrics, "drift": drift}[args.action](root, output, config, monitor.require)
            elif args.action == "recovery":
                from graphrisk.demo.stage09_operations_recovery import recover
                result = recover(SOURCE, root, output, config, monitor.require)
            else:
                paths = {name: getattr(args, name+"_dir") for name in ("metrics", "drift", "recovery")}
                if any(path is None for path in paths.values()):
                    raise ValueError("check needs all three explicit phase directories")
                if any(not path.resolve().is_relative_to(root/"reports/generated/stage09/route7") for path in paths.values()):
                    raise ValueError("check only accepts route7 evidence directories")
                result = check(root, output, config, paths)
        if not (output/"input-bindings.json").exists():
            pin_inputs(root, output, [])
        write_json(output/"result.json", result)
        print(json.dumps({"action": args.action, "status": result["status"], "output": str(output)}, ensure_ascii=True), flush=True)
    except Exception as exc:
        import traceback
        # No exception messages or locals: those can contain DSNs or provider payloads.
        frames = [{"file": Path(f.filename).name, "function": f.name, "line": f.lineno}
                  for f in traceback.extract_tb(exc.__traceback__)]
        write_json(output/"failure.json", {"action": args.action, "status": "failed", "error_type": type(exc).__name__, "frames": frames})
        raise RuntimeError("route7 phase failed; original evidence preserved in its output directory") from None


if __name__ == "__main__":
    main()
