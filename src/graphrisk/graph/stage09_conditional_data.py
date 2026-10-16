"""Owned batches, train-only core normalization and strict local rewiring."""
from pathlib import Path
import hashlib

import numpy as np
import torch
from torch_geometric.data import Batch

from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.graph.stage09_expansion_data import BoundedPackedGraphs
from graphrisk.graph.stage09_packed import transform_edges
from graphrisk.models.enhanced_data import batches
from graphrisk.features.wide_preprocessing import WidePreprocessor


def close_map(array):
    if isinstance(array, np.memmap):
        array._mmap.close()


def fit_normalizer(path, columns, clip, guard=lambda: None):
    """Population moments from train only, using stable chunk-wise merging."""
    if not columns or len(set(columns)) != len(columns) or not np.isfinite(clip) or clip <= 0:
        raise ValueError("unique core columns and finite positive clip required")
    values = np.load(path, mmap_mode="r", allow_pickle=False)
    try:
        if values.ndim != 2 or values.shape[1] != len(columns) or not len(values):
            raise ValueError("core train matrix/columns mismatch")
        count, mean, m2 = 0, np.zeros(len(columns)), np.zeros(len(columns))
        for start in range(0, len(values), 4096):
            guard()
            chunk = np.asarray(values[start:start+4096], dtype=np.float64)
            if not np.isfinite(chunk).all():
                raise ValueError("nonfinite core training inputs")
            n, center = len(chunk), chunk.mean(axis=0)
            spread = ((chunk-center)**2).sum(axis=0)
            delta = center-mean
            combined = count+n
            m2 += spread+delta**2*count*n/combined
            mean += delta*n/combined
            count = combined
        scale = np.sqrt(m2/count)
        scale[scale < 1e-12] = 1.
        if not np.isfinite(scale).all() or not np.isfinite(mean).all():
            raise ValueError("invalid core normalizer moments")
        return {"columns": list(columns), "mean": mean.tolist(), "scale": scale.tolist(),
                "train_rows": count, "clip": float(clip), "labels_used": False,
                "fit_scope": "fold-1-train-only", "variance": "population"}
    finally:
        close_map(values)


def normalized(values, state):
    if values.ndim != 2 or values.shape[1] != len(state["columns"]) or not np.isfinite(values).all():
        raise ValueError("finite aligned core input required")
    return np.clip((values.astype(np.float64)-state["mean"])/state["scale"],
                   -state["clip"], state["clip"]).astype(np.float32)


def neural_columns(state):
    columns = [*state["numeric"], *["missing:"+n for n in state["numeric"]]]
    for name, vocabulary in state["categorical"].items():
        columns += ["category:"+name+":missing", "category:"+name+":unknown"]
        columns += (["category:"+name+":known:"+v for v in vocabulary] if len(vocabulary) <= 8
                    else ["category:"+name+":signed-hash8:"+str(i) for i in range(8)])
    return columns


def neural_core(frame, state):
    """No artificial ordinal category distances or huge dense one-hot matrix."""
    if list(frame.columns) != state["output_columns"]:
        raise ValueError("frozen core output order changed")
    names = [*state["numeric"], *["missing:"+n for n in state["numeric"]]]
    pieces = [frame[names].to_numpy(dtype=np.float32)]
    for name, vocabulary in state["categorical"].items():
        categories = ["__missing__", "__unknown__", *["v:"+v for v in vocabulary]]
        if list(frame[name].cat.categories) != categories:
            raise ValueError("neural category vocabulary/order drift:"+name)
        codes = frame[name].cat.codes.to_numpy()
        if (codes < 0).any():
            raise ValueError("neural category must retain explicit missing/unknown token")
        width = len(vocabulary) if len(vocabulary) <= 8 else 8
        bucket, signs = [0, 1], [1., 1.]
        for index, token in enumerate(vocabulary):
            digest = hashlib.sha256((name+":"+token).encode("utf-8")).digest()
            bucket.append(2+(index if len(vocabulary) <= 8 else int.from_bytes(digest[:8], "little") % 8))
            signs.append(1. if len(vocabulary) <= 8 or digest[8] % 2 == 0 else -1.)
        block = np.zeros((len(frame), width+2), dtype=np.float32)
        block[np.arange(len(frame)), np.asarray(bucket)[codes]] = np.asarray(signs)[codes]
        pieces.append(block)
    values = np.concatenate(pieces, axis=1)
    if not np.isfinite(values).all():
        raise ValueError("nonfinite frozen core neural transformation")
    return values


