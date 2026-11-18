from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator


class PathConfig(BaseModel):
    raw_transactions: Path
    raw_identity: Path | None = None
    interim_transactions: Path
    interim_features: Path
    processed_dir: Path
    feature_report: Path


class DataConfig(BaseModel):
    source: Literal["synthetic", "canonical", "ieee_cis"] = "synthetic"
    regenerate_synthetic: bool = False
    relative_time_anchor: datetime | None = None

    @field_validator("relative_time_anchor")
    @classmethod
    def relative_time_anchor_must_be_timezone_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("relative_time_anchor must include a timezone")
        return value


class SplitConfig(BaseModel):
    train_ratio: float = Field(gt=0, lt=1)
    validation_ratio: float = Field(gt=0, lt=1)
    test_ratio: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def ratios_must_sum_to_one(self) -> "SplitConfig":
        total = self.train_ratio + self.validation_ratio + self.test_ratio
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"split ratios must sum to 1.0, got {total}")
        return self


class SyntheticConfig(BaseModel):
    rows: int = Field(ge=100)
    seed: int
    start_time: datetime

    @field_validator("start_time")
    @classmethod
    def start_time_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("synthetic start_time must include a timezone")
        return value


class LoggingConfig(BaseModel):
    level: str = "INFO"


class ProjectConfig(BaseModel):
    project_name: str
    paths: PathConfig
    data: DataConfig
    split: SplitConfig
    synthetic: SyntheticConfig | None = None
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    _project_root: Path = PrivateAttr()

    @model_validator(mode="after")
    def source_specific_settings_must_be_present(self) -> "ProjectConfig":
        if self.data.source == "synthetic" and self.synthetic is None:
            raise ValueError("synthetic settings are required for synthetic data")
        if self.data.source == "ieee_cis":
            if self.paths.raw_identity is None:
                raise ValueError("raw_identity is required for IEEE-CIS data")
            if self.data.relative_time_anchor is None:
                raise ValueError("relative_time_anchor is required for IEEE-CIS data")
        return self

    def set_project_root(self, root: Path) -> None:
        self._project_root = root.resolve()

    @property
    def project_root(self) -> Path:
        return self._project_root

    def resolve_path(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        return (self.project_root / path).resolve()


def load_config(config_path: str | Path) -> ProjectConfig:
    path = Path(config_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"configuration file does not exist: {path}")

    with path.open("r", encoding="utf-8") as stream:
        raw_config = yaml.safe_load(stream)

    if not isinstance(raw_config, dict):
        raise ValueError(f"configuration root must be a mapping: {path}")

    config = ProjectConfig.model_validate(raw_config)
    if path.parent.name == "configs":
        project_root = path.parent.parent
    else:
        project_root = path.parent
    config.set_project_root(project_root)
    return config
