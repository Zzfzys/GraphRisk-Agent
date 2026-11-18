"""DeepSeek-only transport for the frozen Stage08 original simulated evaluation."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import time
from decimal import Decimal, InvalidOperation
import httpx
from graphrisk.agent.comparison_providers import ComparisonFailure, EXPERIMENT, new_row, post
from graphrisk.agent.hosted_provider import canonical
from graphrisk.agent.provider_budget import EvaluationBudget, BudgetStop
from graphrisk.agent.provider_comparison import ROOT, messages, reviewed_case, score

MODEL = "deepseek-flash"
BASE = "https://api.deepseek.com"


def load_environment(path: Path | None = None) -> None:
    path = path or ROOT / ".env.stage08-deepseek"
    if not path.exists():
        return
    allowed = {"DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "GRAPHRISK_STAGE08_LIVE"}
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or name.strip() not in allowed or name.strip() in values:
            raise ComparisonFailure("invalid_private_environment_file")
        values[name.strip()] = value.strip().strip('"').strip("'")
    for name, value in values.items():
        os.environ.setdefault(name, value)


def credentials() -> str:
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,256}", key):
        raise ComparisonFailure("authentication_unconfigured")
    if os.getenv("DEEPSEEK_BASE_URL", "").rstrip("/") not in (BASE, BASE + "/v1"):
        raise ComparisonFailure("official_endpoint_required")
    return key


def account_preflight(*, transport=None) -> dict:
    """Read-only authentication/model/balance probe, never print credentials or raw failures."""
    key = credentials()
    try:
        with httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                          timeout=20, headers={"Authorization": "Bearer " + key}) as client:
            response = client.get(BASE + "/models")
            if response.status_code != 200:
                raise ComparisonFailure("authentication_failed" if response.status_code in (401,403) else "model_listing_unavailable")
            ids = {m["id"] for m in response.json()["data"]}
            if MODEL not in ids:
                raise ComparisonFailure("required_model_unavailable")
            response = client.get(BASE + "/user/balance")
            if response.status_code != 200:
                raise ComparisonFailure("balance_unavailable")
            body = response.json()
        rows = [b for b in body["balance_infos"] if b["currency"] == "CNY"]
        if len(rows) != 1:
            raise ComparisonFailure("cny_balance_required")
        amount = Decimal(rows[0]["total_balance"])
        if not amount.is_finite() or amount < 0:
            raise ComparisonFailure("invalid_balance")
        return {"model_available": True, "balance_available": body.get("is_available") is True,
                "balance_cny": str(amount)}
    except ComparisonFailure:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError, InvalidOperation):
        raise ComparisonFailure("account_preflight_unavailable") from None


def validate_usage(body: dict, *, output_limit: int) -> dict:
    u = body.get("usage", {})
    names = ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
    if any(type(u.get(n)) is not int or u[n] < 0 for n in names):
        raise ComparisonFailure("invalid_usage")
    if u["total_tokens"] != u["prompt_tokens"] + u["completion_tokens"] or u["prompt_tokens"] != u["prompt_cache_hit_tokens"] + u["prompt_cache_miss_tokens"]:
        raise ComparisonFailure("invalid_usage")
    # Peak rates in micro-CNY/token: 0.04 hit, 2 miss, 8 output.
    # Deliberately retain the peak estimate even in off-peak hours until billing is reconciled.
    cost = (u["prompt_cache_hit_tokens"] * 4 + u["prompt_cache_miss_tokens"] * 200 + u["completion_tokens"] * 800 + 99) // 100
    return {**{n:u[n] for n in names}, "cost_micro_cny_upper":cost,
            "violation":not 0 < u["prompt_tokens"] <= 4096 or u["completion_tokens"] > output_limit,
            "reasoning_tokens":u.get("completion_tokens_details",{}).get("reasoning_tokens",0)}


def request_payload(case: dict, limit: int) -> dict:
    reviewed_case(case)
    return {"model":MODEL, "messages":messages(case), "temperature":0, "max_tokens":limit,
            "thinking":{"type":"disabled"}, "stream":False, "response_format":{"type":"json_object"}}


def deepseek_attempt(case: dict, budget: EvaluationBudget, *, live=False, transport=None,
                     expected_fingerprint: str | None = None) -> dict:
    reviewed_case(case)
    fake = type(transport) is httpx.MockTransport
    if not fake and (transport is not None or not live or os.getenv("GRAPHRISK_STAGE08_LIVE") != "1"):
        raise ComparisonFailure("live_opt_in_required")
    row = new_row(case, "deepseek_fake_http" if fake else "deepseek_hosted", MODEL)
    row.update(calibration_attempted=False, calibration=None, budget_attempt_ids=[],
               cost_state="not_sent", model_revision_guarantee="alias_and_observed_fingerprint_only")
    try:
        key = credentials()
        if key in canonical(messages(case)):
            raise ComparisonFailure("credential_in_input")
        # Only the frozen, short, original inputs may use this paid counting calibration.
        # Byte ceiling is an input-size guard, NOT a claim of exact vendor token counting.
        if sum(len(m["content"].encode()) for m in messages(case)) > 3500:
            raise ComparisonFailure("calibration_input_size_limit")
        deadline = time.perf_counter() + 125
        for limit, phase in ((1,"calibration"),(512,"generation")):
            attempt = budget.reserve(EXPERIMENT, "deepseek-" + case["case_id"] + ("-count" if limit == 1 else ""))
            row["budget_attempt_ids"].append(attempt)
            row["cost_state"] = "reserved_unknown"
            payload = request_payload(case,limit)
            if phase == "calibration": row["calibration_attempted"] = True
            else: row["attempted"] = True
            started = time.perf_counter()
            remaining = deadline - started
            if remaining <= 0:
                raise ComparisonFailure("deadline_exceeded")
            try:
                body = post(BASE + "/chat/completions", payload, headers={"Authorization":"Bearer " + key},
                            timeout=min(60,remaining), transport=transport)
            finally:
                elapsed = round((time.perf_counter()-started)*1000,3)
                row["calibration_wall_ms" if limit == 1 else "generation_wall_ms"] = elapsed
            usage = validate_usage(body,output_limit=limit)
            counter_mismatch = limit == 512 and usage["prompt_tokens"] != row["calibration"]["prompt_tokens"]
            budget.settle(attempt,usage["cost_micro_cny_upper"],violation=usage["violation"] or counter_mismatch)
            row[phase + "_usage"] = usage
            row["cost_state"] = "peak_rate_estimate_bill_pending"
            if usage["violation"] or counter_mismatch:
                raise ComparisonFailure("token_limit_or_counter_mismatch")
            if body.get("model") != MODEL:
                raise ComparisonFailure("model_revision_mismatch")
            fp = body.get("system_fingerprint")
            if not isinstance(fp,str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}",fp) or key in fp:
                raise ComparisonFailure("fingerprint_missing_or_invalid")
            if expected_fingerprint is not None and fp != expected_fingerprint:
                raise ComparisonFailure("backend_fingerprint_changed")
            row["system_fingerprint"] = fp
            expected_fingerprint = fp
            choices = body.get("choices")
            if not isinstance(choices,list) or len(choices)!=1:
                raise ComparisonFailure("invalid_response")
            message = choices[0].get("message",{})
            if not isinstance(message,dict) or message.get("role")!="assistant" or message.get("tool_calls") or message.get("reasoning_content") or message.get("refusal"):
                raise ComparisonFailure("unexpected_reasoning_or_tools")
            if usage["reasoning_tokens"]:
                raise ComparisonFailure("unexpected_reasoning_or_tools")
            content=message.get("content")
            if not isinstance(content,str) or key in content:
                raise ComparisonFailure("invalid_or_sensitive_output")
            if phase == "calibration":
                if choices[0].get("finish_reason") not in ("stop","length"):
                    raise ComparisonFailure("calibration_rejected")
                row["calibration"]={"prompt_tokens":usage["prompt_tokens"],"max_tokens":1,
                                    "messages_sha256":row["messages_sha256"],"wall_ms":elapsed}
                continue
            row.update(prompt_tokens=usage["prompt_tokens"],output_tokens=usage["completion_tokens"],raw_response=content,
                       estimated_generation_cost_cny=usage["cost_micro_cny_upper"]/1e6,
                       request_sha256=hashlib.sha256(canonical(payload).encode()).hexdigest())
            if choices[0].get("finish_reason") != "stop":
                raise ComparisonFailure("output_truncated_or_rejected")
            row["score"]=score(content,case)
    except (ComparisonFailure,BudgetStop) as error:
        row["error"]=str(error)
    except (ValueError,TypeError,KeyError,AttributeError):
        row["error"]="invalid_response"
    row["budget"]=budget.snapshot()
    return row
