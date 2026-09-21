import math

import pytest

from graphrisk.training.metrics import evaluate_binary_classifier, top_fraction_metrics


def test_binary_metrics_for_perfect_ranking() -> None:
    metrics = evaluate_binary_classifier(
        [0, 1, 0, 1],
        [0.1, 0.9, 0.2, 0.8],
        decision_threshold=0.5,
    )

    assert metrics["pr_auc"] == pytest.approx(1.0)
    assert metrics["roc_auc"] == pytest.approx(1.0)
    assert metrics["precision"] == pytest.approx(1.0)
    assert metrics["recall"] == pytest.approx(1.0)
    assert metrics["f1"] == pytest.approx(1.0)


def test_top_fraction_metrics_use_highest_scores() -> None:
    precision, recall = top_fraction_metrics(
        [0, 1, 0, 1, 0],
        [0.1, 0.8, 0.3, 0.9, 0.2],
        fraction=0.4,
    )

    assert precision == pytest.approx(1.0)
    assert recall == pytest.approx(1.0)


def test_single_class_roc_auc_is_nan() -> None:
    metrics = evaluate_binary_classifier(
        [0, 0, 0],
        [0.1, 0.2, 0.3],
        decision_threshold=0.5,
    )

    assert math.isnan(metrics["roc_auc"])
