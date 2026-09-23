"""Train and version a root-node GraphSAGE on Stage 04 temporal ego graphs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

import duckdb
import numpy as np
import torch
from torch import nn
from torch_geometric.data import Batch

from graphrisk.graph.build_artifacts import LoadedGraphArtifacts, load_graph_artifacts
from graphrisk.graph.config import GraphExperimentConfig, load_graph_config
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.training.config import load_training_config
from graphrisk.training.metrics import evaluate_binary_classifier


@dataclass(frozen=True)
class GraphTrainingResult:
    model_path: Path
    metadata_path: Path
    history_path: Path
    best_epoch: int
    validation_pr_auc: float
    test_pr_auc: float
    training_seconds: float
    device: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _seed_everything(seed: int) -> None:
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.use_deterministic_algorithms(True, warn_only=True)


def _select_device(requested: str) -> tuple[torch.device, str | None]:
    if requested == "cpu":
        return torch.device("cpu"), None
    if torch.cuda.is_available():
        return torch.device("cuda"), None
    if requested == "cuda":
        raise RuntimeError("CUDA was requested but is unavailable; choose cpu or auto")
    return torch.device("cpu"), "CUDA unavailable; auto selected CPU"


def _load_labels(
    path: Path, ids: tuple[str, ...], split: str
) -> np.ndarray:
    if not path.is_file():
        raise FileNotFoundError(f"{split} label source is absent: {path}")
    with duckdb.connect(":memory:") as connection:
        rows = connection.execute(
            "SELECT transaction_id, is_fraud FROM read_parquet(?) "
            "ORDER BY transaction_time, transaction_id LIMIT ?",
            [str(path), len(ids)],
        ).fetchall()
    if tuple(row[0] for row in rows) != ids:
        raise ValueError(f"{split} label IDs or order do not match graph manifest")
    if any(row[1] not in (0, 1) for row in rows):
        raise ValueError(f"{split} labels must be binary 0/1")
    return np.asarray([row[1] for row in rows], dtype=np.float32)


def _verify_label_provenance(
    config: GraphExperimentConfig, split_paths: dict[str, Path]
) -> None:
    """Check split labels against canonical and all-feature labels without adding them to graphs."""
    from graphrisk.config import load_config

    data = load_config(config.resolve_path(config.paths.data_config))
    canonical = data.resolve_path(data.paths.interim_transactions)
    features = data.resolve_path(data.paths.interim_features)
    with duckdb.connect(":memory:") as connection:
        for split, path in split_paths.items():
            count = connection.execute(
                "SELECT COUNT(*) FROM read_parquet(?) AS s "
                "LEFT JOIN read_parquet(?) AS c USING (transaction_id) "
                "LEFT JOIN read_parquet(?) AS f USING (transaction_id) "
                "WHERE s.is_fraud IS DISTINCT FROM c.is_fraud "
                "OR s.is_fraud IS DISTINCT FROM f.is_fraud",
                [str(path), str(canonical), str(features)],
            ).fetchone()[0]
            if count:
                raise ValueError(f"{split} labels disagree with canonical/features source")


def _batches(
    store: LoadedGraphArtifacts,
    split: str,
    labels: np.ndarray,
    *,
    batch_size: int,
    shuffle_seed: int | None,
) -> Iterator[tuple[Batch, torch.Tensor]]:
    ids = store.target_ids[split]
    if len(ids) != len(labels):
        raise ValueError(f"{split} graph target and label counts differ")
    positions = list(range(len(ids)))
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(positions)
    for start in range(0, len(ids), batch_size):
        chunk = positions[start : start + batch_size]
        graphs = [store.ego_graph(ids[position]).to_pyg(store.encoder) for position in chunk]
        batch = Batch.from_data_list(graphs)
        if batch.root_index.numel() != len(chunk):
            raise RuntimeError("PyG batch lost root-node alignment")
        yield batch, torch.tensor(labels[chunk], dtype=torch.float32)


@torch.no_grad()
def _evaluate(
    model: GraphSAGERiskModel,
    store: LoadedGraphArtifacts,
    split: str,
    labels: np.ndarray,
    *,
    batch_size: int,
    device: torch.device,
) -> tuple[dict[str, float], np.ndarray]:
    model.eval()
    score_chunks = []
    for batch, _ in _batches(store, split, labels, batch_size=batch_size, shuffle_seed=None):
        logits = model(batch.to(device))
        score_chunks.append(torch.sigmoid(logits).cpu().numpy())
    scores = np.concatenate(score_chunks)
    metrics = evaluate_binary_classifier(labels, scores, decision_threshold=0.5)
    return metrics, scores


def train_graphsage(config_path: str | Path) -> GraphTrainingResult:
    config = load_graph_config(config_path)
    manifest_path = config.resolve_path(config.paths.graph_manifest)
    store = load_graph_artifacts(
        manifest_path, expected_dataset_source=config.dataset_source
    )
    if store.manifest["project_name"] != config.project_name:
        raise ValueError("graph manifest project_name does not match training config")
    if store.manifest["model_version"] != config.training.model_version:
        raise ValueError("graph manifest model_version does not match training config")
    if tuple(store.manifest["fanout"]) != config.sampling.fanout:
        raise ValueError("graph manifest fanout does not match training config")
    if store.manifest["random_seed"] != config.training.random_seed:
        raise ValueError("graph manifest seed does not match training config")
    if tuple(store.manifest["feature_names"]) != store.encoder.feature_names:
        raise ValueError("graph manifest feature dimension does not match encoder")
    for split in ("train", "validation", "test"):
        if len(store.target_ids[split]) != getattr(config.sampling, f"max_{split}_transactions"):
            raise ValueError(f"graph {split} target count does not match training config")
    baseline = load_training_config(config.resolve_path(config.paths.training_config))
    split_paths = {
        split: baseline.resolve_path(getattr(baseline.paths, f"{split}_features"))
        for split in ("train", "validation", "test")
    }
    _verify_label_provenance(
        config, {name: split_paths[name] for name in ("train", "validation")}
    )
    train_labels = _load_labels(split_paths["train"], store.target_ids["train"], "train")
    validation_labels = _load_labels(
        split_paths["validation"], store.target_ids["validation"], "validation"
    )
    positives = int(train_labels.sum())
    negatives = len(train_labels) - positives
    if min(positives, negatives) == 0:
        raise ValueError("graph training split must contain both fraud and non-fraud labels")
    if len(np.unique(validation_labels)) < 2:
        raise ValueError("graph validation split needs both classes for PR-AUC selection")
    positive_weight = negatives / positives
    _seed_everything(config.training.random_seed)
    device, fallback_reason = _select_device(config.training.device)
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    model = GraphSAGERiskModel(
        input_channels=len(store.encoder.feature_names),
        hidden_channels=config.training.hidden_channels,
        dropout=config.training.dropout,
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=config.training.learning_rate)
    loss_fn = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor(positive_weight, dtype=torch.float32, device=device)
    )
    best_pr_auc = -math.inf
    best_epoch = 0
    best_state: dict[str, torch.Tensor] | None = None
    stale_epochs = 0
    history = []
    started = time.perf_counter()
    for epoch in range(1, config.training.max_epochs + 1):
        model.train()
        loss_total = 0.0
        for batch, labels in _batches(
            store,
            "train",
            train_labels,
            batch_size=config.training.batch_size,
            shuffle_seed=config.training.random_seed + epoch,
        ):
            optimizer.zero_grad(set_to_none=True)
            logits = model(batch.to(device))
            loss = loss_fn(logits, labels.to(device))
            loss.backward()
            optimizer.step()
            loss_total += float(loss.detach().cpu()) * len(labels)
        validation_metrics, _ = _evaluate(
            model,
            store,
            "validation",
            validation_labels,
            batch_size=config.training.batch_size,
            device=device,
        )
        val_pr_auc = validation_metrics["pr_auc"]
        val_roc_auc = validation_metrics["roc_auc"]
        row = {
            "epoch": epoch,
            "train_loss": loss_total / len(train_labels),
            "validation_pr_auc": val_pr_auc,
            "validation_roc_auc": val_roc_auc,
        }
        history.append(row)
        print(
            f"epoch={epoch} train_loss={row['train_loss']:.5f} "
            f"validation_pr_auc={val_pr_auc:.5f} validation_roc_auc={val_roc_auc:.5f}",
            flush=True,
        )
        if val_pr_auc > best_pr_auc:
            best_pr_auc = val_pr_auc
            best_epoch = epoch
            best_state = {
                name: value.detach().cpu().clone()
                for name, value in model.state_dict().items()
            }
            stale_epochs = 0
        else:
            stale_epochs += 1
        if stale_epochs >= config.training.early_stopping_patience:
            break
    training_seconds = time.perf_counter() - started
    if best_state is None:
        raise RuntimeError("GraphSAGE training produced no valid validation checkpoint")
    model.load_state_dict(best_state)
    best_validation_metrics, _ = _evaluate(
        model,
        store,
        "validation",
        validation_labels,
        batch_size=config.training.batch_size,
        device=device,
    )
    # Held-out test labels are read only after the best validation epoch is frozen.
    _verify_label_provenance(config, {"test": split_paths["test"]})
    test_labels = _load_labels(split_paths["test"], store.target_ids["test"], "test")
    test_metrics, _ = _evaluate(
        model,
        store,
        "test",
        test_labels,
        batch_size=config.training.batch_size,
        device=device,
    )
    output_dir = config.resolve_path(config.paths.artifact_dir) / "models" / config.training.model_version
    artifact_root = config.resolve_path(config.paths.artifact_dir)
    if not output_dir.resolve().is_relative_to(artifact_root):
        raise ValueError("graph model_version must resolve inside graph artifact_dir")
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.pt"
    temporary = output_dir / "model.pt.tmp"
    torch.save(best_state, temporary)
    temporary.replace(model_path)
    history_path = output_dir / "training-history.json"
    _write_json(history_path, history)
    metadata_path = output_dir / "metadata.json"
    metadata = {
        "model_name": "graphsage",
        "model_version": config.training.model_version,
        "dataset_source": config.dataset_source,
        "created_at": datetime.now(UTC).isoformat(),
        "graph_manifest_path": str(manifest_path),
        "graph_manifest_sha256": store.manifest["content_sha256"],
        "model_sha256": _sha256(model_path),
        "history_sha256": _sha256(history_path),
        "feature_names": store.encoder.feature_names,
        "input_channels": len(store.encoder.feature_names),
        "hidden_channels": config.training.hidden_channels,
        "dropout": config.training.dropout,
        "learning_rate": config.training.learning_rate,
        "batch_size": config.training.batch_size,
        "max_epochs": config.training.max_epochs,
        "early_stopping_patience": config.training.early_stopping_patience,
        "random_seed": config.training.random_seed,
        "selected_by": "validation_pr_auc",
        "best_epoch": best_epoch,
        "stopped_after_epoch": history[-1]["epoch"],
        "target_counts": {split: len(ids) for split, ids in store.target_ids.items()},
        "label_counts": {
            "train_positive": positives,
            "train_negative": negatives,
            "validation_positive": int(validation_labels.sum()),
            "validation_negative": int(len(validation_labels) - validation_labels.sum()),
            "test_positive": int(test_labels.sum()),
            "test_negative": int(len(test_labels) - test_labels.sum()),
        },
        "positive_weight": positive_weight,
        "validation_metrics": best_validation_metrics,
        "test_metrics": test_metrics,
        "training_seconds": training_seconds,
        "device_requested": config.training.device,
        "device_actual": str(device),
        "cuda_name": torch.cuda.get_device_name(device) if device.type == "cuda" else None,
        "cuda_peak_allocated_mib": (
            torch.cuda.max_memory_allocated(device) / 1024 / 1024
            if device.type == "cuda" else None
        ),
        "fallback_reason": fallback_reason,
        "score_calibration": "uncalibrated sigmoid score",
    }
    _write_json(metadata_path, metadata)
    return GraphTrainingResult(
        model_path=model_path,
        metadata_path=metadata_path,
        history_path=history_path,
        best_epoch=best_epoch,
        validation_pr_auc=best_validation_metrics["pr_auc"],
        test_pr_auc=test_metrics["pr_auc"],
        training_seconds=training_seconds,
        device=str(device),
    )


def load_graphsage_checkpoint(
    model_path: str | Path,
    metadata_path: str | Path,
    store: LoadedGraphArtifacts,
    *,
    device: str = "cpu",
    expected_model_version: str | None = None,
) -> GraphSAGERiskModel:
    """Load only matching, hashed tensor weights with an explicit map_location."""
    model_file = Path(model_path)
    metadata_file = Path(metadata_path)
    if not model_file.is_file() or not metadata_file.is_file():
        raise FileNotFoundError("GraphSAGE checkpoint or metadata is missing")
    try:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("GraphSAGE metadata is corrupt") from error
    if metadata.get("dataset_source") != store.manifest["dataset_source"]:
        raise ValueError("GraphSAGE checkpoint dataset_source does not match graph")
    if (
        expected_model_version is not None
        and metadata.get("model_version") != expected_model_version
    ):
        raise ValueError("GraphSAGE checkpoint model_version does not match request")
    if metadata.get("graph_manifest_sha256") != store.manifest["content_sha256"]:
        raise ValueError("GraphSAGE checkpoint manifest hash does not match graph")
    if tuple(metadata.get("feature_names", ())) != store.encoder.feature_names:
        raise ValueError("GraphSAGE checkpoint feature schema does not match graph")
    if metadata.get("model_sha256") != _sha256(model_file):
        raise ValueError("GraphSAGE checkpoint hash does not match metadata")
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA checkpoint loading requested but CUDA is unavailable")
    try:
        state = torch.load(model_file, map_location=torch.device(device), weights_only=True)
        model = GraphSAGERiskModel(
            input_channels=metadata["input_channels"],
            hidden_channels=metadata["hidden_channels"],
            dropout=metadata["dropout"],
        ).to(device)
        model.load_state_dict(state, strict=True)
    except (OSError, RuntimeError, ValueError, pickle.UnpicklingError, KeyError) as error:
        raise ValueError("GraphSAGE checkpoint tensors are corrupt or incompatible") from error
    model.eval()
    return model
