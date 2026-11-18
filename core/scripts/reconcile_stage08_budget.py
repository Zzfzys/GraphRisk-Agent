"""Inspect the real Stage08 ledger or reconcile an attempt from a manually verified bill."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from graphrisk.agent.provider_budget import EvaluationBudget, BudgetStop
ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, help="Local JSON: attempt_id, actual_micro_cny, bill_reference")
    args = parser.parse_args()
    budget = EvaluationBudget(ROOT / "reports/generated/stage08/real-budget.sqlite3")
    if args.receipt:
        raw = args.receipt.read_bytes()
        receipt = json.loads(raw)
        if set(receipt) != {"attempt_id", "actual_micro_cny", "bill_reference"} or not isinstance(receipt["bill_reference"], str) or not receipt["bill_reference"].strip():
            raise ValueError("invalid_receipt")
        budget.reconcile(receipt["attempt_id"], receipt["actual_micro_cny"], hashlib.sha256(raw).hexdigest())
    print(json.dumps({"ledger": budget.snapshot(), "attempts": budget.audit_rows()}))

if __name__ == "__main__":
    try:
        main()
    except (BudgetStop, ValueError, OSError, TypeError):
        print(json.dumps({"error": "reconciliation_failed_check_receipt_and_attempt"}))
        raise SystemExit(2) from None

