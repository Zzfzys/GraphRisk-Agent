from __future__ import annotations

import argparse
import json
import os

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import mlflow

from graphrisk.training.config import load_training_config


def main() -> None:
    parser = argparse.ArgumentParser(description="List Stage 02 MLflow runs")
    parser.add_argument("--config", default="configs/train_baseline.yaml")
    arguments = parser.parse_args()

    config = load_training_config(arguments.config)
    database_path = config.resolve_path(config.paths.mlflow_database)
    mlflow.set_tracking_uri(f"sqlite:///{database_path.as_posix()}")
    client = mlflow.MlflowClient()
    experiment = client.get_experiment_by_name(config.mlflow.experiment_name)
    if experiment is None:
        raise SystemExit(f"experiment not found: {config.mlflow.experiment_name}")

    runs = client.search_runs(
        experiment_ids=[experiment.experiment_id],
        order_by=["attributes.start_time ASC"],
    )
    payload = [
        {
            "run_id": run.info.run_id,
            "run_name": run.info.run_name,
            "status": run.info.status,
            "validation_pr_auc": run.data.metrics.get("validation_pr_auc"),
            "test_pr_auc": run.data.metrics.get("test_pr_auc"),
        }
        for run in runs
    ]
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
