"""Current-product probes for the Stage 07 challenge contract."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from api.main import create_app
from graphrisk.demo.stage06_evaluation import CountingRunner, _fixture_results, _service
from graphrisk.demo.stage07_evaluation import POLICY_CORPUS_SHA256
from graphrisk.serving.investigation_service import Stage06Service
from graphrisk.serving.postgres_service import PostgresInvestigationService


def probe_pending_restart(repo: Path, index_path: Path, *, dsn: str | None = None) -> dict[str, Any]:
    """Use two actual API service lifetimes; the runner is a deterministic fixture."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    results, index_version = _fixture_results(repo, index_path)
    source = results["normal"]
    first_service, _ = _service(source, index_version)
    if dsn is not None:
        first_service = PostgresInvestigationService(
            dsn=dsn, dataset_source="synthetic", versions=first_service.versions,
            runner=CountingRunner(source), max_runs=1000,
        )
    request = {"transaction_id": "T3", "dataset_source": "synthetic",
               "policy_as_of": "2026-07-01", "relation_limit": 3}
    with TestClient(create_app(lambda: first_service)) as client:
        created = client.post("/api/v1/investigations", json=request)
        if created.status_code != 201:
            raise RuntimeError(f"Stage 07 restart probe could not create pending: {created.status_code}")
        before = created.json()
        if before["approval"] is None or before["approval"]["status"] != "pending":
            raise RuntimeError("Stage 07 restart probe did not start a pending approval")
    new_service = (PostgresInvestigationService(
        dsn=dsn, dataset_source="synthetic", versions=first_service.versions,
        runner=CountingRunner(source), max_runs=1000,
    ) if dsn is not None else Stage06Service(
        dataset_source="synthetic", versions=first_service.versions,
        runner=CountingRunner(source), instance_id="stage07eval"))
    with TestClient(create_app(lambda: new_service)) as client:
        response = client.get(f"/api/v1/investigations/{before['run_id']}")
        after = response.json()
    report = before["report"]
    assert report is not None
    citations = report["policy_citations"]
    retrieval = source.graph_run.policy_retrieval if source.graph_run else None
    return {
        "probe_kind": "real_database" if dsn is not None else "fake_api",
        "applied_fault": "restart_after_pending",
        "source_request": request,
        "primary_tool": "GET /api/v1/investigations/{run_id}",
        "primary_parameters": {"run_id": before["run_id"]},
        "created_run_id": before["run_id"], "target_run_id": before["run_id"],
        "session_id": before["session_id"],
        "report_sha256_before": before["report_sha256"],
        "report_sha256_after": after.get("report_sha256"),
        "http_status": response.status_code,
        "branch": after.get("error_code") or after.get("status"),
        "approval": (after.get("approval") or {}).get("status", "none"),
        "error_code": after.get("error_code"),
        "evidence_statuses": {
            "transaction": "ok", "risk_model": report["model"]["status"],
            "graph": report["graph"]["status"], "policy": report["policy_retrieval_status"],
        },
        "report_citations": citations,
        "policy_conclusion_count": len(source.report_run.report.policy_conclusions),
        "retrieved_policy_chunk_ids": ([hit.chunk.chunk_id for hit in retrieval.hits] if retrieval else []),
        "policy_corpus_sha256": POLICY_CORPUS_SHA256,
        "decision_event_count": 0,
        "external_action_count": before["approval"]["external_action_count"],
    }


class _TraceCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith("graphrisk_trace "):
            self.events.append(json.loads(message.removeprefix("graphrisk_trace ")))


def probe_trace_continuity(repo: Path, index_path: Path) -> dict[str, Any]:
    """Observe three API calls and the actual structured logs for one fake run."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    results, index_version = _fixture_results(repo, index_path)
    source = results["normal"]
    service, _ = _service(source, index_version)
    request = {"transaction_id": "T3", "dataset_source": "synthetic",
               "policy_as_of": "2026-07-01", "relation_limit": 3}
    collector = _TraceCollector()
    logger = logging.getLogger("uvicorn.error")
    prior_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(collector)
    try:
        with TestClient(create_app(lambda: service)) as client:
            created_response = client.post("/api/v1/investigations", json=request)
            if created_response.status_code != 201:
                raise RuntimeError("Stage 07 trace probe could not create investigation")
            created = created_response.json()
            run_id = created["run_id"]
            read_response = client.get(f"/api/v1/investigations/{run_id}")
            if read_response.status_code != 200:
                raise RuntimeError("Stage 07 trace probe could not read investigation")
            decision_request = {
                "run_id": run_id, "session_id": created["session_id"],
                "transaction_id": request["transaction_id"],
                "report_sha256": created["report_sha256"],
                "decision": "reject", "reviewer_id": "trace-probe", "note": None,
            }
            decision_response = client.post(
                f"/api/v1/investigations/{run_id}/approval", json=decision_request)
            approval = decision_response.json()
            after = client.get(f"/api/v1/investigations/{run_id}").json()
            stored = service.get(run_id)
        ids = {"create": created_response.headers["X-Request-ID"],
               "get": read_response.headers["X-Request-ID"],
               "approval": decision_response.headers["X-Request-ID"]}
        operations = {"create": "create_investigation", "get": "get_investigation",
                      "approval": "decide_approval"}
        trace_events = [
            {"operation": operation, "run_id": event["run_id"], "request_id": event["request_id"]}
            for operation, request_id in ids.items()
            for event in collector.events
            if event["phase"] == "http" and event["request_id"] == request_id
            and event["tool"] == operations[operation]
        ]
        decision = stored.approval.decision if stored.approval else None
        report = created["report"]
        retrieval = source.graph_run.policy_retrieval if source.graph_run else None
        return {
            "probe_kind": "fake_api", "applied_fault": "three_request_trace",
            "source_request": request,
            "primary_tool": "POST /api/v1/investigations/{run_id}/approval",
            "primary_parameters": {"run_id": run_id, "decision": decision_request["decision"],
                                   "report_sha256": decision_request["report_sha256"]},
            "created_run_id": run_id, "target_run_id": run_id,
            "session_id": created["session_id"],
            "report_sha256_before": created["report_sha256"],
            "report_sha256_after": after.get("report_sha256"),
            "http_status": decision_response.status_code,
            "branch": approval.get("status") if decision_response.status_code == 200 else approval.get("error_code"),
            "approval": approval.get("status") if decision_response.status_code == 200 else "none",
            "error_code": approval.get("error_code"),
            "evidence_statuses": {"transaction": "ok", "risk_model": report["model"]["status"],
                                  "graph": report["graph"]["status"],
                                  "policy": report["policy_retrieval_status"]},
            "report_citations": report["policy_citations"],
            "policy_conclusion_count": len(source.report_run.report.policy_conclusions),
            "retrieved_policy_chunk_ids": ([hit.chunk.chunk_id for hit in retrieval.hits] if retrieval else []),
            "policy_corpus_sha256": POLICY_CORPUS_SHA256,
            "decision_record": {"run_id": run_id,
                                "transaction_id": decision.transaction_id if decision else None,
                                "session_id": stored.session_id,
                                "report_sha256": decision.report_sha256 if decision else None},
            "decision_event_count": sum(event["kind"] in {"approved", "rejected"}
                                        for event in approval.get("audit", [])),
            "external_action_count": approval.get("external_action_count"),
            "trace_events": trace_events,
        }
    finally:
        logger.removeHandler(collector)
        logger.setLevel(prior_level)
