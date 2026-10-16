"""Independent 5B contract, time-only windows and durable fit/access accounting.

This module performs no training, prediction or network calls. The ledger is an
accidental-drift guard, not a security boundary against its filesystem owner.
"""
from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from graphrisk.agent.stage09_preflight import ContractModel, ResourceBudget, _content_hash
from graphrisk.demo.stage09_evaluation import exclusive, read_json, write_json


class Window(ContractModel):
    name: str = Field(pattern=r"^fold-[123]$")
    train_end: float = Field(gt=0, lt=1)
    inner_end: float = Field(gt=0, lt=1)
    outer_end: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def chronological(self):
        if not self.train_end < self.inner_end < self.outer_end:
            raise ValueError("window requires train < inner < outer")
        return self


class FitLimit(ContractModel):
    max_fits: int = Field(gt=0)
    max_fit_seconds: Literal[3600]
    max_total_seconds: int | None = Field(default=None, gt=0)
    lanes: dict[str, int]

    @model_validator(mode="after")
    def allocation(self):
        if any(n <= 0 for n in self.lanes.values()) or sum(self.lanes.values()) != self.max_fits:
            raise ValueError("lane allocation must sum to fit cap")
        return self


class Budgets(ContractModel):
    gnn: FitLimit
    xgboost: FitLimit
    mlp: FitLimit
    new_generation_calls_route0: Literal[0]
    new_paid_calls: Literal[0]
    old_generation_cap: Literal[144]
    concurrency: Literal[1]

    @model_validator(mode="after")
    def fixed_caps(self):
        expected = {
            "gnn": (30, 43200, {"scale": 3, "search": 6, "conditional": 9, "confirmation": 9, "failure": 3}),
            "xgboost": (12, None, {"search": 3, "confirmation": 3, "control": 3, "failure": 3}),
            "mlp": (3, None, {"control": 3}),
        }
        for name, (count, seconds, lanes) in expected.items():
            limit = getattr(self, name)
            if (limit.max_fits, limit.max_total_seconds, limit.lanes) != (count, seconds, lanes):
                raise ValueError("5B fit caps and confirmation reserve cannot change")
        return self


class Metrics(ContractModel):
    minimum_xgboost_roc_auc: Literal[0.80]
    exploratory_xgboost_roc_auc: Literal[0.90]
    minimum_fusion_ap_delta: Literal[0.005]
    minimum_relative_recall_top1_delta: Literal[0.05]
    minimum_positive_folds: Literal[2]
    aggregation: Literal["seed_mean_then_equal_fold_mean"]
    ranking: Literal["ceil_1_percent_score_desc_transaction_id_asc"]
    uncertainty: Literal["paired_day_block_bootstrap_1000_seed_20261009"]
    zero_baseline_recall: Literal["ineligible_relative_gain"]
    subgroup_promotion: Literal["disabled_until_preregistered_before_outer_access"]


class GraphCandidate(ContractModel):
    id: str = Field(pattern=r"^g[1-6]$")
    learning_rate: float = Field(gt=0)
    hidden_channels: int = Field(gt=0)
    dropout: float = Field(ge=0, lt=1)
    positive_weight: Literal["train_ratio", "sqrt_train_ratio"]


class TabularCandidate(ContractModel):
    id: str = Field(pattern=r"^x[1-3]$")
    max_depth: int = Field(gt=0)
    learning_rate: float = Field(gt=0)
    min_child_weight: float = Field(gt=0)


