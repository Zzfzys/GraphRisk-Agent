"""Time-ordered entity postings for repeatable, bounded ego-graph queries."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime
from typing import Iterable

from graphrisk.graph.schema import (
    GraphTransaction,
    NodeType,
    TemporalEgoGraph,
    assemble_ego_graph,
)


class TemporalEntityIndex:
    """Index transaction/entity edges without linking future or equal-time rows."""

    def __init__(self, transactions: Iterable[GraphTransaction]) -> None:
        ordered = sorted(
            transactions, key=lambda row: (row.transaction_time, row.transaction_id)
        )
        self.by_id: dict[str, GraphTransaction] = {}
        postings: dict[tuple[NodeType, str], list[tuple[datetime, str]]] = defaultdict(list)
        for row in ordered:
            if row.transaction_id in self.by_id:
                raise ValueError("graph transaction IDs must be unique")
            self.by_id[row.transaction_id] = row
            for entity in row.valid_entities():
                postings[entity].append((row.transaction_time, row.transaction_id))
        self.postings = dict(postings)

    def build(
        self, target_transaction_id: str, *, max_history_per_entity: int
    ) -> TemporalEgoGraph:
        if max_history_per_entity < 1:
            raise ValueError("max_history_per_entity must be positive")
        try:
            target = self.by_id[target_transaction_id]
        except KeyError as error:
            raise ValueError("target transaction is absent from graph source") from error
        selected: dict[str, GraphTransaction] = {}
        for entity in target.valid_entities():
            posting = self.postings[entity]
            position = bisect_left(posting, (target.transaction_time, "")) - 1
            count = 0
            while position >= 0 and count < max_history_per_entity:
                event_time = posting[position][0]
                first = position
                while first >= 0 and posting[first][0] == event_time:
                    first -= 1
                for _, transaction_id in posting[first + 1 : position + 1]:
                    selected[transaction_id] = self.by_id[transaction_id]
                    count += 1
                    if count == max_history_per_entity:
                        break
                position = first
        return assemble_ego_graph(target, selected.values())

    def candidate_count(self, target_transaction_id: str) -> int:
        """Count direct historical matches across root entities, before fanout."""
        target = self.by_id[target_transaction_id]
        return sum(
            bisect_left(self.postings[entity], (target.transaction_time, ""))
            for entity in target.valid_entities()
        )

    def truncated_entity_count(
        self, target_transaction_id: str, *, max_history_per_entity: int
    ) -> int:
        target = self.by_id[target_transaction_id]
        return sum(
            bisect_left(self.postings[entity], (target.transaction_time, ""))
            > max_history_per_entity
            for entity in target.valid_entities()
        )
