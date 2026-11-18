"""Freeze historical fold-3 category mapping and audit day matrix reproduction.

This is a bounded offline audit, not a new-data feature service.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import threading
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import psutil
import pyarrow.parquet as pq

from graphrisk.models.champion_features import source_columns, training_category_maps, transform_batch

ROUTE = Path(r"E:\GraphRisk-Agent\reports\generated\agent-investigation-upgrade\route6")
STUDY = ROUTE / "20261102-champion-216-xgb-v1"
SIDE = Path(r"E:\GraphRisk-Agent\artifacts\stage-09\route5b\20261009-features-v1\features.parquet")
DERIVED = ROUTE / "20261102-champion-association-features-v2" / "day-features.parquet"
OUT = ROUTE / "20261105-champion-feature-freeze-v1"
PART = STUDY / "fold-3" / "day"
CREATED = False


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def guard():
    available = psutil.virtual_memory().available
    if available < 2 * 1024**3:
        raise RuntimeError(f"available RAM below 2 GiB: {available / 1024**3:.3f} GiB")
    for drive, minimum in (("C", 5), ("D", 5), ("E", 10)):
        if shutil.disk_usage(drive + ":/").free < minimum * 1024**3:
            raise RuntimeError(f"{drive}: disk space below {minimum} GiB")


def write_new(path, obj):
    with path.open("x", encoding="utf-8") as file:
        json.dump(obj, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")


def main():
    global CREATED
    guard()
    if OUT.exists():
        raise FileExistsError(f"audit already exists: {OUT}")
    OUT.mkdir(parents=True)
    CREATED = True
    contract = json.loads((STUDY / "protocol.json").read_text(encoding="utf-8"))
    prepared = json.loads((PART / "prepared.json").read_text(encoding="utf-8"))
    if digest(STUDY / "protocol.json") != prepared["protocol_sha256"]:
        raise ValueError("prepared data do not match the study protocol")
    for path in (SIDE, DERIVED):
        if digest(path) != contract["files"][str(path)]:
            raise ValueError(f"frozen study source hash mismatch: {path}")
    for name in ("test.parquet", "test-ids.parquet"):
        if digest(PART / name) != prepared["files"][name]:
            raise ValueError(f"frozen prepared input hash mismatch: {name}")
    db = duckdb.connect()
    other = duckdb.connect()
    for connection in (db, other):
        connection.execute("SET threads=1")
        connection.execute("SET memory_limit='192MB'")
    stop = threading.Event()
    errors = []
    samples = []

    def monitor():
        while not stop.wait(0.5):
            samples.append(psutil.virtual_memory().available)
            try:
                guard()
            except Exception as exc:
                errors.append(str(exc))
                db.interrupt()
                other.interrupt()
                return

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        maps = training_category_maps(db, SIDE, contract["categorical"], prepared["training_cutoff"], guard)
        if {key: len(value) for key, value in maps.items()} != prepared["category_sizes"]:
            raise ValueError("reconstructed category sizes differ from training")
        write_new(OUT / "category-maps.json", maps)
        print("Training category maps reconstructed; verifying historical test matrix", flush=True)
        ids = pq.ParquetFile(PART / "test-ids.parquet")
        matrix = pq.ParquetFile(PART / "test.parquet")
        headers = ["transaction_id", "source_transaction_dt_seconds", *contract["raw"],
                   "card1_addr1", "card1_addr1_P_emaildomain"]
        fields = list(dict.fromkeys([*contract["base_mapping"]["day"].values(), *contract["uid_extra"]["day"]]))
        derived_headers = ["transaction_id", "source_transaction_dt_seconds", *fields]
        count = 0
        feature_columns = [name for name in matrix.schema_arrow.names if name != "__label__"]
        for ids_batch, matrix_batch in zip(ids.iter_batches(batch_size=2048), matrix.iter_batches(batch_size=2048, columns=feature_columns), strict=True):
            guard()
            target = ids_batch.to_pandas()
            low, high = int(target.source_transaction_dt_seconds.iloc[0]), int(target.source_transaction_dt_seconds.iloc[-1])
            # Time ties may span batches, so select the ID interval as well and retain exact target order.
            source_rows = db.execute(
                'SELECT s.transaction_id,s.source_transaction_dt_seconds,' + source_columns(contract["raw"]) +
                ' FROM read_parquet(?) s WHERE s.source_transaction_dt_seconds BETWEEN ? AND ? ORDER BY s.source_transaction_dt_seconds,s.transaction_id',
                [str(SIDE), low, high],
            ).fetchall()
            derived_rows = other.execute(
                'SELECT transaction_id,source_transaction_dt_seconds,' + ','.join('"' + x + '"' for x in fields) +
                ' FROM read_parquet(?) WHERE source_transaction_dt_seconds BETWEEN ? AND ? ORDER BY source_transaction_dt_seconds,transaction_id',
                [str(DERIVED), low, high],
            ).fetchall()
            source = pd.DataFrame(source_rows, columns=headers).set_index("transaction_id").loc[target.transaction_id].reset_index()
            derived = pd.DataFrame(derived_rows, columns=derived_headers).set_index("transaction_id").loc[target.transaction_id].reset_index()
            actual = transform_batch(source, derived, contract, maps)
            expected = matrix_batch.to_pandas()
            if list(actual.columns) != list(expected.columns) or not np.array_equal(actual.to_numpy(), expected.to_numpy(), equal_nan=True):
                raise ValueError(f"reconstructed matrix differs at offset {count}")
            count += len(actual)
        if count != prepared["counts"]["test"]:
            raise ValueError(f"row count mismatch: {count}")
        write_new(OUT / "audit.json", {
            "study_protocol_sha256": digest(STUDY / "protocol.json"),
            "prepared_sha256": digest(PART / "prepared.json"),
            "raw_source_sha256": digest(SIDE),
            "derived_source_sha256": digest(DERIVED),
            "category_maps_sha256": digest(OUT / "category-maps.json"),
            "historical_matrix_sha256": digest(PART / "test.parquet"),
            "training_cutoff": prepared["training_cutoff"],
            "category_sizes": prepared["category_sizes"],
            "rows_verified": count,
            "columns_verified": 265,
            "exact_float32_and_nan_match": True,
            "minimum_sampled_available_gib": min(samples, default=psutil.virtual_memory().available) / 1024**3,
            "seconds": time.monotonic() - started,
            "limitations": "Historical verified input only. No assertion about arrival time, fresh source, or causal status of supplied aggregates.",
        })
        print(f"Matched {count} rows and 265 columns exactly", flush=True)
    finally:
        stop.set()
        thread.join()
        db.close()
        other.close()
    if errors:
        raise RuntimeError("resource watchdog: " + errors[0])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        if CREATED and not (OUT / "failure.json").exists():
            write_new(OUT / "failure.json", {"type": type(exc).__name__, "error": str(exc)})
        raise
