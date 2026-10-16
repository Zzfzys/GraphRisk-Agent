"""Explicit offline Qwen protocol probe; never opens a network transport."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import yaml

from graphrisk.agent.hosted_provider import ProviderFailure, QwenEvaluationConfig, QwenReportProvider, ReviewedCase
from graphrisk.agent.provider_budget import EvaluationBudget
from graphrisk.agent.reporting import ReportDraft

SMOKE_SHA256 = "f0fa24ca82b3841cc8336e385e68e2c7c50fc4a6a905141b7821e7ae7d6fc61f"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=["qwen-fake-http"])
    parser.add_argument("--scenario", choices=["ok", "wrong_citation", "truncated", "timeout"], default="ok")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    case = ReviewedCase.load(root / "data/evaluation/stage08/provider_contract_smoke.json", SMOKE_SHA256)
    contract = yaml.safe_load((root / "configs/stage08_experiment.yaml").read_text(encoding="utf-8"))
    run = uuid4().hex
    output = args.output or root / f"reports/generated/stage08/provider-{run}.json"
    ledger = EvaluationBudget(root / f"reports/generated/stage08/fake-budgets/{run}.sqlite3", stop_cny=contract["budget"]["stop_threshold_cny"], experiment_cny=contract["budget"]["experiment_cap_cny"])
    calls = []

    def handler(request):
        calls.append(1)
        if args.scenario == "timeout":
            raise httpx.ReadTimeout("simulated")
        draft = {"transaction_id": case.transaction_id, "dataset_source": "synthetic",
                 "evidence_ids": list(case.evidence_ids),
                 "policy_choices": [{"chunk_id": "invented" if args.scenario == "wrong_citation" else case.policy_chunk_ids[0]}],
                 "uncertainty": case.uncertainty, "suggested_action": "human_review"}
        return httpx.Response(200, json={"model": "qwen-plus-2025-12-01",
            "usage": {"prompt_tokens": 300, "completion_tokens": 80, "total_tokens": 380},
            "choices": [{"finish_reason": "length" if args.scenario == "truncated" else "stop",
                         "message": {"role": "assistant", "content": json.dumps(draft)}}]})

    config = QwenEvaluationConfig(endpoint="https://route1-fixture.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
                                  experiment_id="route1-smoke", model_id=contract["hosted"]["model"],
                                  max_input_tokens=contract["comparison"]["input_token_limit"],
                                  max_output_tokens=contract["comparison"]["output_token_limit"],
                                  timeout_seconds=float(contract["comparison"]["request_timeout_seconds"]))
    provider = QwenReportProvider(config, case, ledger, transport=httpx.MockTransport(handler))
    result = {"transport": "fake_http", "scenario": args.scenario, "actual_hosted_calls": 0,
              "actual_hosted_cost_cny": 0, "quality_evaluation": False}
    with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "route1-fake-key"}):
        try:
            generated = provider.generate(case.prompt, ReportDraft.model_json_schema())
            result.update(status="ok", model=generated.model_id, request_sha256=generated.request_sha256,
                          simulated_usage={"input": generated.prompt_tokens, "output": generated.output_tokens},
                          simulated_cost_cny=generated.estimated_cost_cny)
        except ProviderFailure as error:
            result.update(status="rejected", error=asdict(error.audit))
    result.update(fake_http_attempts=len(calls), simulated_ledger=ledger.snapshot())
    expected = {"ok": None, "wrong_citation": "output_evidence_mismatch", "truncated": "output_truncated", "timeout": "transport_timeout"}[args.scenario]
    result["contract_passed"] = result.get("error", {}).get("code") == expected
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result))
    return 0 if result["contract_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())