from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from graphrisk.graph.compare_models import _aligned_frame, _choose_alpha


def test_same_id_alignment_reorders_and_rejects_missing_or_duplicate() -> None:
    frame = pd.DataFrame({
        "transaction_id": ["t2", "t1", "t3"],
        "is_fraud": [0, 1, 0],
    })
    aligned = _aligned_frame(frame, ("t1", "t2"), "validation")
    assert aligned["transaction_id"].tolist() == ["t1", "t2"]
    assert aligned["is_fraud"].tolist() == [1, 0]
    with pytest.raises(ValueError, match="missing"):
        _aligned_frame(frame, ("t1", "absent"), "validation")
    with pytest.raises(ValueError, match="duplicated"):
        _aligned_frame(pd.concat((frame, frame.iloc[[0]])), ("t1", "t2"), "validation")
    with pytest.raises(ValueError, match="unique"):
        _aligned_frame(frame, ("t1", "t1"), "validation")


def test_fusion_alpha_uses_only_aligned_validation_arrays() -> None:
    labels = np.array([1, 0, 1, 0])
    tabular = np.array([0.9, 0.1, 0.8, 0.2])
    graph = np.array([0.1, 0.9, 0.2, 0.8])
    alpha, trials = _choose_alpha(labels, tabular, graph)
    assert alpha == 1.0
    assert len(trials) == 5
    with pytest.raises(ValueError, match="aligned"):
        _choose_alpha(labels, tabular[:-1], graph)
