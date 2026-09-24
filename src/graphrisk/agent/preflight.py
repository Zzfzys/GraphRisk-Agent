"""Reproducible local LangGraph and Ollama smoke checks for Stage 05 route 0."""

from __future__ import annotations

import json
import time
from importlib.metadata import metadata, version
from typing import Any

import httpx
from langgraph.graph import END, START, StateGraph

from graphrisk.agent.config import Stage05Config


_SMOKE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["needs_review"]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "evidence_ids"],
    "additionalProperties": False,
}


def check_fake_stategraph() -> dict[str, str]:
    graph = StateGraph(dict)
    graph.add_node("fake_provider", lambda state: {"result": "fake:" + state["request"]})
    graph.add_edge(START, "fake_provider")
    graph.add_edge("fake_provider", END)
    result = graph.compile().invoke({"request": "route0"})
    if result != {"result": "fake:route0"}:
        raise RuntimeError("LangGraph fake-provider state graph returned unexpected state")
    package = metadata("langgraph")
    return {
        "version": version("langgraph"),
        "license": package.get("License-Expression") or package.get("License") or "unknown",
        "status": "passed",
    }


def check_ollama_generation(config: Stage05Config, client: httpx.Client) -> dict[str, Any]:
    generation = config.generation
    if generation.provider != "local_http" or generation.endpoint is None:
        raise ValueError("Stage 05 route 0 Ollama smoke requires configured local_http generation")
    if not generation.endpoint.endswith("/api/generate"):
        raise ValueError("Stage 05 Ollama smoke endpoint must end with /api/generate")

    tags_url = generation.endpoint.removesuffix("/generate") + "/tags"
    tags = client.get(tags_url).raise_for_status().json()
    models = tags.get("models") if isinstance(tags, dict) else None
    if not isinstance(models, list):
        raise ValueError("Ollama /api/tags did not return a models list")
    selected = next((item for item in models if item.get("name") == generation.model_id), None)
    if selected is None:
        raise ValueError(f"Ollama model is not installed: {generation.model_id}")
    if selected.get("digest") != generation.model_revision:
        raise ValueError("Ollama model digest differs from configured model_revision")

    request = {
        "model": generation.model_id,
        "prompt": "Return JSON only: status needs_review and evidence_ids containing E-1. Synthetic smoke test.",
        "format": _SMOKE_SCHEMA,
        "stream": False,
        "options": {"temperature": 0, "num_predict": min(generation.max_output_tokens, 128)},
    }
    started = time.perf_counter()
    response = client.post(generation.endpoint, json=request).raise_for_status().json()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    if response.get("model") != generation.model_id:
        raise ValueError("Ollama response model differs from configured model_id")
    try:
        parsed = json.loads(response["response"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Ollama response is not parseable JSON") from exc
    if parsed != {"status": "needs_review", "evidence_ids": ["E-1"]}:
        raise ValueError("Ollama structured response did not match the smoke expectation")
    for field in ("total_duration", "load_duration", "prompt_eval_count", "eval_count"):
        if not isinstance(response.get(field), int) or response[field] < 0:
            raise ValueError(f"Ollama response is missing valid {field}")
    return {
        "status": "passed",
        "provider": "local_http",
        "model_id": generation.model_id,
        "model_revision": generation.model_revision,
        "elapsed_ms": elapsed_ms,
        "total_duration_ns": response["total_duration"],
        "load_duration_ns": response["load_duration"],
        "prompt_eval_count": response["prompt_eval_count"],
        "eval_count": response["eval_count"],
        "api_cost_cny": 0.0,
    }


def run_preflight(config: Stage05Config) -> dict[str, object]:
    graph = check_fake_stategraph()
    with httpx.Client(timeout=config.generation.request_timeout_seconds) as client:
        generation = check_ollama_generation(config, client)
    return {"langgraph": graph, "generation": generation}
