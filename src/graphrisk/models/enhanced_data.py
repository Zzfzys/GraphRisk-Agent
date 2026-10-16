"""Label projection is limited to the registered train and inner early-stop IDs."""
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from graphrisk.agent.stage09_preflight import _content_hash
from graphrisk.demo.stage09_evaluation import read_json, write_json
from graphrisk.features.wide_contract import quote
from graphrisk.features.wide_pipeline import fit_fold_profiles
from graphrisk.features.wide_preprocessing import WidePreprocessor
from graphrisk.graph.schema import GraphFeatureEncoder, NUMERIC_FEATURES, NODE_TYPES
from graphrisk.models.tabular import FEATURE_COLUMNS, CATEGORICAL_FEATURES


def targets(db, canonical: Path, window):
    # No calibration/outer/test outcome is projected or evaluated.
    frame = db.execute("SELECT transaction_id, source_transaction_dt_seconds, is_fraud FROM read_parquet(?) WHERE source_transaction_dt_seconds BETWEEN ? AND ? ORDER BY source_transaction_dt_seconds, transaction_id",
        [str(canonical), window["min_relative_seconds"], window["max_relative_seconds"]]).fetchdf()
    ids = frame.transaction_id.tolist()
    if len(ids) != window["count"] or len(set(ids)) != len(ids) or _content_hash({"ids": ids}) != window["ids_sha256"]:
        raise ValueError("label IDs differ from registered chronological window")
    if frame.is_fraud.isna().any() or not frame.is_fraud.isin([0, 1]).all() or frame.is_fraud.nunique() != 2:
        raise ValueError("registered training/early-stop labels must contain both binary classes")
    return frame


def legacy_fields():
    return [{"name": n, "dtype": "numeric" if n in NUMERIC_FEATURES else "categorical", "source": "legacy_compact", "group": "core"} for n in FEATURE_COLUMNS]


def compact_view(db):
    placeholders = {"card_id": "unknown-card-", "device_id": "unknown-device-", "email_domain": "unknown-email-", "address_id": "unknown-address-"}
    projection = [f"CASE WHEN f.{quote(n)}=concat('{placeholders[n]}',f.transaction_id) THEN NULL ELSE f.{quote(n)} END AS {quote(n)}" if n in placeholders else "f."+quote(n) for n in FEATURE_COLUMNS]
    # Missing marker is a model input, never a shared graph entity/relation.
    db.execute("CREATE VIEW compact AS SELECT f.transaction_id, c.source_transaction_dt_seconds," + ",".join(projection) + " FROM compact_source f JOIN canonical_source c USING(transaction_id)")


def root_encoder(frame, window):
    values = frame.loc[:, list(NUMERIC_FEATURES)].to_numpy(dtype=float, na_value=np.nan, copy=True)
    medians = np.nanmedian(values, axis=0)
    medians = np.where(np.isnan(medians), 0., medians)
    values = np.where(np.isnan(values), medians, values)
    if not np.isfinite(values).all():
        raise ValueError("nonfinite root inputs")
    scales = values.std(axis=0)
    scales[scales == 0] = 1.
    encoder = GraphFeatureEncoder(tuple(medians), tuple(values.mean(axis=0)), tuple(scales), tuple(sorted(frame.product_code.unique())))
    if len(encoder.feature_names) != 28:
        raise ValueError("initial GraphSAGE root input must remain 28 dimensions")
    return encoder


def root_values(frame, encoder):
    raw = frame.loc[:, list(NUMERIC_FEATURES)].to_numpy(dtype=float, na_value=np.nan, copy=True)
    missing = np.isnan(raw)
    numeric = (np.where(missing, np.asarray(encoder.medians), raw)-np.asarray(encoder.means)) / np.asarray(encoder.scales)
    products = np.column_stack([(frame.product_code == code).to_numpy() for code in encoder.product_codes])
    types = np.zeros((len(frame), len(NODE_TYPES)))
    types[:, 0] = 1.
    result = np.column_stack([numeric, missing, products, types]).astype(np.float32)
    if not np.isfinite(result).all():
        raise ValueError("nonfinite encoded roots")
    return result


