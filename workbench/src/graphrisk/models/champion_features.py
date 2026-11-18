"""Reproduce the frozen champion study's training categories and matrix.

The caller supplies the already-computed association features.  Their event-time
availability is a separate contract; this module cannot infer it from a frame.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


COMPOSITES = {
    "card1_addr1": ("card1", "addr1"),
    "card1_addr1_P_emaildomain": ("card1", "addr1", "P_emaildomain"),
}


def source_expression(field: str) -> str:
    """Match the study's DuckDB trimming and composite-key representation."""
    columns = COMPOSITES.get(field, (field,))
    if any(not c.replace("_", "").isalnum() for c in columns):
        raise ValueError(f"invalid source field: {field!r}")
    cleaned = [f'NULLIF(trim(s."{c}"), \'\')' for c in columns]
    if field not in COMPOSITES:
        return cleaned[0]
    fields = ", ".join(f"v{i}:={value}" for i, value in enumerate(cleaned))
    missing = " OR ".join(value + " IS NULL" for value in cleaned)
    return f"CASE WHEN {missing} THEN NULL ELSE sha256(to_json(struct_pack({fields}))) END"


def training_category_maps(
    db: duckdb.DuckDBPyConnection,
    source: str | Path,
    categorical: list[str],
    cutoff: int,
    guard: Callable[[], None] = lambda: None,
) -> dict[str, dict[str, int]]:
    """Reconstruct the study mapping using only unlabeled rows through cutoff."""
    if len(set(categorical)) != len(categorical) or set(categorical) & COMPOSITES.keys():
        raise ValueError("categorical fields must be unique and exclude composite keys")
    maps = {}
    for field in [*categorical, *COMPOSITES]:
        guard()
        expression = source_expression(field)
        rows = db.execute(
            f"SELECT DISTINCT {expression} AS v FROM read_parquet(?) s "
            f"WHERE s.source_transaction_dt_seconds<=? AND {expression} IS NOT NULL ORDER BY v",
            [str(source), cutoff],
        ).fetchall()
        maps[field] = {row[0]: index for index, row in enumerate(rows)}
    guard()
    return maps


def source_columns(raw: list[str]) -> str:
    """SQL projection expected by ``transform_batch``; source alias must be s."""
    if len(set(raw)) != len(raw):
        raise ValueError("duplicate raw columns")
    for field in raw:
        if not field.replace("_", "").isalnum():
            raise ValueError(f"invalid source field: {field!r}")
    return ",".join(
        [*(f's."{field}"' for field in raw),
         *(f'{source_expression(name)} AS "{name}"' for name in COMPOSITES)]
    )


def transform_batch(
    source: pd.DataFrame,
    derived: pd.DataFrame,
    contract: Mapping,
    maps: Mapping[str, Mapping[str, int]],
    mode: str = "day",
) -> pd.DataFrame:
    """Make the exact 216+49 float32 matrix from aligned, provenance-checked inputs."""
    if mode not in ("day", "point"):
        raise ValueError("mode must be day or point")
    names, raw, categorical = (contract[key] for key in ("columns", "raw", "categorical"))
    mapping, extra = contract["base_mapping"][mode], contract["uid_extra"][mode]
    if len(names) != 216 or len(set(names)) != 216 or len(extra) != 49 or len(set(extra)) != 49:
        raise ValueError("invalid 216+49 feature contract")
    if len(source) != len(derived):
        raise ValueError("source and derived row counts differ")
    if "transaction_id" not in source or "transaction_id" not in derived:
        raise ValueError("both inputs require transaction_id")
    if source.transaction_id.isna().any() or source.transaction_id.duplicated().any():
        raise ValueError("transaction IDs must be present and unique")
    if not np.array_equal(source.transaction_id.to_numpy(), derived.transaction_id.to_numpy()):
        raise ValueError("source and derived transaction IDs do not match in order")
    required = {"source_transaction_dt_seconds", "TransactionAmt", *raw, *COMPOSITES}
    derived_required = {"source_transaction_dt_seconds", *mapping.values(), *extra}
    if not required.issubset(source.columns) or not derived_required.issubset(derived.columns):
        raise ValueError("source or derived features missing required columns")
    if source.source_transaction_dt_seconds.isna().any():
        raise ValueError("source transaction time is required")
    if not np.array_equal(source.source_transaction_dt_seconds.to_numpy(), derived.source_transaction_dt_seconds.to_numpy()):
        raise ValueError("source and derived transaction times do not match")
    if not set(categorical).union(COMPOSITES).issubset(maps):
        raise ValueError("training category maps are incomplete")
    source = source.reset_index(drop=True)
    derived = derived.reset_index(drop=True)
    source = source.reset_index(drop=True)
    derived = derived.reset_index(drop=True)
    data = {}
    for name in raw:
        if name in categorical:
            data[name] = source[name].str.strip().replace("", None).map(maps[name]).astype(np.float32)
        else:
            values = pd.to_numeric(source[name], errors="raise").astype(np.float32)
            if name in ("D4", "D10", "D11", "D15"):
                values = values - source.source_transaction_dt_seconds.astype(np.float32) / np.float32(86400)
            if np.isinf(values.to_numpy()).any():
                raise ValueError("infinite source value: " + name)
            data[name] = values
    for name in COMPOSITES:
        data[name] = source[name].map(maps[name]).astype(np.float32)
    amount = pd.to_numeric(source.TransactionAmt, errors="raise")
    data["cents"] = (amount - np.floor(amount)).round(4).astype(np.float32)
    for name, column in mapping.items():
        data[name] = pd.to_numeric(derived[column], errors="raise").astype(np.float32)
    result = pd.concat([pd.DataFrame({name: data[name] for name in names}), derived[extra].astype(np.float32)], axis=1)
    if result.shape[1] != 265 or np.isinf(result.to_numpy()).any():
        raise ValueError("prepared matrix invalid")
    return result
