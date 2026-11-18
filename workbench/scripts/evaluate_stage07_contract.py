"""Evaluate Stage 07 challenge observations without conflating not_run and passed."""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from graphrisk.demo.stage07_evaluation import evaluate_contract
from graphrisk.demo.stage07_probes import probe_pending_restart, probe_trace_continuity
from graphrisk.demo.stage07_probe_suite import probe_agent_cases, probe_persistence_cases

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, help="JSON map from case ID to actual probe observation")
    parser.add_argument("--probe-stage06-restart", action="store_true", help="Run the two-service Stage 06 restart gap probe")
    parser.add_argument("--probe-stage07-trace", action="store_true", help="Run the create/read/approval trace probe")
    parser.add_argument("--probe-stage07-postgres-restart", action="store_true", help="Run the PostgreSQL cross-instance restart probe")
    parser.add_argument("--probe-stage07-v2-all", action="store_true", help="Run all 15 frozen probes against an isolated PostgreSQL/pgvector evaluation database")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-complete", action="store_true", help="Fail unless every frozen case passed")
    args = parser.parse_args()
    observations = {}
    if args.observations:
        observations = json.loads(args.observations.read_text(encoding="utf-8"))
        if not isinstance(observations, dict):
            raise ValueError("Observation JSON must be a case-ID keyed object")
    if args.probe_stage06_restart:
        if "pending_restart_recovers" in observations:
            raise ValueError("Restart observation already supplied")
        observations["pending_restart_recovers"] = probe_pending_restart(
            REPO, REPO / "reports/generated/stage07/probe-index.json")
    if args.probe_stage07_postgres_restart:
        if "pending_restart_recovers" in observations:
            raise ValueError("Restart observation already supplied")
        dsn = os.getenv("GRAPHRISK_STAGE07_DSN")
        if not dsn:
            raise ValueError("GRAPHRISK_STAGE07_DSN is required for PostgreSQL restart probe")
        observations["pending_restart_recovers"] = probe_pending_restart(
            REPO, REPO / "reports/generated/stage07/postgres-probe-index.json", dsn=dsn)
    if args.probe_stage07_trace:
        if "trace_links_create_read_approval" in observations:
            raise ValueError("Trace observation already supplied")
        observations["trace_links_create_read_approval"] = probe_trace_continuity(
            REPO, REPO / "reports/generated/stage07/trace-probe-index.json")
    if args.probe_stage07_v2_all:
        if observations:
            raise ValueError("Full v2 probe cannot be combined with supplied observations or individual probes")
        dsn = os.getenv("GRAPHRISK_STAGE07_DSN")
        migration_dsn = os.getenv("GRAPHRISK_STAGE07_MIGRATION_DSN")
        if not dsn or not migration_dsn:
            raise ValueError("Both GRAPHRISK_STAGE07_DSN and GRAPHRISK_STAGE07_MIGRATION_DSN are required")
        index_path = REPO / "reports/generated/stage07/v2-probe-index.json"
        observations.update(probe_persistence_cases(REPO, index_path, dsn))
        observations.update(probe_agent_cases(REPO, index_path, dsn, migration_dsn))
    result = evaluate_contract(REPO, observations)
    if args.probe_stage07_v2_all:
        result["observations"] = observations
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = args.output or REPO / f"reports/generated/stage07/agent-eval-v2-initial-{stamp}.json"
    if not path.is_absolute():
        path = REPO / path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    frozen = result["splits"]["frozen"]
    print(json.dumps({"output": str(path), "frozen_total": frozen["total"],
                      "observed": frozen["observed"], "passed": frozen["passed"],
                      "failed": frozen["failed"], "not_run": frozen["not_run"],
                      "pass_rate_observed": frozen["pass_rate_observed"]}, ensure_ascii=False))
    return int(args.require_complete and (frozen["not_run"] > 0 or frozen["failed"] > 0))


if __name__ == "__main__":
    raise SystemExit(main())
