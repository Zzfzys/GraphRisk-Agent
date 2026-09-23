from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictInt,
    field_validator,
    model_validator,
)

from graphrisk.config import load_config
from graphrisk.investigation.contracts import DatasetSource
from graphrisk.training.config import load_training_config


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GraphExperimentPaths(StrictConfig):
    data_config: Path
    training_config: Path
    artifact_dir: Path
    train_ids: Path
    validation_ids: Path
    test_ids: Path
    train_predictions: Path
    validation_predictions: Path
    test_predictions: Path
    graph_manifest: Path

    @model_validator(mode="after")
    def output_paths_are_distinct(self) -> "GraphExperimentPaths":
        outputs = (
            self.train_ids,
            self.validation_ids,
            self.test_ids,
            self.train_predictions,
            self.validation_predictions,
            self.test_predictions,
            self.graph_manifest,
        )
        if len(outputs) != len(set(outputs)):
            raise ValueError("graph experiment output paths must be distinct")
        if any(path == self.artifact_dir for path in outputs):
            raise ValueError("an output file cannot equal artifact_dir")
        return self


class GraphSampling(StrictConfig):
    fanout: tuple[StrictInt, StrictInt]
    max_train_transactions: int = Field(strict=True, ge=1)
    max_validation_transactions: int = Field(strict=True, ge=1)
    max_test_transactions: int = Field(strict=True, ge=1)
    selection: Literal["chronological_prefix"]

    @field_validator("fanout")
    @classmethod
    def fanout_must_be_positive(cls, value: tuple[int, int]) -> tuple[int, int]:
        if any(count <= 0 for count in value):
            raise ValueError("fanout values must be positive")
        return value


class GraphTraining(StrictConfig):
    model_version: str
    random_seed: int = Field(strict=True, ge=0)
    batch_size: int = Field(strict=True, ge=1)
    hidden_channels: int = Field(strict=True, ge=1)
    max_epochs: int = Field(strict=True, ge=1)
    early_stopping_patience: int = Field(strict=True, ge=1)
    device: Literal["auto", "cpu", "cuda"]

    @field_validator("model_version")
    @classmethod
    def model_version_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model_version must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def patience_fits_epoch_budget(self) -> "GraphTraining":
        if self.early_stopping_patience > self.max_epochs:
            raise ValueError("early_stopping_patience cannot exceed max_epochs")
        return self


class GraphExperimentConfig(StrictConfig):
    project_name: str
    dataset_source: DatasetSource
    paths: GraphExperimentPaths
    sampling: GraphSampling
    training: GraphTraining

    _project_root: Path = PrivateAttr()

    def set_project_root(self, root: Path) -> None:
        self._project_root = root.resolve()

    def resolve_path(self, path: Path) -> Path:
        if path.is_absolute():
            return path.resolve()
        return (self._project_root / path).resolve()

    def validate_output_paths(self) -> None:
        artifact_dir = self.resolve_path(self.paths.artifact_dir)
        allowed_root = (self._project_root / "artifacts").resolve()
        if not artifact_dir.is_relative_to(allowed_root):
            raise ValueError("graph artifact_dir must reside under project artifacts/")
        resolved_outputs: list[Path] = []
        for field in (
            "train_ids",
            "validation_ids",
            "test_ids",
            "train_predictions",
            "validation_predictions",
            "test_predictions",
            "graph_manifest",
        ):
            output = self.resolve_path(getattr(self.paths, field))
            if not output.is_relative_to(artifact_dir):
                raise ValueError(f"graph {field} must reside under artifact_dir")
            resolved_outputs.append(output)
        if len(resolved_outputs) != len(set(resolved_outputs)):
            raise ValueError("resolved graph experiment output paths must be distinct")

    def validate_source_binding(self) -> None:
        """Reject graph experiments wired to another dataset or serving feature table."""
        data = load_config(self.resolve_path(self.paths.data_config))
        baseline = load_training_config(self.resolve_path(self.paths.training_config))
        if (
            data.project_name != self.project_name
            or baseline.project_name != self.project_name
        ):
            raise ValueError("graph, data, and baseline project_name must match")
        if (
            data.data.source != self.dataset_source
            or baseline.dataset_source != self.dataset_source
        ):
            raise ValueError(
                "graph, data, and baseline training dataset_source must match"
            )
        if (
            data.resolve_path(data.paths.interim_features)
            != baseline.resolve_path(baseline.paths.serving_features)
        ):
            raise ValueError(
                "graph data interim_features must match baseline serving_features"
            )
        processed = data.resolve_path(data.paths.processed_dir)
        for split in ("train", "validation", "test"):
            feature_path = baseline.resolve_path(
                getattr(baseline.paths, f"{split}_features")
            )
            if not feature_path.is_relative_to(processed):
                raise ValueError(
                    f"baseline {split}_features must reside in graph data processed_dir"
                )

    def check_stage_inputs(self) -> dict[str, int]:
        """Return sizes for Stage 01/02 artifacts required before graph work."""
        data = load_config(self.resolve_path(self.paths.data_config))
        baseline = load_training_config(self.resolve_path(self.paths.training_config))
        required = {
            "transactions": data.resolve_path(data.paths.interim_transactions),
            "all_features": data.resolve_path(data.paths.interim_features),
            "train_features": baseline.resolve_path(baseline.paths.train_features),
            "validation_features": baseline.resolve_path(
                baseline.paths.validation_features
            ),
            "test_features": baseline.resolve_path(baseline.paths.test_features),
            "baseline_champion_model": baseline.resolve_path(
                baseline.paths.output_dir
            ) / "champion" / "model.joblib",
            "baseline_champion_metadata": baseline.resolve_path(
                baseline.paths.output_dir
            ) / "champion" / "metadata.json",
        }
        sizes: dict[str, int] = {}
        for name, path in required.items():
            if not path.is_file() or path.stat().st_size == 0:
                raise FileNotFoundError(
                    f"Stage 04 requires a non-empty {name} artifact; "
                    "run the Stage 01/02 pipeline for the selected source"
                )
            sizes[name] = path.stat().st_size
        return sizes


def load_graph_config(config_path: str | Path) -> GraphExperimentConfig:
    path = Path(config_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"graph configuration does not exist: {path}")
    with path.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, dict):
        raise ValueError(f"graph configuration root must be a mapping: {path}")

    config = GraphExperimentConfig.model_validate(raw)
    root = path.parent.parent if path.parent.name == "configs" else path.parent
    config.set_project_root(root)
    config.validate_output_paths()
    config.validate_source_binding()
    return config
