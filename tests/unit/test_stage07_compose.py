"""Stage 07 container topology and fixed host Ollama boundary."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
import yaml
from pydantic import ValidationError

from graphrisk.agent.config import Stage05Retrieval, load_stage05_config
from graphrisk.agent.policy_retrieval import OllamaEmbeddingProvider
from graphrisk.serving.stage07_runtime import _dsn

ROOT = Path(__file__).resolve().parents[2]


def test_compose_embedding_host_is_fixed_and_model_digest_is_pinned():
    local = load_stage05_config(ROOT / "configs/stage07_pgvector.yaml")
    container = load_stage05_config(ROOT / "configs/stage07_compose.yaml")
    assert container.retrieval.backend == "pgvector"
    assert container.retrieval.embedding_endpoint == "http://host.docker.internal:11434"
    assert container.generation.provider == "host_ollama"
    assert container.generation.endpoint == "http://host.docker.internal:11434/api/generate"
    assert (container.retrieval.embedding_model_id, container.retrieval.embedding_model_revision) == (
        local.retrieval.embedding_model_id, local.retrieval.embedding_model_revision)
    provider = OllamaEmbeddingProvider(container.retrieval.embedding_model_id,
                                       container.retrieval.embedding_model_revision,
                                       container.retrieval.embedding_endpoint)
    assert provider.base_url == "http://host.docker.internal:11434"
    baseline = container.retrieval.model_dump()
    for endpoint in ("http://example.com:11434", "http://host.docker.internal:1234",
                     "https://host.docker.internal:11434", "http://host.docker.internal:11434/other",
                     "http://user:secret@host.docker.internal:11434"):
        with pytest.raises(ValidationError):
            Stage05Retrieval.model_validate({**baseline, "embedding_endpoint": endpoint})
        with pytest.raises(ValueError):
            OllamaEmbeddingProvider("embedding", "a" * 64, endpoint)


def test_stage07_compose_keeps_db_internal_and_workbench_blind_to_data():
    compose = yaml.safe_load((ROOT / "compose.stage07.yaml").read_text(encoding="utf-8-sig"))
    services = compose["services"]
    db, bootstrap, api, ui = (services[name] for name in ("db", "bootstrap", "api", "workbench"))
    assert not db.get("ports")
    debug = yaml.safe_load((ROOT / "compose.stage07-debug.yaml").read_text(encoding="utf-8-sig"))
    assert debug["services"]["db"]["ports"] == ["127.0.0.1:15433:5432"]
    assert db["networks"] == ["backend"]
    assert compose["networks"]["backend"]["internal"] is True
    assert bootstrap["depends_on"]["db"]["condition"] == "service_healthy"
    assert api["depends_on"]["bootstrap"]["condition"] == "service_completed_successfully"
    assert ui["depends_on"]["api"]["condition"] == "service_healthy"
    assert api["ports"] == ["127.0.0.1:8000:8000"]
    assert ui["ports"] == ["127.0.0.1:8501:8501"]
    assert ui["networks"] == ["frontend"] and not ui.get("volumes") and not ui.get("secrets")
    assert "stage07_db_admin_password" not in api["secrets"]
    mounts = {mount["target"]: mount for mount in api["volumes"]}
    for path in ("/app/data/interim/ieee-cis", "/app/data/processed/ieee-cis",
                 "/app/artifacts/stage-02/ieee-cis", "/app/artifacts/stage-04/ieee-cis",
                 "/app/artifacts/stage-05/policy"):
        assert mounts[path]["read_only"] is True
    assert mounts["/app/reports/generated"]["read_only"] is False
    assert not any("/raw" in target for target in mounts)
    assert compose["volumes"]["stage07_pgdata"] == {}
    assert ".secrets/" in (ROOT / ".dockerignore").read_text(encoding="utf-8-sig")
    assert "/.secrets/" in (ROOT / ".gitignore").read_text(encoding="utf-8-sig")


def test_container_dsn_reads_a_secret_file_without_embedding_it_in_compose(tmp_path):
    secret = tmp_path / "password"
    secret.write_text("a@b:c/long-secret", encoding="utf-8")
    dsn = _dsn("graphrisk_app", secret)
    parsed = urlsplit(dsn)
    assert parsed.hostname == "db" and parsed.port == 5432
    assert unquote(parsed.password) == "a@b:c/long-secret"
    assert "a@b:c/long-secret" not in (ROOT / "compose.stage07.yaml").read_text(encoding="utf-8-sig")


def test_stage07_debug_network_can_publish_the_loopback_database_port():
    base = yaml.safe_load((ROOT / "compose.stage07.yaml").read_text(encoding="utf-8-sig"))
    debug = yaml.safe_load((ROOT / "compose.stage07-debug.yaml").read_text(encoding="utf-8-sig"))
    assert base["services"]["db"]["networks"] == ["backend"]
    assert base["networks"]["backend"]["internal"] is True
    assert debug["services"]["db"]["ports"] == ["127.0.0.1:15433:5432"]
    assert debug["services"]["db"]["networks"] == ["backend", "db_debug"]
    assert debug["networks"]["db_debug"] == {}
