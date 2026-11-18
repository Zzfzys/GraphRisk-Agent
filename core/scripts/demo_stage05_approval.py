"""Demonstrate a human decision against a frozen route-5 report in one process."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from graphrisk.agent.approval import Stage05ApprovalWorkflow
from graphrisk.agent.reporting import ReportRun


def main() -> None:
    parser = argparse.ArgumentParser(description="Pause and resume a Stage 05 recommendation (no enforcement)")
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--decision", choices=("approve", "reject"), required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    source = json.loads(args.report_json.read_text(encoding="utf-8"))
    report_run = ReportRun.model_validate(source["report_run"])
    workflow = Stage05ApprovalWorkflow()
    pending = workflow.start(report_run, args.session_id)
    if pending.status != "pending" or pending.payload is None:
        raise SystemExit(f"approval did not pause: {pending.issue_code or pending.status}")
    result = workflow.resume({
        "session_id": args.session_id, "transaction_id": pending.payload.transaction_id,
        "report_sha256": pending.payload.report_sha256, "decision": args.decision,
        "reviewer_id": args.reviewer_id,
    }, report_run)
    safe_session = hashlib.sha256(args.session_id.encode("utf-8")).hexdigest()[:16]
    output = args.output or Path("reports/generated/stage05") / f"approval-{safe_session}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"pending": pending.model_dump(mode="json"),
                                  "outcome": result.model_dump(mode="json")},
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"pending={pending.status} outcome={result.status} external_actions={result.external_action_count} output={output}")


if __name__ == "__main__":
    main()
