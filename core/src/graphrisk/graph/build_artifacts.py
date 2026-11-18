"""Build reproducible, label-free Stage 04 graph inputs from Stage 01 artifacts."""

from __future__ import annotations

import hashlib
import json
import platform
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb
import torch
import torch_geometric

from graphrisk.config import load_config
from graphrisk.graph.config import GraphExperimentConfig, load_graph_config
from graphrisk.graph.schema import (
    ENTITY_FIELDS,
    GraphFeatureEncoder,
    GraphTransaction,
    TemporalEgoGraph,
)
from graphrisk.graph.temporal_index import TemporalEntityIndex
from graphrisk.models.tabular import NUMERIC_FEATURES
from graphrisk.training.config import load_training_config

GRAPH_SCHEMA_VERSION = "stage04-temporal-ego-v1"
BASE_COLUMNS = (
    "transaction_id",
    "transaction_time",
    "transaction_amount",
    "card_id",
    "device_id",
    "email_domain",
    "address_id",
    "product_code",
)
GRAPH_COLUMNS = (*BASE_COLUMNS, *NUMERIC_FEATURES[1:])


@dataclass(frozen=True)
class GraphBuildSummary:
    manifest_path: Path
    content_sha256: str
    indexed_transactions: int
    target_counts: dict[str, int]
    elapsed_seconds: float


@dataclass(frozen=True)
class LoadedGraphArtifacts:
    """Verified graph source, encoder, and target IDs for training or queries."""

    manifest: dict
    index: TemporalEntityIndex
    encoder: GraphFeatureEncoder
    target_ids: dict[str, tuple[str, ...]]

    def ego_graph(self, transaction_id: str) -> TemporalEgoGraph:
        return self.index.build(
            transaction_id, max_history_per_entity=self.manifest["fanout"][1]
        )


def _sql_path(path: Path) -> str:
    return "'" + str(path.resolve()).replace("'", "''").replace("\\", "/") + "'"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _copy_parquet(connection: duckdb.DuckDBPyConnection, query: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    connection.execute(
        f"COPY ({query}) TO {_sql_path(temporary)} "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    temporary.replace(path)


def _count(connection: duckdb.DuckDBPyConnection, query: str) -> int:
    return int(connection.execute(query).fetchone()[0])


def _validate_sources(
    connection: duckdb.DuckDBPyConnection,
    canonical: Path,
    features: Path,
    splits: dict[str, Path],
) -> int:
    """Fail on stale, duplicate, missing, or cross-source Stage 01 files."""
    for name, path in (("canonical", canonical), ("features", features), *splits.items()):
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"graph input {name} is absent or empty: {path}")
        table = _sql_path(path)
        count, distinct = connection.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT transaction_id) FROM read_parquet({table})"
        ).fetchone()
        if not count or count != distinct:
            raise ValueError(f"graph input {name} has no rows or duplicate transaction IDs")
    feature_table, canonical_table = _sql_path(features), _sql_path(canonical)
    columns = ", ".join(BASE_COLUMNS)
    total = _count(connection, f"SELECT COUNT(*) FROM read_parquet({feature_table})")
    canonical_total = _count(
        connection, f"SELECT COUNT(*) FROM read_parquet({canonical_table})"
    )
    if total != canonical_total or _count(
        connection,
        f"SELECT COUNT(*) FROM (SELECT {columns} FROM read_parquet({feature_table}) "
        f"EXCEPT ALL SELECT {columns} FROM read_parquet({canonical_table}))",
    ):
        raise ValueError("canonical transactions and point-in-time features disagree")

    split_parts = []
    bounds = []
    for name, path in splits.items():
        table = _sql_path(path)
        if _count(
            connection,
            f"SELECT COUNT(*) FROM read_parquet({table}) "
            f"WHERE dataset_split IS DISTINCT FROM '{name}'",
        ):
            raise ValueError(f"{name} feature file has incorrect dataset_split values")
        bounds.append(
            connection.execute(
                f"SELECT MIN(transaction_time), MAX(transaction_time) "
                f"FROM read_parquet({table})"
            ).fetchone()
        )
        split_parts.append(
            f"SELECT {', '.join(GRAPH_COLUMNS)} FROM read_parquet({table})"
        )
    if bounds[0][1] > bounds[1][0] or bounds[1][1] > bounds[2][0]:
        raise ValueError("graph split times are not chronological")
    split_union = " UNION ALL ".join(split_parts)
    split_count, split_distinct = connection.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT transaction_id) FROM ({split_union})"
    ).fetchone()
    if split_count != total or split_distinct != total or _count(
        connection,
        "SELECT COUNT(*) FROM ("
        f"SELECT {', '.join(GRAPH_COLUMNS)} FROM ({split_union}) "
        "EXCEPT ALL "
        f"SELECT {', '.join(GRAPH_COLUMNS)} FROM read_parquet({feature_table})"
        ")",
    ):
        raise ValueError("split files do not partition the configured feature source")
    return total


