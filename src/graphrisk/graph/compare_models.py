"""Same-ID Stage 04 comparison with validation-only fusion selection."""

from __future__ import annotations

import hashlib
import json
import re
import time
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
from torch_geometric.data import Batch

from graphrisk.graph.build_artifacts import LoadedGraphArtifacts, load_graph_artifacts
from graphrisk.graph.config import load_graph_config
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.train_graphsage import (
    _sha256,
    _verify_label_provenance,
    _write_json,
    load_graphsage_checkpoint,
)
from graphrisk.models.tabular import FEATURE_COLUMNS, TabularRiskModel
from graphrisk.training.config import load_training_config
from graphrisk.training.metrics import evaluate_binary_classifier
from graphrisk.training.train_baselines import _load_split

ALPHAS = (0.0, 0.25, 0.5, 0.75, 1.0)
SPLITS = ("train", "validation", "test")


@dataclass(frozen=True)
class ComparisonResult:
    output_dir: Path
    report_path: Path
    metadata_path: Path
    selected_alpha: float
    validation_metrics: dict[str, dict[str, float]]
    test_metrics: dict[str, dict[str, float]]


def _ids_sha256(ids: tuple[str, ...]) -> str:
    return hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()


def _aligned_frame(frame: pd.DataFrame, ids: tuple[str, ...], split: str) -> pd.DataFrame:
    """Require exact one-to-one coverage and return the graph manifest order."""
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"{split} graph target IDs must be non-empty and unique")
    if frame["transaction_id"].isna().any() or frame["transaction_id"].duplicated().any():
        raise ValueError(f"{split} tabular transaction IDs are null or duplicated")
    target = pd.DataFrame({"transaction_id": list(ids)})
    aligned = target.merge(frame, on="transaction_id", how="inner", validate="one_to_one", sort=False)
    if len(aligned) != len(ids):
        raise ValueError(f"{split} tabular source is missing {len(ids) - len(aligned)} graph target IDs")
    if tuple(aligned["transaction_id"]) != ids or len(aligned) != len(ids):
        raise ValueError(f"{split} tabular/graph ID order or count differs")
    labels = aligned["is_fraud"]
    if labels.isna().any() or not labels.isin((0, 1)).all():
        raise ValueError(f"{split} labels must be binary 0/1")
    return aligned


