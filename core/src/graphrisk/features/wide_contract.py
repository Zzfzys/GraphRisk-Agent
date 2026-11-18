"""Raw IEEE fields are explicit inputs; anonymous fields retain timing uncertainty."""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Literal

from graphrisk.agent.stage09_preflight import ContractModel


class FeatureContract(ContractModel):
    schema_version: Literal["stage09-5b-features-v1"]
    statistics_fold: Literal["fold-1"]
    profiles: list[Literal["core", "source_as_received"]]
    unknown_source_timing: Literal["separate_sensitivity_group_not_verified_causal"]
    numeric_imputation: Literal["train_median"]
    numeric_missing_indicators: Literal["all_retained_numeric_columns"]
    categorical_encoding: Literal["train_vocabulary_distinct_missing_unknown"]
    feature_selection: Literal["train_constant_and_exact_duplicate_only"]
    labels_in_side_table: Literal[False]
    duckdb_memory_mib: Literal[768]
    duckdb_threads: Literal[1]
    maximum_spill_gib: Literal[5]
    statistics_batch_columns: Literal[8]
    transform_batch_rows: Literal[4096]
    maximum_pipeline_seconds: Literal[3600]


CORE_TRANSACTION = {"TransactionAmt", "ProductCD", "addr1", "addr2", "P_emaildomain", "R_emaildomain", *[f"card{i}" for i in range(1, 7)]}
CORE_IDENTITY = {"DeviceType", "DeviceInfo"}
NUMERIC_IDENTITY = {f"id_{i:02d}" for i in [*range(1, 12), 13, 14, 17, 18, 19, 20, 21, 22, 24, 25, 26, 32]}
FORBIDDEN = {"isfraud", "is_fraud", "label", "labels", "target", "prediction", "risk_score"}
METADATA = {"transaction_id", "source_transaction_dt_seconds"}
ENTITY_KEYS = {"card": "card_id", "device": "device_id", "email": "email_domain", "address": "address_id"}


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def read_header(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as stream:
        columns = next(csv.reader(stream))
    if len(columns) != len(set(columns)) or any(not c.strip() for c in columns):
        raise ValueError("duplicate or empty raw CSV column names")
    return columns


def raw_fields(transaction_columns: list[str], identity_columns: list[str]) -> list[dict]:
    if not CORE_TRANSACTION | {"TransactionID", "TransactionDT", "isFraud"} <= set(transaction_columns):
        raise ValueError("required transaction feature columns missing")
    if not CORE_IDENTITY | {"TransactionID"} <= set(identity_columns):
        raise ValueError("required identity feature columns missing")
    fields = []
    for table, columns in (("transaction", transaction_columns), ("identity", identity_columns)):
        for name in columns:
            if name in {"TransactionID", "TransactionDT", "isFraud"} and table == "transaction" or name == "TransactionID" and table == "identity":
                continue
            core = name in (CORE_TRANSACTION if table == "transaction" else CORE_IDENTITY)
            if table == "transaction":
                numeric = name == "TransactionAmt" or name in {"dist1", "dist2"} or bool(re.fullmatch(r"(?:C(?:[1-9]|1[0-4])|D(?:[1-9]|1[0-5])|V(?:[1-9]|[1-9][0-9]|[12][0-9]{2}|3[0-2][0-9]|33[0-9]))", name))
                allowed = core or numeric or bool(re.fullmatch(r"M[1-9]", name))
            else:
                numeric = name in NUMERIC_IDENTITY
                allowed = core or bool(re.fullmatch(r"id_(?:0[1-9]|[12][0-9]|3[0-8])", name))
            if not allowed or name.casefold() in FORBIDDEN:
                raise ValueError("unregistered/forbidden raw feature column:" + name)
            fields.append({"name": name, "source": table, "dtype": "numeric" if numeric else "categorical",
                "group": "core" if core else "source_as_received",
                "availability": "event_record_assumption_not_independently_verified" if core else "unknown_generation_time",
                "meaning": "named_source_field" if core else "anonymous_or_unverified_source_field"})
    if len({f["name"] for f in fields}) != len(fields):
        raise ValueError("raw transaction/identity feature names collide")
    return fields


def engineered_fields() -> list[dict]:
    names = ["has_identity", "relative_day", "relative_day_sin", "relative_day_cos"]
    names += [f"hist_{entity}_{suffix}" for entity in ENTITY_KEYS for suffix in (
        "count_24h", "amount_mean_24h", "amount_std_24h", "seconds_since_previous", "age_seconds")]
    return [{"name": n, "dtype": "numeric", "source": "derived", "group": "core",
        "availability": "strictly_prior_unlabelled_events" if n.startswith("hist_") else "current_event_relative_time_or_identity_presence",
        "meaning": "entity_key_is_heuristic_not_verified_person" if n.startswith("hist_") else "relative_phase_not_real_calendar"} for n in names]