def _target_rows(
    connection: duckdb.DuckDBPyConnection, path: Path, limit: int
) -> list[tuple[str, object]]:
    return connection.execute(
        "SELECT transaction_id, CAST(transaction_time AS TIMESTAMPTZ) AS transaction_time "
        f"FROM read_parquet({_sql_path(path)}) "
        "ORDER BY transaction_time, transaction_id LIMIT ?",
        [limit],
    ).fetchall()


def _iter_projected_rows(path: Path):
    with duckdb.connect(":memory:") as connection:
        cursor = connection.execute(
            f"SELECT {', '.join(GRAPH_COLUMNS)} FROM read_parquet({_sql_path(path)}) "
            "ORDER BY transaction_time, transaction_id"
        )
        names = [column[0] for column in cursor.description]
        while batch := cursor.fetchmany(10000):
            for values in batch:
                yield GraphTransaction.from_feature_row(dict(zip(names, values, strict=True)))


def _write_edge_and_node_tables(
    connection: duckdb.DuckDBPyConnection,
    projected: Path,
    edge_path: Path,
    node_path: Path,
) -> tuple[int, int]:
    table = _sql_path(projected)
    edge_selects = [
        f"SELECT transaction_id, transaction_time, '{node_type}'::VARCHAR "
        f"AS entity_type, {field} AS entity_id FROM read_parquet({table}) "
        f"WHERE lower({field}) NOT LIKE 'unknown-%'"
        for field, node_type in ENTITY_FIELDS
    ]
    edges = " UNION ALL ".join(edge_selects)
    _copy_parquet(
        connection,
        f"SELECT * FROM ({edges}) "
        "ORDER BY transaction_time, transaction_id, entity_type, entity_id",
        edge_path,
    )
    entity_edges = _sql_path(edge_path)
    nodes = (
        "SELECT 'transaction'::VARCHAR AS node_type, transaction_id AS node_id, "
        "transaction_time, 0 AS type_rank "
        f"FROM read_parquet({table}) UNION ALL "
        "SELECT entity_type AS node_type, entity_id AS node_id, "
        "NULL::TIMESTAMPTZ AS transaction_time, "
        "CASE entity_type WHEN 'card' THEN 1 WHEN 'device' THEN 2 "
        "WHEN 'email_domain' THEN 3 ELSE 4 END AS type_rank "
        f"FROM read_parquet({entity_edges}) GROUP BY entity_type, entity_id"
    )
    _copy_parquet(
        connection,
        "SELECT (ROW_NUMBER() OVER (ORDER BY type_rank, node_id) - 1)::BIGINT "
        "AS node_index, node_type, node_id, transaction_time "
        f"FROM ({nodes}) ORDER BY node_index",
        node_path,
    )
    return (
        _count(connection, f"SELECT COUNT(*) FROM read_parquet({entity_edges})"),
        _count(connection, f"SELECT COUNT(*) FROM read_parquet({_sql_path(node_path)})"),
    )


