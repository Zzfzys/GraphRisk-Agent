"""Small, allowlisted request trace for the local investigation API."""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import UTC, datetime
from uuid import uuid4

from graphrisk.serving.investigation_service import InvestigationRecord
from graphrisk.monitoring.operations import emit_boundary, emit_run

LOGGER = logging.getLogger("uvicorn.error")
_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_REQUEST_ID = re.compile(r"req-[0-9a-f]{32}\Z")


def new_request_id() -> str:
    """Always generate a server-owned ID; client headers are never trusted as identity."""
    return f"req-{uuid4().hex}"


def valid_request_id(value: str | None) -> bool:
    return value is None or bool(_REQUEST_ID.fullmatch(value))


def _token(value: object) -> str | None:
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else None


def trace_event(*, request_id: str, phase: str, status: str, run_id: str | None = None,
                tool: str | None = None, error_code: str | None = None,
                elapsed_ms: float | None = None, model_version: str | None = None,
                index_version: str | None = None, http_status: int | None = None) -> None:
    """Log only fixed metadata fields; never serialize requests, responses or prompts."""
    if not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("request_id must be server generated")
    duration = (round(float(elapsed_ms), 2) if isinstance(elapsed_ms, (int, float))
                and math.isfinite(elapsed_ms) and elapsed_ms >= 0 else None)
    entry = {
        "at": datetime.now(UTC).isoformat(), "request_id": request_id,
        "run_id": _token(run_id), "phase": _token(phase),
        "tool": _token(tool), "status": _token(status),
        "error_code": _token(error_code), "elapsed_ms": duration,
        "model_version": _token(model_version), "index_version": _token(index_version),
        "http_status": http_status if isinstance(http_status, int) and 100 <= http_status <= 599 else None,
    }
    LOGGER.info("graphrisk_trace %s", json.dumps(entry, ensure_ascii=False, sort_keys=True))
    emit_boundary(request_id=request_id, phase=phase, status=status, run_id=run_id,
                  error_code=error_code, elapsed_ms=duration, model_version=model_version,
                  index_version=index_version, http_status=entry["http_status"])


def trace_run(record: InvestigationRecord, request_id: str) -> None:
    """Emit the existing tool, agent-node and bounded dependency-attempt evidence."""
    if record.idempotent_replay:
        emit_run(record, request_id)
        return
    versions = record.versions
    base = {"request_id": request_id, "run_id": record.run_id,
            "model_version": versions.generation_model_revision or versions.generation_model_id,
            "index_version": versions.policy_index_version}
    graph = record.result.graph_run
    if graph is not None:
        if graph.investigation_audit is not None:
            for event in graph.investigation_audit.events:
                trace_event(**base, phase="tool", tool=event.tool, status=event.status,
                            error_code=event.error_code, elapsed_ms=event.duration_ms)
        for node in graph.node_trace:
            trace_event(**base, phase="agent_node", tool=node, status="executed")
    for attempt in record.result.audit.attempts:
        trace_event(**base, phase="dependency_attempt", tool=attempt.kind,
                    status=attempt.status, error_code=attempt.error_code,
                    elapsed_ms=attempt.elapsed_ms)

    emit_run(record, request_id)
