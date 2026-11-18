"""Small, deterministic point-in-time transaction/entity graph contract.

The in-memory builder is a correctness reference for Stage 04 route 2. It
deliberately does not read Parquet or persist graph artifacts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from statistics import median
from typing import Iterable, Literal, Mapping

import numpy as np
import torch
from torch_geometric.data import Data

from graphrisk.models.tabular import NUMERIC_FEATURES

NodeType = Literal["transaction", "card", "device", "email_domain", "address"]
NODE_TYPES: tuple[NodeType, ...] = (
    "transaction",
    "card",
    "device",
    "email_domain",
    "address",
)
ENTITY_FIELDS: tuple[tuple[str, NodeType], ...] = (
    ("card_id", "card"),
    ("device_id", "device"),
    ("email_domain", "email_domain"),
    ("address_id", "address"),
)
REQUIRED_NUMERIC = frozenset(
    {
        "transaction_amount",
        "tx_count_last_1h",
        "tx_count_last_24h",
        "tx_count_last_7d",
        "transaction_hour",
        "transaction_day_of_week",
    }
)


@dataclass(frozen=True)
class GraphTransaction:
    transaction_id: str
    transaction_time: datetime
    card_id: str
    device_id: str
    email_domain: str
    address_id: str
    product_code: str
    numeric: tuple[float | None, ...]

    @classmethod
    def from_feature_row(cls, row: Mapping[str, object]) -> "GraphTransaction":
        """Project only allowed fields; labels and identity_key are never read."""
        required = (
            "transaction_id",
            "transaction_time",
            "card_id",
            "device_id",
            "email_domain",
            "address_id",
            "product_code",
            *NUMERIC_FEATURES,
        )
        missing = [name for name in required if name not in row]
        if missing:
            raise ValueError(f"graph feature row is missing columns: {missing}")
        time = row["transaction_time"]
        if not isinstance(time, datetime) or time.tzinfo is None or time.utcoffset() is None:
            raise ValueError("transaction_time must be timezone-aware")
        ids: dict[str, str] = {}
        for name in (
            "transaction_id",
            "card_id",
            "device_id",
            "email_domain",
            "address_id",
            "product_code",
        ):
            value = row[name]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            ids[name] = value
        numeric: list[float | None] = []
        for name in NUMERIC_FEATURES:
            value = row[name]
            if value is None:
                if name in REQUIRED_NUMERIC:
                    raise ValueError(f"{name} cannot be null")
                numeric.append(None)
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric or null")
            converted = float(value)
            if not isfinite(converted):
                raise ValueError(f"{name} must be finite")
            numeric.append(converted)
        amount = numeric[NUMERIC_FEATURES.index("transaction_amount")]
        assert amount is not None
        if amount < 0:
            raise ValueError("transaction_amount cannot be negative")
        return cls(
            transaction_id=ids["transaction_id"],
            transaction_time=time.astimezone(timezone.utc),
            card_id=ids["card_id"],
            device_id=ids["device_id"],
            email_domain=ids["email_domain"],
            address_id=ids["address_id"],
            product_code=ids["product_code"],
            numeric=tuple(numeric),
        )

    def valid_entities(self) -> tuple[tuple[NodeType, str], ...]:
        return tuple(
            (node_type, value)
            for field, node_type in ENTITY_FIELDS
            if not (value := getattr(self, field)).lower().startswith("unknown-")
        )


@dataclass(frozen=True)
class GraphNode:
    node_type: NodeType
    node_id: str
    transaction: GraphTransaction | None = None

    @property
    def key(self) -> tuple[NodeType, str]:
        return (self.node_type, self.node_id)


@dataclass(frozen=True)
class TemporalEgoGraph:
    target_transaction_id: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[tuple[int, int], ...]  # transaction index -> entity index
    selected_history_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.nodes or self.nodes[0].key != (
            "transaction",
            self.target_transaction_id,
        ):
            raise ValueError("ego graph root must be the target at node index 0")

    def content_sha256(self) -> str:
        """Hash graph structure and allowed feature values, never labels."""
        payload = {
            "target_transaction_id": self.target_transaction_id,
            "nodes": [
                {
                    "type": node.node_type,
                    "id": node.node_id,
                    "time": (
                        node.transaction.transaction_time.isoformat()
                        if node.transaction is not None
                        else None
                    ),
                    "numeric": (
                        node.transaction.numeric if node.transaction is not None else None
                    ),
                    "product_code": (
                        node.transaction.product_code
                        if node.transaction is not None
                        else None
                    ),
                }
                for node in self.nodes
            ],
            "edges": self.edges,
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_pyg(self, encoder: "GraphFeatureEncoder") -> Data:
        """Export bidirectional message edges; only root is a prediction target."""
        features = torch.tensor(
            [encoder.transform_node(node) for node in self.nodes], dtype=torch.float32
        )
        directed_edges = sorted(
            [(source, target) for source, target in self.edges]
            + [(target, source) for source, target in self.edges]
        )
        edge_index = (
            torch.tensor(directed_edges, dtype=torch.long).t().contiguous()
            if directed_edges
            else torch.empty((2, 0), dtype=torch.long)
        )
        data = Data(x=features, edge_index=edge_index)
        data.root_index = torch.tensor([0], dtype=torch.long)
        data.node_keys = [node.key for node in self.nodes]
        return data


class TemporalEgoBuilder:
    """Reference builder with strict event-time filtering and stable ordering."""

    def __init__(self, transactions: Iterable[GraphTransaction]) -> None:
        rows = tuple(transactions)
        ids = [row.transaction_id for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("graph transaction IDs must be unique")
        self._by_id = {row.transaction_id: row for row in rows}

    def build(
        self, target_transaction_id: str, *, max_history_per_entity: int
    ) -> TemporalEgoGraph:
        if max_history_per_entity < 1:
            raise ValueError("max_history_per_entity must be positive")
        try:
            target = self._by_id[target_transaction_id]
        except KeyError as error:
            raise ValueError("target transaction is absent from graph source") from error

        selected: dict[str, GraphTransaction] = {}
        for entity_type, entity_id in target.valid_entities():
            candidates = _sort_recent(
                (
                    row
                    for row in self._by_id.values()
                    if row.transaction_time < target.transaction_time
                    and (entity_type, entity_id) in row.valid_entities()
                ),
            )
            for row in candidates[:max_history_per_entity]:
                selected[row.transaction_id] = row

        return assemble_ego_graph(target, selected.values())


def assemble_ego_graph(
    target: GraphTransaction, history_rows: Iterable[GraphTransaction]
) -> TemporalEgoGraph:
    """Apply the Stage 04 node/edge ordering to a bounded historical selection."""
    history = _sort_recent(history_rows)
    if any(row.transaction_time >= target.transaction_time for row in history):
        raise ValueError("ego graph history must be strictly earlier than target")
    included = (target, *history)
    transaction_nodes = [
        GraphNode("transaction", row.transaction_id, row) for row in included
    ]
    entities = sorted(
        {entity for row in included for entity in row.valid_entities()},
        key=lambda item: (NODE_TYPES.index(item[0]), item[1]),
    )
    nodes = tuple(
        transaction_nodes
        + [GraphNode(node_type, entity_id) for node_type, entity_id in entities]
    )
    index = {node.key: position for position, node in enumerate(nodes)}
    edges = tuple(
        sorted(
            {
                (index[("transaction", row.transaction_id)], index[entity])
                for row in included
                for entity in row.valid_entities()
            }
        )
    )
    return TemporalEgoGraph(
        target_transaction_id=target.transaction_id,
        nodes=nodes,
        edges=edges,
        selected_history_ids=tuple(row.transaction_id for row in history),
    )


def _sort_recent(rows: Iterable[GraphTransaction]) -> list[GraphTransaction]:
    """Exact datetime ordering with transaction ID as an ascending tie-breaker."""
    ordered = sorted(rows, key=lambda row: row.transaction_id)
    ordered.sort(key=lambda row: row.transaction_time, reverse=True)
    return ordered


@dataclass(frozen=True)
class GraphFeatureEncoder:
    """Train-only numeric imputation/scaling and product vocabulary."""

    medians: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    product_codes: tuple[str, ...]

    @classmethod
    def fit(
        cls,
        transactions: Iterable[GraphTransaction],
        *,
        train_transaction_ids: Iterable[str],
    ) -> "GraphFeatureEncoder":
        source = tuple(transactions)
        rows = {row.transaction_id: row for row in source}
        if len(rows) != len(source):
            raise ValueError("graph transaction IDs must be unique before encoder fit")
        ids = tuple(train_transaction_ids)
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("training transaction IDs must be non-empty and unique")
        missing_ids = sorted(set(ids) - set(rows))
        if missing_ids:
            raise ValueError("training transaction IDs are absent from graph source")
        train = [rows[transaction_id] for transaction_id in ids]
        medians = tuple(
            float(median(available)) if available else 0.0
            for position in range(len(NUMERIC_FEATURES))
            for available in [
                [
                    row.numeric[position]
                    for row in train
                    if row.numeric[position] is not None
                ]
            ]
        )
        values = np.asarray(
            [
                [
                    median_value if value is None else value
                    for value, median_value in zip(row.numeric, medians)
                ]
                for row in train
            ],
            dtype=np.float64,
        )
        means = values.mean(axis=0)
        scales = values.std(axis=0)
        scales[scales == 0] = 1.0
        return cls(
            medians=medians,
            means=tuple(float(value) for value in means),
            scales=tuple(float(value) for value in scales),
            product_codes=tuple(sorted({row.product_code for row in train})),
        )

    @property
    def feature_names(self) -> tuple[str, ...]:
        return (
            *(f"numeric:{name}" for name in NUMERIC_FEATURES),
            *(f"missing:{name}" for name in NUMERIC_FEATURES),
            *(f"product:{value}" for value in self.product_codes),
            *(f"type:{value}" for value in NODE_TYPES),
        )

    def transform_node(self, node: GraphNode) -> list[float]:
        vector = [0.0] * len(self.feature_names)
        type_offset = len(NUMERIC_FEATURES) * 2 + len(self.product_codes)
        vector[type_offset + NODE_TYPES.index(node.node_type)] = 1.0
        if node.transaction is None:
            return vector
        transaction = node.transaction
        for position, value in enumerate(transaction.numeric):
            imputed = self.medians[position] if value is None else value
            vector[position] = (imputed - self.means[position]) / self.scales[position]
            vector[len(NUMERIC_FEATURES) + position] = float(value is None)
        if transaction.product_code in self.product_codes:
            product_position = self.product_codes.index(transaction.product_code)
            vector[len(NUMERIC_FEATURES) * 2 + product_position] = 1.0
        return vector
