import pytest

from graphrisk.serving.day_batch_bands import assign_daily_bands


def test_daily_ranking_capacity_ties_and_original_order():
    ids = [f"{i:03}" for i in range(20)] + ["day2-b", "day2-a"]
    seconds = [0] * 20 + [86400, 86400]
    scores = [0.5] * 22
    scores[0] = 0.9
    levels = assign_daily_bands(ids[::-1], seconds[::-1], scores[::-1], high_threshold=0.8)[::-1]
    assert levels[:4] == ["high", "medium", "medium", "medium"]
    assert levels[4:20] == ["low"] * 16
    assert levels[20:] == ["low", "medium"]


def test_threshold_conflict_is_capped_by_daily_capacity_and_breaks_ties_by_id():
    ids = [f"{i:03}" for i in range(20)][::-1]
    levels = assign_daily_bands(ids, [0] * 20, [0.9] * 20, high_threshold=0.8)
    assert levels.count("high") == 4
    assert [key for key, level in zip(ids, levels) if level == "high"] == ["003", "002", "001", "000"]
    assert levels.count("medium") == 0


@pytest.mark.parametrize(
    ("ids", "seconds", "scores"),
    [
        (["a"], [], [0.5]),
        (["a", "a"], [0, 0], [0.5, 0.5]),
        ([" a"], [0], [0.5]),
        (["a"], [0.5], [0.5]),
        (["a"], [0], [float("nan")]),
        (["a"], [0], [1.1]),
    ],
)
def test_invalid_batch_rejected(ids, seconds, scores):
    with pytest.raises(ValueError):
        assign_daily_bands(ids, seconds, scores, high_threshold=0.8)


def test_invalid_high_threshold_rejected():
    with pytest.raises(ValueError):
        assign_daily_bands(["a"], [0], [0.5], high_threshold=float("nan"))
