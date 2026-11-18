"""In-place execution ledger for the ONE route0 budget; no resets or retries."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from graphrisk.agent.upgrade_contract import BudgetContract, canonical, digest


class ExecutionStop(ValueError):
    pass


class InvestigationBudget:
    def __init__(self, path: Path, contract: dict, *, mode: str):
        BudgetContract.model_validate(contract["budget"])
        if mode not in {"fixture", "live"} or not path.is_file():
            raise ExecutionStop("existing_registered_budget_required")
        self.path, self.contract, self.mode = path, contract, mode
        with self.transaction() as db:
            self._check_registration(db)
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "model_attempts" not in tables:
                raise ExecutionStop("attempt_table_missing_no_reset")
            if "execution_settings" not in tables:
                if db.execute("SELECT count(*) FROM model_attempts").fetchone()[0]:
                    raise ExecutionStop("unversioned_attempts_no_migration")
                db.execute("CREATE TABLE execution_settings(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, mode TEXT NOT NULL)")
                db.execute("INSERT INTO execution_settings VALUES(1,2,?)",(mode,))
                db.execute("CREATE TABLE execution_requests(attempt_id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, phase TEXT NOT NULL, packet_sha TEXT NOT NULL, accounted INTEGER NOT NULL, receipt_sha TEXT, receipt_path TEXT, result_code TEXT, identity TEXT, usage_json TEXT, integrity_stop INTEGER NOT NULL DEFAULT 0)")
                db.execute("CREATE TABLE provider_identity(provider TEXT PRIMARY KEY, identity TEXT NOT NULL)")
                db.execute("CREATE TABLE execution_phases(phase TEXT PRIMARY KEY, manifest_sha TEXT NOT NULL, cloud_cap INTEGER NOT NULL, local_cap INTEGER NOT NULL)")
                db.execute("CREATE TABLE bill_reconciliations(attempt_id TEXT PRIMARY KEY, billed INTEGER NOT NULL, receipt_sha TEXT NOT NULL, previous_accounted INTEGER NOT NULL)")
            elif db.execute("SELECT version,mode FROM execution_settings WHERE id=1").fetchone()!=(2,mode):
                raise ExecutionStop("execution_ledger_mode_or_version_changed")
            elif not {"execution_requests","provider_identity","execution_phases","bill_reconciliations"}<=tables:
                raise ExecutionStop("execution_ledger_incomplete_no_reset")
            self._check_request_links(db)

    @contextmanager
    def transaction(self):
        db=None
        try:
            db=sqlite3.connect(self.path,timeout=10)
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except sqlite3.Error:
            raise ExecutionStop("execution_storage_unavailable") from None
        finally:
            if db is not None:db.close()

    def _check_registration(self, db):
        row=db.execute("SELECT experiment,caps FROM budget_registration WHERE id=1").fetchone()
        if row!=(self.contract["experiment_id"],canonical(self.contract["budget"])):
            raise ExecutionStop("immutable_budget_registration_changed")

    def _check_request_links(self, db):
        missing=db.execute("SELECT count(*) FROM model_attempts a LEFT JOIN execution_requests r USING(attempt_id) WHERE r.attempt_id IS NULL").fetchone()[0]
        extra=db.execute("SELECT count(*) FROM execution_requests r LEFT JOIN model_attempts a USING(attempt_id) WHERE a.attempt_id IS NULL").fetchone()[0]
        if missing or extra:raise ExecutionStop("execution_request_metadata_incomplete_no_reset")

    def register_phase(self, phase: str, manifest_sha: str, *, cloud_cap: int, local_cap: int):
        if not phase or any(type(n) is not int or n<0 for n in (cloud_cap,local_cap)) or len(manifest_sha)!=64:
            raise ExecutionStop("invalid_phase_registration")
        with self.transaction() as db:
            self._check_registration(db)
            wanted=(manifest_sha,cloud_cap,local_cap)
            old=db.execute("SELECT manifest_sha,cloud_cap,local_cap FROM execution_phases WHERE phase=?",(phase,)).fetchone()
            if old is not None and old!=wanted:raise ExecutionStop("phase_registration_changed")
            if old is None:db.execute("INSERT INTO execution_phases VALUES(?,?,?,?)",(phase,*wanted))

    def reserve(self, *, provider: str, request_key: str, packet_sha: str, phase: str, reserve_micro: int) -> str:
        if provider not in ("deepseek","local") or type(reserve_micro) is not int or reserve_micro<0 or (provider=="local" and reserve_micro!=0) or (provider=="deepseek" and reserve_micro==0):
            raise ExecutionStop("invalid_request_reservation")
        with self.transaction() as db:
            self._check_registration(db)
            self._check_request_links(db)
            from graphrisk.agent.full_case_execution import guard as formal_guard
            formal=formal_guard(db,phase=phase,provider=provider,request_key=request_key,packet_sha=packet_sha,reserve_micro=reserve_micro)
            from graphrisk.agent.report_pilot_execution import guard as report_guard
            quarantined = report_guard(db, phase=phase, provider=provider, request_key=request_key, packet_sha=packet_sha)
            from graphrisk.agent.rank_successor import guard as rank_guard
            rank_allowed=rank_guard(db,phase=phase,provider=provider,request_key=request_key,packet_sha=packet_sha,reserve_micro=reserve_micro)
            if formal is None and not quarantined and not rank_allowed and db.execute("SELECT 1 FROM model_attempts WHERE state IN ('reserved','unknown') LIMIT 1").fetchone():
                from graphrisk.agent.controlled_recovery import guard
                guard(db,phase=phase,provider=provider,request_key=request_key,packet_sha=packet_sha)
            if formal is None and not rank_allowed and db.execute("SELECT 1 FROM execution_requests WHERE integrity_stop=1 LIMIT 1").fetchone():
                raise ExecutionStop("integrity_review_required")
            if db.execute("SELECT 1 FROM execution_requests WHERE request_key=?",(request_key,)).fetchone():
                raise ExecutionStop("request_already_reserved_use_saved_result")
            caps=db.execute("SELECT cloud_cap,local_cap FROM execution_phases WHERE phase=?",(phase,)).fetchone()
            if caps is None:raise ExecutionStop("phase_not_registered")
            phase_count=db.execute("SELECT count(*) FROM model_attempts a JOIN execution_requests r USING(attempt_id) WHERE r.phase=? AND a.provider=?",(phase,provider)).fetchone()[0]
            if phase_count>=caps[0 if provider=="deepseek" else 1]:raise ExecutionStop("phase_attempt_cap")
            total=db.execute("SELECT count(*) FROM model_attempts").fetchone()[0]
            used=db.execute("SELECT count(*) FROM model_attempts WHERE provider=?",(provider,)).fetchone()[0]
            cloud_cap,total_cap=600,1000
            if rank_allowed and provider=="deepseek":
                from graphrisk.agent.rank_successor import global_cloud_cap,global_total_cap
                cloud_cap=global_cloud_cap(db,phase)
                total_cap=global_total_cap(db,phase)
            local_cap=400
            if formal is not None:
                cloud_cap=formal['absolute_caps']['deepseek'];local_cap=formal['absolute_caps']['local']
                total_cap=formal['absolute_caps']['total']
            if total>=total_cap or used>=(cloud_cap if provider=="deepseek" else local_cap):raise ExecutionStop("global_attempt_cap")
            amount=db.execute("SELECT COALESCE(sum(accounted),0) FROM execution_requests").fetchone()[0]
            if amount+reserve_micro>80_000_000 or amount+reserve_micro>100_000_000:
                raise ExecutionStop("pause_for_budget_reconciliation")
            from graphrisk.agent.autonomous_session import guard_session_reservation
            guard_session_reservation(db,phase,provider)
            attempt=uuid4().hex
            db.execute("INSERT INTO model_attempts VALUES(?,?,'reserved',?)",(attempt,provider,reserve_micro))
            db.execute("INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)",(attempt,request_key,phase,packet_sha,reserve_micro))
            return attempt

    def unknown(self, attempt: str, code: str):
        with self.transaction() as db:
            row=db.execute("SELECT state FROM model_attempts WHERE attempt_id=?",(attempt,)).fetchone()
            if row is None or row[0] not in ("reserved","unknown"):raise ExecutionStop("invalid_unknown_transition")
            db.execute("UPDATE model_attempts SET state='unknown' WHERE attempt_id=?",(attempt,))
            db.execute("UPDATE execution_requests SET result_code=? WHERE attempt_id=?",(code,attempt))

    def attach_receipt(self, attempt: str, receipt_sha: str, receipt_path: str):
        with self.transaction() as db:
            row=db.execute("SELECT a.state,r.receipt_sha,r.receipt_path FROM model_attempts a JOIN execution_requests r USING(attempt_id) WHERE attempt_id=?",(attempt,)).fetchone()
            if row is None or row[0] not in ("reserved","unknown"):raise ExecutionStop("invalid_receipt_transition")
            if row[1] is not None and (row[1],row[2])!=(receipt_sha,receipt_path):raise ExecutionStop("receipt_binding_changed")
            db.execute("UPDATE execution_requests SET receipt_sha=?,receipt_path=? WHERE attempt_id=?",(receipt_sha,receipt_path,attempt))

    def settle(self, attempt: str, *, cost_micro: int, receipt_sha: str, receipt_path: str,
               identity: str, usage: dict, result_code: str, integrity_stop: bool=False):
        if type(cost_micro) is not int or cost_micro<0 or len(receipt_sha)!=64:
            raise ExecutionStop("invalid_settlement")
        with self.transaction() as db:
            row=db.execute("SELECT provider,state,reserved_micro_cny FROM model_attempts WHERE attempt_id=?",(attempt,)).fetchone()
            if row is None or row[1] not in ("reserved","unknown"):raise ExecutionStop("invalid_settlement_transition")
            provider,_,reserved=row
            old=db.execute("SELECT identity FROM provider_identity WHERE provider=?",(provider,)).fetchone()
            from graphrisk.agent.rank_successor import identity_for_attempt
            successor_identity=identity_for_attempt(db,attempt)
            from graphrisk.agent.full_case_execution import identity_for_attempt as formal_identity
            formal_expected=formal_identity(db,attempt)
            if formal_expected is not None:successor_identity=formal_expected
            expected=successor_identity if successor_identity is not None else (old[0] if old else None)
            violation=integrity_stop or cost_micro>reserved or (expected is not None and expected!=identity)
            if old is None:db.execute("INSERT INTO provider_identity VALUES(?,?)",(provider,identity))
            db.execute("UPDATE model_attempts SET state='settled' WHERE attempt_id=?",(attempt,))
            db.execute("UPDATE execution_requests SET accounted=?,receipt_sha=?,receipt_path=?,result_code=?,identity=?,usage_json=?,integrity_stop=? WHERE attempt_id=?",(cost_micro,receipt_sha,receipt_path,result_code,identity,canonical(usage),int(violation),attempt))
        if violation:raise ExecutionStop("backend_revision_or_reservation_changed")

    def reconcile_bill(self, attempt: str, cost_micro: int, receipt_sha: str):
        """Bill-only reconciliation never erases attempts or integrity stops."""
        if type(cost_micro) is not int or cost_micro<0 or len(receipt_sha)!=64 or any(c not in "0123456789abcdef" for c in receipt_sha):
            raise ExecutionStop("invalid_bill_reconciliation")
        with self.transaction() as db:
            old=db.execute("SELECT billed,receipt_sha FROM bill_reconciliations WHERE attempt_id=?",(attempt,)).fetchone()
            if old is not None:
                if old!=(cost_micro,receipt_sha):raise ExecutionStop("bill_reconciliation_conflict")
                return
            row=db.execute("SELECT accounted FROM execution_requests WHERE attempt_id=?",(attempt,)).fetchone()
            if row is None:raise ExecutionStop("unknown_attempt")
            db.execute("INSERT INTO bill_reconciliations VALUES(?,?,?,?)",(attempt,cost_micro,receipt_sha,row[0]))
            db.execute("UPDATE execution_requests SET accounted=?,result_code=COALESCE(result_code,'billed_without_generation_result') WHERE attempt_id=?",(cost_micro,attempt))
            # For an unknown response, billing resolves money only, not output or
            # provider identity: retain unknown until a trusted response exists.

    def lookup(self, request_key: str):
        with self.transaction() as db:
            self._check_registration(db)
            cursor=db.execute("SELECT a.attempt_id,a.provider,a.state,r.packet_sha,r.accounted,r.receipt_sha,r.receipt_path,r.result_code,r.integrity_stop FROM model_attempts a JOIN execution_requests r USING(attempt_id) WHERE request_key=?",(request_key,))
            row=cursor.fetchone()
            return dict(zip([c[0] for c in cursor.description],row)) if row else None

    def snapshot(self):
        with self.transaction() as db:
            self._check_registration(db)
            self._check_request_links(db)
            counts=dict(db.execute("SELECT provider,count(*) FROM model_attempts GROUP BY provider"))
            amount=db.execute("SELECT COALESCE(sum(accounted),0) FROM execution_requests").fetchone()[0]
            unknown=db.execute("SELECT count(*) FROM model_attempts WHERE state IN ('reserved','unknown')").fetchone()[0]
            integrity=db.execute("SELECT count(*) FROM execution_requests WHERE integrity_stop=1").fetchone()[0]
            return {"total_attempts":sum(counts.values()),"deepseek_attempts":counts.get("deepseek",0),
                    "local_attempts":counts.get("local",0),"accounted_micro_cny":amount,
                    "unresolved_attempts":unknown,"integrity_stops":integrity,
                    "cost_kind":"peak_estimate_or_unknown_reservation_bill_separate","mode":self.mode}
