"""Fresh scale-specific encoders and label-free, strictly temporal graph caches."""
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from graphrisk.agent.stage09_preflight import _content_hash
from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.graph.build_artifacts import GRAPH_COLUMNS
from graphrisk.graph.schema import GraphFeatureEncoder, NODE_TYPES
from graphrisk.graph.stage09_packed import stream_graphs, future_entity_cutoffs
from graphrisk.models.enhanced_data import root_encoder
from graphrisk.models.enhanced_execution import binding, sealed


class BoundedPackedGraphs:
    """Release Windows mapped pages after each owned batch, keeping cache bytes.

    PackedGraphs.batch copies all arrays into an owned PyG Batch. Reopening its
    mappings per batch prevents shuffled training from retaining an entire cache
    in this process's working set; offsets/IDs remain small and unchanged.
    """
    def __init__(self, folder, manifest, cap, split, variant):
        from graphrisk.graph.stage09_packed import PackedGraphs
        self.packed = PackedGraphs(folder,manifest,cap,split,variant)
        self.ids,self.dimension = self.packed.ids,self.packed.dimension
        self.x_path,self.x_shape = Path(self.packed.x.filename),self.packed.x.shape
        self.edge_path = Path(self.packed.edges.filename) if len(self.packed.edges) else None
        self.edge_shape = self.packed.edges.shape
        self.release()

    def release(self):
        for name in ("x","edges"):
            array = getattr(self.packed,name)
            if isinstance(array,np.memmap):
                array._mmap.close()

    def batch(self, positions):
        self.packed.x = np.memmap(self.x_path,dtype=np.float32,mode="r",shape=self.x_shape)
        self.packed.edges = np.memmap(self.edge_path,dtype=np.int64,mode="r",shape=self.edge_shape) if self.edge_path else np.empty((0,2),dtype=np.int64)
        try:
            return self.packed.batch(positions)
        finally:
            self.release()


def prefix_count(frame, requested):
    """Do not split simultaneous events or silently shuffle scale assignment."""
    ids = frame.transaction_id.tolist()
    times = frame.source_transaction_dt_seconds.to_numpy()
    if not ids or len(ids) != len(set(ids)) or list(zip(times, ids)) != sorted(zip(times, ids)):
        raise ValueError("scale targets require unique chronological IDs")
    if requested == "full_allowed_train":
        return len(frame)
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        raise ValueError("positive scale size required")
    count = min(requested, len(frame))
    while 0 < count < len(frame) and times[count-1] == times[count]:
        count -= 1
    if not count:
        raise ValueError("scale is empty after equal-time adjustment")
    return count


def project_source(db, root, destination, last_second):
    """Never project labels, identity heuristics or future validation/test rows."""
    projection = ",".join('f."'+n+'"' for n in GRAPH_COLUMNS)
    db.execute("COPY (SELECT "+projection+" FROM read_parquet($1) f JOIN read_parquet($2) c USING(transaction_id) WHERE c.source_transaction_dt_seconds<=$3 ORDER BY f.transaction_time,f.transaction_id) TO $4 (FORMAT PARQUET)",
        [str(root / "data/interim/ieee-cis/features_all.parquet"),
         str(root / "data/interim/ieee-cis/transactions.parquet"), int(last_second), str(destination)])
    names = [r[0] for r in db.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(destination)]).fetchall()]
    if names != list(GRAPH_COLUMNS):
        raise ValueError("label-free graph projection schema changed")


def encode_graph(graph, encoder):
    """Vectorized numerical equivalent of the reference per-node encoder."""
    result = np.zeros((len(graph.nodes), len(encoder.feature_names)), dtype=np.float32)
    positions = [i for i, n in enumerate(graph.nodes) if n.transaction is not None]
    raw = np.asarray([graph.nodes[i].transaction.numeric for i in positions], dtype=float)
    missing = np.isnan(raw)
    result[positions, :9] = (np.where(missing, encoder.medians, raw)-encoder.means)/encoder.scales
    result[positions, 9:18] = missing
    for j, code in enumerate(encoder.product_codes):
        result[positions, 18+j] = [graph.nodes[i].transaction.product_code == code for i in positions]
    for i, node in enumerate(graph.nodes):
        result[i, -5+NODE_TYPES.index(node.node_type)] = 1
    if not np.isfinite(result).all() or result.shape[1] != 28:
        raise ValueError("invalid 28-dimensional graph encoding")
    return result


def fit_encoders(db, projected, train, scale_targets):
    frame = db.execute("SELECT * FROM read_parquet(?) ORDER BY transaction_time,transaction_id LIMIT ?", [str(projected), len(train)]).fetchdf()
    if frame.transaction_id.tolist() != train.transaction_id.tolist():
        raise ValueError("graph encoder roots differ from registered train IDs")
    encoders, counts = {}, {}
    for target in scale_targets:
        name = str(target)
        count = prefix_count(train, target)
        encoders[name] = root_encoder(frame.iloc[:count], None)
        counts[name] = count
    return encoders, counts


