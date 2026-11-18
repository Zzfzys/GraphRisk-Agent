"""Run the frozen twenty-case provider comparison without changing default services."""
from __future__ import annotations
import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import time
from uuid import uuid4
import httpx
import yaml
from graphrisk.agent.comparison_providers import EXPERIMENT, hosted_attempt, local_attempt, stop_after
from graphrisk.agent.hosted_provider import QwenEvaluationConfig
from graphrisk.agent.local_probe import (LocalModelSpec, ProbeConfig, ResourceSampler,
    host_resources, idle_resource_errors, unload)
from graphrisk.agent.provider_budget import EvaluationBudget
from graphrisk.agent.provider_comparison import (ROOT, SUITE_SHA256, CONTRACT_SHA256,
    load_suite, messages, message_hash, aggregate)

def load_private_env():
    path = ROOT / ".env.stage08-qwen"
    if not path.exists():
        return
    allowed = {"DASHSCOPE_API_KEY", "GRAPHRISK_QWEN_ENDPOINT", "GRAPHRISK_STAGE08_LIVE"}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or name.strip() not in allowed:
            raise ValueError("invalid_private_environment_file")
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=["prepare", "ollama-baseline", "ollama-candidate", "qwen", "deepseek"])
    parser.add_argument("--live", action="store_true", help="Explicitly opt into paid hosted evaluation")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cases = load_suite()
    output = args.output or ROOT / f"reports/generated/stage08/route3/{args.provider}-{uuid4().hex}.json"
    if output.exists():
        raise ValueError("output_exists_use_new_path")
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {"created_at": datetime.now(UTC).isoformat(), "provider": args.provider,
              "suite_sha256": SUITE_SHA256, "contract_sha256": CONTRACT_SHA256,
              "real_model_evaluation": False, "status": "preflight",
              "rows": [], "actual_bill_cny": None, "external_actions": 0,
              "automatic_retries": 0, "scope": "controlled_evidence_decisions_not_end_to_end_agent"}
    def save():
        result["real_model_evaluation"] = any(r.get("attempted") for r in result["rows"])
        result["summary"] = aggregate(result["rows"], cases)
        temporary = output.with_suffix(".json.tmp")
        temporary.write_bytes((json.dumps(result, ensure_ascii=False, indent=2)+"\n").encode())
        temporary.replace(output)
    def record(row):
        result["rows"].append(row)
        save()
        print(json.dumps({"case_id": row["case_id"], "attempted": row["attempted"],
                          "passed": row["score"]["passed"] and row["error"] is None,
                          "error": row["error"] or row["score"]["error"]}), flush=True)
    save()
    if args.provider == "prepare":
        result["status"] = "prepared_not_run"
        result["requests"] = [{"case_id": c["case_id"], "messages_sha256": message_hash(c),
                              "messages": messages(c)} for c in cases]
        save()
        print(json.dumps({"output": str(output), "status": result["status"], "cases": 20}))
        return 0
    if args.provider == "deepseek":
        from decimal import Decimal
        from graphrisk.agent.deepseek_comparison import load_environment, account_preflight, deepseek_attempt
        from graphrisk.agent.comparison_providers import ComparisonFailure
        try:
            load_environment()
            result["account_before"] = account_preflight()
            if not result["account_before"]["balance_available"]:
                raise ComparisonFailure("insufficient_balance")
            if not args.live or os.getenv("GRAPHRISK_STAGE08_LIVE") != "1":
                result["status"] = "authentication_passed_generation_not_enabled"
                save()
                print(json.dumps({"status":result["status"],"output":str(output)}))
                return 2
            budget = EvaluationBudget(ROOT / "reports/generated/stage08/real-budget.sqlite3")
            result["ledger_before"] = budget.snapshot()
            fingerprint = None
            for case in cases:
                row = deepseek_attempt(case,budget,live=True,expected_fingerprint=fingerprint)
                record(row)
                fingerprint = row.get("system_fingerprint",fingerprint)
                if stop_after(row):
                    result["status"] = "stopped_provider_or_budget_failure"
                    break
            else:
                result["status"] = "measured_bill_reconciliation_pending"
            result["ledger"] = budget.snapshot()
            result["calibration_requests"] = sum(r.get("calibration_attempted",False) for r in result["rows"])
            result["generation_requests"] = sum(r.get("attempted",False) for r in result["rows"])
            save()
            try:
                result["account_after"] = account_preflight()
                result["account_balance_delta_cny"] = str(Decimal(result["account_before"]["balance_cny"]) - Decimal(result["account_after"]["balance_cny"]))
                result["billing_evidence"] = "account_wide_delta_not_itemized_receipt"
            except ComparisonFailure as error:
                result["balance_after_error"] = str(error)
        except ComparisonFailure as error:
            result["status"] = "not_run_or_stopped_preflight"
            result["error"] = str(error)
            save()
            print(json.dumps({"status":result["status"],"error":result["error"],"output":str(output)}))
            return 2
    elif args.provider == "qwen":
        load_private_env()
        result["configuration_present"] = {name: bool(os.getenv(name)) for name in ("DASHSCOPE_API_KEY", "GRAPHRISK_QWEN_ENDPOINT")}
        if not args.live or os.getenv("GRAPHRISK_STAGE08_LIVE") != "1":
            result["status"] = "not_run_live_opt_in_required"
            save()
            return 2
        if not os.getenv("DASHSCOPE_API_KEY") or not os.getenv("GRAPHRISK_QWEN_ENDPOINT"):
            result["status"] = "not_run_credentials_or_endpoint_missing"
            save()
            return 2
        config = QwenEvaluationConfig(endpoint=os.environ["GRAPHRISK_QWEN_ENDPOINT"], experiment_id=EXPERIMENT)
        budget = EvaluationBudget(ROOT / "reports/generated/stage08/real-budget.sqlite3")
        for case in cases:
            row = hosted_attempt(case, config, budget, live=True)
            record(row)
            result["ledger"] = budget.snapshot()
            if stop_after(row):
                result["status"] = "stopped_provider_or_budget_failure"
                break
        else:
            result["status"] = "measured_bill_reconciliation_pending"
        result["ledger"] = budget.snapshot()
    else:
        settings = yaml.safe_load((ROOT / "configs/stage08_local_probe.yaml").read_text())
        specs = [LocalModelSpec.model_validate(s) for s in settings["models"]]
        spec = specs[0 if args.provider == "ollama-baseline" else 1]
        config = ProbeConfig()
        result.update(model=spec.model, digest=spec.digest, host_before=host_resources())
        with httpx.Client(trust_env=False, timeout=10) as client:
            active = client.get(config.endpoint + "/api/ps").raise_for_status().json()["models"]
            if any(m["name"] not in {s.model for s in specs} for m in active):
                result["status"] = "not_run_other_models_active"
                save()
                return 2
            for selected in specs:
                unload(client, config, selected)
            result["idle_preflight"] = []
            for _ in range(3):
                snapshot = host_resources()
                result["idle_preflight"].append(snapshot)
                if idle_resource_errors(snapshot):
                    result["status"] = "not_run_resources_busy"
                    save()
                    return 2
                time.sleep(1)
            try:
                with ResourceSampler(config.endpoint) as sampler:
                    for case in cases:
                        row = local_attempt(case, spec)
                        record(row)
                        if stop_after(row):
                            result["status"] = "stopped_local_protocol_failure"
                            break
                    else:
                        result["status"] = "measured"
                result["resource_samples"] = sampler.samples
            finally:
                unload(client, config, spec)
                save()
    save()
    print(json.dumps({"output": str(output), "status": result["status"], "summary": result["summary"]}))
    return 0 if result["summary"]["passed"] == 20 else 1

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, httpx.HTTPError):
        print(json.dumps({"error": "evaluation_preflight_or_storage_failed",
                          "guidance": "Check fixed suite/config, private environment and local Ollama; retain any partial result."}))
        raise SystemExit(2) from None

