"""Freeze Stage 10 inputs and exercise the runner WITHOUT any network or model load."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import yaml

from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.quality_contracts import QualityDraft
from graphrisk.agent.quality_reporting import QUALITY_PROMPT, OllamaQualityProvider
from graphrisk.demo.stage09_evaluation import digest, read_json, write_json
from graphrisk.demo.stage10_measurement import (
    AppendLedger, GuardedTransport, PROTOCOL, binding, load_protocol, select_cases, summarize,
)


def seal(value):
    return {**value, "content_sha256": digest(value)}


def checked(path):
    value = read_json(path)
    if digest({k: v for k, v in value.items() if k != "content_sha256"}) != value.get("content_sha256"):
        raise ValueError("sealed_input_changed:" + path.name)
    return value


def old_ids(value):
    """Read only identity metadata from historical manifests; no scores/labels used."""
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"transaction_id", "source_id"} and isinstance(child, (str, int)):
                found.add(str(child))
            elif isinstance(child, (list, dict)):
                found.update(old_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(old_ids(child))
    return found


def inventory(source):
    # Same coverage as the accepted layered runner; no generated outputs included.
    import sys
    sys.path.insert(0, str(source / "scripts"))
    from stage09_ci import source_inventory
    return source_inventory(source)


def ledger_for(root, manifest, *, fixture_path=None):
    parent = root / "reports/generated/stage09/generation-ledger.json"
    return AppendLedger(fixture_path or root / "reports/generated/stage10/generation-ledger.jsonl", parent,
                        manifest["parent_ledger"], manifest["content_sha256"],
                        "fixture" if fixture_path else "real", [c["run_key"] for c in manifest["queue"]])


def check_registered_source(registered, current):
    if any(current.get(name) != wanted for name, wanted in registered.items()):
        raise ValueError("registered_experiment_source_changed")
    # A later orchestration module can be added before observing outcomes, but is
    # NOT covered by route1 acceptance. Route2 must freeze these hashes separately.
    return {name: value for name, value in current.items() if name not in registered}


def freeze(source, root, output):
    if output.exists():
        raise ValueError("new_output_directory_required")
    if (root / "reports/generated/stage10/generation-ledger.jsonl").exists():
        raise ValueError("stage10_already_registered; check existing experiment, do not reset budget")
    contract = load_protocol(source / "configs/stage10_measurement.json")
    parent = root / "reports/generated/stage09/generation-ledger.json"
    if binding(parent)["sha256"] != contract["parent_ledger_sha256"]:
        raise ValueError("parent_ledger_drift")
    route0 = root / "reports/generated/stage10/route0/20261017-preflight-v2/acceptance.json"
    if binding(route0)["sha256"] != "ffd9e9fc4cb4255f895de1ad14c6010170cff2669918f5bc02a4d7d232c358ab":
        raise ValueError("route0_acceptance_changed")
    generation_config = root / "configs/stage09_coverage.yaml"
    config = load_stage05_config(generation_config)
    if (config.generation.model_id != contract["model_id"]
            or config.generation.model_revision != contract["model_revision"]
            or config.generation.endpoint != contract["endpoint"]
            or config.generation.max_output_tokens != 512 or config.report_mode != "quality_v1"):
        raise ValueError("enhanced_profile_generation_drift")
    profile_path = config.resolve_path(config.model_policy)
    profile = checked(profile_path)
    if profile["content_sha256"] != config.model_policy_sha256:
        raise ValueError("coverage_profile_drift")
    scores = root / profile["scores_path"]
    if profile["files"].get(profile["scores_path"]) != binding(scores):
        raise ValueError("coverage_scores_drift")
    # Parquet column projection explicitly excludes ALL scores and ALL labels.
    rows = pq.read_table(scores, columns=["transaction_id", "event_second", "split", "selection_cutoff"]).to_pylist()
    if len(rows) != 177162 or any(sum(r["split"] == split for r in rows) != 88581 for split in ("validation", "test")):
        raise ValueError("coverage_population_changed")
    for row in rows:
        if row["selection_cutoff"] != profile["splits"][row["split"]]["selection_cutoff"]:
            raise ValueError("fold_time_qualification_changed")
    parents = [root / name for name in [
        "reports/generated/stage09/route1/20261007-baseline-v1/cases.json",
        "reports/generated/stage09/route5a/20261008-quality-v1/cases.json",
        "reports/generated/stage09/route5a/20261008-quality-v2/cases.json",
        "reports/generated/stage09/route5b/20261015-coverage-verification-v3/frozen-cases.json",
    ]]
    parents += sorted((source / "data/evaluation").rglob("*.json"))
    parents += sorted((source / "data/evaluation").glob("*.yaml"))
    excluded = set()
    for path in parents:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) if path.suffix == ".yaml" else read_json(path)
        excluded.update(old_ids(value))
    cases = select_cases(rows, excluded)
    queue = [{"run_key": c["case_id"], "case_id": c["case_id"], "kind": "first"} for c in cases]
    queue += [{"run_key": cases[i]["case_id"] + "-repeat", "case_id": cases[i]["case_id"], "kind": "repeat"} for i in (0, 5)]
    runtime = [generation_config, profile_path, scores, parent, route0]
    runtime += [root / "artifacts/stage-05/policy/corpus-manifest.json",
                root / "artifacts/stage-05/policy/policy-index-tfidf.json"]
    for name, expected in profile["files"].items():
        if binding(root / name) != expected:
            raise ValueError("frozen_coverage_dependency_changed:" + name)
    manifest = seal({"version": contract["version"], "protocol": contract, "cases": cases, "queue": queue,
                     "parent_ledger": binding(parent), "source_inventory": inventory(source),
                     "runtime_inputs": {p.relative_to(root).as_posix(): binding(p) for p in runtime},
                     "exclusion_inputs": [{"root": "source" if p.is_relative_to(source) else "runtime",
                                           "path": p.relative_to(source if p.is_relative_to(source) else root).as_posix(),
                                           **binding(p)} for p in parents],
                     "excluded_id_count": len(excluded), "excluded_ids_sha256": digest(sorted(excluded)),
                     "excluded_ids_present_in_coverage": len(excluded & {r["transaction_id"] for r in rows}),
                     "selection": "per split sort(time,id), indices floor(i*(N-1)/4), i=0..4, after exclusion",
                     "selection_columns": ["transaction_id", "event_second", "split", "selection_cutoff"],
                     "quality": {"first_denominator": 10, "minimum_joint_pass": 9, "maximum_unsafe": 0},
                     "new_real_fits": 0, "new_real_llm_calls": 0, "paid_calls": 0})
    output.mkdir(parents=True)
    write_json(output / "manifest.json", manifest)
    # Canonical ledger across this entire stage; check never creates or resets it.
    ledger = ledger_for(root, manifest)
    ledger.initialize()
    write_json(output / "registration.json", seal({"manifest_sha256": manifest["content_sha256"],
               "ledger_relative_path": "reports/generated/stage10/generation-ledger.jsonl",
               "ledger_initial_state": ledger.state(), "route0_sha256": binding(route0)["sha256"]}))
    return {"status": "frozen", "first_cases": 10, "repeats": 2, "new_real_llm_calls": 0,
            "remaining_actual_request_budget": ledger.state()["remaining"]}


def check(source, root, output):
    manifest = checked(output / "manifest.json")
    registration = checked(output / "registration.json")
    load_protocol(source / "configs/stage10_measurement.json")
    if manifest["protocol"] != PROTOCOL:
        raise ValueError("experiment_source_or_protocol_drift")
    additions = check_registered_source(manifest["source_inventory"], inventory(source))
    if registration["manifest_sha256"] != manifest["content_sha256"]:
        raise ValueError("registration_binding_changed")
    for name, expected in manifest["runtime_inputs"].items():
        if binding(root / name) != expected:
            raise ValueError("runtime_input_changed:" + name)
    config = load_stage05_config(root / "configs/stage09_coverage.yaml")
    profile = checked(config.resolve_path(config.model_policy))
    for name, expected in profile["files"].items():
        if binding(root / name) != expected:
            raise ValueError("coverage_dependency_changed:" + name)
    for item in manifest["exclusion_inputs"]:
        path = (source if item["root"] == "source" else root) / item["path"]
        if binding(path) != {k: item[k] for k in ("bytes", "sha256")}:
            raise ValueError("exclusion_input_changed")
    # Mutable, append-only ledger is checked semantically instead of pinning old bytes.
    return {"status": "passed", "manifest_sha256": manifest["content_sha256"],
            "first_cases": len(manifest["cases"]), "queue": len(manifest["queue"]),
            "additional_source_files_not_covered_until_route2_registration": additions,
            "ledger": ledger_for(root, manifest).state()}


def dry_run(source, root, output, result_dir):
    check(source, root, output)
    if result_dir.exists():
        raise ValueError("new_dry_run_directory_required")
    result_dir.mkdir(parents=True)
    manifest = checked(output / "manifest.json")
    before = ledger_for(root, manifest).state()
    fixture = ledger_for(root, manifest, fixture_path=result_dir / "fixture-ledger.jsonl")
    fixture.initialize()
    config = load_stage05_config(root / "configs/stage09_coverage.yaml")
    samples = []
    packet = {"fixture_only": True, "no_real_transaction_or_tool_evidence": True}
    draft = {"policy_ref": "P1", "history_activity": "none", "relationship_evidence": "none",
             "graph_evidence": "unavailable", "policy_applicability": "not_established",
             "risk_interpretation": "uncalibrated_signal", "evidence_refs": ["F2", "F3", "F4", "F5"],
             "unknowns": ["policy_conditions", "verified_identity", "calibration", "graph_evidence"]}
    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={"models": [{"name": PROTOCOL["model_id"], "digest": PROTOCOL["model_revision"]}]})
        return httpx.Response(200, json={"model": PROTOCOL["model_id"], "done": True, "response": json.dumps(draft)})
    for item in manifest["queue"]:
        # Fake monotonic clock is deliberate, so these timings are NOT performance.
        ticks = iter((1., 1.025))
        guard = GuardedTransport(httpx.MockTransport(handle), fixture, item["run_key"], digest(packet), clock=lambda: next(ticks))
        with httpx.Client(transport=guard, trust_env=False) as client:
            OllamaQualityProvider(config.generation, client).with_deadline(lambda: 100.).generate(
                QUALITY_PROMPT + "\nEVIDENCE_JSON:\n" + json.dumps(packet), QualityDraft.model_json_schema())
        samples.extend(guard.samples)
    if ledger_for(root, manifest).state() != before:
        raise ValueError("real_ledger_changed_during_fixture_run")
    value = seal({"status": "passed", "mode": "mock_http_and_fake_clock_only", "performance_evidence": False,
                  "quality_evidence": False, "samples": [s.model_dump() for s in samples],
                  "summary": summarize(samples, 12), "fixture_ledger": fixture.state(), "real_ledger": before,
                  "new_real_llm_calls": 0, "paid_calls": 0})
    write_json(result_dir / "result.json", value)
    return {k: value[k] for k in ("status", "mode", "quality_evidence", "performance_evidence", "new_real_llm_calls")}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("freeze", "check", "dry-run"))
    p.add_argument("--runtime-root", type=Path, default=Path("E:/GraphRisk-Agent"))
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--result-dir", type=Path)
    args = p.parse_args()
    source = Path(__file__).resolve().parents[3]
    root, output = args.runtime_root.resolve(), args.output_dir.resolve()
    base = root / "reports/generated/stage10/route1"
    if not output.is_relative_to(base) or output == base:
        p.error("dedicated ignored stage10/route1 output directory required")
    if args.action == "dry-run":
        if args.result_dir is None or not args.result_dir.resolve().is_relative_to(output):
            p.error("dry-run needs a new --result-dir inside --output-dir")
        result = dry_run(source, root, output, args.result_dir.resolve())
    else:
        result = freeze(source, root, output) if args.action == "freeze" else check(source, root, output)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
