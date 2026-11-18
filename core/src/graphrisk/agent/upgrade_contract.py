"""Route 0 registration only. No provider send, tool dispatch, or live reservation."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class UpgradeContractError(ValueError):
    """Stable codes deliberately exclude rejected input and credentials."""


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class BudgetContract(StrictModel):
    total_attempts: Literal[1000]
    deepseek_attempts: Literal[600]
    local_attempts: Literal[400]
    hard_limit_cny: Literal[100]
    pause_estimate_cny: Literal[80]


def load_contract(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        BudgetContract.model_validate(value["budget"])
        if (value["schema_version"] != "autonomous-investigation-v1"
                or value["experiment_id"] != "agent-investigation-20261019"
                or value["live_generation_enabled"] is not False
                or value["models"]["primary_candidate"] != "deepseek"
                or value["models"]["local_comparison"] != "qwen3:4b"):
            raise ValueError
        s = value["sampling"]
        if [s[k] for k in ("development", "natural_validation", "diagnostic_validation",
                            "diagnostic_per_label", "repeat_cases_per_provider", "human_read_cases")] != [15, 30, 30, 15, 5, 5]:
            raise ValueError
        if s["exclude_previously_seen"] is not True:
            raise ValueError
        limits = value["draft_limits"]
        for key in ("steps_per_turn", "model_calls_per_turn", "tool_calls_per_turn",
                    "semantic_repairs_per_turn", "seconds_per_turn", "turns_per_conversation",
                    "model_calls_per_conversation", "tool_calls_per_conversation", "context_tokens",
                    "max_output_tokens"):
            if type(limits[key]) is not int or limits[key] <= 0:
                raise ValueError
        if limits["semantic_repairs_per_turn"] != 1:
            raise ValueError
        canonical(value)
        return value
    except (OSError, ValueError, KeyError, TypeError, ValidationError):
        raise UpgradeContractError("invalid_experiment_contract") from None


def register_budget(path: Path, contract: dict) -> dict:
    """Idempotent single registration; never unlink/reset or grant live permission.

    Route 2 must add atomic reserve/settle/reconcile and test crash recovery before
    any model POST. Draft token/step changes do not mint a fresh budget.
    """
    BudgetContract.model_validate(contract["budget"])
    path.parent.mkdir(parents=True, exist_ok=True)
    wanted = (contract["experiment_id"], canonical(contract["budget"]))
    try:
        with sqlite3.connect(path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "budget_registration" not in tables:
                raise UpgradeContractError("unrecognized_budget_database")
            db.execute("CREATE TABLE IF NOT EXISTS budget_registration (id INTEGER PRIMARY KEY CHECK(id=1), experiment TEXT NOT NULL, caps TEXT NOT NULL)")
            row = db.execute("SELECT experiment,caps FROM budget_registration WHERE id=1").fetchone()
            if row is None:
                if tables:
                    raise UpgradeContractError("budget_registration_missing_no_reset")
                db.execute("INSERT INTO budget_registration VALUES (1,?,?)", wanted)
            elif row != wanted:
                raise UpgradeContractError("immutable_budget_registration_changed")
            # Empty by construction at route0. Future route2 migrates in place.
            db.execute("CREATE TABLE IF NOT EXISTS model_attempts (attempt_id TEXT PRIMARY KEY, provider TEXT NOT NULL CHECK(provider IN ('deepseek','local')), state TEXT NOT NULL CHECK(state IN ('reserved','settled','unknown')), reserved_micro_cny INTEGER NOT NULL CHECK(reserved_micro_cny>=0))")
            rows = db.execute("SELECT provider,state,reserved_micro_cny FROM model_attempts").fetchall()
            if rows:
                raise UpgradeContractError("route0_registration_contains_attempts_check_route2")
        return {"experiment_id": wanted[0], "caps_sha256": digest(contract["budget"]),
                "total_attempts_used": 0, "deepseek_attempts_used": 0, "local_attempts_used": 0,
                "reserved_micro_cny": 0, "live_budget_executor_ready": False}
    except sqlite3.Error:
        raise UpgradeContractError("budget_database_unavailable") from None


class HistorySummary(StrictModel):
    window_seconds: int = Field(gt=0, le=2592000)
    earlier_transaction_count: int | None = Field(ge=0)
    earlier_amount_sum: float | None = Field(ge=0)


class ModelSignal(StrictModel):
    role: Literal["primary", "experimental"]
    family: Literal["xgboost", "graphsage"]
    version: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    score: float | None = Field(ge=0, le=1)
    interpretation: Literal["uncalibrated_signal"]


class RelationSummary(StrictModel):
    relation: Literal["shared_card", "shared_device", "shared_email_domain", "shared_address"]
    other_transaction_count: int | None = Field(ge=0)
    neighbor_scores_available: bool
    truncated: bool


class PolicyExcerpt(StrictModel):
    policy_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    excerpt: str = Field(min_length=1, max_length=2000)
    applicability: Literal["unverified_conditions", "not_applicable", "measured_conditions_only"]


class MinimalPacket(StrictModel):
    schema_version: Literal["investigation-minimal-v1"]
    case_alias: str = Field(pattern=r"^[a-f0-9]{32}$")
    intent: Literal["investigate", "explain_evidence", "policy_conditions", "continue_checks"]
    focus: Literal["overview", "support_and_counterevidence", "policy_prerequisites", "missing_evidence"]
    amount: float = Field(ge=0)
    history: list[HistorySummary] = Field(max_length=8)
    model_signals: list[ModelSignal] = Field(max_length=2)
    relations: list[RelationSummary] = Field(max_length=4)
    policies: list[PolicyExcerpt] = Field(max_length=6)
    # No raw question/chat/history, transaction/entity ID, label, path, or old report.
    # Future context/judge formats need their own reviewed version, not a text escape.


class PayloadReview(StrictModel):
    schema_version: Literal["payload-review-v1"]
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    boundary_revision: Literal["investigation-minimal-v1"]


def reviewed_packet(value: dict, review: dict, *, expected_source_sha256: str,
                    approved_review_sha256: str) -> dict:
    """Structural/hash check, NOT an automatic privacy or semantic review.

    approved_review_sha256 must come from the server's frozen registry. Taking it
    from the same caller would invalidate the boundary. No network is performed.
    """
    try:
        packet = MinimalPacket.model_validate(value).model_dump(mode="json")
        record = PayloadReview.model_validate(review).model_dump(mode="json")
        if (digest(packet) != record["payload_sha256"]
                or record["source_bundle_sha256"] != expected_source_sha256
                or digest(record) != approved_review_sha256):
            raise ValueError
        return packet
    except (ValidationError, ValueError, TypeError):
        raise UpgradeContractError("outbound_packet_not_registered") from None


def old_ids(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"transaction_id", "source_id"} and type(child) in (str, int):
                found.add(str(child))
            elif isinstance(child, (dict, list)):
                found.update(old_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(old_ids(child))
    return found


def sample_cases(rows: list[dict], labels: dict[str, int], excluded: set[str], *, seed: int) -> dict:
    """Offline sampler: natural arm never consults label or score values.

    rows are ID/time/model-selection-cutoff metadata ONLY. Labels returned under a
    separate evaluation key must never be copied into provider/tool input.
    """
    import random
    rng = random.Random(seed)
    if len({str(r["transaction_id"]) for r in rows}) != len(rows):
        raise UpgradeContractError("duplicate_candidate_id")
    eligible = []
    for row in rows:
        if row["event_second"] <= row["selection_cutoff"]:
            raise UpgradeContractError("candidate_not_strictly_after_selection")
        if str(row["transaction_id"]) not in excluded:
            eligible.append(row)
    eligible.sort(key=lambda r: (r["event_second"], str(r["transaction_id"])))

    def stratify(pool, bins, take):
        selected = []
        for index in range(bins):
            group = pool[len(pool) * index // bins:len(pool) * (index + 1) // bins]
            if len(group) < take:
                raise UpgradeContractError("insufficient_sampling_population")
            selected.extend(rng.sample(group, take))
        return selected

    development = stratify(eligible, 5, 3)
    used = {str(r["transaction_id"]) for r in development}
    remaining = [r for r in eligible if str(r["transaction_id"]) not in used]
    natural = stratify(remaining, 6, 5)
    used.update(str(r["transaction_id"]) for r in natural)
    diagnostic = []
    for label in (0, 1):
        pool = [r for r in remaining if str(r["transaction_id"]) not in used
                and labels.get(str(r["transaction_id"])) == label]
        chosen = stratify(pool, 5, 3)
        diagnostic.extend(chosen)
        used.update(str(r["transaction_id"]) for r in chosen)
    validation = sorted(natural + diagnostic, key=lambda r: (r["event_second"], str(r["transaction_id"])))
    repeat = rng.sample(validation, 5)
    read_cases = rng.sample(validation, 5)
    all_rows = development + validation
    if any(labels.get(str(r["transaction_id"])) not in (0, 1) for r in all_rows):
        raise UpgradeContractError("evaluation_label_missing")
    return {"eligible_count": len(eligible), "development": development,
            "natural": natural, "diagnostic": diagnostic,
            "repeat_ids": [str(r["transaction_id"]) for r in repeat],
            "human_read_ids": [str(r["transaction_id"]) for r in read_cases],
            "evaluation_labels": {str(r["transaction_id"]): labels[str(r["transaction_id"])] for r in all_rows}}
