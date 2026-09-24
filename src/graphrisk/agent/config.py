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
    policy_artifact_dir: Path
    run_dir: Path


class Stage05Generation(StrictConfig):
    provider: Literal["unconfigured", "fake", "local_http", "api"]
    model_id: str | None = None
    endpoint: str | None = None
    max_output_tokens: int = Field(strict=True, ge=32, le=4096)
    request_timeout_seconds: float = Field(strict=True, gt=0, le=300)

    @model_validator(mode="after")
    def provider_has_required_fields(self) -> "Stage05Generation":
        if self.provider == "unconfigured":
            if self.model_id is not None or self.endpoint is not None:
                raise ValueError("unconfigured generation cannot declare model_id or endpoint")
            return self
        if self.model_id is None or not self.model_id.strip():
            raise ValueError("configured generation requires a non-empty model_id")
        if self.provider == "fake":
            if self.endpoint is not None:
                raise ValueError("fake generation cannot declare an endpoint")
            return self
        if self.endpoint is None:
            raise ValueError("HTTP generation requires an endpoint")
        parsed = urlsplit(self.endpoint)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("generation endpoint cannot contain credentials, query or fragment")
        if self.provider == "local_http":
            if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("local_http endpoint must use loopback HTTP")
        elif parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("api endpoint must use HTTPS")
        return self


class Stage05Retrieval(StrictConfig):
    backend: Literal["tfidf", "local_embedding"]
    embedding_model_id: str | None = None
    top_k: int = Field(strict=True, ge=1, le=20)

    @model_validator(mode="after")
    def embedding_model_matches_backend(self) -> "Stage05Retrieval":
        if self.backend == "local_embedding" and (
            self.embedding_model_id is None or not self.embedding_model_id.strip()
        ):
            raise ValueError("local_embedding retrieval requires embedding_model_id")
        if self.backend == "tfidf" and self.embedding_model_id is not None:
            raise ValueError("tfidf retrieval cannot declare embedding_model_id")
        return self


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
