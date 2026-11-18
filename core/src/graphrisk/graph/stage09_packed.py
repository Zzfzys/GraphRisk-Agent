"""Streaming, label-free temporal graphs and bounded disk-backed PyG batches."""
from __future__ import annotations
from collections import defaultdict
from itertools import groupby
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
import torch
from torch_geometric.data import Batch
from graphrisk.demo.stage09_evaluation import digest, read_json, write_json
from graphrisk.graph.build_artifacts import _iter_projected_rows
from graphrisk.graph.schema import GraphFeatureEncoder, assemble_ego_graph
from graphrisk.graph.train_graphsage import _sha256

VARIANTS = ("full", "no_edges", "rewired", "no_history_numeric")


def transform_edges(edges, types, root, key, seed):
    """Permute entity endpoints within type among historical transactions only.

    Keeps roots, per-node degree, type incidence, node set and strict historical
    timestamps. A type with one distinct entity cannot be rewired.
    """
    forward = edges[(types[edges[:, 0]] == 0) & (types[edges[:, 1]] != 0)].copy()
    number = int(hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()[:16], 16)
    rng = np.random.default_rng(number)
    changed = 0
    for kind in range(1, 5):
        positions = np.flatnonzero((types[forward[:, 1]] == kind) & (forward[:, 0] != root))
        before = forward[positions, 1].copy()
        if len(np.unique(before)) > 1:
            after = rng.permutation(before)
            forward[positions, 1] = after
            changed += int(np.count_nonzero(before != after))
    combined = np.concatenate([forward, forward[:, ::-1]])
    order = np.lexsort((combined[:, 1], combined[:, 0]))
    result = combined[order]
    if len(np.unique(result, axis=0)) != len(result):
        raise ValueError("rewiring produced duplicate message edges")
    return result, changed


def project_variant(x, edges, variant, key, seed):
    if variant not in VARIANTS:
        raise ValueError("unregistered graph variant")
    if variant == "no_edges":
        return x, np.empty((0, 2), dtype=np.int64), 0
    if variant == "no_history_numeric":
        x = x.copy()
        mask = (x[:, -5] == 1)
        mask[0] = False
        x[mask, :18] = 0
    if variant == "rewired":
        types = np.argmax(x[:, -5:], axis=1)
        edges, changed = transform_edges(edges, types, 0, key, seed)
        return x, edges, changed
    return x, edges, 0


def load_manifest_inputs(root, path):
    manifest = read_json(path)
    if digest({k: v for k, v in manifest.items() if k != "content_sha256"}) != manifest["content_sha256"]:
        raise ValueError("source graph manifest content changed")
    for section in ("sources", "artifacts"):
        for item in manifest[section].values():
            file = (root / item["path"]).resolve()
            if not file.is_relative_to(root) or _sha256(file) != item["sha256"]:
                raise ValueError("source graph artifact changed")
    data = read_json(root / manifest["artifacts"]["encoder"]["path"])
    encoder = GraphFeatureEncoder(tuple(data["medians"]), tuple(data["means"]),
        tuple(data["scales"]), tuple(data["product_codes"]))
    if tuple(data["feature_names"]) != encoder.feature_names:
        raise ValueError("encoder schema changed")
    ids = {s: tuple(read_json(root / manifest["artifacts"][s + "_ids"]["path"])["transaction_ids"])
           for s in ("train", "validation", "test")}
    return manifest, encoder, ids


def future_entity_cutoffs(projected, target_ids):
    """Only selected root entities matter; expire postings after their final query."""
    import duckdb
    from graphrisk.graph.schema import ENTITY_FIELDS
    with duckdb.connect(":memory:") as connection:
        cursor = connection.execute(
            "SELECT transaction_time,card_id,device_id,email_domain,address_id "
            "FROM read_parquet(?) WHERE transaction_id IN (SELECT unnest(?))",
            [str(projected), [key for ids in target_ids.values() for key in ids]])
        result = {}
        while batch := cursor.fetchmany(1000):
            for time, *entities in batch:
                for (_, node_type), entity in zip(ENTITY_FIELDS, entities, strict=True):
                    if not entity.lower().startswith("unknown-"):
                        key = (node_type, entity)
                        result[key] = max(result.get(key, time), time)
    return result