def build_scale_caches(projected, destination, train, inner, encoders, counts, guard=lambda: None,
                       matched_roots=None):
    """One chronological stream, independent encoding/file arrays per scale."""
    if destination.exists():
        raise FileExistsError("fresh graph caches required; never overwrite")
    destination.mkdir(parents=True)
    all_ids = {"train": tuple(train.transaction_id), "validation": tuple(inner.transaction_id)}
    streams, offsets, stats = {}, {}, {}
    for scale in counts:
        for split in all_ids:
            key = (scale, split)
            folder = destination / scale / ("cap5-full-"+split)
            folder.mkdir(parents=True)
            streams[key] = ((folder / "x.f32").open("xb"), (folder / "edges.i64").open("xb"))
            offsets[key] = ([0], [0])
            stats[key] = {"targets": 0, "nodes": 0, "message_edges": 0, "historical_transactions": 0,
                          "zero_history_targets": 0, "time_violations": 0, "train_context_from_inner": 0,
                          "matched_root_max_abs_delta": 0.0}
    ranks = {"train": 0, "validation": 0}
    first_inner_time = pd.to_datetime(inner.source_transaction_dt_seconds.iloc[0], unit="s", origin="2000-01-01", utc=True)
    try:
        cutoffs = future_entity_cutoffs(projected, all_ids)
        guard()
        for split, target, _, graph in stream_graphs(projected, all_ids, (5,), guard, cutoffs):
            rank = ranks[split]
            ranks[split] += 1
            times = [n.transaction.transaction_time for n in graph.nodes[1:] if n.transaction is not None]
            if any(t >= target.transaction_time for t in times):
                raise ValueError("future or simultaneous graph context")
            if split == "train" and any(t >= first_inner_time for t in times):
                raise ValueError("training graph contains inner-validation relationships")
            edges = np.asarray(sorted([*graph.edges, *((b,a) for a,b in graph.edges)]), dtype=np.int64).reshape(-1,2)
            for scale, count in counts.items():
                if split == "train" and rank >= count:
                    continue
                key = scale, split
                x = encode_graph(graph, encoders[scale])
                if matched_roots is not None and scale == "full_allowed_train":
                    expected = matched_roots[split][rank]
                    delta = float(np.max(np.abs(x[0]-expected)))
                    stats[key]["matched_root_max_abs_delta"] = max(stats[key]["matched_root_max_abs_delta"], delta)
                    if delta > 1e-6:
                        raise ValueError("full-scale root differs from matched tabular control")
                streams[key][0].write(x.tobytes())
                streams[key][1].write(edges.tobytes())
                offsets[key][0].append(offsets[key][0][-1]+len(x))
                offsets[key][1].append(offsets[key][1][-1]+len(edges))
                stat = stats[key]
                stat["targets"] += 1
                stat["nodes"] += len(x)
                stat["message_edges"] += len(edges)
                stat["historical_transactions"] += len(times)
                stat["zero_history_targets"] += not times
            if (rank+1) % 10000 == 0:
                print(f"cache {split} roots={rank+1}", flush=True)
                guard()
    finally:
        for xf, ef in streams.values():
            xf.close()
            ef.close()
    manifests = {}
    for scale, count in counts.items():
        files, statistics = {}, {}
        for split in all_ids:
            name = "cap5-full-"+split
            folder = destination / scale / name
            no, eo = offsets[(scale,split)]
            expected_count = count if split == "train" else len(inner)
            if len(no) != expected_count+1:
                raise ValueError("incomplete graph cache")
            np.savez(folder / "offsets.npz", nodes=np.asarray(no,dtype=np.int64), edges=np.asarray(eo,dtype=np.int64))
            files[name] = {p.name: binding(p) for p in folder.iterdir()}
            statistics[name] = stats[(scale,split)]
        result = {"schema_version": "stage09-5b-packed-v1", "feature_names": encoders[scale].feature_names,
                  "encoder": asdict(encoders[scale]), "target_ids": {"train": all_ids["train"][:count], "validation": all_ids["validation"]},
                  "caps": [5], "labels_in_cache": False, "files": files, "statistics": statistics,
                  "encoder_fit_ids_sha256": _content_hash({"ids": list(all_ids["train"][:count])}),
                  "history_rule": "strictly_earlier_equal_times_simultaneous", "scale_requested": scale,
                  "source": binding(projected)}
        # Normalize tuples before sealing for identical JSON round trips.
        import json
        result = sealed(json.loads(json.dumps(result)))
        write_json(destination / scale / "manifest.json", result)
        manifests[scale] = result["content_sha256"]
    return manifests
