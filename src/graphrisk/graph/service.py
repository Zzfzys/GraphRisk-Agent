"""Load a verified graph model and serve held-out temporal graph evidence."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from math import ceil
from pathlib import Path

import pandas as pd
import torch

from graphrisk.config import load_config
from graphrisk.graph.build_artifacts import LoadedGraphArtifacts, load_graph_artifacts
from graphrisk.graph.config import GraphExperimentConfig
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.train_graphsage import _sha256, load_graphsage_checkpoint
from graphrisk.investigation.contracts import ErrorCode


class GraphTargetNotCoveredError(Exception):
    """Target has no eligible held-out score or is outside the indexed graph."""


@dataclass(frozen=True)
class GraphRiskPrediction:
    transaction_id: str
    graph_risk_score: float
    graph_model_version: str
    score_scope: str = "held_out"


@dataclass(frozen=True)
class ScoredGraphNeighbor:
    transaction_id: str
    graph_risk_score: float
    seconds_before_target: int
    shared_entities: tuple[tuple[str, str], ...]


class _SourceMismatch(Exception):
    pass


class _VersionMismatch(Exception):
    pass


def _ids_sha256(ids: tuple[str, ...]) -> str:
    return hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()


class GraphRiskService:
    """Cache the verified graph, model, and held-out scores once per process."""

    def __init__(
        self,
        config: GraphExperimentConfig,
        *,
        comparison_dir: str | Path,
    ) -> None:
        self.dataset_source = config.dataset_source
        self.model_version = config.training.model_version
        self.transaction_parquet: Path | None = None
        self.feature_parquet: Path | None = None
        self.startup_error: ErrorCode | None = None
        self.store: LoadedGraphArtifacts | None = None
        self.model: GraphSAGERiskModel | None = None
        self._scores: dict[str, float] = {}
        try:
            data_config = load_config(config.resolve_path(config.paths.data_config))
            if data_config.data.source != self.dataset_source:
                raise _SourceMismatch
            self.transaction_parquet = data_config.resolve_path(data_config.paths.interim_transactions)
            self.feature_parquet = data_config.resolve_path(data_config.paths.interim_features)
            self._load(config, Path(comparison_dir).resolve())
        except _SourceMismatch:
            self.startup_error = "data_source_mismatch"
        except _VersionMismatch:
            self.startup_error = "graph_model_version_mismatch"
        except Exception:
            # Never expose filesystem paths, tensor contents, or raw entities in tool errors.
            self.startup_error = "graph_artifact_unavailable"

    def _load(self, config: GraphExperimentConfig, comparison_dir: Path) -> None:
        artifact_root = config.resolve_path(config.paths.artifact_dir)
        if not comparison_dir.is_relative_to(artifact_root):
            raise ValueError("comparison directory is outside graph artifacts")
        store = load_graph_artifacts(
            config.resolve_path(config.paths.graph_manifest),
            expected_dataset_source=config.dataset_source,
        )
        if store.manifest["model_version"] != self.model_version:
            raise _VersionMismatch
        comparison = json.loads((comparison_dir / "metadata.json").read_text(encoding="utf-8"))
        if comparison["dataset_source"] != self.dataset_source:
            raise _SourceMismatch
        if comparison["graph_manifest_sha256"] != store.manifest["content_sha256"]:
            raise ValueError("comparison graph manifest hash differs")
        for split in ("train", "validation", "test"):
            expected_ids = store.target_ids[split]
            if comparison["splits"][split]["ordered_id_sha256"] != _ids_sha256(expected_ids):
                raise ValueError("comparison target IDs differ")
        model_path = Path(comparison["graph_model_path"]).resolve()
        if not model_path.is_relative_to(artifact_root):
            raise ValueError("graph checkpoint is outside artifact root")
        if comparison["graph_model_sha256"] != _sha256(model_path):
            raise ValueError("comparison graph checkpoint hash differs")
        metadata_path = model_path.with_name("metadata.json")
        model_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if model_metadata.get("model_version") != self.model_version:
            raise _VersionMismatch
        if model_metadata.get("dataset_source") != self.dataset_source:
            raise _SourceMismatch
        model = load_graphsage_checkpoint(
            model_path, metadata_path, store,
            expected_model_version=self.model_version,
        )
        scores: dict[str, float] = {}
        for split in ("validation", "test"):
            output = comparison["outputs"][f"{split}_predictions"]
            score_path = (comparison_dir / output["path"]).resolve()
            if not score_path.is_relative_to(comparison_dir) or _sha256(score_path) != output["sha256"]:
                raise ValueError("graph prediction artifact hash differs")
            # Column projection deliberately excludes is_fraud and all tabular scores.
            frame = pd.read_parquet(
                score_path,
                columns=("transaction_id", "split", "graphsage_score", "graphsage_model_version"),
            )
            ids = tuple(frame["transaction_id"])
            if ids != store.target_ids[split] or frame["transaction_id"].duplicated().any():
                raise ValueError("graph prediction IDs do not match manifest order")
            if set(frame["split"]) != {split}:
                raise ValueError("graph prediction split differs")
            if set(frame["graphsage_model_version"]) != {self.model_version}:
                raise _VersionMismatch
            values = frame["graphsage_score"].to_numpy(dtype=float)
            if not all(math.isfinite(value) and 0 <= value <= 1 for value in values):
                raise ValueError("graph scores must be finite and within [0,1]")
            scores.update(zip(ids, (float(value) for value in values), strict=True))
        if len(scores) != len(store.target_ids["validation"]) + len(store.target_ids["test"]):
            raise ValueError("held-out graph score IDs overlap")
        self.store = store
        self.model = model
        self._scores = scores

    def _covered_target(self, transaction_id: str) -> tuple[LoadedGraphArtifacts, datetime]:
        if self.startup_error is not None or self.store is None:
            raise RuntimeError("graph service is unavailable")
        if transaction_id not in self._scores or transaction_id not in self.store.index.by_id:
            raise GraphTargetNotCoveredError(transaction_id)
        return self.store, self.store.index.by_id[transaction_id].transaction_time

    def predict(self, transaction_id: str) -> GraphRiskPrediction:
        """Recompute a covered target's score with the frozen checkpoint."""
        store, _ = self._covered_target(transaction_id)
        assert self.model is not None
        graph = store.ego_graph(transaction_id).to_pyg(store.encoder)
        with torch.no_grad():
            score = float(torch.sigmoid(self.model(graph)).item())
        if not math.isfinite(score) or abs(score - self._scores[transaction_id]) > 1e-5:
            raise ValueError("graph model score differs from verified prediction artifact")
        return GraphRiskPrediction(transaction_id, score, self.model_version)

    def find_neighbors(self, transaction_id: str, *, limit: int) -> list[ScoredGraphNeighbor]:
        """Rank fanout-selected, strictly earlier, independently scored neighbors."""
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        store, target_time = self._covered_target(transaction_id)
        target = store.index.by_id[transaction_id]
        root_entities = target.valid_entities()
        ranked: list[ScoredGraphNeighbor] = []
        for candidate_id in dict.fromkeys(store.ego_graph(transaction_id).selected_history_ids):
            score = self._scores.get(candidate_id)
            if score is None:
                continue  # Training in-sample and unscored transactions cannot be evidence.
            candidate = store.index.by_id[candidate_id]
            if candidate.transaction_time >= target_time:
                continue
            candidate_entities = set(candidate.valid_entities())
            shared = tuple(entity for entity in root_entities if entity in candidate_entities)
            if not shared:
                continue
            seconds = ceil((target_time - candidate.transaction_time).total_seconds())
            ranked.append(ScoredGraphNeighbor(candidate_id, score, seconds, shared))
        ranked.sort(key=lambda item: (-item.graph_risk_score, item.seconds_before_target, item.transaction_id))
        return ranked[:limit]
