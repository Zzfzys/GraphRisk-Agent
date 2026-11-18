"""Allowlisted operational metadata without changing persisted reports or API views."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
import logging
import math
import re
from typing import Literal

from pydantic import Field
from graphrisk.investigation.contracts import StrictModel

LOGGER = logging.getLogger("uvicorn.error")
HASH = r"^[a-f0-9]{64}$"
RUN = re.compile(r"run-[A-Za-z0-9]{1,16}-[a-f0-9]{32}\Z")
REQUEST = re.compile(r"req-[a-f0-9]{32}\Z")
TOOLS = {"get_transaction", "get_transaction_features", "get_risk_score",
         "find_related_transactions", "find_risky_neighbors", "policy_retrieval", "generation"}
STATUSES = {"verified", "partial_evidence", "insufficient_evidence", "invalid_input", "not_found",
            "invalid_plan", "retrieval_error", "generation_error", "deadline_exceeded", "budget_exhausted",
            "upstream_error", "ok", "error", "transient_error", "permanent_error", "deadline", "budget_stop",
            "completed", "executed", "pending", "approved", "rejected", "expired", "success", "unknown"}


def binding(value: str | None) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value is not None else None


class VersionBindings(StrictModel):
    primary_model: str | None = Field(default=None, pattern=HASH)
    graph_model: str | None = Field(default=None, pattern=HASH)
    graph_comparison: str | None = Field(default=None, pattern=HASH)
    model_decision: str | None = Field(default=None, pattern=HASH)
    llm_model: str | None = Field(default=None, pattern=HASH)
    llm_revision: str | None = Field(default=None, pattern=HASH)
    prompt_protocol: str | None = Field(default=None, pattern=HASH)
    policy_index: str | None = Field(default=None, pattern=HASH)
    # Legacy boundary receives revision-or-model fallback, not an attested revision.
    reported_model: str | None = Field(default=None, pattern=HASH)


class OperationEvent(StrictModel):
    schema_version: Literal["stage09-operation-v1"] = "stage09-operation-v1"
    event_id: str = Field(pattern=HASH)
    at: datetime
    request_id: str | None = Field(default=None, pattern=r"^req-[a-f0-9]{32}$")
    run_id: str | None = Field(default=None, pattern=r"^run-[A-Za-z0-9]{1,16}-[a-f0-9]{32}$")
    kind: Literal["run", "replay", "tool", "attempt", "http", "approval", "measurement_error"]
    status: str = Field(pattern=r"^[a-z_]{1,40}$")
    tool: Literal["get_transaction", "get_transaction_features", "get_risk_score",
                  "find_related_transactions", "find_risky_neighbors", "policy_retrieval", "generation", "other"] | None = None
    attempt_no: int | None = Field(default=None, ge=1, strict=True)
    attempt_id: str | None = Field(default=None, pattern=r"^att-[a-f0-9]{32}$")
    error_family: Literal["none", "request", "missing_evidence", "schema", "evidence", "deadline",
                          "budget", "resource", "storage", "retrieval", "generation", "dependency", "observability"] = "none"
    elapsed_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    http_status: int | None = Field(default=None, ge=100, le=599, strict=True)
    versions: VersionBindings = Field(default_factory=VersionBindings)
    report_sha256: str | None = Field(default=None, pattern=HASH)
    prompt_sha256: str | None = Field(default=None, pattern=HASH)
    prompt_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)
    generation_attempts: int | None = Field(default=None, ge=0, strict=True)
    policy_attempts: int | None = Field(default=None, ge=0, strict=True)
    retries: int | None = Field(default=None, ge=0, strict=True)
    graph_state: Literal["available", "unavailable", "not_recorded"] = "not_recorded"
    cold_warm: Literal["unknown"] = "unknown"
    cold_warm_basis: Literal["not_independently_measured"] = "not_independently_measured"
    external_action_count: Literal[0] = 0


def error_family(status: str, issue: str | None = None) -> str:
    code = (issue or "").lower()
    if any(part in code for part in ("resource", "memory", "inference_busy")):
        return "resource"
    if "persist" in code or "database" in code or "checkpoint" in code:
        return "storage"
    if status in {"deadline", "deadline_exceeded"} or "timeout" in code or "deadline" in code:
        return "deadline"
    if status in {"budget_stop", "budget_exhausted"}:
        return "budget"
    if "schema" in code or "draft_invalid" in code:
        return "schema"
    if any(part in code for part in ("evidence", "citation", "binding", "quote", "report_changed", "policy_stale")):
        return "evidence"
    if status in {"invalid_input", "invalid_plan"} or code.startswith("invalid_"):
        return "request"
    if status in {"partial_evidence", "insufficient_evidence", "not_found"}:
        return "missing_evidence"
    if status == "retrieval_error":
        return "retrieval"
    if status == "generation_error":
        return "generation"
    if status in {"upstream_error", "permanent_error", "transient_error", "error"}:
        return "dependency"
    return "none"


def _event(*, kind, request_id=None, run_id=None, at=None, tool=None, attempt_no=None, **fields):
    request_id = request_id if isinstance(request_id, str) and REQUEST.fullmatch(request_id) else None
    run_id = run_id if isinstance(run_id, str) and RUN.fullmatch(run_id) else None
    status = fields.get("status", "unknown")
    fields["status"] = status if status in STATUSES else "unknown"
    tool = tool if tool in TOOLS else "other" if tool is not None else None
    key = f"{request_id}|{run_id}|{kind}|{tool}|{attempt_no}"
    event_id = hashlib.sha256(key.encode()).hexdigest()
    attempt_id = "att-" + hashlib.sha256(f"{run_id}|{kind}|{tool}|{attempt_no}".encode()).hexdigest()[:32] if attempt_no is not None else None
    return OperationEvent(event_id=event_id, kind=kind, at=at or datetime.now(UTC),
                          request_id=request_id, run_id=run_id, tool=tool,
                          attempt_no=attempt_no, attempt_id=attempt_id, **fields)


def project_run(record, request_id: str | None = None) -> list[OperationEvent]:
    """Project validated records read-only; absent historical IDs/measurements stay absent."""
    versions, result = record.versions, record.result
    report_run = result.report_run
    report = report_run.report if report_run else None
    report_audit = report_run.audit if report_run else None
    refs = VersionBindings(
        primary_model=binding(versions.primary_model_version or (report.model_version if report else None)),
        graph_model=binding(versions.experimental_graph_model_version or (report.graph_model_version if report else None)),
        graph_comparison=binding(versions.graph_comparison_run), model_decision=binding(versions.model_decision_sha256),
        llm_model=binding(versions.generation_model_id), llm_revision=binding(versions.generation_model_revision),
        prompt_protocol=binding(report_audit.prompt_version if report_audit else versions.report_protocol),
        policy_index=binding(versions.policy_index_version))
    base = dict(request_id=request_id if request_id is not None else record.created_request_id,
                run_id=record.run_id, at=record.created_at, versions=refs)
    if record.idempotent_replay:
        return [_event(**base, kind="replay", status=record.status, report_sha256=record.report_sha256)]
    audit = result.audit
    graph = result.graph_run
    investigation = graph.investigation_report if graph else None
    neighbors = investigation.risky_neighbors if investigation else None
    graph_state = "not_recorded" if neighbors is None else "available" if neighbors.status == "ok" else "unavailable"
    issue = report_run.issue_code if report_run else None
    events = [_event(**base, kind="run", status=record.status, error_family=error_family(record.status, issue),
                     elapsed_ms=audit.elapsed_ms, report_sha256=record.report_sha256,
                     prompt_sha256=report_audit.prompt_sha256 if report_audit else None,
                     prompt_tokens=audit.prompt_tokens, output_tokens=audit.output_tokens,
                     generation_attempts=audit.generation_attempts, policy_attempts=audit.policy_attempts,
                     retries=audit.retries, graph_state=graph_state)]
    if graph is not None and graph.investigation_audit is not None:
        for item in graph.investigation_audit.events:
            events.append(_event(**base, kind="tool", tool=item.tool, attempt_no=item.sequence,
                                 status=item.status, elapsed_ms=item.duration_ms,
                                 error_family=error_family(item.status, item.error_code)))
    for item in audit.attempts:
        events.append(_event(**base, kind="attempt", tool=item.kind, attempt_no=item.attempt,
                             status=item.status, elapsed_ms=item.elapsed_ms,
                             error_family=error_family(item.status, item.error_code)))
    return events


def emit_events(events):
    for event in events:
        LOGGER.info("graphrisk_operation %s", event.model_dump_json())


def emit_run(record, request_id):
    try:
        emit_events(project_run(record, request_id))
    except (ValueError, TypeError, AttributeError):
        emit_events([_event(kind="measurement_error", request_id=request_id, status="error",
                            error_family="observability")])


def emit_boundary(*, request_id, phase, status, run_id=None, tool=None, error_code=None,
                  elapsed_ms=None, model_version=None, index_version=None, http_status=None):
    if phase not in {"http", "approval"}:
        return
    try:
        emit_events([_event(kind=phase, request_id=request_id, run_id=run_id, status=status,
                            error_family=error_family(status, error_code), elapsed_ms=elapsed_ms,
                            http_status=http_status,
                            versions=VersionBindings(reported_model=binding(model_version), policy_index=binding(index_version)))])
    except (ValueError, TypeError):
        emit_events([_event(kind="measurement_error", request_id=request_id, status="error",
                            error_family="observability")])


def latency_summary(values):
    items = sorted(float(v) for v in values if v is not None)
    if any(not math.isfinite(v) or v < 0 for v in items):
        raise ValueError("latency must be finite and nonnegative")
    def quantile(q):
        if not items:
            return None
        point = (len(items)-1)*q
        lower, upper = math.floor(point), math.ceil(point)
        return items[lower] + (items[upper]-items[lower])*(point-lower)
    return {"observed": len(items), "p50_ms": quantile(.5), "p95_ms": quantile(.95),
            "small_sample": len(items) < 100, "production_slo_claimed": False}


def summarize_events(events):
    unique = {}
    for raw in events:
        event = OperationEvent.model_validate(raw)
        if event.event_id in unique and unique[event.event_id] != event:
            raise ValueError("conflicting operational event identity")
        unique[event.event_id] = event
    rows = list(unique.values())
    runs = [e for e in rows if e.kind == "run"]
    timings = {}
    for event in rows:
        if event.kind in {"tool", "attempt"}:
            timings.setdefault(f"{event.kind}:{event.tool}", []).append(event.elapsed_ms)
    return {"schema_version": "stage09-operation-summary-v1", "run_count": len(runs),
            "replays_not_counted_as_execution": sum(e.kind == "replay" for e in rows),
            "statuses": dict(sorted(Counter(e.status for e in runs).items())),
            "error_families": dict(sorted(Counter(e.error_family for e in runs).items())),
            "run_latency": latency_summary(e.elapsed_ms for e in runs),
            "tool_latency": {k: latency_summary(v) for k, v in sorted(timings.items())},
            "graph_coverage": dict(sorted(Counter(e.graph_state for e in runs).items())),
            "cold_warm": dict(sorted(Counter(e.cold_warm for e in runs).items())),
            "request_id_observed_runs": sum(e.request_id is not None for e in runs),
            "prompt_tokens": {"observed_runs": sum(e.prompt_tokens is not None for e in runs),
                              "total_observed": sum(e.prompt_tokens or 0 for e in runs)},
            "output_tokens": {"observed_runs": sum(e.output_tokens is not None for e in runs),
                              "total_observed": sum(e.output_tokens or 0 for e in runs)},
            "retry_count": sum(e.retries or 0 for e in runs),
            "measurement_errors": sum(e.kind == "measurement_error" for e in rows),
            "external_action_count": 0, "real_model_quality_claimed": False}
