"""Explicit evaluation transports; never wired into the default investigation service."""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from urllib.parse import urlsplit

import httpx

from graphrisk.agent.hosted_provider import QwenEvaluationConfig, canonical, _strict_json
from graphrisk.agent.local_probe import LocalModelSpec, ProbeConfig, verify_model, running_model
from graphrisk.agent.provider_budget import BudgetStop, EvaluationBudget
from graphrisk.agent.provider_comparison import messages, message_hash, reviewed_case, score

MODEL = "qwen-plus-2025-12-01"
EXPERIMENT = "stage08-route3-v1"


class ComparisonFailure(ValueError):
    pass


def stop_after(row: dict) -> bool:
    """An exhausted output cap fails this case; integrity/transport failures stop the run."""
    return row.get("error") not in (None, "output_truncated", "output_truncated_or_rejected")



async def _post(url, payload, headers, timeout, transport):
    async def request():
        async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                                     timeout=timeout) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code != 200:
                    code = ("authentication_failed" if response.status_code in (401, 403) else
                            "rate_limited" if response.status_code == 429 else
                            "redirect_rejected" if 300 <= response.status_code < 400 else "upstream_rejected")
                    raise ComparisonFailure(code)
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise ComparisonFailure("response_too_large")
                body = _strict_json(raw.decode("utf-8"))
                if not isinstance(body, dict):
                    raise ComparisonFailure("invalid_response")
                return body
    try:
        return await asyncio.wait_for(request(), timeout=timeout)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        raise ComparisonFailure("transport_timeout") from None
    except httpx.HTTPError:
        raise ComparisonFailure("transport_unavailable") from None
    except (UnicodeError, ValueError) as error:
        if isinstance(error, ComparisonFailure):
            raise
        raise ComparisonFailure("invalid_response") from None


def post(url, payload, *, headers=None, timeout=60.0, transport=None):
    return asyncio.run(_post(url, payload, headers or {}, timeout, transport))


def new_row(case: dict, provider: str, model: str) -> dict:
    return {"case_id": case["case_id"], "group": case["group"], "provider": provider,
            "model": model, "messages_sha256": message_hash(case), "attempted": False,
            "error": None, "raw_response": None, "generation_wall_ms": 0,
            "prompt_tokens": None, "output_tokens": None, "score": score(None, case),
            "estimated_generation_cost_cny": None}


def local_attempt(case: dict, spec: LocalModelSpec, *, transport=None) -> dict:
    reviewed_case(case)
    row = new_row(case, "ollama", spec.model)
    row["digest"] = spec.digest
    config = ProbeConfig()
    try:
        with httpx.Client(transport=transport, trust_env=False, timeout=10) as client:
            verify_model(client, config, spec)
            before = running_model(client, config, spec)
        row["thermal_state"] = "resident" if before else "not_resident"
        # Open local Qwen tokenizers are byte-based; leave template and output headroom.
        # This is a local conservative guard, never used as the hosted model's counter.
        if sum(len(m["content"].encode()) for m in messages(case)) + 128 + 512 > 4096:
            raise ComparisonFailure("local_input_byte_guard")
        started = time.perf_counter()
        row["attempted"] = True
        try:
            body = post(config.endpoint + "/api/chat", {"model": spec.model, "messages": messages(case),
                "format": "json", "stream": False, "think": False, "keep_alive": "5m",
                "options": {"temperature": 0, "seed": 20260929, "num_ctx": 4096, "num_predict": 512}},
                transport=transport)
        finally:
            row["generation_wall_ms"] = round((time.perf_counter()-started)*1000, 3)
        for source, target in (("prompt_eval_count", "prompt_tokens"), ("eval_count", "output_tokens")):
            if type(body.get(source)) is not int or body[source] < 0:
                raise ComparisonFailure("invalid_usage")
            row[target] = body[source]
        if row["prompt_tokens"] > 4096 or row["output_tokens"] > 512 or sum((row["prompt_tokens"], row["output_tokens"])) > 4096:
            raise ComparisonFailure("token_limit_exceeded")
        for name in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration"):
            if type(body.get(name)) is not int or body[name] < 0:
                raise ComparisonFailure("invalid_timing")
            row[name] = body[name]
        if body.get("model") != spec.model or body.get("done") is not True:
            raise ComparisonFailure("incomplete_or_wrong_model")
        message = body.get("message", {})
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ComparisonFailure("invalid_output")
        if message.get("thinking") or message.get("tool_calls"):
            raise ComparisonFailure("unexpected_reasoning_or_tools")
        if not isinstance(message.get("content"), str):
            raise ComparisonFailure("invalid_output")
        row["raw_response"] = message["content"]
        if body.get("done_reason") != "stop":
            raise ComparisonFailure("output_truncated")
        row["score"] = score(message["content"], case)
        with httpx.Client(transport=transport, trust_env=False, timeout=10) as client:
            verify_model(client, config, spec)
            after = running_model(client, config, spec)
        if not after or after.get("context_length") != 4096:
            raise ComparisonFailure("runtime_context_mismatch")
        row["ps_after"] = after
        row["estimated_generation_cost_cny"] = 0  # hosted API charges only; not electricity
    except (ComparisonFailure, ValueError, TypeError, KeyError, AttributeError, httpx.HTTPError) as error:
        row["error"] = str(error) if isinstance(error, ComparisonFailure) else "local_integrity_or_transport_failure"
    return row