class TrainingRules(ContractModel):
    gnn_input_dimensions_initial: Literal[28]
    gnn_layers_initial: Literal[2]
    gnn_aggregation_initial: Literal["mean"]
    max_epochs: Literal[50]
    patience: Literal[10]
    xgboost_max_estimators: Literal[2000]
    xgboost_patience: Literal[50]
    preprocessing: Literal["train_only"]
    history: Literal["strictly_earlier_equal_times_simultaneous"]
    candidate_selection: Literal["inner_early_stop_ap_then_lower_cost_then_registered_order"]
    fusion_alphas_xgboost_weight: list[float]
    fusion_fit: Literal["inner_calibration_only_after_early_stop"]
    conditional_registration: Literal["new_frozen_appendix_before_fit_no_outer_or_test_metrics"]

    @model_validator(mode="after")
    def alpha_grid(self):
        if self.fusion_alphas_xgboost_weight != [0.0, 0.25, 0.5, 0.75, 1.0]:
            raise ValueError("fusion grid differs")
        return self


class EnhancementContract(ContractModel):
    schema_version: Literal["stage09-5b-v1"]
    dataset: Literal["ieee_cis"]
    execution_drive: Literal["E"]
    output_root: Literal["reports/generated/stage09/route5b"]
    artifact_root: Literal["artifacts/stage-09/route5b"]
    confirmation_seeds: list[int] = Field(min_length=3, max_length=3)
    windows: list[Window] = Field(min_length=3, max_length=3)
    search_fold: Literal["fold-1"]
    inner_calibration_fraction: Literal[0.50]
    outer_access: Literal["all_after_final_decision_freeze"]
    earlier_outer_labels: Literal["later_final_fit_only_after_decision_freeze"]
    test_status: Literal["historically_observed_not_blind"]
    maximum_final_test_batches: Literal[1]
    metrics: Metrics
    budgets: Budgets
    gnn_scale_targets: list[int | str]
    gnn_candidates: list[GraphCandidate] = Field(min_length=6, max_length=6)
    xgboost_candidates: list[TabularCandidate] = Field(min_length=3, max_length=3)
    training_rules: TrainingRules
    resources: ResourceBudget

    @model_validator(mode="after")
    def registrations(self):
        if len(set(self.confirmation_seeds)) != 3:
            raise ValueError("confirmation seeds must be distinct")
        if [w.name for w in self.windows] != ["fold-1", "fold-2", "fold-3"]:
            raise ValueError("three ordered folds required")
        if self.windows[-1].outer_end != 1.0:
            raise ValueError("final fold must end at development boundary")
        for previous, current in zip(self.windows, self.windows[1:]):
            if previous.outer_end > current.inner_end or previous.train_end >= current.train_end:
                raise ValueError("outer windows must not overlap; training cutoffs must expand")
        if self.gnn_scale_targets != [50000, 150000, "full_allowed_train"]:
            raise ValueError("scale order differs")
        for candidates, prefix in ((self.gnn_candidates, "g"), (self.xgboost_candidates, "x")):
            if [c.id for c in candidates] != [f"{prefix}{i+1}" for i in range(len(candidates))]:
                raise ValueError("candidate ids/order differ")
            values = [{k: v for k, v in c.model_dump().items() if k != "id"} for c in candidates]
            if len({_content_hash(v) for v in values}) != len(values):
                raise ValueError("candidate settings must be distinct")
        return self


def load_contract(path: Path) -> EnhancementContract:
    return EnhancementContract.model_validate_json(path.read_text(encoding="utf-8"))


