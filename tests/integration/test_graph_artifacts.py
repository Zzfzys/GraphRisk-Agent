from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
import torch
import yaml
from torch_geometric.data import Batch, Data

from graphrisk.graph.build_artifacts import build_graph_artifacts, load_graph_artifacts
from graphrisk.graph.train_graphsage import load_graphsage_checkpoint, train_graphsage
from graphrisk.pipeline import run_pipeline


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _setup(tmp_path: Path) -> Path:
    root = tmp_path
    configs = root / "configs"
    data_config = configs / "dev.yaml"
    _write_yaml(
        data_config,
        {
            "project_name": "test-graphrisk",
            "paths": {
                "raw_transactions": "data/raw/transactions.csv",
                "interim_transactions": "data/interim/transactions.parquet",
                "interim_features": "data/interim/features.parquet",
                "processed_dir": "data/processed",
                "feature_report": "reports/feature-report.md",
            },
            "data": {"source": "synthetic", "regenerate_synthetic": True},
            "split": {"train_ratio": 0.70, "validation_ratio": 0.15, "test_ratio": 0.15},
            "synthetic": {
                "rows": 120,
                "seed": 7,
                "start_time": "2025-01-01T00:00:00+00:00",
            },
        },
    )
    run_pipeline(data_config)
    training_config = configs / "train.yaml"
    _write_yaml(
        training_config,
        {
            "project_name": "test-graphrisk",
            "dataset_source": "synthetic",
            "paths": {
                "train_features": "data/processed/train_features.parquet",
                "validation_features": "data/processed/validation_features.parquet",
                "test_features": "data/processed/test_features.parquet",
                "serving_features": "data/interim/features.parquet",
                "output_dir": "artifacts/stage-02",
                "results_csv": "reports/baseline.csv",
                "mlflow_database": "artifacts/stage-02/mlflow.db",
                "mlflow_artifacts": "artifacts/stage-02/mlflow-artifacts",
            },
            "training": {
                "random_seed": 7,
                "decision_threshold": 0.5,
                "models": ["dummy", "logistic_regression", "xgboost"],
                "latency_sample_size": 2,
            },
            "mlflow": {"experiment_name": "test"},
            "serving": {"low_threshold": 0.3, "high_threshold": 0.7},
        },
    )
    champion = root / "artifacts/stage-02/champion"
    champion.mkdir(parents=True)
    (champion / "model.joblib").write_bytes(b"fixture")
    (champion / "metadata.json").write_text("{}", encoding="utf-8")
    graph_config = configs / "graph.yaml"
    artifact = "artifacts/stage-04/test"
    _write_yaml(
        graph_config,
        {
            "project_name": "test-graphrisk",
            "dataset_source": "synthetic",
            "paths": {
                "data_config": "configs/dev.yaml",
                "training_config": "configs/train.yaml",
                "artifact_dir": artifact,
                "train_ids": f"{artifact}/train-ids.json",
                "validation_ids": f"{artifact}/validation-ids.json",
                "test_ids": f"{artifact}/test-ids.json",
                "train_predictions": f"{artifact}/train-predictions.parquet",
                "validation_predictions": f"{artifact}/validation-predictions.parquet",
                "test_predictions": f"{artifact}/test-predictions.parquet",
                "graph_manifest": f"{artifact}/graph-manifest.json",
            },
            "sampling": {
                "fanout": [4, 2],
                "max_train_transactions": 20,
                "max_validation_transactions": 5,
                "max_test_transactions": 5,
                "selection": "chronological_prefix",
            },
            "training": {
                "model_version": "graphsage-test",
                "random_seed": 7,
                "batch_size": 4,
                "hidden_channels": 8,
                "max_epochs": 2,
                "early_stopping_patience": 1,
                "device": "cpu",
            },
        },
    )
    return graph_config


def test_graph_artifacts_repeat_and_exclude_labels(tmp_path: Path) -> None:
    config = _setup(tmp_path)
    first = build_graph_artifacts(config)
    manifest_bytes = first.manifest_path.read_bytes()
    second = build_graph_artifacts(config)
    assert second.manifest_path.read_bytes() == manifest_bytes
    assert second.content_sha256 == first.content_sha256
    assert first.target_counts == {"train": 20, "validation": 5, "test": 5}
    manifest = json.loads(manifest_bytes)
    assert manifest["indexed_transaction_count"] <= 120
    assert manifest["global_entity_edge_count"] > 0
    assert manifest["ego_statistics"]["train"]["targets"] == 20
    assert "is_fraud" not in manifest["feature_names"]

    artifact_dir = first.manifest_path.parent
    with duckdb.connect(":memory:") as connection:
        projected = artifact_dir / "projected-transactions.parquet"
        columns = [
            row[0]
            for row in connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(projected)]
            ).fetchall()
        ]
        assert "is_fraud" not in columns
        assert "identity_key" not in columns
        assert "dataset_split" not in columns
        future_edges = connection.execute(
            "SELECT COUNT(*) FROM read_parquet(?) WHERE transaction_time > ?",
            [str(artifact_dir / "entity-edges.parquet"), manifest["indexed_through_time"]],
        ).fetchone()[0]
        assert future_edges == 0
    sample = json.loads((artifact_dir / "ego-sample.json").read_text(encoding="utf-8"))
    assert len(sample["graphs"]) == 2
    data_list = [
        Data(
            x=torch.tensor(graph["x"]),
            edge_index=torch.tensor(graph["edge_index"], dtype=torch.long),
            root_index=torch.tensor(graph["root_index"]),
        )
        for graph in sample["graphs"]
    ]
    batch = Batch.from_data_list(data_list)
    assert batch.num_graphs == 2
    assert batch.root_index.tolist() == [0, data_list[0].num_nodes]
    assert batch.x.shape[0] == sum(len(graph["node_keys"]) for graph in sample["graphs"])
    loaded = load_graph_artifacts(first.manifest_path, expected_dataset_source="synthetic")
    validation = loaded.ego_graph(loaded.target_ids["validation"][0])
    assert all(
        node.transaction.transaction_time < validation.nodes[0].transaction.transaction_time
        for node in validation.nodes[1:]
        if node.transaction is not None
    )
    with pytest.raises(ValueError, match="dataset_source"):
        load_graph_artifacts(first.manifest_path, expected_dataset_source="ieee_cis")
    encoder_path = artifact_dir / "encoder.json"
    original_encoder = encoder_path.read_bytes()
    encoder_path.write_bytes(original_encoder + b" ")
    with pytest.raises(ValueError, match="different hash"):
        load_graph_artifacts(first.manifest_path)
    encoder_path.write_bytes(original_encoder)
    with (tmp_path / "data/interim/features.parquet").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="source changed"):
        load_graph_artifacts(first.manifest_path)