def hosted_attempt(case: dict, config: QwenEvaluationConfig, budget: EvaluationBudget,
                   *, transport=None, live=False) -> dict:
    reviewed_case(case)
    if config.experiment_id != EXPERIMENT:
        raise ComparisonFailure("experiment_identity_mismatch")
    fake = type(transport) is httpx.MockTransport
    if not fake and (transport is not None or not live or os.getenv("GRAPHRISK_STAGE08_LIVE") != "1"):
        raise ComparisonFailure("live_opt_in_required")
    row = new_row(case, "qwen_fake_http" if fake else "qwen_hosted", MODEL)
    row.update(tokenization_attempted=False, tokenization_wall_ms=None,
               tokenization_reservation_cny=0, cost_state="not_sent", budget_attempt_ids=[])
    key = os.getenv(config.api_key_env, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,256}", key):
        row["error"] = "authentication_unconfigured"
        return row
    if key in canonical(messages(case)):
        row["error"] = "credential_in_input"
        return row
    headers = {"Authorization": "Bearer " + key}
    tokenizer = "https://" + urlsplit(config.endpoint).hostname + "/api/v1/tokenizer"
    deadline = time.perf_counter() + 125
    try:
        attempt = budget.reserve(EXPERIMENT, case["case_id"] + "-tokenize")
        row["budget_attempt_ids"].append(attempt)
        row["cost_state"] = "reserved_unknown"
        row["tokenization_reservation_cny"] = 0.10
        row["tokenization_attempted"] = True
        started = time.perf_counter()
        try:
            counted = post(tokenizer, {"model": MODEL, "input": {"messages": messages(case)},
                           "parameters": {"enable_thinking": False}}, headers=headers, transport=transport)
        finally:
            row["tokenization_wall_ms"] = round((time.perf_counter()-started)*1000, 3)
        count = counted.get("usage", {}).get("input_tokens")
        if type(count) is not int or not 0 < count <= config.max_input_tokens:
            raise ComparisonFailure("unverified_or_excessive_input_tokens")
        if counted.get("model", MODEL) != MODEL:
            raise ComparisonFailure("tokenizer_model_mismatch")
        row["provider_input_tokens"] = count
        # Tokenization price is not established: retain the whole reservation.
        # This is accounted budget, NOT a claim of actual billed cost.
        budget.settle(attempt, EvaluationBudget.RESERVATION)
        row["cost_state"] = "tokenization_conservative_hold"
        attempt = budget.reserve(EXPERIMENT, case["case_id"])
        row["budget_attempt_ids"].append(attempt)
        row["cost_state"] = "generation_reserved_unknown"
        payload = {"model": MODEL, "messages": messages(case), "temperature": 0,
                   "max_tokens": config.max_output_tokens, "enable_thinking": False,
                   "enable_search": False, "stream": False, "response_format": {"type": "json_object"}}
        row["request_sha256"] = hashlib.sha256(canonical(payload).encode()).hexdigest()
        row["attempted"] = True
        started = time.perf_counter()
        try:
            remaining = deadline - started
            if remaining <= 0:
                raise ComparisonFailure("deadline_exceeded")
            body = post(config.endpoint, payload, headers=headers, transport=transport,
                        timeout=min(config.timeout_seconds, remaining))
        finally:
            row["generation_wall_ms"] = round((time.perf_counter()-started)*1000, 3)
        usage = body.get("usage", {})
        prompt, output = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if any(type(v) is not int or v < 0 for v in (prompt, output)) or type(usage.get("total_tokens")) is not int or usage["total_tokens"] != prompt + output:
            raise ComparisonFailure("invalid_usage")
        row.update(prompt_tokens=prompt, output_tokens=output)
        violation = prompt > count or prompt > config.max_input_tokens or output > config.max_output_tokens
        cost = (prompt * 8 + output * 20 + 9) // 10
        budget.settle(attempt, cost, violation=violation)
        row["estimated_generation_cost_cny"] = cost / 1e6
        row["cost_state"] = "violation" if violation else "usage_estimate_plus_tokenization_hold"
        if violation:
            raise ComparisonFailure("token_limit_or_counter_mismatch")
        if body.get("model") != MODEL:
            raise ComparisonFailure("model_revision_mismatch")
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ComparisonFailure("invalid_response")
        choice = choices[0]
        if choice.get("finish_reason") != "stop":
            raise ComparisonFailure("output_truncated_or_rejected")
        message = choice.get("message", {})
        if message.get("role") != "assistant" or message.get("tool_calls") or message.get("reasoning_content") or message.get("refusal"):
            raise ComparisonFailure("unexpected_reasoning_or_tools")
        content = message.get("content")
        if not isinstance(content, str):
            raise ComparisonFailure("invalid_output")
        if key in content:
            raise ComparisonFailure("credential_reflected")
        row["raw_response"] = content
        row["score"] = score(content, case)
    except (ComparisonFailure, BudgetStop) as error:
        row["error"] = str(error)
    except (TypeError, KeyError, AttributeError, ValueError):
        row["error"] = "invalid_response"
    row["budget"] = budget.snapshot()
    return row
