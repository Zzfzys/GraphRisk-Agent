from __future__ import annotations

import json

import httpx
import pytest

from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.preflight import check_fake_stategraph, check_ollama_generation


def _client(*, digest: str | None = None, generated: str = '{"status":"needs_review","evidence_ids":["E-1"]}'):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "qwen2.5:0.5b", "digest": digest or config.generation.model_revision}]},
            )
        if request.url.path == "/api/generate":
            body = json.loads(request.content)
            assert body["format"]["required"] == ["status", "evidence_ids"]
            assert body["stream"] is False
            return httpx.Response(
                200,
                json={
                    "model": "qwen2.5:0.5b",
                    "response": generated,
                    "total_duration": 1000,
                    "load_duration": 100,
                    "prompt_eval_count": 10,
                    "eval_count": 8,
                },
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    config = load_stage05_config("configs/stage05.yaml")
    return config, httpx.Client(transport=httpx.MockTransport(handler))


def test_fake_langgraph_reaches_end() -> None:
    assert check_fake_stategraph()["status"] == "passed"


def test_ollama_smoke_verifies_digest_and_schema_without_network() -> None:
    config, client = _client()
    with client:
        result = check_ollama_generation(config, client)
    assert result["status"] == "passed"
    assert result["model_revision"] == config.generation.model_revision
    assert result["api_cost_cny"] == 0.0


def test_ollama_smoke_rejects_changed_model_digest() -> None:
    config, client = _client(digest="0" * 64)
    with client, pytest.raises(ValueError, match="digest differs"):
        check_ollama_generation(config, client)


def test_ollama_smoke_rejects_structurally_wrong_model_output() -> None:
    config, client = _client(generated='{"status":"needs_review","evidence_ids":[]}')
    with client, pytest.raises(ValueError, match="structured response"):
        check_ollama_generation(config, client)
