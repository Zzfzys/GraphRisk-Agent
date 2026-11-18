"""Route 5 launch and Compose boundaries without requiring a Docker daemon."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml

from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.reporting import OllamaReportProvider
from scripts.run_stage06_local import _check_ollama, _port_available


ROOT = Path(__file__).resolve().parents[2]


def test_host_ollama_runtime_checks_digest_before_generation():
    config = load_stage05_config(ROOT / "configs/stage06_compose.yaml")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.host, request.url.path))
        return httpx.Response(200, json={"models": [{
            "name": config.generation.model_id, "digest": "0" * 64,
        }]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaReportProvider(config.generation, client=client)
        with pytest.raises(ValueError, match="model or digest"):
            provider.generate("unused", {})
    assert calls == [("GET", "host.docker.internal", "/api/tags")]


def test_local_launcher_preflight_requires_exact_ollama_digest(monkeypatch):
    config = load_stage05_config(ROOT / "configs/stage05.yaml")
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"models": [{
        "name": config.generation.model_id, "digest": config.generation.model_revision,
    }]}))
    actual_client = httpx.Client
    monkeypatch.setattr("scripts.run_stage06_local.httpx.Client",
                        lambda **kwargs: actual_client(transport=transport, **kwargs))
    _check_ollama(config)
    wrong = httpx.MockTransport(lambda _: httpx.Response(200, json={"models": [{
        "name": config.generation.model_id, "digest": "0" * 64,
    }]}))
    monkeypatch.setattr("scripts.run_stage06_local.httpx.Client",
                        lambda **kwargs: actual_client(transport=wrong, **kwargs))
    with pytest.raises(RuntimeError, match="digest"):
        _check_ollama(config)


def test_port_guard_rejects_occupied_loopback_socket():
    import socket
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        assert not _port_available(occupied.getsockname()[1])


def test_compose_keeps_browser_separate_and_mounts_artifacts_read_only():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8-sig"))
    api = compose["services"]["api"]
    ui = compose["services"]["workbench"]
    assert api["ports"] == ["127.0.0.1:8000:8000"]
    assert ui["ports"] == ["127.0.0.1:8501:8501"]
    assert api["environment"]["GRAPHRISK_STAGE06_CONFIG"] == "configs/stage06_compose.yaml"
    assert ui["environment"]["GRAPHRISK_STAGE06_API_URL"] == "http://api:8000"
    assert ui["depends_on"]["api"]["condition"] == "service_healthy"
    assert not ui.get("volumes")
    volumes = {volume["target"]: volume for volume in api["volumes"]}
    for target in (
        "/app/data/interim/ieee-cis", "/app/data/processed/ieee-cis",
        "/app/artifacts/stage-02/ieee-cis",
        "/app/artifacts/stage-04/ieee-cis", "/app/artifacts/stage-05/policy",
    ):
        assert volumes[target]["read_only"] is True
    assert volumes["/app/reports/generated"]["read_only"] is False
    assert not any("/raw" in target for target in volumes)


def test_build_context_excludes_raw_data_secrets_and_model_weights():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8-sig").splitlines()
    for pattern in (".env", ".env.*", "data/raw/", "data/interim/", "data/processed/",
                    "artifacts/", "reports/", "dist/", "*.pt", "*.parquet", "*.csv"):
        assert pattern in ignored
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8-sig")
    assert "COPY . " not in dockerfile
    assert "COPY data/policies/stage05 " in dockerfile
    assert "COPY artifacts" not in dockerfile
    assert "COPY data/raw" not in dockerfile
