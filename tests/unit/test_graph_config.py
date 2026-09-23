from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

import graphrisk.graph.config as graph_config_module
from graphrisk.graph.config import GraphExperimentConfig, load_graph_config
from graphrisk.training.config import load_training_config


@pytest.fixture
def raw_config() -> dict[str, object]:
    config = load_graph_config("configs/graph_ieee_cis.yaml")
    return config.model_dump(mode="python")


def test_ieee_graph_config_binds_stage_01_and_02_sources() -> None:
    config = load_graph_config("configs/graph_ieee_cis.yaml")
    assert config.dataset_source == "ieee_cis"
    assert config.training.model_version == "graphsage-v1"
    assert config.sampling.selection == "chronological_prefix"
    assert config.resolve_path(config.paths.train_ids) == (
        Path.cwd() / "artifacts/stage-04/ieee-cis/train-ids.json"
    ).resolve()


def test_missing_stage_input_gives_actionable_error(
    raw_config: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = GraphExperimentConfig.model_validate(raw_config)
    candidate.set_project_root(Path.cwd())
    data = graph_config_module.load_config("configs/ieee_cis.yaml")
    data.paths.interim_transactions = Path("data/interim/ieee-cis/not-there.parquet")
    monkeypatch.setattr(graph_config_module, "load_config", lambda _: data)
    with pytest.raises(FileNotFoundError, match="run the Stage 01/02 pipeline"):
        candidate.check_stage_inputs()


@pytest.mark.parametrize(
    ("section", "field", "invalid"),
    [
        ("sampling", "fanout", [-1, 5]),
        ("sampling", "fanout", [0, 5]),
        ("sampling", "fanout", [5]),
        ("sampling", "fanout", ["10", 5]),
        ("sampling", "max_train_transactions", 0),
        ("sampling", "max_train_transactions", "100"),
        ("training", "model_version", "   "),
        ("training", "device", "tpu"),
        ("training", "batch_size", 0),
        ("training", "early_stopping_patience", 21),
    ],
)
def test_invalid_graph_settings_are_rejected(
    raw_config: dict[str, object], section: str, field: str, invalid: object
) -> None:
    candidate = deepcopy(raw_config)
    candidate[section][field] = invalid
    with pytest.raises(ValidationError):
        GraphExperimentConfig.model_validate(candidate)


def test_duplicate_prediction_output_is_rejected(raw_config: dict[str, object]) -> None:
    candidate = deepcopy(raw_config)
    candidate["paths"]["test_predictions"] = candidate["paths"][
        "validation_predictions"
    ]
    with pytest.raises(ValidationError, match="output paths must be distinct"):
        GraphExperimentConfig.model_validate(candidate)


def test_generated_outputs_must_stay_in_ignored_artifact_directory(
    raw_config: dict[str, object],
) -> None:
    candidate = GraphExperimentConfig.model_validate(raw_config)
    candidate.set_project_root(Path.cwd())
    candidate.paths.test_predictions = Path("reports/test-predictions.parquet")
    with pytest.raises(ValueError, match="test_predictions must reside"):
        candidate.validate_output_paths()


def test_resolved_output_aliases_are_rejected(raw_config: dict[str, object]) -> None:
    candidate = GraphExperimentConfig.model_validate(raw_config)
    candidate.set_project_root(Path.cwd())
    candidate.paths.test_ids = Path(
        "artifacts/stage-04/ieee-cis/subfolder/../validation-ids.json"
    )
    with pytest.raises(ValueError, match="resolved graph experiment output paths"):
        candidate.validate_output_paths()


@pytest.mark.parametrize(
    ("data_config", "training_config", "message"),
    [
        ("configs/dev.yaml", None, "dataset_source must match"),
        (None, "configs/train_baseline.yaml", "dataset_source must match"),
    ],
)
def test_cross_source_configs_are_rejected(
    raw_config: dict[str, object],
    data_config: str | None,
    training_config: str | None,
    message: str,
) -> None:
    candidate = GraphExperimentConfig.model_validate(raw_config)
    candidate.set_project_root(Path.cwd())
    if data_config is not None:
        candidate.paths.data_config = Path(data_config)
    if training_config is not None:
        candidate.paths.training_config = Path(training_config)
    with pytest.raises(ValueError, match=message):
        candidate.validate_source_binding()


def test_same_source_but_different_serving_feature_path_is_rejected(
    raw_config: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = GraphExperimentConfig.model_validate(raw_config)
    candidate.set_project_root(Path.cwd())
    baseline = load_training_config("configs/train_baseline_ieee_cis.yaml")
    baseline.paths.serving_features = Path("data/interim/ieee-cis/wrong.parquet")
    monkeypatch.setattr(graph_config_module, "load_training_config", lambda _: baseline)
    with pytest.raises(ValueError, match="interim_features must match"):
        candidate.validate_source_binding()


def test_project_names_must_match(raw_config: dict[str, object]) -> None:
    candidate = GraphExperimentConfig.model_validate(raw_config)
    candidate.project_name = "unrelated-project"
    candidate.set_project_root(Path.cwd())
    with pytest.raises(ValueError, match="project_name must match"):
        candidate.validate_source_binding()
