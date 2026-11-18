"""Exercise one real held-out investigation across two OS processes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from api.main import create_app
from graphrisk.serving.postgres_service import PostgresInvestigationService
from graphrisk.serving.postgres_store import PostgresStore


REPO = Path(__file__).resolve().parents[1]


def _resume_in_child() -> None:
    payload = json.loads(sys.stdin.read())
    dsn = os.getenv("GRAPHRISK_STAGE07_DSN")
    if not dsn:
        raise RuntimeError("PostgreSQL DSN is missing")
    created, request, key = payload["created"], payload["request"], payload["key"]
    second = PostgresInvestigationService.from_config(
        "configs/stage05.yaml", dsn=dsn, max_runs=1000)
    with TestClient(create_app(lambda: second)) as client:
        run_id = created["run_id"]
        replay = client.post("/api/v1/investigations", json=request,
                             headers={"Idempotency-Key": key})
        if replay.status_code != 200 or replay.json() != created:
            raise RuntimeError("Restarted creation retry was not idempotent")
        reread = client.get(f"/api/v1/investigations/{run_id}")
        if reread.status_code != 200 or reread.json() != created:
            raise RuntimeError("Restart changed the frozen investigation")
        decision = {"run_id": run_id, "session_id": created["session_id"],
                    "transaction_id": request["transaction_id"],
                    "report_sha256": created["report_sha256"],
                    "decision": "reject", "reviewer_id": "route4-probe", "note": None}
        rejected = client.post(f"/api/v1/investigations/{run_id}/approval", json=decision)
        if rejected.status_code != 200 or rejected.json()["status"] != "rejected":
            raise RuntimeError("Recovered approval was not rejected")
        repeated = client.post(f"/api/v1/investigations/{run_id}/approval", json=decision)
        if repeated.status_code != 200 or repeated.json() != rejected.json():
            raise RuntimeError("Exact decision retry was not idempotent")
        conflicted = client.post(f"/api/v1/investigations/{run_id}/approval",
                                 json={**decision, "decision": "approve"})
        if conflicted.status_code != 409:
            raise RuntimeError("Conflicting decision was not rejected")
    print(json.dumps({"pid": os.getpid(), "status": "rejected",
                      "external_action_count": rejected.json()["external_action_count"]}))


def main() -> None:
    dsn = os.getenv("GRAPHRISK_STAGE07_DSN")
    if not dsn:
        raise SystemExit("GRAPHRISK_STAGE07_DSN is required")
    request = {"transaction_id": "3488959", "dataset_source": "ieee_cis",
               "policy_as_of": "2026-07-01", "relation_limit": 3}
    key = "route4-real-" + uuid4().hex
    first = PostgresInvestigationService.from_config(
        "configs/stage05.yaml", dsn=dsn, max_runs=1000)
    with TestClient(create_app(lambda: first)) as client:
        ready = client.get("/api/v1/ready")
        if ready.status_code != 200:
            raise RuntimeError("Stage 07 real dependencies are not ready")
        created_response = client.post("/api/v1/investigations", json=request,
                                       headers={"Idempotency-Key": key})
        if created_response.status_code != 201:
            raise RuntimeError(f"Creation failed: {created_response.status_code} "
                               f"{created_response.json().get('error_code')}")
        created = created_response.json()
        if created["status"] != "verified" or created["approval"]["status"] != "pending":
            raise RuntimeError("Expected a verified pending report")
    child = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--resume-child"],
        input=json.dumps({"created": created, "request": request, "key": key}),
        capture_output=True, text=True, cwd=REPO, timeout=120, check=False,
    )
    if child.returncode != 0:
        raise RuntimeError(f"Independent resume process failed with code {child.returncode}")
    outcome = json.loads(child.stdout.strip().splitlines()[-1])
    if outcome["pid"] == os.getpid() or outcome["status"] != "rejected":
        raise RuntimeError("Resume did not run in a distinct process")
    store = PostgresStore(dsn)
    with store.connect() as conn:
        events = conn.execute(
            "SELECT kind FROM approval_events WHERE run_id = %s ORDER BY sequence",
            (created["run_id"],),
        ).fetchall()
        costs = conn.execute(
            "SELECT attempt_no, state, actual_cny FROM cost_attempts "
            "WHERE run_id = %s ORDER BY attempt_no",
            (created["run_id"],),
        ).fetchall()
    if [event["kind"] for event in events] != ["created", "rejected"]:
        raise RuntimeError("Persisted approval audit is inconsistent")
    report = {
        "status": "passed", "run_id": created["run_id"],
        "creator_pid": os.getpid(), "resumer_pid": outcome["pid"],
        "report_sha256": created["report_sha256"],
        "policy_index_version": created["versions"]["policy_index_version"],
        "generation_model_revision": created["versions"]["generation_model_revision"],
        "approval_status": "rejected", "approval_events": len(events),
        "cost_attempts": len(costs), "cost_states": [row["state"] for row in costs],
        "external_action_count": outcome["external_action_count"],
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = REPO / f"reports/generated/stage07/route4-real-postgres-{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"evidence": str(path), **report}, ensure_ascii=False))


if __name__ == "__main__":
    if "--resume-child" in sys.argv:
        _resume_in_child()
    else:
        main()