def stream_graphs(projected, target_ids, caps, guard=lambda: None, entity_cutoffs=None):
    """Keep only each entity's latest max(cap) earlier records; no full index."""
    if not caps or any(isinstance(c, bool) or not isinstance(c, int) or c < 1 for c in caps):
        raise ValueError("history caps must be positive integers")
    wanted = {key: split for split, ids in target_ids.items() for key in ids}
    if len(wanted) != sum(map(len, target_ids.values())):
        raise ValueError("target IDs overlap or duplicate")
    latest, count, max_cap = {}, 0, max(caps)
    found = defaultdict(list)
    for event_time, group in groupby(_iter_projected_rows(projected), key=lambda r: r.transaction_time):
        rows = list(group)  # Queries order tied events by ID ascending.
        for target in rows:
            split = wanted.get(target.transaction_id)
            if split is None:
                continue
            for cap in caps:
                selected = {r.transaction_id: r for entity in target.valid_entities()
                            for r in latest.get(entity, ())[:cap]}
                graph = assemble_ego_graph(target, selected.values())
                if any(r.transaction_time >= event_time for r in selected.values()):
                    raise ValueError("nonhistorical context in streaming graph")
                yield split, target, cap, graph
            found[split].append(target.transaction_id)
        additions = defaultdict(list)
        for row in rows:
            for entity in row.valid_entities():
                additions[entity].append(row)
        for entity, earlier_in_group in additions.items():
            if entity_cutoffs is not None and entity_cutoffs.get(entity, event_time) <= event_time:
                latest.pop(entity, None)
                continue
            latest[entity] = (earlier_in_group + latest.get(entity, []))[:max_cap]
        count += len(rows)
        if count >= 1000:
            count = 0
            guard()
    for split, ids in target_ids.items():
        if tuple(found[split]) != tuple(ids):
            raise ValueError("streaming graph IDs/order differ from frozen targets")


def build_cache(root, destination, manifest_path, target_ids, caps, rewiring_seed, guard=lambda: None):
    if destination.exists():
        raise FileExistsError("graph cache exists; verify and reuse, never overwrite")
    manifest, encoder, original_ids = load_manifest_inputs(root, manifest_path)
    if any(tuple(ids) != original_ids[s] for s, ids in target_ids.items()):
        raise ValueError("cache targets differ from source graph")
    destination.mkdir(parents=True)
    streams, offsets, statistics = {}, {}, {}
    for cap in caps:
        for split in target_ids:
            variants = VARIANTS if cap == 5 else ("full",)
            for variant in variants:
                if variant in ("no_edges", "no_history_numeric"):
                    continue  # Bounded batch transformation shares immutable full graph arrays.
                name = f"cap{cap}-{variant}-{split}"
                folder = destination / name
                folder.mkdir()
                streams[name] = ((folder / "x.f32").open("wb"), (folder / "edges.i64").open("wb"))
                offsets[name] = ([0], [0])
                statistics[name] = {"targets": 0, "changed_graphs": 0, "changed_forward_edges": 0}
    try:
        projected = root / manifest["artifacts"]["projected_transactions"]["path"]
        cutoffs = future_entity_cutoffs(projected, target_ids)
        guard()
        for split, target, cap, graph in stream_graphs(projected, target_ids, caps, guard, cutoffs):
            x = np.asarray([encoder.transform_node(n) for n in graph.nodes], dtype=np.float32)
            edges = np.asarray(sorted([*graph.edges, *((b, a) for a, b in graph.edges)]), dtype=np.int64).reshape(-1, 2)
            for variant in (("full", "rewired") if cap == 5 else ("full",)):
                name = f"cap{cap}-{variant}-{split}"
                xx, ee, changed = project_variant(x, edges, variant, target.transaction_id, rewiring_seed)
                xf, ef = streams[name]
                xf.write(xx.tobytes())
                ef.write(ee.tobytes())
                no, eo = offsets[name]
                no.append(no[-1] + len(xx))
                eo.append(eo[-1] + len(ee))
                stats = statistics[name]
                stats["targets"] += 1
                stats["changed_graphs"] += changed > 0
                stats["changed_forward_edges"] += changed
    finally:
        for xf, ef in streams.values():
            xf.close()
            ef.close()
    files = {}
    for name, (no, eo) in offsets.items():
        folder = destination / name
        np.savez(folder / "offsets.npz", nodes=np.asarray(no, dtype=np.int64),
                 edges=np.asarray(eo, dtype=np.int64))
        files[name] = {file.name: {"sha256": _sha256(file), "bytes": file.stat().st_size}
                       for file in folder.iterdir()}
    result = {"schema_version": "stage09-packed-temporal-v1",
        "source_manifest_sha256": manifest["content_sha256"], "feature_names": encoder.feature_names,
        "target_ids": {s: list(ids) for s, ids in target_ids.items()},
        "encoder": {"medians": encoder.medians, "means": encoder.means, "scales": encoder.scales,
                    "product_codes": encoder.product_codes},
        "statistics": statistics, "caps": list(caps), "rewiring_seed": rewiring_seed, "files": files,
        "labels_in_cache": False, "rule": "same strict temporal schema; per-node source-time features"}
    result = json.loads(json.dumps(result))
    result["content_sha256"] = digest(result)
    write_json(destination / "manifest.json", result)
    return result


