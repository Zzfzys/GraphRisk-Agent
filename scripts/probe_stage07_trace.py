"""Reproduce a privacy-limited Stage 07 route-2 trace with local IEEE-CIS and Ollama."""
from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app
from graphrisk.serving.investigation_service import Stage06Service

REPO = Path(__file__).resolve().parents[1]


class _TraceCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith("graphrisk_trace "):
            self.events.append(json.loads(message.removeprefix("graphrisk_trace ")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/stage05.yaml")
    parser.add_argument("--transaction-id", default="3488959")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    service = Stage06Service.from_config(args.config)
    collector = _TraceCollector()
    logger = logging.getLogger("uvicorn.error")
    prior_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(collector)
    try:
        with TestClient(create_app(lambda: service)) as client:
            ready = client.get("/api/v1/ready")
            if ready.status_code != 200:
                raise RuntimeError(f"Local prerequisites not ready: {ready.json()}")
            response = client.post("/api/v1/investigations", json={
                "transaction_id": args.transaction_id, "dataset_source": "ieee_cis",
                "policy_as_of": "2026-07-01", "relation_limit": 3,
            })
            if response.status_code != 201 or response.json().get("status") != "verified":
                raise RuntimeError(f"Local investigation not verified: {response.status_code}, {response.json().get('error_code') or response.json().get('status')}")
            created = response.json()
            run_id = created["run_id"]
            create_id = response.headers["X-Request-ID"]
            reread = client.get(f"/api/v1/investigations/{run_id}")
            if reread.status_code != 200 or reread.json() != created:
                raise RuntimeError("Read changed the frozen investigation")
            read_id = reread.headers["X-Request-ID"]
            approval = client.post(f"/api/v1/investigations/{run_id}/approval", json={
                "run_id": run_id, "session_id": created["session_id"],
                "transaction_id": args.transaction_id, "report_sha256": created["report_sha256"],
                "decision": "reject", "reviewer_id": "trace-probe", "note": None,
            })
            decide_id = approval.headers["X-Request-ID"]
            if approval.status_code != 200 or approval.json().get("status") != "rejected":
                raise RuntimeError(f"Approval trace failed: {approval.status_code}")
            if approval.json()["audit"][-1]["request_id"] != decide_id:
                raise RuntimeError("Approval audit request ID is unbound")
        events = [event for event in collector.events if event["run_id"] == run_id]
        expected = {"tool", "agent_node", "dependency_attempt", "approval", "http"}
        phases = {event["phase"] for event in events}
        tool_events = created["audit"]["tool_events"]
        if (not expected <= phases or len({create_id, read_id, decide_id}) != 3
                or not tool_events or any(event["request_id"] != create_id for event in tool_events)
                or not any(event["request_id"] == read_id and event["phase"] == "http" for event in events)
                or not any(event["request_id"] == decide_id and event["phase"] == "approval" for event in events)):
            raise RuntimeError(f"Run/request/tool trace is incomplete: phases={sorted(phases)}, tool_events={len(tool_events)}, events={len(events)}")
        result = {
            "status": "passed", "run_id": run_id,
            "request_ids": {"create": create_id, "read": read_id, "decision": decide_id},
            "investigation_status": created["status"], "approval_status": approval.json()["status"],
            "report_sha256": created["report_sha256"],
            "policy_index_version": created["versions"]["policy_index_version"],
            "generation_model_revision": created["versions"]["generation_model_revision"],
            "tool_events": [{"tool": event["tool"], "phase": event["phase"],
                             "status": event["status"], "request_id": event["request_id"]}
                            for event in tool_events],
            "trace_phase_counts": {phase: sum(event["phase"] == phase for event in events)
                                   for phase in sorted(phases)},
            "external_action_count": approval.json()["external_action_count"],
        }
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = args.output or REPO / f"reports/generated/stage07/route2-real-trace-{stamp}.json"
        if not path.is_absolute():
            path = REPO / path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        print(json.dumps({"output": str(path), "status": result["status"],
                          "run_id": run_id, "tool_events": len(tool_events),
                          "trace_phase_counts": result["trace_phase_counts"]}, ensure_ascii=False))
        return 0
    finally:
        logger.removeHandler(collector)
        logger.setLevel(prior_level)


if __name__ == "__main__":
    raise SystemExit(main())
