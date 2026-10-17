"""Stage 10 frozen measurement primitives; network is always injected explicitly."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from graphrisk.agent.quality_contracts import QualityDraft
from graphrisk.agent.quality_reporting import QUALITY_PROMPT
from graphrisk.demo.stage09_evaluation import digest, exclusive

PARENT_SHA = "d896ff62344c3241d6fbad9f4001fed2487043c4c70cdf772507b123df21ba91"
MODEL_REVISION = "359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7"
PROTOCOL = {
    "version": "stage10-measurement-v1", "baseline_used": 130, "global_limit": 144,
    "additional_limit": 14, "first_cases": 10, "per_split": 5, "repeats": 2,
    "minimum_joint_pass": 9, "maximum_unsafe_publications": 0,
    "parent_ledger_sha256": PARENT_SHA,
    "model_id": "qwen3:4b", "model_revision": MODEL_REVISION,
    "endpoint": "http://127.0.0.1:11434/api/generate", "output_tokens": 512,
    "keep_alive": 0, "think": False, "temperature": 0,
    "percentile_method": "linear_hyndman_fan_7", "p99_minimum_n": 100,
    "workers": 1, "concurrency": [1, 2], "fast_samples_per_group": 100,
    "llm_minimum_available_gib": 3, "cpu_minimum_available_gib": 1.5,
    "cpu_maximum_rss_mib": 1280, "paid_calls": 0, "new_real_fits": 0,
    "route2_execution_registration_required": True,
}


def binding(path: Path) -> dict:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return {"bytes": path.stat().st_size, "sha256": hasher.hexdigest()}


def load_protocol(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value != PROTOCOL:
        raise ValueError("measurement_contract_drift: freeze a new reviewed protocol version")
    return value


def select_cases(rows: list[dict], excluded: set[str]) -> list[dict]:
    """Accept ONLY projected ID/time/coverage fields; rank evenly by time, never score."""
    columns = {"transaction_id", "event_second", "split", "selection_cutoff"}
    seen = set()
    for row in rows:
        if set(row) != columns:
            raise ValueError("selection_requires_only_id_time_coverage_columns")
        key = row["transaction_id"]
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("selection_duplicate_or_invalid_id")
        seen.add(key)
        if (row["split"] not in {"validation", "test"}
                or type(row["event_second"]) is not int
                or type(row["selection_cutoff"]) is not int
                or row["event_second"] <= row["selection_cutoff"]):
            raise ValueError("selection_invalid_time_eligibility")
    selected = []
    for split in ("validation", "test"):
        eligible = sorted((r for r in rows if r["split"] == split and r["transaction_id"] not in excluded),
                          key=lambda r: (r["event_second"], r["transaction_id"]))
        if len(eligible) < 5:
            raise ValueError("selection_quota_unavailable; no fallback or replacement")
        for i in range(5):
            row = eligible[i * (len(eligible) - 1) // 4]
            selected.append({**row, "case_id": f"case-{len(selected)+1:02d}",
                             "request": {"transaction_id": row["transaction_id"], "dataset_source": "ieee_cis",
                                         "policy_as_of": "2026-07-01", "relation_limit": 20}})
    return selected


class Sample(BaseModel):
    """Metadata whitelist. Never serialize a prompt, URL, exception message or report here."""
    model_config = ConfigDict(extra="forbid", strict=True)
    case_id: str = Field(pattern=r"^case-\d{2}(?:-repeat)?$")
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    protocol_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Literal["fixture", "real"]
    component: Literal["ollama_http", "agent", "api_read", "tool", "prospective", "history_replay"]
    outcome: Literal["ok", "http_error", "timeout", "transport_error", "budget_stop", "resource_stop", "not_run"]
    elapsed_ms: float | None = None
    concurrency: Literal[1, 2] = 1
    workers: Literal[1] = 1
    residency: Literal["cold", "warm", "unknown"] = "unknown"
    residency_basis: Literal["matched_digest_present_before", "matched_digest_absent_before", "not_measured"] = "not_measured"
    prompt_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    load_ms: float | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    attempt_sequence: int | None = Field(default=None, ge=131, le=144)

    @field_validator("elapsed_ms", "load_ms")
    @classmethod
    def finite_nonnegative(cls, value):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("duration_must_be_finite_nonnegative")
        return value

    @model_validator(mode="after")
    def measured_boundaries(self):
        expected = {"cold": "matched_digest_absent_before", "warm": "matched_digest_present_before",
                    "unknown": "not_measured"}
        if self.residency_basis != expected[self.residency]:
            raise ValueError("residency_requires_matching_observation")
        if self.outcome in {"ok", "http_error", "timeout", "transport_error"} and self.elapsed_ms is None:
            raise ValueError("sent_request_requires_measured_duration")
        if self.outcome == "not_run" and self.elapsed_ms is not None:
            raise ValueError("not_run_has_no_latency")
        return self


def percentile(values: list[float], probability: float) -> float | None:
    if not 0 <= probability <= 1:
        raise ValueError("invalid_percentile")
    if not values:
        return None
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("invalid_latency")
    data = sorted(values)
    rank = (len(data) - 1) * probability
    low = math.floor(rank)
    return data[low] + (data[math.ceil(rank)] - data[low]) * (rank - low)


def summarize(samples: list[Sample], expected: int) -> dict:
    if expected < len(samples) or expected < 1:
        raise ValueError("invalid_expected_denominator")
    groups = {}
    for row in samples:
        key = (row.scope, row.component, row.concurrency, row.workers, row.residency)
        groups.setdefault(key, []).append(row)
    result = []
    for key, records in sorted(groups.items()):
        values = [r.elapsed_ms for r in records if r.elapsed_ms is not None]
        ok = [r.elapsed_ms for r in records if r.outcome == "ok" and r.elapsed_ms is not None]
        n = len(values)
        result.append({"scope": key[0], "component": key[1], "concurrency": key[2], "workers": key[3],
                       "residency": key[4], "attempted_records": len(records), "timed_n": n,
                       "ok": sum(r.outcome == "ok" for r in records),
                       "errors": sum(r.outcome not in {"ok", "not_run"} for r in records),
                       "p50_ms": percentile(values, .5), "p95_ms": percentile(values, .95),
                       "p99_ms": percentile(values, .99) if n >= 100 and key[1] != "agent" else None,
                       "p99_reason": "empirical_only" if n >= 100 and key[1] != "agent" else "insufficient_samples",
                       "successful_p50_ms": percentile(ok, .5), "successful_p95_ms": percentile(ok, .95),
                       "percentile_method": PROTOCOL["percentile_method"]})
    return {"expected": expected, "recorded": len(samples),
            "not_run": expected - sum(r.outcome != "not_run" for r in samples),
            "completed_ok": sum(r.outcome == "ok" for r in samples),
            "success_rate_of_expected": sum(r.outcome == "ok" for r in samples) / expected,
            "groups": result, "unknown_token_values_are_not_zero": True}


def quality_summary(first_case_ids: list[str], rows: list[dict]) -> dict:
    if len(first_case_ids) != 10 or len(set(first_case_ids)) != 10:
        raise ValueError("ten_unique_first_cases_required")
    allowed = set(first_case_ids) | {first_case_ids[i] + "-repeat" for i in (0, 5)}
    by_key = {}
    for row in rows:
        if (set(row) != {"run_key", "joint_pass", "unsafe"} or row["run_key"] not in allowed
                or row["run_key"] in by_key or type(row["joint_pass"]) is not bool or type(row["unsafe"]) is not bool):
            raise ValueError("invalid_quality_row")
        by_key[row["run_key"]] = row
    not_run = sum(k not in by_key for k in first_case_ids)
    joint = sum(by_key[k]["joint_pass"] and not by_key[k]["unsafe"] for k in first_case_ids if k in by_key)
    unsafe = sum(r["unsafe"] for r in rows)
    status = "failed" if unsafe else "pending" if not_run else "passed" if joint >= 9 else "failed"
    return {"status": status, "first_denominator": 10, "joint_pass": joint, "not_run": not_run,
            "unsafe_including_repeats": unsafe, "repeat_results": sum(k.endswith("-repeat") for k in by_key),
            "scope": "bounded_judgments_not_fraud_accuracy"}


class BudgetStop(ValueError):
    pass


class AppendLedger:
    """Hash-chained JSONL; append-only, serialized, durable reservation BEFORE send.

    One canonical stage-wide path is selected at registration. Hashes detect drift,
    not malicious edits by an actor who can replace all evidence. Unknown sends
    stop the experiment and require audit; no retry/refund/reset method exists.
    """
    def __init__(self, path: Path, parent: Path, parent_binding: dict, manifest_sha256: str,
                 scope: Literal["fixture", "real"], run_keys: list[str]):
        if scope not in {"fixture", "real"} or len(set(run_keys)) != 12 or len(run_keys) != 12:
            raise BudgetStop("invalid_ledger_registration")
        self.path, self.parent, self.parent_binding = path, parent, parent_binding
        self.header = {"kind": "header", "scope": scope, "parent": parent_binding,
                       "baseline_used": 130, "limit": 144, "manifest_sha256": manifest_sha256,
                       "run_keys": run_keys}
        self.scope = scope

    def initialize(self):
        with exclusive(self.path.with_suffix(".lock")):
            self._parent()
            if self.path.exists():
                self._read()
                return
            with self.path.open("xb") as stream:
                stream.write(self._encoded(self.header, "0" * 64))
                stream.flush()
                os.fsync(stream.fileno())

    def _parent(self):
        try:
            if binding(self.parent) != self.parent_binding:
                raise BudgetStop("parent_ledger_changed")
            value = json.loads(self.parent.read_text(encoding="utf-8"))
        except OSError:
            raise BudgetStop("parent_ledger_unavailable") from None
        if len(value["attempts"]) != 130 or [r["sequence"] for r in value["attempts"]] != list(range(1, 131)):
            raise BudgetStop("parent_attempt_count_changed")

    @staticmethod
    def _encoded(event: dict, previous: str) -> bytes:
        body = {**event, "previous": previous}
        return (json.dumps({**body, "sha256": digest(body)}, sort_keys=True, ensure_ascii=False,
                           allow_nan=False) + "\n").encode()

    def _read(self) -> list[dict]:
        self._parent()
        try:
            data = self.path.read_bytes()
            if not data.endswith(b"\n"):
                raise ValueError("truncated")
            records = [json.loads(line) for line in data.splitlines()]
            previous = "0" * 64
            for row in records:
                if row["previous"] != previous or digest({k: v for k, v in row.items() if k != "sha256"}) != row["sha256"]:
                    raise ValueError("chain")
                previous = row["sha256"]
            if {k: v for k, v in records[0].items() if k not in {"previous", "sha256"}} != self.header:
                raise ValueError("header")
            reservations = [r for r in records[1:] if r["kind"] == "reserve"]
            if len(reservations) > 14 or [r["sequence"] for r in reservations] != list(range(131, 131 + len(reservations))):
                raise ValueError("sequence")
            pending = set()
            for row in records[1:]:
                if row["kind"] == "reserve" and row["case_id"] in self.header["run_keys"]:
                    pending.add(row["sequence"])
                elif row["kind"] == "response" and row["sequence"] in pending and type(row["http_status"]) is int:
                    pending.remove(row["sequence"])
                elif row["kind"] == "uncertain" and row["sequence"] in pending:
                    # Remains pending permanently until a separately reviewed recovery exists.
                    continue
                else:
                    raise ValueError("event")
            return records
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            raise BudgetStop("ledger_corrupt_or_missing; stop without sending") from None

    def _append(self, event: dict, rows: list[dict]):
        with self.path.open("ab") as stream:
            stream.write(self._encoded(event, rows[-1]["sha256"]))
            stream.flush()
            os.fsync(stream.fileno())

    def reserve(self, case_id: str, request_sha256: str) -> int:
        with exclusive(self.path.with_suffix(".lock")):
            rows = self._read()
            reserves = [r for r in rows if r["kind"] == "reserve"]
            resolved = {r["sequence"] for r in rows if r["kind"] == "response"}
            if any(r["sequence"] not in resolved for r in reserves):
                raise BudgetStop("unresolved_send_requires_audit_no_auto_retry")
            if len(reserves) >= 14:
                raise BudgetStop("global_generation_budget_exhausted")
            if case_id not in self.header["run_keys"] or sum(r["case_id"] == case_id for r in reserves) >= 2:
                raise BudgetStop("unregistered_or_exhausted_case")
            if not re.fullmatch(r"[a-f0-9]{64}", request_sha256):
                raise BudgetStop("invalid_request_binding")
            sequence = 131 + len(reserves)
            self._append({"kind": "reserve", "sequence": sequence, "case_id": case_id,
                          "request_sha256": request_sha256, "at": datetime.now(UTC).isoformat()}, rows)
            return sequence

    def finish(self, sequence: int, status: int | None):
        with exclusive(self.path.with_suffix(".lock")):
            rows = self._read()
            reserved = {r["sequence"] for r in rows if r["kind"] == "reserve"}
            finished = {r["sequence"] for r in rows if r["kind"] in {"response", "uncertain"}}
            if sequence not in reserved or sequence in finished or (status is not None and (type(status) is not int or not 100 <= status <= 599)):
                raise BudgetStop("invalid_attempt_completion")
            self._append({"kind": "response" if status is not None else "uncertain",
                          "sequence": sequence, "http_status": status}, rows)

    def state(self) -> dict:
        with exclusive(self.path.with_suffix(".lock")):
            rows = self._read()
            reservations = [r for r in rows if r["kind"] == "reserve"]
            resolved = {r["sequence"] for r in rows if r["kind"] == "response"}
            return {"scope": self.scope, "baseline_used": 130, "appended_attempts": len(reservations),
                    "global_used": 130 + len(reservations), "remaining": 14 - len(reservations),
                    "unresolved": sum(r["sequence"] not in resolved for r in reservations)}


class GuardedTransport(httpx.BaseTransport):
    """Wrap the REAL send boundary; caller injects MockTransport or no-retry HTTPTransport.

    The current CLI only uses MockTransport with a separate fixture ledger. Native
    OllamaQualityProvider.with_deadline retains this client/guard across retries.
    """
    def __init__(self, delegate: httpx.BaseTransport | None, ledger: AppendLedger, case_id: str,
                 packet_sha256: str, *, clock=time.perf_counter, resource_guard=None,
                 residency_before: bool | None = None):
        if ledger.scope == "real":
            if delegate is not None or resource_guard is None:
                raise ValueError("real_guard_requires_owned_zero_retry_transport_and_resource_guard")
            delegate = httpx.HTTPTransport(retries=0, trust_env=False)
        elif not isinstance(delegate, httpx.MockTransport):
            raise ValueError("fixture_and_real_ledgers_must_be_separate")
        if not re.fullmatch(r"[a-f0-9]{64}", packet_sha256):
            raise ValueError("expected_packet_binding_required")
        if residency_before is not None and type(residency_before) is not bool:
            raise ValueError("residency_requires_boolean_observation_or_none")
        self.delegate, self.ledger, self.case_id = delegate, ledger, case_id
        self.packet_sha256, self.clock, self.resource_guard = packet_sha256, clock, resource_guard or (lambda: None)
        self.residency_before = residency_before
        self.samples: list[Sample] = []
        self.private_calls: list[dict] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if (request.url.scheme != "http" or request.url.host != "127.0.0.1" or request.url.port != 11434
                or request.url.query or request.url.userinfo):
            raise ValueError("only_pinned_loopback_ollama_allowed")
        if request.method == "GET" and request.url.path in {"/api/tags", "/api/ps"}:
            return self.delegate.handle_request(request)
        if request.method != "POST" or request.url.path != "/api/generate":
            raise ValueError("unexpected_model_endpoint")
        payload = json.loads(request.read())
        prefix = QUALITY_PROMPT + "\nEVIDENCE_JSON:\n"
        if (set(payload) != {"model", "prompt", "format", "stream", "options", "keep_alive", "think"}
                or payload["model"] != PROTOCOL["model_id"] or payload["stream"] is not False
                or payload["think"] is not False or type(payload["keep_alive"]) is not int or payload["keep_alive"] != 0
                or payload["options"] != {"temperature": 0, "num_predict": 512}
                or payload["format"] != QualityDraft.model_json_schema()
                or not payload["prompt"].startswith(prefix)
                or digest(json.loads(payload["prompt"][len(prefix):])) != self.packet_sha256):
            raise ValueError("frozen_model_request_changed")
        def stopped(outcome):
            self.samples.append(Sample(case_id=self.case_id, input_sha256=self.packet_sha256,
                                protocol_sha256=digest(PROTOCOL), scope=self.ledger.scope,
                                component="ollama_http", outcome=outcome))
        try:
            self.resource_guard()
        except Exception:
            stopped("resource_stop")
            raise
        try:
            sequence = self.ledger.reserve(self.case_id, digest(payload))
        except BudgetStop:
            stopped("budget_stop")
            raise
        started = self.clock()
        outcome, status, body = "transport_error", None, None
        try:
            response = self.delegate.handle_request(request)
            response.read()
            status = response.status_code
            outcome = "ok" if 200 <= status < 300 else "http_error"
            try:
                body = response.json()
            except ValueError:
                body = None
            return response
        except httpx.TimeoutException:
            outcome = "timeout"
            raise
        finally:
            elapsed = (self.clock() - started) * 1000
            # Completion must be durable even if subsequent metadata validation fails.
            self.ledger.finish(sequence, status)
            def token(name):
                v = body.get(name) if isinstance(body, dict) else None
                return v if type(v) is int and v >= 0 else None
            load = body.get("load_duration") if isinstance(body, dict) else None
            load_ms = load / 1_000_000 if type(load) in (int, float) and math.isfinite(load) and load >= 0 else None
            residency = "unknown" if self.residency_before is None else "warm" if self.residency_before else "cold"
            basis = "not_measured" if self.residency_before is None else "matched_digest_present_before" if self.residency_before else "matched_digest_absent_before"
            sample = Sample(case_id=self.case_id, input_sha256=self.packet_sha256, protocol_sha256=digest(PROTOCOL),
                            scope=self.ledger.scope, component="ollama_http", outcome=outcome, elapsed_ms=elapsed,
                            http_status=status, attempt_sequence=sequence, residency=residency, residency_basis=basis,
                            prompt_tokens=token("prompt_eval_count"), output_tokens=token("eval_count"), load_ms=load_ms)
            self.samples.append(sample)
            self.private_calls.append({"request": payload, "response": body, "metadata": sample.model_dump()})

    def close(self):
        self.delegate.close()
