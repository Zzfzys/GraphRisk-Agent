from __future__ import annotations

import math

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def evaluate_binary_classifier(
    labels,
    scores,
    *,
    decision_threshold: float,
) -> dict[str, float]:
    labels_array = np.asarray(labels, dtype=int)
    scores_array = np.asarray(scores, dtype=float)
    if labels_array.ndim != 1 or scores_array.ndim != 1:
        raise ValueError("labels and scores must be one-dimensional")
    if len(labels_array) != len(scores_array) or len(labels_array) == 0:
        raise ValueError("labels and scores must have equal non-zero length")
    if not np.isfinite(scores_array).all():
        raise ValueError("scores must be finite")
    if ((scores_array < 0) | (scores_array > 1)).any():
        raise ValueError("scores must be probabilities in [0, 1]")

    predictions = (scores_array >= decision_threshold).astype(int)
    metrics = {
        "pr_auc": _safe_ranking_metric(average_precision_score, labels_array, scores_array),
        "roc_auc": _safe_ranking_metric(roc_auc_score, labels_array, scores_array),
        "precision": float(precision_score(labels_array, predictions, zero_division=0)),
        "recall": float(recall_score(labels_array, predictions, zero_division=0)),
        "f1": float(f1_score(labels_array, predictions, zero_division=0)),
    }
    for fraction, label in ((0.01, "1pct"), (0.05, "5pct")):
        precision_at_k, recall_at_k = top_fraction_metrics(
            labels_array,
            scores_array,
            fraction=fraction,
        )
        metrics[f"precision_at_{label}"] = precision_at_k
        metrics[f"recall_at_{label}"] = recall_at_k
    return metrics


def top_fraction_metrics(labels, scores, *, fraction: float) -> tuple[float, float]:
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    labels_array = np.asarray(labels, dtype=int)
    scores_array = np.asarray(scores, dtype=float)
    selected_count = max(1, math.ceil(len(labels_array) * fraction))
    order = np.argsort(-scores_array, kind="stable")[:selected_count]
    selected_positives = int(labels_array[order].sum())
    total_positives = int(labels_array.sum())
    precision = selected_positives / selected_count
    recall = selected_positives / total_positives if total_positives else 0.0
    return float(precision), float(recall)


def _safe_ranking_metric(metric, labels: np.ndarray, scores: np.ndarray) -> float:
    if len(np.unique(labels)) < 2:
        return float("nan")
    return float(metric(labels, scores))
