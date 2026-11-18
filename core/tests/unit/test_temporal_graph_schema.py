from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import torch

from graphrisk.graph.schema import (
    GraphFeatureEncoder,
    GraphNode,
    GraphTransaction,
    TemporalEgoBuilder,
)
from graphrisk.models.tabular import NUMERIC_FEATURES

FIXTURE = Path(__file__).parents[1] / "fixtures" / "stage04_temporal_graph.json"
BASE = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _raw_rows() -> list[dict[str, object]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _feature_row(item: dict[str, object]) -> dict[str, object]:
    minute = int(item["minute"])
    return {
        "transaction_id": item["transaction_id"],
        "transaction_time": BASE + timedelta(minutes=minute),
        "transaction_amount": item["amount"],
        "card_id": item["card_id"],
        "device_id": item["device_id"],
        "email_domain": item["email_domain"],
        "address_id": item["address_id"],
        "product_code": item["product_code"],
        "tx_count_last_1h": minute,
        "tx_count_last_24h": minute,
        "tx_count_last_7d": minute,
        "amount_mean_last_24h": None if item["transaction_id"] == "T0" else 10.0,
        "amount_std_last_24h": None if item["transaction_id"] == "T0" else 1.0,
        "amount_deviation_last_24h": None if item["transaction_id"] == "T0" else 2.0,
        "transaction_hour": 0,
        "transaction_day_of_week": 5,
        "is_fraud": item["is_fraud"],
        "identity_key": f"heuristic-{item['transaction_id']}",
        "split": item["split"],
    }


def _transactions(raw: list[dict[str, object]] | None = None) -> list[GraphTransaction]:
    return [
        GraphTransaction.from_feature_row(_feature_row(item))
        for item in (_raw_rows() if raw is None else raw)
    ]


def _keys(graph) -> list[tuple[str, str]]:
    return [node.key for node in graph.nodes]


def test_hand_computed_ego_graph_has_only_strict_history_and_expected_edges() -> None:
    graph = TemporalEgoBuilder(_transactions()).build(
        "T3", max_history_per_entity=2
    )
    assert graph.selected_history_ids == ("T2", "T8", "T1")
    assert _keys(graph) == [
        ("transaction", "T3"),
        ("transaction", "T2"),
        ("transaction", "T8"),
        ("transaction", "T1"),
        ("card", "C1"),
        ("card", "C2"),
        ("device", "D1"),
        ("device", "D2"),
        ("email_domain", "e1.example"),
        ("address", "A1"),
        ("address", "A2"),
        ("address", "A3"),
        ("address", "A8"),
    ]
    assert graph.edges == (
        (0, 4), (0, 7), (0, 8), (0, 11),
        (1, 5), (1, 7), (1, 8), (1, 10),
        (2, 4), (2, 7), (2, 8), (2, 12),
        (3, 4), (3, 6), (3, 8), (3, 9),
    )
    assert all(
        node.transaction.transaction_time < graph.nodes[0].transaction.transaction_time
        for node in graph.nodes[1:4]
    )
    assert ("transaction", "T4") not in _keys(graph)
    assert ("transaction", "T5") not in _keys(graph)
    assert ("transaction", "T9") not in _keys(graph)  # capped by recency


def test_future_rows_and_labels_do_not_change_earlier_ego_graph() -> None:
    original = _raw_rows()
    without_future = [row for row in original if row["transaction_id"] != "T5"]
    baseline = TemporalEgoBuilder(_transactions(without_future)).build(
        "T3", max_history_per_entity=2
    )
    with_future = TemporalEgoBuilder(_transactions(original)).build(
        "T3", max_history_per_entity=2
    )
    assert baseline.content_sha256() == with_future.content_sha256()
    assert ("transaction", "T5") not in _keys(with_future)
    encoder = GraphFeatureEncoder.fit(
        _transactions(without_future), train_transaction_ids=["T0", "T1", "T2"]
    )
    assert torch.equal(baseline.to_pyg(encoder).x, with_future.to_pyg(encoder).x)
    assert torch.equal(
        baseline.to_pyg(encoder).edge_index, with_future.to_pyg(encoder).edge_index
    )

    changed_labels = deepcopy(original)
    for row in changed_labels:
        row["is_fraud"] = 1 - row["is_fraud"]
    changed_rows = [_feature_row(row) for row in changed_labels]
    for row in changed_rows:
        row["identity_key"] = "changed-but-ignored"
    changed_graph = TemporalEgoBuilder(
        GraphTransaction.from_feature_row(row) for row in changed_rows
    ).build("T3", max_history_per_entity=2)
    assert changed_graph.content_sha256() == with_future.content_sha256()
    assert torch.equal(changed_graph.to_pyg(encoder).x, with_future.to_pyg(encoder).x)


def test_same_timestamp_and_unknown_placeholders_do_not_form_history() -> None:
    builder = TemporalEgoBuilder(_transactions())
    graph = builder.build("T4", max_history_per_entity=20)
    assert "T3" not in graph.selected_history_ids
    assert "T4" not in graph.selected_history_ids
    unknown = builder.build("T7", max_history_per_entity=20)
    assert unknown.selected_history_ids == ()
    assert _keys(unknown) == [("transaction", "T7")]
    assert unknown.edges == ()


def test_shuffling_input_does_not_change_mapping_edges_or_hash() -> None:
    original = _transactions()
    reordered = list(reversed(original))
    first = TemporalEgoBuilder(original).build("T3", max_history_per_entity=2)
    second = TemporalEgoBuilder(reordered).build("T3", max_history_per_entity=2)
    assert first.nodes == second.nodes
    assert first.edges == second.edges
    assert first.content_sha256() == second.content_sha256()


def test_training_target_cannot_see_later_validation_or_test_links() -> None:
    graph = TemporalEgoBuilder(_transactions()).build(
        "T1", max_history_per_entity=20
    )
    assert graph.selected_history_ids == ("T9", "T0")
    assert all(
        node.node_id not in {"T2", "T3", "T4", "T5", "T8"}
        for node in graph.nodes
        if node.node_type == "transaction"
    )


def test_feature_encoder_fits_training_ids_only_and_excludes_entity_ids() -> None:
    rows = _transactions()
    encoder = GraphFeatureEncoder.fit(
        rows, train_transaction_ids=["T0", "T1", "T2"]
    )
    changed_validation = _raw_rows()
    next(row for row in changed_validation if row["transaction_id"] == "T3")[
        "amount"
    ] = 1_000_000
    assert (
        GraphFeatureEncoder.fit(
            _transactions(changed_validation),
            train_transaction_ids=["T0", "T1", "T2"],
        )
        == encoder
    )
    assert encoder.product_codes == ("H", "W")
    mean_position = NUMERIC_FEATURES.index("amount_mean_last_24h")
    assert encoder.medians[mean_position] == 10.0
    assert not any(
        token in name
        for name in encoder.feature_names
        for token in ("C1", "D2", "e1.example", "A3", "is_fraud", "identity_key")
    )
    target = next(row for row in rows if row.transaction_id == "T3")
    target_vector = encoder.transform_node(GraphNode("transaction", "T3", target))
    product_start = 2 * len(NUMERIC_FEATURES)
    assert target_vector[product_start : product_start + 2] == [0.0, 0.0]
    assert encoder.transform_node(GraphNode("card", "C1")) == (
        encoder.transform_node(GraphNode("card", "different-card-id"))
    )


def test_fixture_exports_valid_pyg_data_with_bidirectional_edges() -> None:
    rows = _transactions()
    graph = TemporalEgoBuilder(rows).build("T3", max_history_per_entity=2)
    encoder = GraphFeatureEncoder.fit(
        rows, train_transaction_ids=["T0", "T1", "T2"]
    )
    data = graph.to_pyg(encoder)
    assert data.num_nodes == 13
    assert data.x.shape == (13, len(encoder.feature_names))
    assert data.edge_index.shape == (2, 32)
    assert data.root_index.tolist() == [0]
    directed = {tuple(pair) for pair in data.edge_index.t().tolist()}
    assert (0, 4) in directed and (4, 0) in directed
    assert torch.isfinite(data.x).all()
    assert data.y is None


def test_duplicate_ids_invalid_time_and_invalid_numeric_fail_explicitly() -> None:
    rows = _transactions()
    with pytest.raises(ValueError, match="must be unique"):
        TemporalEgoBuilder([*rows, rows[0]])
    with pytest.raises(ValueError, match="max_history_per_entity must be positive"):
        TemporalEgoBuilder(rows).build("T3", max_history_per_entity=0)
    row = _feature_row(_raw_rows()[0])
    row["transaction_time"] = datetime(2000, 1, 1)
    with pytest.raises(ValueError, match="timezone-aware"):
        GraphTransaction.from_feature_row(row)
    row["transaction_time"] = BASE
    row["transaction_amount"] = "10"
    with pytest.raises(ValueError, match="must be numeric"):
        GraphTransaction.from_feature_row(row)


def test_train_encoder_requires_nonempty_known_unique_training_ids() -> None:
    rows = _transactions()
    for ids in ([], ["T0", "T0"], ["nonexistent"]):
        with pytest.raises(ValueError, match="training transaction IDs"):
            GraphFeatureEncoder.fit(rows, train_transaction_ids=ids)
    with pytest.raises(ValueError, match="graph transaction IDs must be unique"):
        GraphFeatureEncoder.fit([*rows, rows[0]], train_transaction_ids=["T0"])


def test_equal_time_history_candidates_use_transaction_id_tie_break() -> None:
    base = _feature_row(_raw_rows()[0])
    target = dict(base, transaction_id="Z", transaction_time=BASE + timedelta(minutes=3))
    candidate_b = dict(base, transaction_id="B", transaction_time=BASE)
    candidate_a = dict(base, transaction_id="A", transaction_time=BASE)
    graph = TemporalEgoBuilder(
        GraphTransaction.from_feature_row(row)
        for row in (candidate_b, target, candidate_a)
    ).build("Z", max_history_per_entity=1)
    assert graph.selected_history_ids == ("A",)