def freeze_windows(rows: list[tuple[str, int]], contract: EnhancementContract) -> list[dict]:
    """Use IDs and source-relative seconds only; move cuts left to keep ties together."""
    if len(rows) < 30 or len({r[0] for r in rows}) != len(rows):
        raise ValueError("unique development IDs and at least 30 events required")
    if any(not isinstance(t, int) or isinstance(t, bool) for _, t in rows):
        raise ValueError("integer relative event times required")
    rows = sorted(rows, key=lambda r: (r[1], r[0]))

    def cut(fraction):
        position = int(len(rows) * fraction)
        while 0 < position < len(rows) and rows[position - 1][1] == rows[position][1]:
            position -= 1
        return position

    def segment(begin, end):
        part = rows[begin:end]
        if not part:
            raise ValueError("empty temporal window after simultaneous-time adjustment")
        return {"start_rank": begin, "end_rank_exclusive": end, "count": len(part),
                "min_relative_seconds": part[0][1], "max_relative_seconds": part[-1][1],
                "ids_sha256": _content_hash({"ids": [r[0] for r in part]})}

    result = []
    for window in contract.windows:
        train, inner, outer = cut(window.train_end), cut(window.inner_end), cut(window.outer_end)
        middle = train + int((inner - train) * (1 - contract.inner_calibration_fraction))
        while 0 < middle < len(rows) and rows[middle - 1][1] == rows[middle][1]:
            middle -= 1
        result.append({"name": window.name, "train": segment(0, train),
                       "early_stop": segment(train, middle),
                       "calibration": segment(middle, inner), "outer": segment(inner, outer)})
    return result


