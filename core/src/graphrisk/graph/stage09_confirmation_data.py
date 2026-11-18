"""Fold-local preprocessing and strictly temporal, label-free confirmation graphs."""
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from graphrisk.agent.stage09_preflight import _content_hash
from graphrisk.demo.stage09_evaluation import read_json, write_json
from graphrisk.features.wide_pipeline import fit_fold_profiles
from graphrisk.features.wide_preprocessing import WidePreprocessor
from graphrisk.graph.schema import GraphFeatureEncoder, NUMERIC_FEATURES
from graphrisk.graph.stage09_conditional_data import close_map, fit_normalizer, neural_columns, neural_core, normalized
from graphrisk.graph.stage09_expansion_data import encode_graph, project_source
from graphrisk.graph.stage09_packed import future_entity_cutoffs, stream_graphs
from graphrisk.models.enhanced_data import batches, root_encoder, targets
from graphrisk.models.enhanced_execution import binding, sealed


def identities(db, canonical, window):
    frame = db.execute("SELECT transaction_id,source_transaction_dt_seconds FROM read_parquet(?) WHERE source_transaction_dt_seconds BETWEEN ? AND ? ORDER BY source_transaction_dt_seconds,transaction_id",
        [str(canonical), window["min_relative_seconds"], window["max_relative_seconds"]]).fetchdf()
    ids = frame.transaction_id.tolist()
    if len(ids) != window["count"] or len(set(ids)) != len(ids) or _content_hash({"ids": ids}) != window["ids_sha256"]:
        raise ValueError("confirmation ID/time window mismatch")
    return frame


def core_matrices(db, root, side, directory, frames, fold, guard):
    preprocessor = WidePreprocessor.load(directory / "fold-1-core.json")
    columns = neural_columns(preprocessor.state)
    for split, frame in frames.items():
        # Prediction-only targets have no outcome column. Dummy zeros satisfy the
        # existing iterator interface; they are never persisted as target labels.
        adapted = frame if "is_fraud" in frame else frame.assign(is_fraud=0)
        matrix = np.lib.format.open_memmap(directory / ("core-raw-"+split+".npy"), mode="w+",
            dtype=np.float32, shape=(len(frame), len(columns)))
        offset = 0
        try:
            for transformed, _ in batches(db, root, directory, side, "core", adapted, split, guard):
                values = neural_core(transformed, preprocessor.state)
                matrix[offset:offset+len(values)] = values
                offset += len(values)
            if offset != len(frame):
                raise ValueError("confirmation core rows incomplete")
            matrix.flush()
        finally:
            close_map(matrix)
    state = fit_normalizer(directory / "core-raw-train.npy", columns, 10., guard)
    state["fit_scope"] = fold+"-train-only"
    write_json(directory / "core-normalizer.json", sealed(state))
    for split in frames:
        raw = np.load(directory / ("core-raw-"+split+".npy"), mmap_mode="r", allow_pickle=False)
        output = np.lib.format.open_memmap(directory / ("core-"+split+".npy"), mode="w+", dtype=np.float32, shape=raw.shape)
        try:
            for begin in range(0, len(raw), 4096):
                guard()
                output[begin:begin+4096] = normalized(np.asarray(raw[begin:begin+4096]), state)
            output.flush()
        finally:
            close_map(raw)
            close_map(output)
    return state


def temporal_cache(projected, directory, frames, encoder, guard):
    directory.mkdir(parents=True, exist_ok=False)
    # The existing packed reader uses "validation" for the early-stop segment.
    ids = {("validation" if split == "inner" else split): tuple(frame.transaction_id)
           for split, frame in frames.items()}
    streams, offsets, stats = {}, {}, {}
    first_inner = (pd.to_datetime(frames["inner"].source_transaction_dt_seconds.iloc[0],
                                unit="s", origin="2000-01-01", utc=True) if "inner" in frames else None)
    for split in ids:
        path = directory / ("cap5-full-"+split)
        path.mkdir()
        streams[split] = ((path / "x.f32").open("xb"), (path / "edges.i64").open("xb"))
        offsets[split] = ([0], [0])
        stats[split] = {"targets": 0, "nodes": 0, "message_edges": 0, "historical_transactions": 0,
                        "zero_history_targets": 0, "time_violations": 0, "train_context_from_inner": 0}
    try:
        cutoffs = future_entity_cutoffs(projected, ids)
        for split, target, _, graph in stream_graphs(projected, ids, (5,), guard, cutoffs):
            history = [n.transaction.transaction_time for n in graph.nodes[1:] if n.transaction is not None]
            if any(time >= target.transaction_time for time in history) or split == "train" and (first_inner is None or any(time >= first_inner for time in history)):
                raise ValueError("future/simultaneous or inner relationship in training graph")
            x = encode_graph(graph, encoder)
            edges = np.asarray(sorted([*graph.edges, *((b, a) for a, b in graph.edges)]), dtype=np.int64).reshape(-1, 2)
            streams[split][0].write(x.tobytes())
            streams[split][1].write(edges.tobytes())
            no, eo = offsets[split]
            no.append(no[-1]+len(x))
            eo.append(eo[-1]+len(edges))
            stat = stats[split]
            stat["targets"] += 1
            stat["nodes"] += len(x)
            stat["message_edges"] += len(edges)
            stat["historical_transactions"] += len(history)
            stat["zero_history_targets"] += not history
            if stat["targets"] % 25000 == 0:
                print(f"confirmation cache {split} roots={stat['targets']}", flush=True)
                guard()
    finally:
        for xf, ef in streams.values():
            xf.close()
            ef.close()
    files = {}
    for split in ids:
        if stats[split]["targets"] != len(ids[split]):
            raise ValueError("confirmation graph targets incomplete")
        path = directory / ("cap5-full-"+split)
        no, eo = offsets[split]
        np.savez(path / "offsets.npz", nodes=np.asarray(no, dtype=np.int64), edges=np.asarray(eo, dtype=np.int64))
        files[path.name] = {p.name: binding(p) for p in path.iterdir()}
    manifest = sealed({"schema_version": "stage09-5b-confirmation-cache-v1", "target_ids": {k: list(v) for k,v in ids.items()},
        "feature_names": list(encoder.feature_names), "encoder": asdict(encoder), "caps": [5],
        "labels_in_cache": False, "history": "strictly_earlier_equal_times_simultaneous",
        "statistics": stats, "files": files, "source": binding(projected)})
    write_json(directory / "manifest.json", manifest)
    return manifest


