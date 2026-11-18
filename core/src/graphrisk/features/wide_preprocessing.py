"""Train-only, reloadable numeric and native categorical preprocessing.

No outcome column is accepted. Numeric zero, numeric missing, missing category
and unseen category remain distinct. Models and labels do not enter this module.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from graphrisk.agent.stage09_preflight import _content_hash
from graphrisk.features.wide_contract import FORBIDDEN, quote


def train_statistics(db, table: str, fields: list[dict], maximum_time: int, batch_size=8):
    """Exact statistics in small column batches; only train rows enter aggregates."""
    result = {}
    count = db.execute(f"SELECT count(*) FROM {quote(table)} WHERE source_transaction_dt_seconds <= ?", [maximum_time]).fetchone()[0]
    if not count:
        raise ValueError("empty preprocessing training period")
    for begin in range(0, len(fields), batch_size):
        batch = fields[begin:begin + batch_size]
        expressions = []
        for field in batch:
            name = quote(field["name"])
            expressions += [f"count({name})", f"count(DISTINCT {name})", f"bit_xor(hash({name}))"]
            if field["dtype"] == "numeric":
                expressions += [f"median({name})", f"avg({name})", f"stddev_pop({name})"]
        values = iter(db.execute(f"SELECT {', '.join(expressions)} FROM {quote(table)} WHERE source_transaction_dt_seconds <= ?", [maximum_time]).fetchone())
        for field in batch:
            nonnull, distinct, signature = next(values), next(values), next(values)
            stats = {"count": count, "missing_count": count - nonnull, "distinct_nonnull": distinct,
                     "signature": str(signature)}
            if field["dtype"] == "numeric":
                stats.update(median=next(values), mean=next(values), std=next(values))
            result[field["name"]] = stats
    # Hash is only a shortlist. Exact equality confirms duplicates, including NULLs.
    groups, duplicates = {}, {}
    for field in fields:
        name, dtype = field["name"], field["dtype"]
        stats = result[name]
        if stats["distinct_nonnull"] == 0 or stats["distinct_nonnull"] == 1 and stats["missing_count"] == 0:
            continue
        key = (dtype, stats["signature"], stats["missing_count"], stats["distinct_nonnull"])
        for previous in groups.get(key, []):
            different = db.execute(f"SELECT count(*) FROM {quote(table)} WHERE source_transaction_dt_seconds <= ? AND {quote(name)} IS DISTINCT FROM {quote(previous)}", [maximum_time]).fetchone()[0]
            if not different:
                duplicates[name] = previous
                break
        groups.setdefault(key, []).append(name)
    return result, duplicates


def fit_preprocessor(db, table: str, fields: list[dict], train: dict, profile: str, stats: dict, duplicates: dict):
    if profile not in {"core", "source_as_received"}:
        raise ValueError("unknown feature profile")
    selected = [f for f in fields if profile == "source_as_received" or f["group"] == "core"]
    numeric, categorical, dropped = {}, {}, {}
    kept = set()
    for field in selected:
        name = field["name"]
        if name.casefold() in FORBIDDEN:
            raise ValueError("label/prediction cannot be a preprocessing input")
        value = stats[name]
        if value["distinct_nonnull"] == 0 or value["distinct_nonnull"] == 1 and value["missing_count"] == 0:
            dropped[name] = "train_constant_including_missingness"
        elif duplicates.get(name) in kept:
            dropped[name] = "exact_train_duplicate_of:" + duplicates[name]
        elif field["dtype"] == "numeric":
            observed = value["count"] - value["missing_count"]
            mean = (observed * value["mean"] + value["missing_count"] * value["median"]) / value["count"]
            variance = (observed * ((value["std"] or 0.0)**2 + (value["mean"] - mean)**2)
                + value["missing_count"] * (value["median"] - mean)**2) / value["count"]
            if not all(np.isfinite(v) for v in (mean, variance, value["median"])):
                raise ValueError("nonfinite training preprocessing statistics:" + name)
            numeric[name] = {"median": value["median"], "mean": mean, "scale": variance**0.5 or 1.0}
            kept.add(name)
        else:
            vocabulary = [r[0] for r in db.execute(f"SELECT DISTINCT {quote(name)} FROM {quote(table)} WHERE source_transaction_dt_seconds <= ? AND {quote(name)} IS NOT NULL ORDER BY {quote(name)}", [train["max_relative_seconds"]]).fetchall()]
            categorical[name] = vocabulary
            kept.add(name)
    state = {"schema_version": "stage09-5b-preprocessor-v1", "profile": profile,
        "fit_scope": train, "numeric": numeric, "categorical": categorical, "dropped": dropped,
        "input_columns": [f["name"] for f in selected if f["name"] in kept],
        "output_columns": [*numeric, *["missing:" + n for n in numeric], *categorical],
        "categorical_policy": "native_categorical_train_vocab_distinct_missing_unknown",
        "label_columns_read": []}
    return {**state, "content_sha256": _content_hash(state)}


class WidePreprocessor:
    def __init__(self, state: dict):
        payload = {k: v for k, v in state.items() if k != "content_sha256"}
        if _content_hash(payload) != state.get("content_sha256") or state.get("schema_version") != "stage09-5b-preprocessor-v1":
            raise ValueError("preprocessor schema/content drift")
        if set(state["input_columns"]) != set(state["numeric"]) | set(state["categorical"]) or any(n.casefold() in FORBIDDEN for n in state["input_columns"]):
            raise ValueError("invalid/forbidden preprocessing inputs")
        self.state = state

    @classmethod
    def load(cls, path: Path):
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def transform(self, frame: pd.DataFrame, *, standardize=False) -> pd.DataFrame:
        if set(frame.columns) != set(self.state["input_columns"]) or frame.columns.duplicated().any():
            raise ValueError("preprocessing requires exact feature projection; labels/metadata/extras forbidden")
        output = {}
        missing = {}
        for name, numeric in self.state["numeric"].items():
            try:
                values = pd.to_numeric(frame[name], errors="raise").to_numpy(dtype=float, na_value=np.nan, copy=True)
            except (ValueError, TypeError):
                raise ValueError("invalid numeric feature:" + name) from None
            absent = np.isnan(values)
            if not np.isfinite(values[~absent]).all():
                raise ValueError("nonfinite numeric feature:" + name)
            values[absent] = numeric["median"]
            if standardize:
                values = (values - numeric["mean"]) / numeric["scale"]
            if (np.abs(values) > np.finfo(np.float32).max).any():
                raise ValueError("numeric feature exceeds float32 range:" + name)
            output[name] = values.astype(np.float32)
            missing["missing:" + name] = absent.astype(np.float32)
        output.update(missing)
        for name, vocabulary in self.state["categorical"].items():
            known = set(vocabulary)
            tokens = ["__missing__" if pd.isna(value) else "v:" + str(value) if value in known else "__unknown__" for value in frame[name]]
            output[name] = pd.Categorical(tokens, categories=["__missing__", "__unknown__", *["v:" + v for v in vocabulary]])
        return pd.DataFrame(output, index=frame.index).loc[:, self.state["output_columns"]]


def feature_batches(db, path: Path, preprocessor: WidePreprocessor, begin_time: int, end_time: int, batch_rows=4096):
    columns = preprocessor.state["input_columns"]
    query = f"SELECT {', '.join(quote(n) for n in columns)} FROM read_parquet(?) WHERE source_transaction_dt_seconds BETWEEN ? AND ? ORDER BY source_transaction_dt_seconds, transaction_id"
    reader = db.execute(query, [str(path), begin_time, end_time])
    while rows := reader.fetchmany(batch_rows):
        yield preprocessor.transform(pd.DataFrame(rows, columns=columns))
