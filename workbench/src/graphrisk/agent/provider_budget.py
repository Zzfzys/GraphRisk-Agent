"""Persistent, serialized cost reservations for isolated Stage 08 experiments."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class BudgetStop(ValueError):
    """Stable public code; never include database contents in errors."""


class EvaluationBudget:
    """Amounts are integer micro-CNY. Unknown outcomes block subsequent calls.

    Use one ledger for the entire stage. Route 1 uses a separate simulated ledger;
    it must never be merged with or presented as actual provider billing.
    """

    RESERVATION = 100_000

    def __init__(self, path: Path, *, stop_cny: int = 80, experiment_cny: int = 5):
        if type(stop_cny) is not int or not 0 < stop_cny <= 80:
            raise BudgetStop("invalid_budget")
        if type(experiment_cny) is not int or not 0 < experiment_cny <= 5:
            raise BudgetStop("invalid_budget")
        self.path = path
        self.stop = stop_cny * 1_000_000
        self.experiment = experiment_cny * 1_000_000
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, stop INTEGER, experiment INTEGER)")
            db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?, ?)", (self.stop, self.experiment))
            if db.execute("SELECT stop, experiment FROM settings WHERE id=1").fetchone() != (self.stop, self.experiment):
                raise BudgetStop("budget_settings_changed")
            db.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, experiment TEXT NOT NULL, "
                       "case_id TEXT NOT NULL, amount INTEGER NOT NULL, state TEXT NOT NULL)")

    @contextmanager
    def _transaction(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=5)
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except sqlite3.Error:
            raise BudgetStop("budget_storage_unavailable") from None
        finally:
            if db is not None:
                db.close()

    def reserve(self, experiment: str, case_id: str) -> str:
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM attempts WHERE state IN ('pending','violation') LIMIT 1").fetchone():
                raise BudgetStop("budget_unreconciled")
            count = db.execute("SELECT COUNT(*) FROM attempts WHERE experiment=? AND case_id=?", (experiment, case_id)).fetchone()[0]
            if count >= 2:
                raise BudgetStop("attempt_limit")
            total = db.execute("SELECT COALESCE(SUM(amount),0) FROM attempts").fetchone()[0]
            subtotal = db.execute("SELECT COALESCE(SUM(amount),0) FROM attempts WHERE experiment=?", (experiment,)).fetchone()[0]
            if total + self.RESERVATION > self.stop or subtotal + self.RESERVATION > self.experiment:
                raise BudgetStop("budget_exhausted")
            attempt = uuid4().hex
            db.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, 'pending')", (attempt, experiment, case_id, self.RESERVATION))
        return attempt

    def settle(self, attempt: str, amount_micro_cny: int, *, violation: bool = False) -> None:
        if type(amount_micro_cny) is not int or amount_micro_cny < 0:
            raise BudgetStop("invalid_cost")
        with self._transaction() as db:
            row = db.execute("SELECT amount, state FROM attempts WHERE id=?", (attempt,)).fetchone()
            if row is None or row[1] != "pending":
                raise BudgetStop("invalid_settlement")
            state = "violation" if violation or amount_micro_cny > row[0] else "settled"
            db.execute("UPDATE attempts SET amount=?, state=? WHERE id=?", (amount_micro_cny, state, attempt))

    def snapshot(self) -> dict[str, int]:
        with self._transaction() as db:
            amount, pending = db.execute("SELECT COALESCE(SUM(amount),0), "
                                        "COALESCE(SUM(state='pending'),0) FROM attempts").fetchone()
        return {"accounted_micro_cny": amount, "pending_attempts": pending}


    def reconcile(self, attempt: str, amount_micro_cny: int, receipt_sha256: str) -> None:
        """Manual bill reconciliation; retain its receipt hash and prior accounting."""
        if (type(amount_micro_cny) is not int or amount_micro_cny < 0
            or not isinstance(receipt_sha256, str) or len(receipt_sha256) != 64
            or any(c not in "0123456789abcdef" for c in receipt_sha256)):
            raise BudgetStop("invalid_reconciliation")
        with self._transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS reconciliations "
                       "(attempt TEXT PRIMARY KEY, amount INTEGER, receipt_hash TEXT, previous_amount INTEGER)")
            old = db.execute("SELECT amount, receipt_hash FROM reconciliations WHERE attempt=?", (attempt,)).fetchone()
            if old is not None:
                if old != (amount_micro_cny, receipt_sha256):
                    raise BudgetStop("reconciliation_conflict")
                return
            row = db.execute("SELECT amount, state FROM attempts WHERE id=?", (attempt,)).fetchone()
            if row is None:
                raise BudgetStop("unknown_attempt")
            state = "violation" if row[1] == "violation" or amount_micro_cny > self.RESERVATION else "settled"
            db.execute("INSERT INTO reconciliations VALUES (?, ?, ?, ?)",
                       (attempt, amount_micro_cny, receipt_sha256, row[0]))
            db.execute("UPDATE attempts SET amount=?, state=? WHERE id=?", (amount_micro_cny, state, attempt))

    def audit_rows(self) -> list[dict]:
        with self._transaction() as db:
            return [dict(zip(("attempt_id", "experiment", "case_id", "accounted_micro_cny", "state"), row))
                    for row in db.execute("SELECT id, experiment, case_id, amount, state FROM attempts ORDER BY rowid")]
