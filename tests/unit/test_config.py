from pathlib import Path

import pytest
from pydantic import ValidationError

from graphrisk.config import SplitConfig, load_config


def test_load_config_resolves_project_root() -> None:
    config = load_config("configs/dev.yaml")

    assert config.project_name == "graphrisk-agent"
    assert config.project_root == Path.cwd().resolve()
    assert config.resolve_path(config.paths.processed_dir) == (
        Path.cwd() / "data" / "processed"
    ).resolve()


def test_split_ratios_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="split ratios must sum to 1.0"):
        SplitConfig(train_ratio=0.7, validation_ratio=0.2, test_ratio=0.2)
