from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from graphrisk.agent.config import Stage05Config, load_stage05_config
from graphrisk.agent.contracts import Stage05Request
from graphrisk.graph.config import load_graph_config


@pytest.fixture
def raw_config() -> dict[str, object]:
    return yaml.safe_load(Path("configs/stage05.yaml").read_text(encoding="utf-8"))


def _bound(raw: dict[str, object]) -> Stage05Config:
    config = Stage05Config.model_validate(raw)
    config.set_project_root(Path.cwd())
    return config


def test_stage05_config_binds_existing_ieee_sources_and_ignored_outputs() -> None:
    config = load_stage05_config("configs/stage05.yaml")
    assert config.generation.provider == "local_http"
    assert config.generation.model_id == "qwen2.5:0.5b"
    assert config.generation.model_revision.startswith("a8b0c5157701")
    assert config.graph_comparison_run == "same-id-v4"
    assert config.resolve_path(config.paths.policy_source_dir) == (
        Path.cwd() / "data/policies/stage05"
    ).resolve()
    assert config.resolve_path(config.paths.policy_artifact_dir) == (
        Path.cwd() / "artifacts/stage-05/policy"
    ).resolve()


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("generation", "max_output_tokens", 0),
        ("generation", "request_timeout_seconds", 0.0),
        ("limits", "api_budget_cny", -0.1),
        ("limits", "api_stop_cny", -0.1),
        ("limits", "max_steps", 0),
        ("limits", "max_model_calls", "2"),
        ("retrieval", "top_k", 0),
        ("retrieval", "backend", "pgvector"),
    ],
)
def test_invalid_budget_or_runtime_setting_fails_closed(raw_config, section, field, value) -> None:
    candidate = deepcopy(raw_config)
    candidate[section][field] = value
    with pytest.raises(ValidationError):
        Stage05Config.model_validate(candidate)


def test_api_stop_threshold_must_fit_cap(raw_config) -> None:
    candidate = deepcopy(raw_config)
    candidate["limits"]["api_stop_cny"] = 31.0
    with pytest.raises(ValidationError, match="cannot exceed"):
        Stage05Config.model_validate(candidate)


def test_model_and_endpoint_must_match_provider(raw_config) -> None:
    candidate = deepcopy(raw_config)
    candidate["generation"]["endpoint"] = "https://example.com"
    with pytest.raises(ValidationError, match="loopback HTTP"):
        Stage05Config.model_validate(candidate)
    candidate["generation"]["endpoint"] = "http://127.0.0.1:11434/api"
    assert Stage05Config.model_validate(candidate).generation.provider == "local_http"
    candidate["generation"]["endpoint"] = "http://user:secret@127.0.0.1:11434/api"
    with pytest.raises(ValidationError, match="credentials"):
        Stage05Config.model_validate(candidate)
    candidate["generation"].update(provider="api", endpoint="http://example.com")
    with pytest.raises(ValidationError, match="HTTPS"):
        Stage05Config.model_validate(candidate)


def test_http_model_revision_is_required_and_safe(raw_config) -> None:
    candidate = deepcopy(raw_config)
    candidate["generation"]["model_revision"] = None
    with pytest.raises(ValidationError, match="model_revision"):
        Stage05Config.model_validate(candidate)
    candidate["generation"]["model_revision"] = "../other"
    with pytest.raises(ValidationError, match="model_revision"):
        Stage05Config.model_validate(candidate)


def test_unconfigured_or_dense_backend_cannot_claim_unverified_model(raw_config) -> None:
    candidate = deepcopy(raw_config)
    candidate["generation"].update(provider="unconfigured", model_id=None, model_revision=None, endpoint=None)
    candidate["generation"]["model_id"] = "claimed-model"
    with pytest.raises(ValidationError, match="unconfigured generation"):
        Stage05Config.model_validate(candidate)
    candidate = deepcopy(raw_config)
    candidate["retrieval"]["backend"] = "local_embedding"
    with pytest.raises(ValidationError, match="embedding_model_id"):
        Stage05Config.model_validate(candidate)


def test_output_paths_and_comparison_name_cannot_escape_project(raw_config) -> None:
    candidate = deepcopy(raw_config)
    candidate["paths"]["policy_source_dir"] = "data/raw/ieee-cis"
    with pytest.raises(ValueError, match="data/policies"):
        _bound(candidate).validate_paths()
    candidate = deepcopy(raw_config)
    candidate["paths"]["policy_artifact_dir"] = "reports/policy"
    with pytest.raises(ValueError, match="artifacts"):
        _bound(candidate).validate_paths()
    candidate = deepcopy(raw_config)
    candidate["paths"]["run_dir"] = "../outside"
    with pytest.raises(ValueError, match="reports"):
        _bound(candidate).validate_paths()
    candidate = deepcopy(raw_config)
    candidate["graph_comparison_run"] = "../escape"
    with pytest.raises(ValidationError, match="safe non-empty"):
        Stage05Config.model_validate(candidate)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("data_config", "configs/dev.yaml", "dataset_source must match"),
        ("training_config", "configs/train_baseline.yaml", "dataset_source must match"),
    ],
)
def test_cross_source_wiring_is_rejected(raw_config, field, replacement, message) -> None:
    candidate = deepcopy(raw_config)
    candidate["paths"][field] = replacement
    with pytest.raises(ValueError, match=message):
        _bound(candidate).validate_source_binding()


def test_graph_wiring_rejects_same_source_but_different_config_path(monkeypatch, raw_config) -> None:
    graph = load_graph_config("configs/graph_ieee_cis.yaml")
    graph.paths.data_config = Path("configs/dev.yaml")
    monkeypatch.setattr("graphrisk.agent.config.load_graph_config", lambda path: graph)
    with pytest.raises(ValueError, match="data_config must be the same"):
        _bound(raw_config).validate_source_binding()


def test_policy_date_is_explicit_and_not_ieee_transaction_time() -> None:
    valid = Stage05Request.model_validate(
        {"transaction_id": "3488959", "dataset_source": "ieee_cis", "policy_as_of": "2026-09-24"}
    )
    assert valid.policy_as_of.isoformat() == "2026-09-24"
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        Stage05Request.model_validate(
            {"transaction_id": "3488959", "dataset_source": "ieee_cis", "policy_as_of": "2026-09-24T00:00:00"}
        )
    with pytest.raises(ValidationError, match="transaction timestamp"):
        Stage05Request.model_validate(
            {"transaction_id": "3488959", "dataset_source": "ieee_cis", "policy_as_of": datetime.now(timezone.utc)}
        )
    with pytest.raises(ValidationError):
        Stage05Request.model_validate(
            {"transaction_id": "", "dataset_source": "ieee_cis", "policy_as_of": "2026-09-24"}
        )