def build_graph_artifacts(config_path: str | Path) -> GraphBuildSummary:
    started = time.perf_counter()
    config: GraphExperimentConfig = load_graph_config(config_path)
    config.check_stage_inputs()
    if config.sampling.fanout[0] < len(ENTITY_FIELDS):
        raise ValueError("first graph fanout must cover all four root entity types")
    data = load_config(config.resolve_path(config.paths.data_config))
    baseline = load_training_config(config.resolve_path(config.paths.training_config))
    canonical = data.resolve_path(data.paths.interim_transactions)
    features = data.resolve_path(data.paths.interim_features)
    splits = {
        name: baseline.resolve_path(getattr(baseline.paths, f"{name}_features"))
        for name in ("train", "validation", "test")
    }
    root = Path(config_path).resolve().parent.parent
    artifact_dir = config.resolve_path(config.paths.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "projected_transactions": artifact_dir / "projected-transactions.parquet",
        "entity_edges": artifact_dir / "entity-edges.parquet",
        "node_mapping": artifact_dir / "node-mapping.parquet",
        "encoder": artifact_dir / "encoder.json",
        "ego_sample": artifact_dir / "ego-sample.json",
    }
    configured_outputs = {
        config.resolve_path(getattr(config.paths, name))
        for name in (
            "train_ids", "validation_ids", "test_ids",
            "train_predictions", "validation_predictions", "test_predictions",
            "graph_manifest",
        )
    }
    if set(outputs.values()) & configured_outputs:
        raise ValueError("graph artifact paths overlap configured experiment outputs")
    with duckdb.connect(":memory:") as connection:
        connection.execute("SET TimeZone='UTC'")
        source_count = _validate_sources(connection, canonical, features, splits)
        target_rows = {
            name: _target_rows(
                connection,
                path,
                getattr(config.sampling, f"max_{name}_transactions"),
            )
            for name, path in splits.items()
        }
        if any(not rows for rows in target_rows.values()):
            raise ValueError("each graph split requires at least one target transaction")
        config.resolve_path(config.paths.graph_manifest).unlink(missing_ok=True)
        last_target_time = max(rows[-1][1] for rows in target_rows.values())
        projection = ", ".join(
            "CAST(transaction_time AS TIMESTAMPTZ) AS transaction_time"
            if column == "transaction_time" else column
            for column in GRAPH_COLUMNS
        )
        query = (
            f"SELECT {projection} FROM read_parquet({_sql_path(features)}) "
            f"WHERE transaction_time <= TIMESTAMPTZ '{last_target_time.isoformat()}' "
            "ORDER BY transaction_time, transaction_id"
        )
        _copy_parquet(connection, query, outputs["projected_transactions"])
        edge_count, node_count = _write_edge_and_node_tables(
            connection,
            outputs["projected_transactions"],
            outputs["entity_edges"],
            outputs["node_mapping"],
        )

    index = TemporalEntityIndex(_iter_projected_rows(outputs["projected_transactions"]))
    target_ids = {
        name: [transaction_id for transaction_id, _ in rows]
        for name, rows in target_rows.items()
    }
    if any(transaction_id not in index.by_id for ids in target_ids.values() for transaction_id in ids):
        raise ValueError("selected graph target is absent from projected transactions")
    encoder = GraphFeatureEncoder.fit(
        (index.by_id[transaction_id] for transaction_id in target_ids["train"]),
        train_transaction_ids=target_ids["train"],
    )
    _write_json(
        outputs["encoder"],
        {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "feature_names": encoder.feature_names,
            "numeric_features": NUMERIC_FEATURES,
            "train_id_sha256": hashlib.sha256(
                json.dumps(target_ids["train"], separators=(",", ":")).encode()
            ).hexdigest(),
            **asdict(encoder),
        },
    )
    id_paths = {
        name: config.resolve_path(getattr(config.paths, f"{name}_ids"))
        for name in target_ids
    }
    for name, path in id_paths.items():
        _write_json(
            path,
            {"dataset_source": config.dataset_source, "split": name, "transaction_ids": target_ids[name]},
        )
    max_history = config.sampling.fanout[1]
    sample_graphs = [
        index.build(transaction_id, max_history_per_entity=max_history).to_pyg(encoder)
        for transaction_id in target_ids["train"][:2]
    ]
    _write_json(
        outputs["ego_sample"],
        {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "graphs": [
                {
                    "target_transaction_id": target_ids["train"][position],
                    "x": graph.x.tolist(),
                    "edge_index": graph.edge_index.tolist(),
                    "root_index": graph.root_index.tolist(),
                    "node_keys": graph.node_keys,
                }
                for position, graph in enumerate(sample_graphs)
            ],
        },
    )
    stats: dict[str, dict[str, int]] = {}
    for name, ids in target_ids.items():
        counts = {
            "targets": len(ids),
            "ego_nodes_total": 0,
            "ego_edges_total": 0,
            "zero_history_targets": 0,
            "zero_entity_targets": 0,
            "truncated_entity_postings": 0,
        }
        for transaction_id in ids:
            graph = index.build(transaction_id, max_history_per_entity=max_history)
            counts["ego_nodes_total"] += len(graph.nodes)
            counts["ego_edges_total"] += len(graph.edges)
            counts["zero_history_targets"] += not graph.selected_history_ids
            counts["zero_entity_targets"] += not index.by_id[transaction_id].valid_entities()
            counts["truncated_entity_postings"] += index.truncated_entity_count(
                transaction_id, max_history_per_entity=max_history
            )
        stats[name] = counts
    source_paths = {"canonical": canonical, "features": features, **splits}
    output_paths = {**outputs, **{f"{name}_ids": path for name, path in id_paths.items()}}
    manifest = {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "dataset_source": config.dataset_source,
        "project_name": config.project_name,
        "model_version": config.training.model_version,
        "selection": config.sampling.selection,
        "random_seed": config.training.random_seed,
        "fanout": config.sampling.fanout,
        "sampling_rule": "root entity types (maximum fanout[0]); strict earlier transactions per root entity (fanout[1])",
        "placeholder_rule": "case-insensitive unknown-* entity values do not form nodes or edges",
        "selected_target_counts": {name: len(ids) for name, ids in target_ids.items()},
        "target_time_bounds": {
            name: [rows[0][1].isoformat(), rows[-1][1].isoformat()]
            for name, rows in target_rows.items()
        },
        "indexed_through_time": last_target_time.isoformat(),
        "source_transaction_count": source_count,
        "indexed_transaction_count": len(index.by_id),
        "global_node_count": node_count,
        "global_entity_edge_count": edge_count,
        "feature_names": encoder.feature_names,
        "sources": {
            name: {"path": path.resolve().relative_to(root).as_posix(), "sha256": _sha256(path)}
            for name, path in source_paths.items()
        },
        "artifacts": {
            name: {"path": path.resolve().relative_to(root).as_posix(), "sha256": _sha256(path)}
            for name, path in output_paths.items()
        },
        "ego_statistics": stats,
        "software": {
            "python": platform.python_version(),
            "duckdb": duckdb.__version__,
            "torch": torch.__version__,
            "torch_geometric": torch_geometric.__version__,
        },
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    manifest["content_sha256"] = hashlib.sha256(encoded).hexdigest()
    manifest_path = config.resolve_path(config.paths.graph_manifest)
    _write_json(manifest_path, manifest)
    return GraphBuildSummary(
        manifest_path=manifest_path,
        content_sha256=manifest["content_sha256"],
        indexed_transactions=len(index.by_id),
        target_counts={name: len(ids) for name, ids in target_ids.items()},
        elapsed_seconds=time.perf_counter() - started,
    )


def load_graph_artifacts(
    manifest_path: str | Path, *, expected_dataset_source: str | None = None
) -> LoadedGraphArtifacts:
    """Reject corrupt or cross-source artifacts before rebuilding the in-memory index."""
    path = Path(manifest_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"graph manifest does not exist: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    claimed_hash = manifest.get("content_sha256")
    unsigned = {key: value for key, value in manifest.items() if key != "content_sha256"}
    actual_hash = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    if claimed_hash != actual_hash or manifest.get("schema_version") != GRAPH_SCHEMA_VERSION:
        raise ValueError("graph manifest content hash or schema version does not match")
    if expected_dataset_source is not None and manifest.get("dataset_source") != expected_dataset_source:
        raise ValueError("graph manifest dataset_source does not match requested source")
    projected_relative = Path(manifest["artifacts"]["projected_transactions"]["path"])
    root = next(
        (ancestor for ancestor in path.parents if (ancestor / projected_relative).is_file()),
        None,
    )
    if root is None:
        raise FileNotFoundError("graph projected-transactions artifact is absent")
    for name, metadata in manifest["sources"].items():
        relative = Path(metadata["path"])
        resolved = (root / relative).resolve()
        if relative.is_absolute() or not resolved.is_relative_to(root):
            raise ValueError(f"graph {name} source path escapes the project root")
        if not resolved.is_file() or _sha256(resolved) != metadata["sha256"]:
            raise ValueError(f"graph {name} source changed since artifact construction")
    artifact_paths: dict[str, Path] = {}
    for name, metadata in manifest["artifacts"].items():
        relative = Path(metadata["path"])
        resolved = (root / relative).resolve()
        if relative.is_absolute() or not resolved.is_relative_to(root):
            raise ValueError(f"graph {name} artifact path escapes the project root")
        if not resolved.is_file() or _sha256(resolved) != metadata["sha256"]:
            raise ValueError(f"graph {name} artifact is missing or has a different hash")
        artifact_paths[name] = resolved
    encoder_data = json.loads(artifact_paths["encoder"].read_text(encoding="utf-8"))
    encoder = GraphFeatureEncoder(
        medians=tuple(encoder_data["medians"]),
        means=tuple(encoder_data["means"]),
        scales=tuple(encoder_data["scales"]),
        product_codes=tuple(encoder_data["product_codes"]),
    )
    if tuple(encoder_data["feature_names"]) != encoder.feature_names:
        raise ValueError("graph encoder feature names do not match its fitted metadata")
    target_ids: dict[str, tuple[str, ...]] = {}
    for split in ("train", "validation", "test"):
        payload = json.loads(artifact_paths[f"{split}_ids"].read_text(encoding="utf-8"))
        if payload["dataset_source"] != manifest["dataset_source"] or payload["split"] != split:
            raise ValueError(f"graph {split} target ID list has the wrong source or split")
        target_ids[split] = tuple(payload["transaction_ids"])
    index = TemporalEntityIndex(_iter_projected_rows(artifact_paths["projected_transactions"]))
    if len(index.by_id) != manifest["indexed_transaction_count"]:
        raise ValueError("graph projected transaction count disagrees with manifest")
    return LoadedGraphArtifacts(manifest, index, encoder, target_ids)
