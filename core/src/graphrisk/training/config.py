from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, PrivateAttr, model_validator

from graphrisk.models.tabular import ModelName


class TrainingPaths(BaseModel):
    train_features: Path
    validation_features: Path
    test_features: Path
    serving_features: Path
    output_dir: Path
    results_csv: Path
    mlflow_database: Path
    mlflow_artifacts: Path


class TrainingSettings(BaseModel):
    random_seed: int
    decision_threshold: float = Field(ge=0, le=1)
    models: list[ModelName]
    latency_sample_size: int = Field(ge=1)

    @model_validator(mode="after")
    def models_must_be_unique_and_complete(self) -> "TrainingSettings":
        if len(self.models) != len(set(self.models)):
            raise ValueError("training model names must be unique")
        required = {"dummy", "logistic_regression", "xgboost"}
        if set(self.models) != required:
            raise ValueError(f"training models must be exactly {sorted(required)}")
        return self


class MlflowSettings(BaseModel):
    experiment_name: str


class ServingSettings(BaseModel):
    low_threshold: float = Field(ge=0, le=1)
    high_threshold: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def thresholds_must_be_ordered(self) -> "ServingSettings":
        if self.low_threshold >= self.high_threshold:
            raise ValueError("low_threshold must be less than high_threshold")
        return self


class BaselineTrainingConfig(BaseModel):
    project_name: str
    dataset_source: str
    paths: TrainingPaths
    training: TrainingSettings
    mlflow: MlflowSettings
    serving: ServingSettings

    _project_root: Path = PrivateAttr()

    def set_project_root(self, root: Path) -> None:
        self._project_root = root.resolve()

    def resolve_path(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        return (self._project_root / path).resolve()


def load_training_config(config_path: str | Path) -> BaselineTrainingConfig:
    path = Path(config_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"training configuration does not exist: {path}")
    with path.open("r", encoding="utf-8") as stream:
        raw_config = yaml.safe_load(stream)
    if not isinstance(raw_config, dict):
        raise ValueError(f"training configuration root must be a mapping: {path}")

    config = BaselineTrainingConfig.model_validate(raw_config)
    project_root = path.parent.parent if path.parent.name == "configs" else path.parent
    config.set_project_root(project_root)
    return config