def _predict_graph(
    model: GraphSAGERiskModel,
    store: LoadedGraphArtifacts,
    ids: tuple[str, ...],
    *,
    batch_size: int,
    device: torch.device,
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    model.eval()
    scores: list[np.ndarray] = []
    embeddings: list[np.ndarray] = []
    build_seconds = 0.0
    inference_seconds = 0.0
    batch_latencies_ms: list[float] = []
    zero_history = 0
    zero_entities = 0
    cold_start_targets = 0
    truncated_targets = 0
    for start in range(0, len(ids), batch_size):
        graphs = []
        for transaction_id in ids[start : start + batch_size]:
            started = time.perf_counter()
            ego = store.ego_graph(transaction_id)
            graphs.append(ego.to_pyg(store.encoder))
            build_seconds += time.perf_counter() - started
            zero_history += not ego.selected_history_ids
            zero_entities += not store.index.by_id[transaction_id].valid_entities()
            cold_start_targets += _has_cold_start_entity(store, transaction_id)
            truncated_targets += bool(store.index.truncated_entity_count(
                transaction_id, max_history_per_entity=store.manifest["fanout"][1]
            ))
        batch = Batch.from_data_list(graphs).to(device)
        started = time.perf_counter()
        with torch.no_grad():
            root_embedding = model.encode_root(batch)
            logits = model.classifier(root_embedding).squeeze(-1)
            probabilities = torch.sigmoid(logits)
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        elapsed = time.perf_counter() - started
        inference_seconds += elapsed
        batch_latencies_ms.append(elapsed * 1000)
        scores.append(probabilities.cpu().numpy())
        embeddings.append(root_embedding.cpu().numpy())
    result_scores = np.concatenate(scores)
    result_embeddings = np.concatenate(embeddings)
    if len(result_scores) != len(ids) or result_embeddings.shape != (len(ids), model.hidden_channels):
        raise RuntimeError("GraphSAGE scores or embeddings lost target alignment")
    return result_scores, result_embeddings, {
        "graph_build_seconds": build_seconds,
        "graph_inference_seconds": inference_seconds,
        "graph_batch_p95_ms": float(np.percentile(batch_latencies_ms, 95)),
        "graph_inference_ms_per_target": inference_seconds * 1000 / len(ids),
        "zero_history_targets": zero_history,
        "zero_entity_targets": zero_entities,
        "cold_start_targets": cold_start_targets,
        "cold_start_fraction": cold_start_targets / len(ids),
        "truncated_targets": truncated_targets,
        "zero_history_fraction": zero_history / len(ids),
        "truncated_fraction": truncated_targets / len(ids),
        "graph_single_sample_p95_ms": _graph_single_p95(model, store, ids, device),
    }


def _has_cold_start_entity(store: LoadedGraphArtifacts, transaction_id: str) -> bool:
    transaction = store.index.by_id[transaction_id]
    return any(
        bisect_left(store.index.postings[entity], (transaction.transaction_time, "")) == 0
        for entity in transaction.valid_entities()
    )


def _graph_single_p95(
    model: GraphSAGERiskModel,
    store: LoadedGraphArtifacts,
    ids: tuple[str, ...],
    device: torch.device,
) -> float:
    durations = []
    for transaction_id in ids[: min(20, len(ids))]:
        started = time.perf_counter()
        graph = store.ego_graph(transaction_id).to_pyg(store.encoder).to(device)
        with torch.no_grad():
            model(graph)
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        durations.append((time.perf_counter() - started) * 1000)
    return float(np.percentile(durations, 95))


def _predict_tabular(model: TabularRiskModel, frame: pd.DataFrame) -> tuple[np.ndarray, dict[str, float]]:
    started = time.perf_counter()
    scores = model.predict_scores(frame)
    full_seconds = time.perf_counter() - started
    single_ms = []
    for index in range(min(20, len(frame))):
        started = time.perf_counter()
        model.predict_scores(frame.iloc[[index]])
        single_ms.append((time.perf_counter() - started) * 1000)
    return scores, {
        "tabular_inference_seconds": full_seconds,
        "tabular_single_sample_p95_ms": float(np.percentile(single_ms, 95)),
        "tabular_batch_ms_per_target": full_seconds * 1000 / len(frame),
    }


def _choose_alpha(labels: np.ndarray, tabular: np.ndarray, graph: np.ndarray) -> tuple[float, list[dict[str, float]]]:
    if labels.shape != tabular.shape or labels.shape != graph.shape:
        raise ValueError("fusion inputs must be aligned one-dimensional arrays")
    trials = []
    for alpha in ALPHAS:
        score = alpha * tabular + (1 - alpha) * graph
        metrics = evaluate_binary_classifier(labels, score, decision_threshold=0.5)
        trials.append({"alpha": alpha, "validation_pr_auc": metrics["pr_auc"]})
    # Deterministic tie-break: prefer the tabular model, including alpha=1.
    winner = max(trials, key=lambda row: (row["validation_pr_auc"], row["alpha"]))
    return winner["alpha"], trials


def _metrics(labels: np.ndarray, tabular: np.ndarray, graph: np.ndarray, alpha: float) -> dict[str, dict[str, float]]:
    return {
        "xgboost": evaluate_binary_classifier(labels, tabular, decision_threshold=0.5),
        "graphsage": evaluate_binary_classifier(labels, graph, decision_threshold=0.5),
        "fusion": evaluate_binary_classifier(
            labels, alpha * tabular + (1 - alpha) * graph, decision_threshold=0.5
        ),
    }


def _write_split(
    output_dir: Path,
    split: str,
    ids: tuple[str, ...],
    labels: np.ndarray,
    tabular: np.ndarray,
    graph: np.ndarray,
    embeddings: np.ndarray,
    alpha: float,
    graph_version: str,
) -> tuple[Path, Path]:
    prediction_path = output_dir / f"{split}-predictions.parquet"
    embedding_path = output_dir / f"{split}-embeddings.parquet"
    if embeddings.shape[0] != len(ids):
        raise ValueError(f"{split} embedding count does not match target IDs")
    embedding_frame = pd.DataFrame(
        embeddings, columns=[f"dim_{i:03d}" for i in range(embeddings.shape[1])]
    )
    embedding_frame.insert(0, "transaction_id", list(ids))
    embedding_frame.insert(1, "split", split)
    embedding_frame.to_parquet(embedding_path, index=False)
    pd.DataFrame({
        "transaction_id": list(ids),
        "split": split,
        "is_fraud": labels.astype(int),
        "xgboost_score": tabular,
        "graphsage_score": graph,
        "fusion_score": alpha * tabular + (1 - alpha) * graph,
        "xgboost_model_version": "xgboost-controlled-v1",
        "graphsage_model_version": graph_version,
        "embedding_file": embedding_path.name,
    }).to_parquet(prediction_path, index=False)
    return prediction_path, embedding_path


def _report(metadata: dict) -> str:
    lines = [
        "# Stage 04 route 4: same-ID comparison",
        "",
        f"Dataset: `{metadata['dataset_source']}`; graph manifest SHA-256: `{metadata['graph_manifest_sha256']}`.",
        f"Graph checkpoint SHA-256: `{metadata['graph_model_sha256']}`. XGBoost trained on exactly the graph train IDs.",
        "",
        "| Split | Count | Positive | Ordered ID SHA-256 | Zero-history | Cold-start | Truncated |",
        "|---|---:|---:|---|---:|---:|---:|",
    ]
    for split in SPLITS:
        item = metadata["splits"][split]
        lines.append(
            f"| {split} | {item['count']} | {item['positives']} | `{item['ordered_id_sha256']}` | "
            f"{item['graph_diagnostics']['zero_history_fraction']:.4f} | "
            f"{item['graph_diagnostics']['cold_start_fraction']:.4f} | "
            f"{item['graph_diagnostics']['truncated_fraction']:.4f} |"
        )
    lines += [
        "",
        f"Fusion: `alpha * XGBoost + (1 - alpha) * GraphSAGE`; validation-selected alpha = **{metadata['selected_alpha']}**.",
        "Candidate alphas and validation PR-AUC: "
        + ", ".join(f"{row['alpha']}: {row['validation_pr_auc']:.6f}" for row in metadata["alpha_trials"])
        + ".",
        "",
        "| Split | Model | PR-AUC | ROC-AUC | Precision | Recall | F1 | P@1% | R@1% | P@5% | R@5% |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    columns = ("pr_auc", "roc_auc", "precision", "recall", "f1", "precision_at_1pct", "recall_at_1pct", "precision_at_5pct", "recall_at_5pct")
    for split in ("validation", "test"):
        for model_name, metrics in metadata["metrics"][split].items():
            lines.append(f"| {split} | {model_name} | " + " | ".join(f"{metrics[key]:.6f}" for key in columns) + " |")
    lines += [
        "",
        f"XGBoost training: {metadata['xgboost_training_seconds']:.2f}s; model size: {metadata['xgboost_model_size_bytes']} bytes. "
        f"GraphSAGE training: {metadata['graph_training_seconds']:.2f}s; model size: {metadata['graph_model_size_bytes']} bytes.",
        "Graph construction, batch/single inference and XGBoost latency are in `metadata.json` under each split's diagnostics.",
        "Decision metrics use a fixed 0.5 threshold for illustration; scores and their fusion are uncalibrated, not fraud probabilities.",
        "Cold-start means at least one valid root entity has no strictly earlier occurrence; truncation means at least one root entity exceeds the fanout cap.",
        "All model and alpha selection used training/validation only; test labels were accessed after alpha was frozen.",
        "Stage 02 full-train XGBoost is not a row in this controlled comparison.",
        "",
    ]
    return "\n".join(lines)


def compare_same_ids(
    config_path: str | Path,
    *,
    run_name: str = "same-id-v1",
    graph_checkpoint_dir: str | Path | None = None,
) -> ComparisonResult:
    """Train controlled XGBoost, select alpha on validation, then evaluate held-out test."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_name) or run_name in (".", ".."):
        raise ValueError("run_name must be a safe single directory name")
    config = load_graph_config(config_path)
    artifact_root = config.resolve_path(config.paths.artifact_dir)
    output_dir = artifact_root / "comparisons" / run_name
    if (output_dir / "metadata.json").exists():
        raise FileExistsError(f"comparison run_name already has a completed run: {run_name}")
    store = load_graph_artifacts(
        config.resolve_path(config.paths.graph_manifest),
        expected_dataset_source=config.dataset_source,
    )
    if store.manifest["model_version"] != config.training.model_version:
        raise ValueError("GraphSAGE graph manifest model_version does not match config")
    for split in SPLITS:
        if len(store.target_ids[split]) != getattr(config.sampling, f"max_{split}_transactions"):
            raise ValueError(f"{split} graph target count differs from config")
    checkpoint = (
        Path(graph_checkpoint_dir).resolve()
        if graph_checkpoint_dir is not None
        else artifact_root / "models" / config.training.model_version
    )
    if not checkpoint.is_relative_to(artifact_root):
        raise ValueError("GraphSAGE checkpoint must reside under graph artifact_dir")
    model_path = checkpoint / "model.pt"
    graph_metadata_path = checkpoint / "metadata.json"
    graph_model = load_graphsage_checkpoint(
        model_path, graph_metadata_path, store,
        expected_model_version=config.training.model_version,
    )
    graph_metadata = json.loads(graph_metadata_path.read_text(encoding="utf-8"))
    if graph_metadata.get("target_counts") != {split: len(store.target_ids[split]) for split in SPLITS}:
        raise ValueError("GraphSAGE checkpoint target counts do not match graph")
    training = load_training_config(config.resolve_path(config.paths.training_config))
    paths = {
        split: training.resolve_path(getattr(training.paths, f"{split}_features"))
        for split in SPLITS
    }
    _verify_label_provenance(config, {split: paths[split] for split in ("train", "validation")})
    train = _aligned_frame(_load_split(training, training.paths.train_features, "train"), store.target_ids["train"], "train")
    validation = _aligned_frame(_load_split(training, training.paths.validation_features, "validation"), store.target_ids["validation"], "validation")
    train_labels = train["is_fraud"].to_numpy(dtype=int)
    validation_labels = validation["is_fraud"].to_numpy(dtype=int)
    positives = int(train_labels.sum())
    negatives = len(train_labels) - positives
    if min(positives, negatives) == 0 or len(np.unique(validation_labels)) != 2:
        raise ValueError("train and validation each require both label classes")
    tabular_model = TabularRiskModel.create(
        "xgboost", random_seed=config.training.random_seed, positive_weight=negatives / positives
    )
    started = time.perf_counter()
    tabular_model.train(train, train["is_fraud"])
    training_seconds = time.perf_counter() - started
    validation_tabular, validation_tabular_diagnostics = _predict_tabular(tabular_model, validation)
    validation_graph, validation_embeddings, validation_diagnostics = _predict_graph(
        graph_model, store, store.target_ids["validation"],
        batch_size=config.training.batch_size, device=torch.device("cpu"),
    )
    alpha, trials = _choose_alpha(validation_labels, validation_tabular, validation_graph)
    validation_metrics = _metrics(validation_labels, validation_tabular, validation_graph, alpha)

    # The first access to test labels occurs after the fusion coefficient is frozen.
    _verify_label_provenance(config, {"test": paths["test"]})
    test = _aligned_frame(_load_split(training, training.paths.test_features, "test"), store.target_ids["test"], "test")
    test_labels = test["is_fraud"].to_numpy(dtype=int)
    if len(np.unique(test_labels)) != 2:
        raise ValueError("test split needs both label classes")
    test_tabular, test_tabular_diagnostics = _predict_tabular(tabular_model, test)
    test_graph, test_embeddings, test_diagnostics = _predict_graph(
        graph_model, store, store.target_ids["test"],
        batch_size=config.training.batch_size, device=torch.device("cpu"),
    )
    test_metrics = _metrics(test_labels, test_tabular, test_graph, alpha)

    output_dir.mkdir(parents=True, exist_ok=True)
    tabular_path = output_dir / "xgboost-controlled.joblib"
    joblib.dump(tabular_model, tabular_path, compress=3)
    output_files = {}
    for split, labels, tabular, graph, embeddings in (
        ("validation", validation_labels, validation_tabular, validation_graph, validation_embeddings),
        ("test", test_labels, test_tabular, test_graph, test_embeddings),
    ):
        prediction, embedding = _write_split(
            output_dir, split, store.target_ids[split], labels, tabular, graph,
            embeddings, alpha, config.training.model_version,
        )
        output_files[f"{split}_predictions"] = prediction
        output_files[f"{split}_embeddings"] = embedding
    sample_path = output_dir / "embedding-sample.json"
    _write_json(sample_path, {
        "model_version": config.training.model_version,
        "embedding_dimensions": int(validation_embeddings.shape[1]),
        "sample": [
            {"transaction_id": transaction_id,
             "first_eight_dimensions": validation_embeddings[index, :8].tolist()}
            for index, transaction_id in enumerate(store.target_ids["validation"][:3])
        ],
    })
    output_files["embedding_sample"] = sample_path
    split_metadata = {}
    for split, diagnostics, labels in (
        ("train", store.manifest["ego_statistics"]["train"], train_labels),
        ("validation", validation_diagnostics, validation_labels),
        ("test", test_diagnostics, test_labels),
    ):
        ids = store.target_ids[split]
        if split == "train":
            truncated = sum(
                bool(store.index.truncated_entity_count(
                    transaction_id, max_history_per_entity=store.manifest["fanout"][1]
                )) for transaction_id in ids
            )
            cold_start = sum(_has_cold_start_entity(store, transaction_id) for transaction_id in ids)
            diagnostics = {
                **diagnostics,
                "zero_history_fraction": diagnostics["zero_history_targets"] / len(ids),
                "truncated_targets": truncated,
                "truncated_fraction": truncated / len(ids),
                "cold_start_targets": cold_start,
                "cold_start_fraction": cold_start / len(ids),
            }
        split_metadata[split] = {
            "count": len(ids), "positives": int(labels.sum()),
            "ordered_id_sha256": _ids_sha256(ids),
            "first_id": ids[0], "last_id": ids[-1],
            "graph_diagnostics": diagnostics,
            "tabular_diagnostics": (
                validation_tabular_diagnostics if split == "validation"
                else test_tabular_diagnostics if split == "test" else None
            ),
        }
    metadata = {
        "dataset_source": config.dataset_source,
        "graph_manifest_sha256": store.manifest["content_sha256"],
        "graph_model_path": model_path.relative_to(artifact_root).as_posix(),
        "graph_model_sha256": _sha256(model_path),
        "graph_training_seconds": graph_metadata["training_seconds"],
        "graph_model_size_bytes": model_path.stat().st_size,
        "xgboost_model_sha256": _sha256(tabular_path),
        "xgboost_training_seconds": training_seconds,
        "xgboost_model_size_bytes": tabular_path.stat().st_size,
        "xgboost_feature_columns": FEATURE_COLUMNS,
        "xgboost_train_id_sha256": _ids_sha256(store.target_ids["train"]),
        "splits": split_metadata,
        "alpha_candidates": ALPHAS,
        "alpha_trials": trials,
        "selected_alpha": alpha,
        "selected_by": "validation_pr_auc",
        "test_evaluated_after_alpha_selection": True,
        "metrics": {"validation": validation_metrics, "test": test_metrics},
        "score_calibration": "uncalibrated scores; 0.5 threshold is illustrative",
        "outputs": {name: {"path": path.name, "sha256": _sha256(path), "bytes": path.stat().st_size}
                    for name, path in output_files.items()},
    }
    metadata_path = output_dir / "metadata.json"
    _write_json(metadata_path, metadata)
    report_path = output_dir / "report.md"
    report_path.write_text(_report(metadata), encoding="utf-8")
    return ComparisonResult(output_dir, report_path, metadata_path, alpha, validation_metrics, test_metrics)