def test_graph_artifacts_reject_stale_feature_source(tmp_path: Path) -> None:
    config = _setup(tmp_path)
    feature_path = tmp_path / "data/interim/features.parquet"
    with duckdb.connect(":memory:") as connection:
        altered = tmp_path / "altered.parquet"
        connection.from_parquet(str(feature_path)).create_view("features")
        connection.sql(
            "SELECT * REPLACE (transaction_amount + 1 AS transaction_amount) "
            "FROM features"
        ).write_parquet(str(altered))
    altered.replace(feature_path)
    with pytest.raises(ValueError, match="canonical transactions and point-in-time features disagree"):
        build_graph_artifacts(config)


def test_graph_artifacts_reject_split_feature_drift(tmp_path: Path) -> None:
    config = _setup(tmp_path)
    train_path = tmp_path / "data/processed/train_features.parquet"
    with duckdb.connect(":memory:") as connection:
        altered = tmp_path / "altered-train.parquet"
        connection.from_parquet(str(train_path)).create_view("train_features")
        connection.sql(
            "SELECT * REPLACE ('Z'::VARCHAR AS product_code) FROM train_features"
        ).write_parquet(str(altered))
    altered.replace(train_path)
    with pytest.raises(ValueError, match="split files do not partition"):
        build_graph_artifacts(config)


def test_graphsage_trains_and_reloads_on_small_temporal_source(tmp_path: Path) -> None:
    config = _setup(tmp_path)
    split_paths = {
        name: tmp_path / f"data/processed/{name}_features.parquet"
        for name in ("train", "validation", "test")
    }
    positives = []
    with duckdb.connect(":memory:") as connection:
        for name, path in split_paths.items():
            ids = [
                row[0]
                for row in connection.execute(
                    "SELECT transaction_id FROM read_parquet(?) "
                    "ORDER BY transaction_time, transaction_id LIMIT ?",
                    [str(path), 20 if name == "train" else 5],
                ).fetchall()
            ]
            positives.extend(ids[:2] if name == "train" else ids[:1])
    quoted = ", ".join(f"'{transaction_id}'" for transaction_id in positives)
    source_paths = [
        tmp_path / "data/interim/transactions.parquet",
        tmp_path / "data/interim/features.parquet",
        *split_paths.values(),
    ]
    for path in source_paths:
        altered = path.with_name("altered-" + path.name)
        with duckdb.connect(":memory:") as connection:
            connection.from_parquet(str(path)).create_view("source")
            connection.sql(
                "SELECT * REPLACE (CASE WHEN transaction_id IN "
                f"({quoted}) THEN 1 ELSE 0 END::TINYINT AS is_fraud) FROM source"
            ).write_parquet(str(altered))
        altered.replace(path)
    build_graph_artifacts(config)
    result = train_graphsage(config)
    first_history = result.history_path.read_bytes()
    repeated = train_graphsage(config)
    assert repeated.history_path.read_bytes() == first_history
    assert repeated.validation_pr_auc == result.validation_pr_auc
    assert result.best_epoch in (1, 2)
    assert 0 <= result.validation_pr_auc <= 1
    assert 0 <= result.test_pr_auc <= 1
    assert result.device == "cpu"
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["label_counts"]["train_positive"] == 2
    assert metadata["positive_weight"] == 9.0
    assert metadata["selected_by"] == "validation_pr_auc"
    assert metadata["score_calibration"] == "uncalibrated sigmoid score"
    store = load_graph_artifacts(tmp_path / "artifacts/stage-04/test/graph-manifest.json")
    restored = load_graphsage_checkpoint(result.model_path, result.metadata_path, store)
    graph = store.ego_graph(store.target_ids["validation"][0]).to_pyg(store.encoder)
    with torch.no_grad():
        assert torch.isfinite(restored(graph)).all()
    config_data = yaml.safe_load(config.read_text(encoding="utf-8"))
    config_data["training"]["model_version"] = "different-version"
    _write_yaml(config, config_data)
    with pytest.raises(ValueError, match="model_version"):
        train_graphsage(config)