class PackedGraphs:
    def __init__(self, folder, manifest, cap, split, variant):
        if variant not in VARIANTS or cap not in (5, 10) or (cap == 10 and variant != "full"):
            raise ValueError("unsupported packed graph variant")
        self.variant, self.split = variant, split
        self.ids = tuple(manifest["target_ids"][split])
        name = f"cap{cap}-{'rewired' if variant == 'rewired' else 'full'}-{split}"
        directory = folder / name
        arrays = np.load(directory / "offsets.npz", allow_pickle=False)
        self.no, self.eo = arrays["nodes"], arrays["edges"]
        self.dimension = len(manifest["feature_names"])
        self.x = np.memmap(directory / "x.f32", dtype=np.float32, mode="r",
                           shape=(int(self.no[-1]), self.dimension))
        # Empty-edge tiny fixtures are valid; real-data full graphs have edges.
        self.edges = (np.memmap(directory / "edges.i64", dtype=np.int64, mode="r",
                               shape=(int(self.eo[-1]), 2)) if self.eo[-1] else np.empty((0, 2), dtype=np.int64))
        if len(self.no) != len(self.ids) + 1 or (np.diff(self.no) < 1).any():
            raise ValueError("packed node offsets invalid")
        if len(self.eo) != len(self.ids) + 1 or (np.diff(self.eo) < 0).any():
            raise ValueError("packed edge offsets invalid")

    def batch(self, positions):
        positions = list(positions)
        if not positions or any(i < 0 or i >= len(self.ids) for i in positions):
            raise ValueError("packed batch positions invalid")
        xs, es, memberships, roots, cursor = [], [], [], [], 0
        for i, position in enumerate(positions):
            x = np.array(self.x[self.no[position]:self.no[position + 1]], copy=True)
            edge = np.array(self.edges[self.eo[position]:self.eo[position + 1]], copy=True)
            if self.variant == "no_edges":
                edge = np.empty((0, 2), dtype=np.int64)
            elif self.variant == "no_history_numeric":
                mask = (x[:, -5] == 1)
                mask[0] = False
                x[mask, :18] = 0
            xs.append(x)
            es.append(edge + cursor)
            memberships.append(np.full(len(x), i, dtype=np.int64))
            roots.append(cursor)
            cursor += len(x)
        batch = Batch(x=torch.from_numpy(np.concatenate(xs)),
            edge_index=torch.from_numpy(np.concatenate(es)).t().contiguous(),
            batch=torch.from_numpy(np.concatenate(memberships)),
            root_index=torch.tensor(roots, dtype=torch.long))
        batch._num_graphs = len(positions)
        return batch


def verify_cache(folder):
    manifest = read_json(folder / "manifest.json")
    if digest({k: v for k, v in manifest.items() if k != "content_sha256"}) != manifest["content_sha256"]:
        raise ValueError("packed manifest changed")
    for name, files in manifest["files"].items():
        for filename, item in files.items():
            file = (folder / name / filename).resolve()
            if not file.is_relative_to(folder.resolve()) or _sha256(file) != item["sha256"]:
                raise ValueError("packed graph cache bytes changed")
    return manifest