def prepare_core(db, root, tabular, side, train, inner, destination, clip, guard=lambda: None):
    preprocessor = WidePreprocessor.load(tabular / "fold-1-core.json")
    columns = neural_columns(preprocessor.state)
    for split, targets in (("train", train), ("inner", inner)):
        path = destination / ("core-raw-"+split+".npy")
        matrix = np.lib.format.open_memmap(path, mode="w+", dtype=np.float32,
                                          shape=(len(targets), len(columns)))
        offset = 0
        try:
            for frame, _ in batches(db, root, tabular, side, "core", targets, split, guard):
                value = neural_core(frame, preprocessor.state)
                if not np.isfinite(value).all():
                    raise ValueError("nonfinite frozen core transformation")
                matrix[offset:offset+len(value)] = value
                offset += len(value)
            if offset != len(targets):
                raise ValueError("incomplete core matrix")
            matrix.flush()
        finally:
            close_map(matrix)
    state = fit_normalizer(destination / "core-raw-train.npy", columns, clip, guard)
    write_json(destination / "core-normalizer.json", state)
    for split in ("train", "inner"):
        raw = np.load(destination / ("core-raw-"+split+".npy"), mmap_mode="r", allow_pickle=False)
        result = np.lib.format.open_memmap(destination / ("core-"+split+".npy"),
                    mode="w+", dtype=np.float32, shape=raw.shape)
        try:
            for start in range(0, len(raw), 4096):
                guard()
                result[start:start+4096] = normalized(np.asarray(raw[start:start+4096]), state)
            result.flush()
        finally:
            close_map(raw)
            close_map(result)
    return state


def audit_rewiring(x, original, transformed):
    """No new nodes/times; preserve types, root relations and per-node degrees."""
    kinds = np.argmax(x[:, -5:], axis=1)
    if original.shape != transformed.shape or not np.array_equal(
            np.bincount(original[:, 0], minlength=len(x)),
            np.bincount(transformed[:, 0], minlength=len(x))):
        raise ValueError("rewiring changed edge count or per-node degree")
    if len(np.unique(transformed, axis=0)) != len(transformed):
        raise ValueError("rewiring has duplicate edges")
    before = original[(original[:, 0] == 0) | (original[:, 1] == 0)]
    after = transformed[(transformed[:, 0] == 0) | (transformed[:, 1] == 0)]
    if not np.array_equal(before, after):
        raise ValueError("rewiring changed root relations")
    if len(transformed) and ((transformed < 0).any() or (transformed >= len(x)).any()):
        raise ValueError("rewiring endpoint outside original past-node set")
    if len(transformed) and not np.all((kinds[transformed[:, 0]] == 0) != (kinds[transformed[:, 1]] == 0)):
        raise ValueError("rewiring changed bipartite node-type incidence")
    for kind in range(1, 5):
        a = original[kinds[original[:, 1]] == kind, 0]
        b = transformed[kinds[transformed[:, 1]] == kind, 0]
        if not np.array_equal(np.sort(a), np.sort(b)):
            raise ValueError("rewiring changed transaction relation types")