class EnhancementLedger:
    """Reserve BEFORE fit; unresolved attempts stop work and consume their full cap.

    Confirmation count AND worst-case wall time are earmarked. Completed failed
    fits retain their count and actual elapsed time; abandoned fits retain 60min.
    """
    def __init__(self, path: Path, contract: EnhancementContract, freeze_hash: str):
        self.path, self.contract, self.freeze_hash = path, contract, freeze_hash

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = {"schema_version": "stage09-5b-ledger-v1", "freeze_sha256": self.freeze_hash,
                 "fits": [], "decision_sha256": None, "outer_access": [], "test_access": []}
        with self.path.open("x", encoding="utf-8") as stream:
            import json
            json.dump({**value, "content_sha256": _content_hash(value)}, stream, sort_keys=True, indent=2)

    def read(self):
        value = read_json(self.path)
        payload = {k: v for k, v in value.items() if k != "content_sha256"}
        if _content_hash(payload) != value.get("content_sha256") or value.get("freeze_sha256") != self.freeze_hash:
            raise ValueError("ledger binding/content drift; do not reset budget")
        return payload

    def _save(self, value):
        write_json(self.path, {**value, "content_sha256": _content_hash(value)})

    def _active(self):
        if self.path.parent.parent.name != "route5b":
            return  # Tiny test fixtures are outside the production budget root.
        pointer = self.path.parent.parent / "budget-binding.json"
        value = read_json(pointer)
        payload = {k: v for k, v in value.items() if k != "content_sha256"}
        if (_content_hash(payload) != value.get("content_sha256")
                or Path(value["ledger_path"]).resolve() != self.path.resolve()
                or value["origin_freeze_sha256"] != self.freeze_hash
                or value["contract_sha256"] != _content_hash(self.contract.model_dump())):
            raise ValueError("this ledger is not the shared active 5B budget")

    def _resolved(self, value):
        if any(f["state"] == "reserved" for f in value["fits"]):
            raise ValueError("unresolved fit; stop and audit before recovery")

    def reserve(self, family: str, lane: str, name: str, registration: dict, clock=time.perf_counter):
        self._active()
        if family not in ("gnn", "xgboost", "mlp"):
            raise ValueError("unknown model family")
        limit = getattr(self.contract.budgets, family)
        if lane not in limit.lanes or not name or len(name) > 100:
            raise ValueError("unknown lane or invalid attempt name")
        input_hash = registration.get("input_sha256", "")
        if len(input_hash) != 64 or any(c not in "0123456789abcdef" for c in input_hash) or registration.get("fold") not in {w.name for w in self.contract.windows}:
            raise ValueError("registered fold and input hash required")
        with exclusive(self.path.with_suffix(".lock")):
            value = self.read()
            self._resolved(value)
            if value["outer_access"] or value["test_access"]:
                raise ValueError("results opened; candidate/final fits sealed")
            if any(f["name"] == name for f in value["fits"]):
                raise ValueError("attempt name already used; never refund/reuse")
            if lane in {"scale", "search", "conditional"}:
                if value["decision_sha256"] or registration["fold"] != self.contract.search_fold:
                    raise ValueError("search restricted to fold-1 before decision freeze")
            elif lane == "control":
                allowed = {"legacy14", "core", "root28"} if family == "xgboost" else {"root28"}
                appendix = registration.get("appendix_sha256", "")
                if registration.get("control_id") not in allowed or len(appendix) != 64 or any(c not in "0123456789abcdef" for c in appendix):
                    raise ValueError("control requires registered identity and frozen appendix hash")
                if not value["decision_sha256"] and registration["fold"] != self.contract.search_fold:
                    raise ValueError("development control restricted to fold-1")
                if any(f["family"] == family and f["lane"] == "control" and f["registration"]["fold"] == registration["fold"] and f["registration"].get("control_id") == registration["control_id"] for f in value["fits"]):
                    raise ValueError("control fold/input already attempted; failure lane required")
            elif lane != "failure" and not value["decision_sha256"]:
                raise ValueError("confirmation requires final decision freeze")
            if lane == "failure":
                parent = next((f for f in value["fits"] if f["name"] == registration.get("retry_of")), None)
                if not parent or parent["family"] != family or parent["state"] not in {"failed", "abandoned"}:
                    raise ValueError("failure reserve requires a failed/abandoned parent fit")
                # Recovery cannot change a candidate into a fit using later-fold labels.
                if registration["fold"] != parent["registration"]["fold"] or registration["input_sha256"] != parent["registration"]["input_sha256"]:
                    raise ValueError("failure recovery must retain registered inputs/fold")
                if value["decision_sha256"] and parent["lane"] in {"scale", "search", "conditional"}:
                    raise ValueError("candidate recovery forbidden after decision freeze")
                if registration.get("seed") != parent["registration"].get("seed"):
                    raise ValueError("failure recovery must retain registered seed")
            fits = [f for f in value["fits"] if f["family"] == family]
            if len(fits) >= limit.max_fits or sum(f["lane"] == lane for f in fits) >= limit.lanes[lane]:
                raise ValueError("fit/lane quota exhausted")
            if lane == "search":
                candidates = self.contract.gnn_candidates if family == "gnn" else self.contract.xgboost_candidates
                position = sum(f["lane"] == lane for f in fits)
                if registration.get("candidate_id") != candidates[position].id:
                    raise ValueError("search must follow preregistered candidate order")
            if lane == "scale":
                position = sum(f["lane"] == lane for f in fits)
                if registration.get("scale_target") != self.contract.gnn_scale_targets[position]:
                    raise ValueError("scale must follow preregistered order")
            if lane == "conditional" and not registration.get("appendix_sha256"):
                raise ValueError("conditional fit requires frozen appendix hash")
            if lane == "confirmation" and family == "gnn":
                if registration.get("seed") not in self.contract.confirmation_seeds:
                    raise ValueError("unregistered confirmation seed")
                if any(f["lane"] == lane and f["registration"]["fold"] == registration["fold"] and f["registration"].get("seed") == registration["seed"] for f in fits):
                    raise ValueError("confirmation fold/seed already used; failure lane required")
            cap = limit.max_fit_seconds
            registered_cap = registration.get("max_fit_seconds")
            if lane == "failure" and parent["registration"].get("max_fit_seconds") is not None and registered_cap != parent["registration"]["max_fit_seconds"]:
                raise ValueError("recovery must retain the conditional registered deadline")
            if registered_cap is not None:
                if (family != "gnn" or lane not in {"conditional", "failure"}
                        or isinstance(registered_cap, bool) or not isinstance(registered_cap, int)
                        or not 0 < registered_cap <= limit.max_fit_seconds):
                    raise ValueError("only conditional GNN fits may register a tighter positive deadline")
                if lane == "failure" and registered_cap != parent["registration"].get("max_fit_seconds"):
                    raise ValueError("recovery must retain the conditional registered deadline")
                cap = min(cap, registered_cap)
            if limit.max_total_seconds is not None:
                spent = sum(f["seconds"] if f["state"] != "reserved" else f["cap_seconds"] for f in fits)
                confirmation_left = limit.lanes["confirmation"] - sum(f["lane"] == "confirmation" for f in fits)
                earmark = max(0, confirmation_left - (lane == "confirmation")) * limit.max_fit_seconds
                cap = min(cap, limit.max_total_seconds - spent - earmark)
                if cap <= 0:
                    raise ValueError("wall-clock exhausted; confirmation reserve protected")
            value["fits"].append({"name": name, "family": family, "lane": lane,
                "registration": registration, "cap_seconds": cap, "state": "reserved",
                "at": datetime.now(UTC).isoformat()})
            self._save(value)
        return FitAttempt(self, name, cap, clock(), clock)

    def finish(self, name, state, seconds):
        self._active()
        if state not in {"completed", "failed", "abandoned"} or not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("invalid fit completion")
        with exclusive(self.path.with_suffix(".lock")):
            value = self.read()
            entry = next(f for f in value["fits"] if f["name"] == name)
            if entry["state"] != "reserved":
                raise ValueError("fit already finalized")
            if state == "completed" and seconds >= entry["cap_seconds"]:
                raise ValueError("deadline exceeded; record failed instead of completed")
            # A killed process's unknown elapsed time cannot be reported as zero.
            entry.update(state=state, seconds=max(seconds, entry["cap_seconds"]) if state == "abandoned" else seconds)
            self._save(value)

    def freeze_decision(self, decision_sha256):
        self._active()
        if len(decision_sha256) != 64 or any(c not in "0123456789abcdef" for c in decision_sha256):
            raise ValueError("decision SHA256 required")
        with exclusive(self.path.with_suffix(".lock")):
            value = self.read()
            self._resolved(value)
            if value["decision_sha256"]:
                raise ValueError("decision already frozen")
            value["decision_sha256"] = decision_sha256
            self._save(value)

    def open_results(self, kind):
        self._active()
        if kind not in {"outer", "test"}:
            raise ValueError("unknown result access")
        with exclusive(self.path.with_suffix(".lock")):
            value = self.read()
            self._resolved(value)
            if not value["decision_sha256"]:
                raise ValueError("final decision must be frozen before label access")
            if kind == "test" and not value["outer_access"]:
                raise ValueError("test review follows outer confirmation")
            if value[kind + "_access"]:
                raise ValueError("result access already reserved; no repeat batches")
            if kind == "outer":
                def confirmation_origin(fit):
                    while fit["lane"] == "failure":
                        fit = next(f for f in value["fits"] if f["name"] == fit["registration"]["retry_of"])
                    return fit["lane"] == "confirmation"

                finished = [f for f in value["fits"] if f["family"] == "gnn" and f["state"] == "completed" and confirmation_origin(f)]
                grid = {(w.name, s) for w in self.contract.windows for s in self.contract.confirmation_seeds}
                actual = {(f["registration"]["fold"], f["registration"].get("seed")) for f in finished}
                if not grid <= actual:
                    raise ValueError("three folds x three successful seeds required before opening outer scores")
            value[kind + "_access"].append({"state": "reserved_before_label_read",
                "decision_sha256": value["decision_sha256"], "at": datetime.now(UTC).isoformat()})
            self._save(value)


class FitAttempt:
    def __init__(self, ledger, name, cap, started, clock):
        self.ledger, self.name, self.cap, self.started, self.clock = ledger, name, cap, started, clock

    def guard(self):
        if self.clock() - self.started >= self.cap:
            raise TimeoutError("5B fit wall-clock deadline reached")

    def finish(self, state):
        self.ledger.finish(self.name, state, self.clock() - self.started)
