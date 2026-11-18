"""Trial investigation priorities for a completed daily score batch."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from numbers import Integral, Real


def assign_daily_bands(
    transaction_ids: Sequence[str], event_seconds: Sequence[int], scores: Sequence[float],
    *, high_threshold: float,
) -> list[str]:
    """Rank each relative day; output levels in the original input order.

    High plus medium uses the established 14,571/88,531 daily investigation
    workload. Above the model-specific high threshold, reviewed rows are high;
    if more rows exceed it than capacity, the rest remain low. Equal scores at
    the capacity boundary are resolved by transaction ID. These trial levels
    are not probabilities or grounds for automated rejection.
    """
    if isinstance(high_threshold, bool) or not isinstance(high_threshold, Real) or not math.isfinite(high_threshold) or not 0 <= high_threshold <= 1:
        raise ValueError("high threshold must be a finite number in [0, 1]")
    if not (len(transaction_ids) == len(event_seconds) == len(scores)):
        raise ValueError("transaction IDs, timestamps and scores must have equal lengths")
    if any(not isinstance(key, str) or not key or key != key.strip() for key in transaction_ids):
        raise ValueError("transaction IDs must be nonempty trimmed strings")
    if len(set(transaction_ids)) != len(transaction_ids):
        raise ValueError("transaction IDs must be unique")
    days: dict[int, list[int]] = defaultdict(list)
    for index, (second, score) in enumerate(zip(event_seconds, scores)):
        if isinstance(second, bool) or not isinstance(second, Integral) or second < 0:
            raise ValueError("timestamps must be nonnegative whole seconds")
        if isinstance(score, bool) or not isinstance(score, Real) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("scores must be finite numbers in [0, 1]")
        days[second // 86400].append(index)
    levels = ["low"] * len(transaction_ids)
    for indices in days.values():
        order = sorted(indices, key=lambda i: (-scores[i], transaction_ids[i]))
        review = math.ceil(len(order) * 14571 / 88531)
        for index in order[:review]:
            levels[index] = "high" if scores[index] >= high_threshold else "medium"
    return levels
