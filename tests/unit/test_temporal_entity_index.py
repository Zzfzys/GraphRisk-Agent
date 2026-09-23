from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from graphrisk.graph.schema import GraphTransaction, TemporalEgoBuilder
from graphrisk.graph.temporal_index import TemporalEntityIndex

FIXTURE = Path(__file__).parents[1] / "fixtures" / "stage04_temporal_graph.json"
BASE = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _rows() -> list[GraphTransaction]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = []
    for item in raw:
        minute = item["minute"]
        result.append(
            GraphTransaction.from_feature_row(
                {
                    "transaction_id": item["transaction_id"],
                    "transaction_time": BASE + timedelta(minutes=minute),
                    "transaction_amount": item["amount"],
                    "card_id": item["card_id"],
                    "device_id": item["device_id"],
                    "email_domain": item["email_domain"],
                    "address_id": item["address_id"],
                    "product_code": item["product_code"],
                    "tx_count_last_1h": 0,
                    "tx_count_last_24h": 0,
                    "tx_count_last_7d": 0,
                    "amount_mean_last_24h": None,
                    "amount_std_last_24h": None,
                    "amount_deviation_last_24h": None,
                    "transaction_hour": 0,
                    "transaction_day_of_week": 5,
                }
            )
        )
    return result


@pytest.mark.parametrize("limit", [1, 2, 5, 20])
def test_index_matches_reference_for_every_fixture_target(limit: int) -> None:
    rows = _rows()
    reference = TemporalEgoBuilder(rows)
    index = TemporalEntityIndex(reversed(rows))
    for row in rows:
        expected = reference.build(row.transaction_id, max_history_per_entity=limit)
        actual = index.build(row.transaction_id, max_history_per_entity=limit)
        assert actual.nodes == expected.nodes
        assert actual.edges == expected.edges
        assert actual.content_sha256() == expected.content_sha256()


def test_index_counts_truncation_and_rejects_invalid_inputs() -> None:
    rows = _rows()
    index = TemporalEntityIndex(rows)
    assert index.truncated_entity_count("T3", max_history_per_entity=2) >= 1
    assert index.candidate_count("T3") > 2
    with pytest.raises(ValueError, match="must be unique"):
        TemporalEntityIndex([*rows, rows[0]])
    with pytest.raises(ValueError, match="must be positive"):
        index.build("T3", max_history_per_entity=0)
    with pytest.raises(ValueError, match="absent"):
        index.build("missing", max_history_per_entity=2)


def test_index_tie_break_is_ascending_id_at_equal_history_time() -> None:
    base = _rows()[0]
    from dataclasses import replace

    target = replace(base, transaction_id="Z", transaction_time=BASE + timedelta(minutes=3))
    earlier_a = replace(base, transaction_id="A")
    earlier_b = replace(base, transaction_id="B")
    index = TemporalEntityIndex([earlier_b, target, earlier_a])
    assert index.build("Z", max_history_per_entity=1).selected_history_ids == ("A",)
