from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from graphrisk.graph.schema import GraphTransaction
from graphrisk.graph.service import GraphRiskService, GraphTargetNotCoveredError
from graphrisk.graph.temporal_index import TemporalEntityIndex


def _transaction(
    transaction_id: str,
    minute: int,
    *,
    card: str = "card-A",
    device: str = "device-other",
    email: str = "mail-other",
    address: str = "address-other",
) -> GraphTransaction:
    return GraphTransaction(
        transaction_id=transaction_id,
        transaction_time=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=minute),
        card_id=card,
        device_id=device,
        email_domain=email,
        address_id=address,
        product_code="W",
        numeric=(10.0, 0.0, 0.0, 0.0, None, None, None, 0.0, 2.0),
    )


@pytest.fixture
def service() -> GraphRiskService:
    rows = [
        _transaction("T", 10, device="device-A", email="mail-A", address="address-A"),
        _transaction("H_high", 9),
        _transaction("H_multi", 8, device="device-A", email="mail-A", address="address-A"),
        _transaction("H_tie", 8),
        _transaction("H_unscored", 9),
        _transaction("H_same", 10),
        _transaction("H_future", 11),
        _transaction("U", 10, card="unknown-card-X", device="unknown-device-X",
                     email="unknown-email-X", address="unknown-address-X"),
        _transaction("U_before", 9, card="unknown-card-X", device="unknown-device-X",
                     email="unknown-email-X", address="unknown-address-X"),
    ]
    index = TemporalEntityIndex(rows)
    instance = GraphRiskService.__new__(GraphRiskService)
    instance.startup_error = None
    instance.model_version = "graphsage-test"
    instance.store = SimpleNamespace(
        index=index,
        manifest={"fanout": (10, 20)},
        ego_graph=lambda transaction_id: index.build(transaction_id, max_history_per_entity=20),
    )
    instance._scores = {
        "T": 0.1,
        "H_high": 0.8,
        "H_multi": 0.8,
        "H_tie": 0.8,
        "H_same": 0.99,
        "H_future": 0.99,
        "U": 0.2,
        "U_before": 0.9,
    }
    return instance


def test_neighbors_are_historical_scored_deduplicated_and_stably_sorted(service) -> None:
    neighbors = service.find_neighbors("T", limit=10)
    assert [neighbor.transaction_id for neighbor in neighbors] == [
        "H_high", "H_multi", "H_tie"
    ]
    assert neighbors[0].seconds_before_target == 60
    assert neighbors[1].seconds_before_target == neighbors[2].seconds_before_target == 120
    assert neighbors[1].shared_entities == (
        ("card", "card-A"),
        ("device", "device-A"),
        ("email_domain", "mail-A"),
        ("address", "address-A"),
    )
    assert [neighbor.transaction_id for neighbor in service.find_neighbors("T", limit=2)] == [
        "H_high", "H_multi"
    ]
    assert service.find_neighbors("T", limit=10) == neighbors
    assert service.find_neighbors("U", limit=10) == []


def test_uncovered_target_and_invalid_limit_are_explicit(service) -> None:
    with pytest.raises(GraphTargetNotCoveredError):
        service.find_neighbors("H_unscored", limit=5)
    with pytest.raises(ValueError, match="limit"):
        service.find_neighbors("T", limit=0)
