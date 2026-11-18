from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import joblib

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import mlflow
import numpy as np
import pandas as pd

from graphrisk.logging_config import configure_logging
from graphrisk.models.tabular import FEATURE_COLUMNS, ModelName, TabularRiskModel
from graphrisk.training.config import BaselineTrainingConfig, load_training_config
from graphrisk.training.metrics import evaluate_binary_classifier

LOGGER = logging.getLogger(__name__)
TARGET_COLUMN = "is_fraud"


@dataclass(frozen=True)
class ModelRunResult:
    model_name: str
    model_version: str
    validation_metrics: dict[str, float]
    test_metrics: dict[str, float]
    training_seconds: float
    p95_latency_ms: float
    model_size_bytes: int
    transformed_feature_count: int
    model_path: Path
    metadata_path: Path


@dataclass(frozen=True)
class TrainingSummary:
    champion_name: str
    champion_model_path: Path
    champion_metadata_path: Path
    results_csv: Path
    runs: tuple[ModelRunResult, ...]


def train_baselines(config_path: str | Path) -> TrainingSummary:
    config = load_training_config(config_path)
    configure_logging("INFO")

    train_frame = _load_split(config, config.paths.train_features, "train")
    validation_frame = _load_split(config, config.paths.validation_features, "validation")
    test_frame = _load_split(config, config.paths.test_features, "test")
    _validate_temporal_boundaries(train_frame, validation_frame, test_frame)

    output_dir = config.resolve_path(config.paths.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    mlflow_database = config.resolve_path(config.paths.mlflow_database)
    mlflow_database.parent.mkdir(parents=True, exist_ok=True)
    mlflow_artifacts = config.resolve_path(config.paths.mlflow_artifacts)
    mlflow_artifacts.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(f"sqlite:///{mlflow_database.as_posix()}")
    client = mlflow.MlflowClient()
    experiment = client.get_experiment_by_name(config.mlflow.experiment_name)
    if experiment is None:
        experiment_id = client.create_experiment(
            config.mlflow.experiment_name,
            artifact_location=mlflow_artifacts.as_uri(),
        )
    else:
        experiment_id = experiment.experiment_id
    mlflow.set_experiment(experiment_id=experiment_id)

    negative_count = int((train_frame[TARGET_COLUMN] == 0).sum())
    positive_count = int((train_frame[TARGET_COLUMN] == 1).sum())
    if positive_count == 0 or negative_count == 0:
        raise ValueError("training split must contain both fraud and non-fraud labels")
    positive_weight = negative_count / positive_count

    runs: list[ModelRunResult] = []
    for model_name in config.training.models:
        run = _train_one_model(
            config,
            model_name,
            train_frame,
            validation_frame,
            test_frame,
            positive_weight=positive_weight,
            output_dir=output_dir,
        )
        runs.append(run)

    champion = max(runs, key=lambda run: run.validation_metrics["pr_auc"])
    champion_dir = output_dir / "champion"
    champion_dir.mkdir(parents=True, exist_ok=True)
    champion_model_path = champion_dir / "model.joblib"
    champion_metadata_path = champion_dir / "metadata.json"
    shutil.copy2(champion.model_path, champion_model_path)
    champion_metadata = json.loads(champion.metadata_path.read_text(encoding="utf-8"))
    champion_metadata["is_champion"] = True
    champion_metadata["selected_by"] = "validation_pr_auc"
    champion_metadata_path.write_text(
        json.dumps(champion_metadata, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    results_csv = config.resolve_path(config.paths.results_csv)
    _write_results_csv(runs, results_csv)
    LOGGER.info(
        "baseline training completed",
        extra={
            "champion": champion.model_name,
            "validation_pr_auc": champion.validation_metrics["pr_auc"],
            "test_pr_auc": champion.test_metrics["pr_auc"],
            "results_csv": str(results_csv),
        },
    )
    return TrainingSummary(
        champion_name=champion.model_name,
        champion_model_path=champion_model_path,
        champion_metadata_path=champion_metadata_path,
        results_csv=results_csv,
        runs=tuple(runs),
    )


def _train_one_model(
    config: BaselineTrainingConfig,
    model_name: ModelName,
    train_frame: pd.DataFrame,
    validation_frame: pd.DataFrame,
    test_frame: pd.DataFrame,
    *,
    positive_weight: float,
    output_dir: Path,
) -> ModelRunResult:
    model = TabularRiskModel.create(
        model_name,
        random_seed=config.training.random_seed,
        positive_weight=positive_weight,
    )
    training_started = time.perf_counter()
    model.train(train_frame, train_frame[TARGET_COLUMN])
    training_seconds = time.perf_counter() - training_started

    validation_scores = model.predict_scores(validation_frame)
    test_scores = model.predict_scores(test_frame)
    validation_metrics = evaluate_binary_classifier(
        validation_frame[TARGET_COLUMN],
        validation_scores,
        decision_threshold=config.training.decision_threshold,
    )
    test_metrics = evaluate_binary_classifier(
        test_frame[TARGET_COLUMN],
        test_scores,
        decision_threshold=config.training.decision_threshold,
    )
    p95_latency_ms = _measure_p95_latency(
        model,
        test_frame,
        sample_size=config.training.latency_sample_size,
    )

    model_dir = output_dir / model_name
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "model.joblib"
    metadata_path = model_dir / "metadata.json"
    joblib.dump(model, model_path, compress=3)

    model_version = f"{model_name}-v1"
    metadata = {
        "model_name": model_name,
        "model_version": model_version,
        "dataset_source": config.dataset_source,
        "created_at": datetime.now(UTC).isoformat(),
        "git_commit": _git_commit(),
        "feature_columns": list(FEATURE_COLUMNS),
        "transformed_feature_count": model.transformed_feature_count(),
        "decision_threshold": config.training.decision_threshold,
        "risk_thresholds": {
            "low": config.serving.low_threshold,
            "high": config.serving.high_threshold,
        },
        "validation_metrics": validation_metrics,
        "test_metrics": test_metrics,
        "training_seconds": training_seconds,
        "p95_latency_ms": p95_latency_ms,
        "is_champion": False,
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    model_size_bytes = model_path.stat().st_size
    with mlflow.start_run(run_name=model_name):
        mlflow.log_params(
            {
                "model_name": model_name,
                "random_seed": config.training.random_seed,
                "decision_threshold": config.training.decision_threshold,
                "dataset_source": config.dataset_source,
                "train_rows": len(train_frame),
                "validation_rows": len(validation_frame),
                "test_rows": len(test_frame),
                "positive_weight": positive_weight,
                "feature_count": len(FEATURE_COLUMNS),
            }
        )
        mlflow.log_metrics(
            {
                **{f"validation_{key}": value for key, value in validation_metrics.items()},
                **{f"test_{key}": value for key, value in test_metrics.items()},
                "training_seconds": training_seconds,
                "p95_latency_ms": p95_latency_ms,
                "model_size_bytes": model_size_bytes,
                "transformed_feature_count": model.transformed_feature_count(),
            }
        )
        mlflow.set_tags(
            {
                "stage": "stage-02",
                "dataset_source": config.dataset_source,
                "git_commit": metadata["git_commit"],
            }
        )
        mlflow.log_artifact(str(model_path), artifact_path="model")
        mlflow.log_artifact(str(metadata_path), artifact_path="model")

    return ModelRunResult(
        model_name=model_name,
        model_version=model_version,
        validation_metrics=validation_metrics,
        test_metrics=test_metrics,
        training_seconds=training_seconds,
        p95_latency_ms=p95_latency_ms,
        model_size_bytes=model_size_bytes,
        transformed_feature_count=model.transformed_feature_count(),
        model_path=model_path,
        metadata_path=metadata_path,
    )


def _load_split(
    config: BaselineTrainingConfig,
    configured_path: Path,
    expected_split: str,
) -> pd.DataFrame:
    path = config.resolve_path(configured_path)
    if not path.is_file():
        raise FileNotFoundError(f"{expected_split} feature file does not exist: {path}")
    with duckdb.connect() as connection:
        frame = connection.from_parquet(str(path)).df()
    required_columns = set(FEATURE_COLUMNS) | {
        TARGET_COLUMN,
        "transaction_id",
        "transaction_time",
        "dataset_split",
    }
    missing = sorted(required_columns - set(frame.columns))
    if missing:
        raise ValueError(f"{expected_split} split is missing columns: {', '.join(missing)}")
    actual_splits = set(frame["dataset_split"].unique())
    if actual_splits != {expected_split}:
        raise ValueError(
            f"expected only split {expected_split!r}, found {sorted(actual_splits)}"
        )
    return frame.sort_values(["transaction_time", "transaction_id"]).reset_index(drop=True)


def _validate_temporal_boundaries(
    train_frame: pd.DataFrame,
    validation_frame: pd.DataFrame,
    test_frame: pd.DataFrame,
) -> None:
    if train_frame["transaction_time"].max() > validation_frame["transaction_time"].min():
        raise ValueError("train and validation time ranges overlap out of order")
    if validation_frame["transaction_time"].max() > test_frame["transaction_time"].min():
        raise ValueError("validation and test time ranges overlap out of order")


def _measure_p95_latency(
    model: TabularRiskModel,
    frame: pd.DataFrame,
    *,
    sample_size: int,
) -> float:
    sample = frame.iloc[: min(sample_size, len(frame))]
    if sample.empty:
        raise ValueError("latency sample cannot be empty")
    model.predict_scores(sample.iloc[[0]])
    latencies_ms: list[float] = []
    for row_index in range(len(sample)):
        started = time.perf_counter()
        model.predict_scores(sample.iloc[[row_index]])
        latencies_ms.append((time.perf_counter() - started) * 1000)
    return float(np.percentile(latencies_ms, 95))


def _write_results_csv(runs: list[ModelRunResult], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for run in runs:
        row: dict[str, object] = {
            "model": run.model_name,
            "model_version": run.model_version,
            "training_seconds": run.training_seconds,
            "p95_latency_ms": run.p95_latency_ms,
            "model_size_bytes": run.model_size_bytes,
            "transformed_feature_count": run.transformed_feature_count,
        }
        row.update({f"validation_{key}": value for key, value in run.validation_metrics.items()})
        row.update({f"test_{key}": value for key, value in run.test_metrics.items()})
        rows.append(row)

    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Train GraphRisk tabular baselines")
    parser.add_argument("--config", default="configs/train_baseline.yaml")
    arguments = parser.parse_args()

    summary = train_baselines(arguments.config)
    print(
        f"champion={summary.champion_name}, model={summary.champion_model_path}, "
        f"results={summary.results_csv}"
    )


if __name__ == "__main__":
    main()