def prepare_legacy(db, root, artifact, train, inner, guard):
    compact = root / "data/interim/ieee-cis/features_all.parquet"
    db.from_parquet(str(compact)).create_view("compact_source")
    db.from_parquet(str(root / "data/interim/ieee-cis/transactions.parquet")).create_view("canonical_source")
    compact_view(db)
    # fit_fold_profiles verifies train ID hash before any distribution fitting.
    db.execute("CREATE VIEW side AS SELECT * FROM compact")
    legacy = fit_fold_profiles(db, legacy_fields(), train, ["core"], artifact, guard)["core"]
    (artifact / "fold-1-core.json").rename(artifact / "legacy14.json")
    (artifact / "field-catalog.json").rename(artifact / "legacy14-catalog.json")
    db.execute("DROP VIEW side")
    frame = db.execute("SELECT " + ",".join(quote(n) for n in FEATURE_COLUMNS) + " FROM compact WHERE source_transaction_dt_seconds BETWEEN ? AND ? ORDER BY source_transaction_dt_seconds, transaction_id", [train["min_relative_seconds"], train["max_relative_seconds"]]).fetchdf()
    encoder = root_encoder(frame, train)
    write_json(artifact / "root-encoder.json", {"encoder": asdict(encoder), "feature_names": list(encoder.feature_names), "fit_scope": train, "labels_used": False})
    write_json(artifact / "root-windows.json", {"train": train, "inner": inner})
    del frame
    for name, window in (("train", train), ("inner", inner)):
        ids = [r[0] for r in db.execute("SELECT transaction_id FROM compact WHERE source_transaction_dt_seconds BETWEEN ? AND ? ORDER BY source_transaction_dt_seconds, transaction_id", [window["min_relative_seconds"], window["max_relative_seconds"]]).fetchall()]
        if len(ids) != window["count"] or _content_hash({"ids": ids}) != window["ids_sha256"]:
            raise ValueError("root matrix chronological ID alignment changed")
        matrix = np.lib.format.open_memmap(artifact / ("root-"+name+".npy"), mode="w+", dtype=np.float32, shape=(window["count"], 28))
        reader = db.execute("SELECT " + ",".join(quote(n) for n in FEATURE_COLUMNS) + " FROM compact WHERE source_transaction_dt_seconds BETWEEN ? AND ? ORDER BY source_transaction_dt_seconds, transaction_id", [window["min_relative_seconds"], window["max_relative_seconds"]])
        offset = 0
        while rows := reader.fetchmany(4096):
            guard()
            values = root_values(pd.DataFrame(rows, columns=FEATURE_COLUMNS), encoder)
            matrix[offset:offset+len(values)] = values
            offset += len(values)
        if offset != window["count"]:
            raise ValueError("root row alignment changed")
        matrix.flush()
        del matrix
    return {"legacy_input_columns": legacy["input_columns"], "legacy_output_columns": legacy["output_columns"], "root_dimensions": 28}


class MatrixIterator(xgb.DataIter):
    def __init__(self, factory, expected_rows, guard=lambda: None):
        super().__init__(release_data=True)
        self.factory, self.expected_rows, self.guard = factory, expected_rows, guard
        self.reader = None
        self.count = 0

    def reset(self):
        self.reader = iter(self.factory())
        self.count = 0

    def next(self, input_data):
        self.guard()
        item = next(self.reader, None)
        if item is None:
            if self.count != self.expected_rows:
                raise ValueError("matrix iterator row count differs")
            return False
        data, labels = item
        self.count += len(labels)
        input_data(data=data, label=labels)
        return True


def batches(db, root, artifact, side_path, profile, targets_frame, split, guard=lambda: None):
    ids, labels = targets_frame.transaction_id.tolist(), targets_frame.is_fraud.to_numpy(dtype=np.float32)
    order = list(zip(targets_frame.source_transaction_dt_seconds.tolist(), ids))
    if not ids or len(set(ids)) != len(ids) or order != sorted(order):
        raise ValueError("feature/label chronological ID order mismatch")
    if profile == "root28":
        if split not in {"train", "inner"}:
            raise ValueError("explicit root split required")
        name = split
        expected = read_json(artifact / "root-windows.json")[split]
        if _content_hash({"ids": ids}) != expected["ids_sha256"]:
            raise ValueError("root matrix/label ID order mismatch")
        # Explicit row hash matches these matrices in the frozen preparation manifest.
        values = np.load(artifact / ("root-"+name+".npy"), mmap_mode="r")
        if len(values) != len(ids):
            raise ValueError("root matrix/label row mismatch")
        for start in range(0, len(ids), 4096):
            guard()
            yield pd.DataFrame(np.array(values[start:start+4096]), columns=["root_"+str(i) for i in range(28)]), labels[start:start+4096]
        return
    preprocessor = WidePreprocessor.load(artifact / ("legacy14.json" if profile == "legacy14" else "fold-1-"+profile+".json"))
    columns = preprocessor.state["input_columns"]
    table = "compact" if profile == "legacy14" else "wide"
    begin, end = int(targets_frame.source_transaction_dt_seconds.iloc[0]), int(targets_frame.source_transaction_dt_seconds.iloc[-1])
    reader = db.execute("SELECT transaction_id," + ",".join(quote(n) for n in columns) + " FROM "+table+" WHERE source_transaction_dt_seconds BETWEEN ? AND ? ORDER BY source_transaction_dt_seconds, transaction_id", [begin, end])
    offset = 0
    while rows := reader.fetchmany(4096):
        guard()
        if [r[0] for r in rows] != ids[offset:offset+len(rows)]:
            raise ValueError("feature/label ID order mismatch")
        frame = pd.DataFrame([r[1:] for r in rows], columns=columns)
        yield preprocessor.transform(frame), labels[offset:offset+len(rows)]
        offset += len(rows)
    if offset != len(ids):
        raise ValueError("feature/label row count mismatch")
