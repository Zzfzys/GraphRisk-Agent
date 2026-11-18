"""Fail-closed configuration for the Stage 05 experiment."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from graphrisk.config import load_config
from graphrisk.graph.config import load_graph_config
from graphrisk.investigation.contracts import DatasetSource
from graphrisk.training.config import load_training_config


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Stage05Paths(StrictConfig):
    data_config: Path
    training_config: Path
    graph_config: Path
    policy_source_dir: Path
    policy_artifact_dir: Path
    run_dir: Path


class Stage05Generation(StrictConfig):
    provider: Literal["unconfigured", "fake", "local_http", "host_ollama", "api"]
    model_id: str | None = None
    model_revision: str | None = None
    endpoint: str | None = None
    max_output_tokens: int = Field(strict=True, ge=32, le=4096)
    request_timeout_seconds: float = Field(strict=True, gt=0, le=300)

    @model_validator(mode="after")
    def provider_has_required_fields(self) -> "Stage05Generation":
        if self.provider == "unconfigured":
            if self.model_id is not None or self.model_revision is not None or self.endpoint is not None:
                raise ValueError("unconfigured generation cannot declare model_id, revision or endpoint")
            return self
        if self.model_id is None or not self.model_id.strip():
            raise ValueError("configured generation requires a non-empty model_id")
        if self.provider == "fake":
            if self.endpoint is not None or self.model_revision is not None:
                raise ValueError("fake generation cannot declare a model revision or endpoint")
            return self
        if self.model_revision is None or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", self.model_revision):
            raise ValueError("HTTP generation requires a safe model_revision of 8-128 characters")
        if self.endpoint is None:
            raise ValueError("HTTP generation requires an endpoint")
        parsed = urlsplit(self.endpoint)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("generation endpoint cannot contain credentials, query or fragment")
        if self.provider == "local_http":
            if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("local_http endpoint must use loopback HTTP")
        elif self.provider == "host_ollama":
            if (parsed.scheme != "http" or parsed.hostname != "host.docker.internal"
                    or parsed.port != 11434 or parsed.path != "/api/generate"):
                raise ValueError("host_ollama endpoint must be http://host.docker.internal:11434/api/generate")
        elif parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("api endpoint must use HTTPS")
        return self


class Stage05Retrieval(StrictConfig):
    backend: Literal["tfidf", "local_embedding", "pgvector"]
    embedding_model_id: str | None = None
    embedding_model_revision: str | None = None
    embedding_endpoint: str | None = None
    top_k: int = Field(strict=True, ge=1, le=20)

    @model_validator(mode="after")
    def embedding_model_matches_backend(self) -> "Stage05Retrieval":
        if self.backend in {"local_embedding", "pgvector"}:
            if not self.embedding_model_id or not self.embedding_model_id.strip():
                raise ValueError("local_embedding retrieval requires embedding_model_id")
            if not self.embedding_model_revision or not re.fullmatch(r"[a-f0-9]{64}", self.embedding_model_revision):
                raise ValueError("local_embedding retrieval requires a 64-character model digest")
            if not self.embedding_endpoint:
                raise ValueError("local_embedding retrieval requires embedding_endpoint")
            parsed = urlsplit(self.embedding_endpoint)
            local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            docker_host = parsed.hostname == "host.docker.internal" and parsed.port == 11434
            if (parsed.scheme != "http" or not (local or docker_host) or parsed.username
                    or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
                raise ValueError("embedding_endpoint must use loopback HTTP or http://host.docker.internal:11434")
        elif any(value is not None for value in (self.embedding_model_id, self.embedding_model_revision, self.embedding_endpoint)):
            raise ValueError("tfidf retrieval cannot declare embedding model or endpoint")
        return self

    @property
    def index_backend(self) -> Literal["tfidf", "local_embedding"]:
        return "local_embedding" if self.backend == "pgvector" else self.backend


class Stage05Limits(StrictConfig):
    max_steps: int = Field(strict=True, ge=1, le=50)
    max_model_calls: int = Field(strict=True, ge=1, le=10)
    max_policy_queries: int = Field(strict=True, ge=1, le=10)
    max_retries: int = Field(strict=True, ge=0, le=5)
    max_total_seconds: float = Field(strict=True, gt=0, le=600)
    api_budget_cny: float = Field(strict=True, ge=0, le=200)
    api_stop_cny: float = Field(strict=True, ge=0, le=200)

    @model_validator(mode="after")
    def call_limits_fit_step_budget(self) -> "Stage05Limits":
        if self.max_model_calls + self.max_policy_queries > self.max_steps:
            raise ValueError("model and policy query limits cannot exceed max_steps")
        if self.api_stop_cny > self.api_budget_cny:
            raise ValueError("api_stop_cny cannot exceed api_budget_cny")
        return self


class Stage05Config(StrictConfig):
    project_name: str
    dataset_source: DatasetSource
    graph_comparison_run: str
    report_mode: Literal["legacy", "quality_v1"] = "legacy"
    model_policy: Path | None = None
    model_policy_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    paths: Stage05Paths
    generation: Stage05Generation
    retrieval: Stage05Retrieval
    limits: Stage05Limits

    _project_root: Path = PrivateAttr()

    @field_validator("project_name", "graph_comparison_run")
    @classmethod
    def names_must_be_safe(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", normalized) or normalized in (".", ".."):
            raise ValueError("project_name and graph_comparison_run must be safe non-empty names")
        return normalized

    def set_project_root(self, root: Path) -> None:
        self._project_root = root.resolve()

    def resolve_path(self, path: Path) -> Path:
        return (path if path.is_absolute() else self._project_root / path).resolve()

    def validate_paths(self) -> None:
        if (self.model_policy is None) != (self.model_policy_sha256 is None):
            raise ValueError("model_policy and its SHA256 must be declared together")
        if self.model_policy is not None and not self.resolve_path(self.model_policy).is_relative_to(self._project_root / "artifacts"):
            raise ValueError("model_policy must reside under artifacts")
        source_root = (self._project_root / "data" / "policies").resolve()
        if not self.resolve_path(self.paths.policy_source_dir).is_relative_to(source_root):
            raise ValueError("Stage 05 policy_source_dir must reside under data/policies")
        allowed = (
            (self.paths.policy_artifact_dir, self._project_root / "artifacts"),
            (self.paths.run_dir, self._project_root / "reports" / "generated"),
        )
        for path, root in allowed:
            if not self.resolve_path(path).is_relative_to(root.resolve()):
                raise ValueError(f"Stage 05 output path must reside under {root.relative_to(self._project_root)}")
        if self.resolve_path(self.paths.policy_artifact_dir) == (self._project_root / "artifacts").resolve():
            raise ValueError("policy_artifact_dir must be a dedicated subdirectory")

    def validate_source_binding(self) -> None:
        data = load_config(self.resolve_path(self.paths.data_config))
        training = load_training_config(self.resolve_path(self.paths.training_config))
        graph = load_graph_config(self.resolve_path(self.paths.graph_config))
        if {self.project_name, data.project_name, training.project_name, graph.project_name} != {self.project_name}:
            raise ValueError("Stage 05 project_name must match Stage 01/02/04")
        if {self.dataset_source, data.data.source, training.dataset_source, graph.dataset_source} != {self.dataset_source}:
            raise ValueError("Stage 05 dataset_source must match Stage 01/02/04")
        if graph.resolve_path(graph.paths.data_config) != self.resolve_path(self.paths.data_config):
            raise ValueError("Stage 05 and graph data_config must be the same")
        if graph.resolve_path(graph.paths.training_config) != self.resolve_path(self.paths.training_config):
            raise ValueError("Stage 05 and graph training_config must be the same")
        comparison = graph.resolve_path(graph.paths.artifact_dir) / "comparisons" / self.graph_comparison_run
        if not comparison.is_relative_to(graph.resolve_path(graph.paths.artifact_dir)):
            raise ValueError("graph comparison path must remain under graph artifacts")


def load_stage05_config(config_path: str | Path) -> Stage05Config:
    path = Path(config_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Stage 05 configuration does not exist: {path}")
    with path.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, dict):
        raise ValueError(f"Stage 05 configuration root must be a mapping: {path}")
    config = Stage05Config.model_validate(raw)
    root = path.parent.parent if path.parent.name == "configs" else path.parent
    config.set_project_root(root)
    config.validate_paths()
    config.validate_source_binding()
    return config
