"""Measure two pinned local models without changing the default service."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import httpx
import yaml

from graphrisk.agent.local_probe import (
    LocalModelSpec, ProbeConfig, ResourceSampler, host_resources, probe_once,
    summarize, unload, verify_model, idle_resource_errors,
)

ROOT = Path(__file__).resolve().parents[1]


def process_memory() -> object:
    if os.name != "nt":
        return {"status": "unavailable"}
    try:
        command = ("Get-Process *ollama*,*llama* -ErrorAction SilentlyContinue | "
                   "Select-Object ProcessName,Id,WorkingSet64,PrivateMemorySize64 | ConvertTo-Json -Compress")
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                           capture_output=True, text=True, timeout=15,
                           creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        return json.loads(p.stdout) if p.stdout.strip() else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"status": "unavailable"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/stage08_local_probe.yaml")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    config = ProbeConfig.model_validate(settings["measurement"])
    specs = [LocalModelSpec.model_validate(s) for s in settings["models"]]
    if len(specs) != 2 or len({s.model for s in specs}) != 2:
        raise ValueError("Exactly two distinct pinned models are required")
    raw = (ROOT / settings["suite"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != settings["suite_sha256"]:
        raise ValueError("local smoke suite hash mismatch")
    suite = json.loads(raw)
    if len(suite["cases"]) != 3 or suite["provenance"] != "project-original-simulated":
        raise ValueError("Expected the three reviewed original simulated smoke cases")
    output = args.output or ROOT / f"reports/generated/stage08/route2/local-{uuid4().hex}.json"
    if output.exists():
        raise FileExistsError("Use a new output path to retain prior measurements")
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence = {
        "created_at": datetime.now(UTC).isoformat(), "purpose": suite["purpose"],
        "suite_sha256": settings["suite_sha256"], "measurement": config.model_dump(),
        "cold_definition": "model absent from api/ps after unload; OS disk cache is not flushed",
        "quality_benchmark": False, "paid_api_calls": 0, "models": [],
        "host_before": host_resources(), "processes_before": process_memory(),
    }

    def save():
        temporary = output.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(output)

    save()
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
        evidence["ollama_version"] = client.get(config.endpoint + "/api/version").raise_for_status().json()
        initial_ps = client.get(config.endpoint + "/api/ps").raise_for_status().json()["models"]
        evidence["initial_ps"] = initial_ps
        unrelated = [m["name"] for m in initial_ps if m["name"] not in {s.model for s in specs}]
        if unrelated:
            raise ValueError("Other models are resident; run this benchmark in an idle Ollama window")
        for spec in specs:
            verify_model(client, config, spec)
            unload(client, config, spec)
        evidence["resource_preflight"] = {"samples": [], "errors": []}
        for index in range(3):
            snapshot = host_resources()
            evidence["resource_preflight"]["samples"].append(snapshot)
            evidence["resource_preflight"]["errors"].extend(idle_resource_errors(snapshot))
            if index < 2:
                time.sleep(1)
        evidence["resource_preflight"]["errors"] = sorted(set(evidence["resource_preflight"]["errors"]))
        save()
        if evidence["resource_preflight"]["errors"]:
            evidence["status"] = "resource_preflight_blocked"
            save()
            print(json.dumps({"output": str(output), "status": evidence["status"],
                              "errors": evidence["resource_preflight"]["errors"]}), flush=True)
            return 2
        for spec in specs:
            # Unload only the explicitly selected comparison models.
            for selected in specs:
                unload(client, config, selected)
            entry = verify_model(client, config, spec)
            info = client.post(config.endpoint + "/api/show", json={"model": spec.model}).raise_for_status().json()
            metadata = {"tag": entry, "show_details": info.get("details"),
                        "capabilities": info.get("capabilities"), "model_info": info.get("model_info"),
                        "license_sha256": hashlib.sha256(info.get("license", "").encode()).hexdigest()}
            run = {"model": spec.model, "digest": spec.digest, "metadata": metadata,
                   "host_before": host_resources(), "processes_before": process_memory(), "rows": []}
            evidence["models"].append(run)
            try:
                with ResourceSampler(config.endpoint) as sampler:
                    tasks = [("cold", suite["cases"][0])] + [
                        ("warm", suite["cases"][0]) for _ in range(config.warm_repeats)
                    ] + [("safety_smoke", case) for case in suite["cases"][1:]]
                    for phase, case in tasks:
                        row = probe_once(client, config, spec, case, phase=phase)
                        run["rows"].append(row)
                        run["summary"] = summarize(run["rows"])
                        save()
                        print(json.dumps({"model": spec.model, "phase": phase, "case": case["id"],
                                          "wall_ms": row["wall_ms"], "passed": row["expected_match"],
                                          "error": row["error"]}), flush=True)
                        if row["error"] in {"transport_timeout", "transport_error", "deadline_exceeded"}:
                            break
                run["resource_samples"] = sampler.samples
                run["processes_after"] = process_memory()
                run["host_after"] = host_resources()
                try:
                    p = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=10,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    run["ollama_ps_cli"] = p.stdout.strip() if p.returncode == 0 else "unavailable"
                except (OSError, subprocess.SubprocessError):
                    run["ollama_ps_cli"] = "unavailable"
            finally:
                unload(client, config, spec)
                save()
        evidence["host_after"] = host_resources()
        evidence["processes_after"] = process_memory()
        evidence["all_calls_passed"] = all(
            len(m["rows"]) == config.warm_repeats + 3 and m["summary"]["passed"] == len(m["rows"])
            for m in evidence["models"])
        save()
    print(json.dumps({"output": str(output), "all_calls_passed": evidence["all_calls_passed"]}))
    # Capability failures remain in evidence and cause a nonzero exit, never silently pass.
    return 0 if evidence["all_calls_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())