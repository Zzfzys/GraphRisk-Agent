"""Bounded, label-free raw side table and strictly prior entity statistics."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import duckdb

from graphrisk.agent.stage09_preflight import _content_hash, _sha256
from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.features.wide_contract import ENTITY_KEYS, engineered_fields, quote, raw_fields, read_header
from graphrisk.features.wide_preprocessing import WidePreprocessor, feature_batches, fit_preprocessor, train_statistics


def _nonempty(name):
    return f"NULLIF(TRIM({quote(name)}), '')"


def _validate_raw(db, table, fields, *, transaction):
    id_value = _nonempty("TransactionID")
    total, unique, bad = db.execute(f"SELECT count(*), count(DISTINCT {id_value}), count_if({id_value} IS NULL OR NOT regexp_full_match({id_value}, '[0-9]+')) FROM {quote(table)}").fetchone()
    if not total and transaction or unique != total or bad:
        raise ValueError("empty, duplicate or invalid raw transaction/identity IDs")
    for start in range(0, len(fields), 8):
        numeric = [f for f in fields[start:start+8] if f["dtype"] == "numeric"]
        if not numeric:
            continue
        expressions = []
        for field in numeric:
            value = _nonempty(field["name"])
            expressions.append(f"count_if({value} IS NOT NULL AND (TRY_CAST({value} AS DOUBLE) IS NULL OR NOT isfinite(TRY_CAST({value} AS DOUBLE))))")
        errors = db.execute(f"SELECT {', '.join(expressions)} FROM {quote(table)}").fetchone()
        for field, count in zip(numeric, errors):
            if count:
                raise ValueError(f"invalid numeric source column:{field['name']}; count={count}")
    if transaction:
        time_value, amount = _nonempty("TransactionDT"), _nonempty("TransactionAmt")
        invalid = db.execute(f"SELECT count_if({time_value} IS NULL OR NOT regexp_full_match({time_value}, '[0-9]+') OR TRY_CAST({time_value} AS BIGINT) IS NULL), count_if({amount} IS NULL OR TRY_CAST({amount} AS DOUBLE) < 0), count_if({_nonempty('ProductCD')} IS NULL) FROM {quote(table)}").fetchone()
        if any(invalid):
            raise ValueError("invalid time, amount or missing product in raw source")
    return total


def _typed(fields):
    return [f"CAST({_nonempty(f['name'])} AS DOUBLE) AS {quote(f['name'])}" if f["dtype"] == "numeric" else f"{_nonempty(f['name'])} AS {quote(f['name'])}" for f in fields]


def build_side_table(db, transaction_csv: Path, identity_csv: Path, canonical: Path, output: Path, guard=lambda: None):
    if output.exists():
        raise ValueError("side table already exists; never overwrite")
    fields = raw_fields(read_header(transaction_csv), read_header(identity_csv))
    tx = [f for f in fields if f["source"] == "transaction"]
    identity = [f for f in fields if f["source"] == "identity"]
    guard()
    # Raw label bytes remain only in the original CSV; the projected table excludes them.
    db.read_csv(str(transaction_csv), header=True, all_varchar=True).create_view("raw_transaction_source")
    db.execute("CREATE TABLE raw_tx AS SELECT " + ", ".join(quote(n) for n in ["TransactionID", "TransactionDT", *[f["name"] for f in tx]]) + " FROM raw_transaction_source")
    db.read_csv(str(identity_csv), header=True, all_varchar=True).create_view("raw_identity_source")
    db.execute("CREATE TABLE raw_id AS SELECT " + ", ".join(quote(n) for n in ["TransactionID", *[f["name"] for f in identity]]) + " FROM raw_identity_source")
    count = _validate_raw(db, "raw_tx", tx, transaction=True)
    identity_count = _validate_raw(db, "raw_id", identity, transaction=False)
    guard()
    db.execute("CREATE TABLE typed_tx AS SELECT " + _nonempty("TransactionID") + " AS transaction_id, CAST(" + _nonempty("TransactionDT") + " AS BIGINT) AS source_transaction_dt_seconds, " + ", ".join(_typed(tx)) + " FROM raw_tx")
    db.execute("CREATE TABLE typed_id AS SELECT " + _nonempty("TransactionID") + " AS transaction_id, " + ", ".join(_typed(identity)) + " FROM raw_id")
    db.from_parquet(str(canonical)).create_view("canonical_source")
    db.execute("CREATE TABLE contexts AS SELECT transaction_id, source_transaction_dt_seconds, transaction_amount, card_id, device_id, email_domain, address_id FROM canonical_source")
    canonical_count, canonical_unique = db.execute("SELECT count(*), count(DISTINCT transaction_id) FROM contexts").fetchone()
    mismatch = db.execute("SELECT count(*) FROM contexts c FULL OUTER JOIN typed_tx t USING(transaction_id) WHERE c.transaction_id IS NULL OR t.transaction_id IS NULL OR c.source_transaction_dt_seconds IS DISTINCT FROM t.source_transaction_dt_seconds OR c.transaction_amount IS DISTINCT FROM t.TransactionAmt").fetchone()[0]
    orphan_identity = db.execute("SELECT count(*) FROM typed_id i ANTI JOIN typed_tx t USING(transaction_id)").fetchone()[0]
    if canonical_count != count or canonical_unique != count or mismatch or orphan_identity:
        raise ValueError("raw/canonical ID-time-amount alignment failure or orphan identity")
    if db.execute("SELECT count(*) FROM contexts WHERE card_id IS NULL OR device_id IS NULL OR email_domain IS NULL OR address_id IS NULL").fetchone()[0]:
        raise ValueError("canonical entity keys missing; do not silently merge unknown identities")
    expressions, windows = [], []
    for entity, key in ENTITY_KEYS.items():
        day, past = entity + "_day", entity + "_past"
        windows += [f"{day} AS (PARTITION BY {quote(key)} ORDER BY source_transaction_dt_seconds RANGE BETWEEN 86400 PRECEDING AND 1 PRECEDING)",
                    f"{past} AS (PARTITION BY {quote(key)} ORDER BY source_transaction_dt_seconds RANGE BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)"]
        expressions += [f"count(*) OVER {day}::DOUBLE AS hist_{entity}_count_24h",
            f"avg(transaction_amount) OVER {day} AS hist_{entity}_amount_mean_24h",
            f"stddev_pop(transaction_amount) OVER {day} AS hist_{entity}_amount_std_24h",
            f"(source_transaction_dt_seconds - max(source_transaction_dt_seconds) OVER {past})::DOUBLE AS hist_{entity}_seconds_since_previous",
            f"(source_transaction_dt_seconds - min(source_transaction_dt_seconds) OVER {past})::DOUBLE AS hist_{entity}_age_seconds"]
    db.execute("CREATE TABLE history AS SELECT transaction_id, " + ", ".join(expressions) + " FROM contexts WINDOW " + ", ".join(windows))
    guard()
    feature_names = [f["name"] for f in fields]
    raw_projection = [f"{'t' if f['source']=='transaction' else 'i'}.{quote(f['name'])}" for f in fields]
    derived = engineered_fields()
    hist_names = [f["name"] for f in derived if f["name"].startswith("hist_")]
    db.execute("CREATE TABLE side AS SELECT t.transaction_id, t.source_transaction_dt_seconds, " + ", ".join(raw_projection) + ", (i.transaction_id IS NOT NULL)::DOUBLE AS has_identity, (t.source_transaction_dt_seconds // 86400)::DOUBLE AS relative_day, sin(2*pi()*(t.source_transaction_dt_seconds % 86400)/86400)::DOUBLE AS relative_day_sin, cos(2*pi()*(t.source_transaction_dt_seconds % 86400)/86400)::DOUBLE AS relative_day_cos, " + ", ".join("h." + n for n in hist_names) + " FROM typed_tx t LEFT JOIN typed_id i USING(transaction_id) JOIN history h USING(transaction_id) ORDER BY t.source_transaction_dt_seconds, t.transaction_id")
    side_count, unique = db.execute("SELECT count(*), count(DISTINCT transaction_id) FROM side").fetchone()
    if side_count != count or unique != count:
        raise ValueError("feature join changed target cardinality")
    output.parent.mkdir(parents=True, exist_ok=True)
    db.execute("COPY side TO ? (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 8192)", [str(output)])
    guard()
    return {"rows": count, "identity_rows": identity_count, "raw_fields": len(feature_names),
            "fields": fields + derived, "schema": [{"name": r[0], "duckdb_type": r[1]} for r in db.execute("DESCRIBE side").fetchall()],
            "labels_in_side_table": False, "history_rule": "integer_seconds_strictly_prior_equal_timestamps_simultaneous"}


def fit_fold_profiles(db, fields, train, profiles, directory: Path, guard=lambda: None):
    ids = [r[0] for r in db.execute("SELECT transaction_id FROM side WHERE source_transaction_dt_seconds <= ? ORDER BY source_transaction_dt_seconds, transaction_id", [train["max_relative_seconds"]]).fetchall()]
    if len(ids) != train["count"] or _content_hash({"ids": ids}) != train["ids_sha256"]:
        raise ValueError("preprocessing fit IDs differ from frozen training window")
    stats, duplicates = train_statistics(db, "side", fields, train["max_relative_seconds"])
    guard()
    states = {}
    for profile in profiles:
        state = fit_preprocessor(db, "side", fields, train, profile, stats, duplicates)
        path = directory / ("fold-1-" + profile + ".json")
        if path.exists():
            raise ValueError("preprocessor version already exists")
        write_json(path, state)
        states[profile] = {"path": path.name, "sha256": _sha256(path),
            "input_columns": len(state["input_columns"]), "output_columns": len(state["output_columns"]),
            "numeric_columns": len(state["numeric"]), "categorical_columns": len(state["categorical"]),
            "dropped": state["dropped"], "content_sha256": state["content_sha256"]}
    catalog = [{**f, "fit_scope": "fold-1-train-only", "statistics": {k: v for k, v in stats[f["name"]].items() if k != "signature"}, "exact_train_duplicate_of": duplicates.get(f["name"])} for f in fields]
    write_json(directory / "field-catalog.json", catalog)
    return states


def audit_history(db, count, guard=lambda: None):
    """Direct filtered aggregates on narrow source rows, independent of window SQL."""
    ranks = sorted({0, 1, 2, count//4, count//2, 3*count//4, count-1})
    targets = [db.execute("SELECT transaction_id, source_transaction_dt_seconds FROM contexts ORDER BY source_transaction_dt_seconds, transaction_id LIMIT 1 OFFSET ?", [rank]).fetchone() for rank in ranks if rank < count]
    ties = db.execute("SELECT c.transaction_id, c.source_transaction_dt_seconds FROM contexts c JOIN (SELECT source_transaction_dt_seconds FROM contexts GROUP BY source_transaction_dt_seconds HAVING count(*) > 1 ORDER BY source_transaction_dt_seconds LIMIT 1) t USING(source_transaction_dt_seconds) ORDER BY c.transaction_id LIMIT 3").fetchall()
    targets = list(dict.fromkeys(targets + ties))
    checked = 0
    for transaction_id, target_time in targets:
        guard()
        for entity, key in ENTITY_KEYS.items():
            entity_value = db.execute(f"SELECT {quote(key)} FROM contexts WHERE transaction_id=?", [transaction_id]).fetchone()[0]
            n, avg, std = db.execute(f"SELECT count(*), avg(transaction_amount), stddev_pop(transaction_amount) FROM contexts WHERE {quote(key)}=? AND source_transaction_dt_seconds >= ? AND source_transaction_dt_seconds < ?", [entity_value, target_time-86400, target_time]).fetchone()
            previous, first = db.execute(f"SELECT max(source_transaction_dt_seconds), min(source_transaction_dt_seconds) FROM contexts WHERE {quote(key)}=? AND source_transaction_dt_seconds < ?", [entity_value, target_time]).fetchone()
            expected = [n, avg, std, None if previous is None else target_time-previous, None if first is None else target_time-first]
            names = [f"hist_{entity}_{suffix}" for suffix in ("count_24h", "amount_mean_24h", "amount_std_24h", "seconds_since_previous", "age_seconds")]
            actual = db.execute("SELECT " + ", ".join(names) + " FROM side WHERE transaction_id=?", [transaction_id]).fetchone()
            for got, wanted in zip(actual, expected):
                if (got is None) != (wanted is None) or wanted is not None and not math.isclose(got, wanted, rel_tol=1e-8, abs_tol=1e-6):
                    raise ValueError("independent strict-prior history audit mismatch:" + entity)
                checked += 1
    return {"status": "passed", "targets": len(targets), "simultaneous_targets": len(ties), "entity_types": len(ENTITY_KEYS), "scalar_comparisons": checked,
            "method": "direct_source_filter_time_lt_target_no_window_function_or_labels", "tolerance": {"relative": 1e-8, "absolute": 1e-6}}


def verify_transform_batches(db, output: Path, states: dict, directory: Path, train, guard=lambda: None):
    result = {}
    for profile in states:
        preprocessor = WidePreprocessor.load(directory / states[profile]["path"])
        total = batches = 0
        for batch in feature_batches(db, output, preprocessor, train["min_relative_seconds"], train["max_relative_seconds"]):
            guard()
            total += len(batch)
            batches += 1
        if total != train["count"]:
            raise ValueError("batch preprocessing changed row count")
        result[profile] = {"rows_transformed": total, "batches": batches, "output_columns": len(preprocessor.state["output_columns"]),
                           "scope": "frozen_training_only_no_outcome_or_model"}
    return result