def prepare_fold_data(db, root, side, directory, window, fields, guard=lambda: None):
    canonical = root / "data/interim/ieee-cis/transactions.parquet"
    frames = {"train": targets(db, canonical, window["train"]),
              "inner": targets(db, canonical, window["early_stop"]),
              "calibration": identities(db, canonical, window["calibration"]),
              "outer": identities(db, canonical, window["outer"])}
    for split, frame in frames.items():
        frame.to_parquet(directory / ("targets-"+split+".parquet"), index=False)
    db.execute("CREATE VIEW side AS SELECT * FROM wide")
    try:
        profiles = fit_fold_profiles(db, fields, window["train"], ["core", "source_as_received"], directory, guard)
    finally:
        db.execute("DROP VIEW side")
    catalog = read_json(directory / "field-catalog.json")
    for field in catalog:
        field["fit_scope"] = window["name"]+"-train-only"
    write_json(directory / "field-catalog.json", catalog)
    # Legacy filenames are iterator compatibility only; the actual fit_scope,
    # ID hashes, directory and all moments are specific to this window.
    for profile in profiles:
        state = WidePreprocessor.load(directory / ("fold-1-"+profile+".json")).state
        if state["fit_scope"] != window["train"]:
            raise ValueError("fold-local preprocessing scope drift")
    frame = db.execute("SELECT "+",".join(NUMERIC_FEATURES)+",product_code FROM compact_source WHERE transaction_id IN (SELECT transaction_id FROM canonical_source WHERE source_transaction_dt_seconds BETWEEN ? AND ?) ORDER BY transaction_time,transaction_id",
        [window["train"]["min_relative_seconds"], window["train"]["max_relative_seconds"]]).fetchdf()
    if len(frame) != len(frames["train"]):
        raise ValueError("graph encoder training row mismatch")
    encoder = root_encoder(frame, window["train"])
    del frame
    write_json(directory / "root-encoder.json", sealed({"encoder": asdict(encoder), "feature_names": list(encoder.feature_names),
        "fit_scope": window["train"], "labels_used": False}))
    core = core_matrices(db, root, side, directory, frames, window["name"], guard)
    project_source(db, root, directory / "projected.parquet", window["outer"]["max_relative_seconds"])
    cache = temporal_cache(directory / "projected.parquet", directory / "cache", frames, encoder, guard)
    return {"window": window, "profiles": profiles, "input_dimension": 28+len(core["columns"]),
        "normalizer_sha256": binding(directory / "core-normalizer.json")["sha256"],
        "cache_sha256": cache["content_sha256"], "files": {p.name: binding(p) for p in directory.iterdir() if p.is_file()},
        "calibration_outer_labels_projected": False, "labels_used_for_features": False}


def prepare_prediction_data(db, root, side, directory, trained, test_window, guard=lambda: None):
    """Apply the last fold's frozen encoder/moments; no new fit or test outcome."""
    canonical = root / "data/interim/ieee-cis/transactions.parquet"
    frame = identities(db, canonical, test_window)
    frame.to_parquet(directory / "targets-test.parquet", index=False)
    preprocessor = WidePreprocessor.load(trained / "fold-1-core.json")
    from graphrisk.models.enhanced_execution import checked_json
    state = checked_json(trained / "core-normalizer.json")
    columns = neural_columns(preprocessor.state)
    if columns != state["columns"]:
        raise ValueError("historical-test neural input schema mismatch")
    output = np.lib.format.open_memmap(directory / "core-test.npy", mode="w+", dtype=np.float32,
                                      shape=(len(frame), len(columns)))
    offset = 0
    try:
        for transformed, _ in batches(db, root, trained, side, "core", frame.assign(is_fraud=0), "test", guard):
            values = normalized(neural_core(transformed, preprocessor.state), state)
            output[offset:offset+len(values)] = values
            offset += len(values)
        if offset != len(frame):
            raise ValueError("historical-test core transformation incomplete")
        output.flush()
    finally:
        close_map(output)
    encoder_value = checked_json(trained / "root-encoder.json")["encoder"]
    encoder = GraphFeatureEncoder(tuple(encoder_value["medians"]), tuple(encoder_value["means"]),
                                  tuple(encoder_value["scales"]), tuple(encoder_value["product_codes"]))
    project_source(db, root, directory / "projected.parquet", test_window["max_relative_seconds"])
    cache = temporal_cache(directory / "projected.parquet", directory / "cache", {"test": frame}, encoder, guard)
    return {"window": test_window, "trained_preprocessor": binding(trained / "fold-1-core.json"),
            "normalizer": binding(trained / "core-normalizer.json"), "cache_sha256": cache["content_sha256"],
            "files": {p.name: binding(p) for p in directory.iterdir() if p.is_file()}, "test_labels_projected": False}