def prepare_rewired(folder, manifest, destination, seed, guard=lambda: None):
    destination.mkdir(exist_ok=False)
    statistics = {}
    for split in ("train", "validation"):
        data = BoundedPackedGraphs(folder, manifest, 5, split, "full")
        stat = {"targets": len(data.ids), "changed_graphs": 0, "changed_forward_edges": 0,
                "nodes": 0, "message_edges": 0, "no_history_numeric_changed_graphs": 0,
                "degree_type_root_violations": 0, "new_nodes": 0, "label_access": False}
        with (destination / (split+"-edges.i64")).open("xb") as stream:
            for start in range(0, len(data.ids), 256):
                guard()
                positions = list(range(start, min(start+256, len(data.ids))))
                batch = data.batch(positions)
                x, edges = batch.x.numpy(), batch.edge_index.t().numpy()
                roots = [*batch.root_index.tolist(), len(x)]
                edge_start = 0
                for j, position in enumerate(positions):
                    xx = x[roots[j]:roots[j+1]]
                    count = int(data.packed.eo[position+1]-data.packed.eo[position])
                    original = edges[edge_start:edge_start+count]-roots[j]
                    edge_start += count
                    transformed, changed = transform_edges(original, np.argmax(xx[:, -5:], axis=1), 0,
                                                           data.ids[position], seed)
                    audit_rewiring(xx, original, transformed)
                    stream.write(transformed.tobytes())
                    stat["changed_graphs"] += changed > 0
                    stat["changed_forward_edges"] += changed
                    stat["nodes"] += len(xx)
                    stat["message_edges"] += count
                    history = (xx[:, -5] == 1)
                    history[0] = False
                    stat["no_history_numeric_changed_graphs"] += bool(np.any(xx[history, :18] != 0))
                if edge_start != len(edges):
                    raise ValueError("rewiring batch edge offsets inconsistent")
                if start % 25600 == 0:
                    print(f"rewiring audit {split} {start+len(positions)}/{len(data.ids)} changed={stat['changed_graphs']}", flush=True)
        stat["changed_graph_fraction"] = stat["changed_graphs"]/len(data.ids)
        statistics[split] = stat
    write_json(destination / "audit.json", statistics)
    return statistics


class ConditionalGraphs:
    def __init__(self, folder, manifest, split, variant, rewired=None, core=None):
        self.core_matrix, self.closed, self.variant = None, False, variant
        if variant not in {"full", "no_edges", "no_history_numeric", "rewired"}:
            raise ValueError("unregistered conditional graph transformation")
        self.base = BoundedPackedGraphs(folder, manifest, 5, split,
                                       "full" if variant == "rewired" else variant)
        self.ids, self.dimension, self.core = self.base.ids, self.base.dimension, core
        if variant == "rewired":
            if rewired is None or Path(rewired).stat().st_size != int(np.prod(self.base.edge_shape))*8:
                raise ValueError("rewired file/offset size mismatch")
            self.base.edge_path = Path(rewired)
        if core is not None:
            if Path(core).stat().st_size > 512*2**20:
                raise ValueError("bounded core mapping exceeds 512MiB")
            matrix = np.load(core, mmap_mode="r", allow_pickle=False)
            try:
                if matrix.ndim != 2 or len(matrix) != len(self.ids) or matrix.dtype != np.float32:
                    raise ValueError("core matrix/graph ID count or dtype mismatch")
                self.dimension += matrix.shape[1]
            except BaseException:
                close_map(matrix)
                raise
            self.core_matrix = matrix

    def close(self):
        close_map(self.core_matrix)
        self.closed = True

    def __del__(self):
        close_map(getattr(self, "core_matrix", None))

    def no_edges_batch(self, positions):
        if not positions or any(i < 0 or i >= len(self.ids) for i in positions):
            raise ValueError("packed batch positions invalid")
        matrix = np.memmap(self.base.x_path, dtype=np.float32, mode="r", shape=self.base.x_shape)
        xs, memberships, roots, cursor = [], [], [], 0
        try:
            for index, position in enumerate(positions):
                x = np.array(matrix[self.base.packed.no[position]:self.base.packed.no[position+1]], copy=True)
                xs.append(x)
                roots.append(cursor)
                memberships.append(np.full(len(x), index, dtype=np.int64))
                cursor += len(x)
            batch = Batch(x=torch.from_numpy(np.concatenate(xs)), edge_index=torch.empty((2, 0), dtype=torch.long),
                          batch=torch.from_numpy(np.concatenate(memberships)), root_index=torch.tensor(roots, dtype=torch.long))
            batch._num_graphs = len(positions)
            return batch
        finally:
            close_map(matrix)

    def batch(self, positions):
        if self.closed:
            raise ValueError("conditional reader is closed")
        positions = list(positions)
        batch = self.no_edges_batch(positions) if self.variant == "no_edges" else self.base.batch(positions)
        if self.core is not None:
            values = np.array(self.core_matrix[positions], copy=True)
            if not np.isfinite(values).all():
                raise ValueError("nonfinite normalized core roots")
            combined = np.zeros((len(batch.x), self.dimension), dtype=np.float32)
            combined[:, :self.base.dimension] = batch.x.numpy()
            combined[batch.root_index.numpy(), self.base.dimension:] = values
            batch.x = torch.from_numpy(combined)
        return batch
