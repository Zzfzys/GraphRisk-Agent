"""Explicit PostgreSQL mode for recoverable Stage 07 investigation approvals."""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterator, Mapping
from uuid import uuid4

from psycopg.types.json import Jsonb

from graphrisk.agent.approval import ApprovalDecision, ApprovalPayload, Stage05ApprovalWorkflow
from graphrisk.agent.reliability import BudgetExceeded, ReliableRun, Stage05ReliableRunner
from graphrisk.agent.contracts import Stage05Request
from graphrisk.serving.investigation_service import (
    InvestigationRecord, PersistedValidation, ReadinessStatus, ServiceError, Stage06Service, _evidence_state,
)
from graphrisk.serving.investigation_views import InvestigationView, project_investigation
from graphrisk.serving.postgres_store import PostgresStore, SCHEMA_VERSION


_ACTIVE_ATTEMPT: ContextVar[dict[str, object] | None] = ContextVar("graphrisk_cost_attempt", default=None)
_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}\Z")


def _fingerprint(value: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


class PersistentCostLedger:
    """Durable local budget accounting; unknown provider outcomes retain reservations."""

    def __init__(self, store: PostgresStore, *, stop: Decimal, budget: Decimal):
        self.store, self.stop, self.budget = store, stop, budget
        self.store.configure_budget(stop=stop, budget=budget)

    @contextmanager
    def run_context(self, run_id: str) -> Iterator[None]:
        token = _ACTIVE_ATTEMPT.set({"run_id": run_id, "attempt": 0})
        try:
            yield
        finally:
            _ACTIVE_ATTEMPT.reset(token)

    @property
    def spent(self) -> Decimal:
        with self.store.connect() as conn:
            return conn.execute(
                "SELECT spent_cny FROM cost_budget WHERE singleton = true"
            ).fetchone()["spent_cny"]

    def reserve(self, upper_bound_cny: Decimal) -> None:
        if upper_bound_cny < 0:
            raise ValueError("API cost upper bound must be nonnegative")
        context = _ACTIVE_ATTEMPT.get()
        if context is None:
            raise ServiceError("persistence_unavailable")
        attempt = int(context["attempt"]) + 1
        with self.store.connect() as conn:
            with conn.transaction():
                budget_row = conn.execute(
                    "SELECT spent_cny FROM cost_budget WHERE singleton = true FOR UPDATE"
                ).fetchone()
                if budget_row is None:
                    raise ServiceError("persistence_unavailable")
                next_spent = budget_row["spent_cny"] + upper_bound_cny
                if next_spent > self.stop or next_spent > self.budget:
                    raise BudgetExceeded("API stop threshold would be exceeded")
                conn.execute(
                    """INSERT INTO cost_attempts(run_id, attempt_no, reserved_cny, state)
                       VALUES (%s, %s, %s, 'reserved')""",
                    (context["run_id"], attempt, upper_bound_cny),
                )
                conn.execute(
                    "UPDATE cost_budget SET spent_cny = %s WHERE singleton = true",
                    (next_spent,),
                )
        context["attempt"] = attempt

    def settle(self, upper_bound_cny: Decimal, actual_cny: Decimal | None) -> None:
        context = _ACTIVE_ATTEMPT.get()
        if context is None or int(context["attempt"]) < 1:
            raise ServiceError("persistence_unavailable")
        if actual_cny is not None and actual_cny < 0:
            raise ValueError("actual API cost must be nonnegative")
        with self.store.connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    """SELECT reserved_cny, actual_cny, state FROM cost_attempts
                       WHERE run_id = %s AND attempt_no = %s FOR UPDATE""",
                    (context["run_id"], context["attempt"]),
                ).fetchone()
                if row is None or row["reserved_cny"] != upper_bound_cny:
                    raise ServiceError("persistence_unavailable")
                if row["state"] == "settled":
                    if row["actual_cny"] != actual_cny:
                        raise ServiceError("persistence_unavailable")
                    return
                if row["state"] != "reserved":
                    raise ServiceError("persistence_unavailable")
                if actual_cny is None:
                    conn.execute(
                        """UPDATE cost_attempts SET state = 'uncertain'
                           WHERE run_id = %s AND attempt_no = %s""",
                        (context["run_id"], context["attempt"]),
                    )
                    return
                budget_row = conn.execute(
                    "SELECT spent_cny FROM cost_budget WHERE singleton = true FOR UPDATE"
                ).fetchone()
                next_spent = budget_row["spent_cny"] - upper_bound_cny + actual_cny
                conn.execute(
                    """UPDATE cost_attempts SET state = 'settled', actual_cny = %s
                       WHERE run_id = %s AND attempt_no = %s""",
                    (actual_cny, context["run_id"], context["attempt"]),
                )
                conn.execute(
                    "UPDATE cost_budget SET spent_cny = %s WHERE singleton = true",
                    (next_spent,),
                )
        if actual_cny > upper_bound_cny:
            raise BudgetExceeded("actual API cost exceeded reserved upper bound")

    def mark_uncertain(self, run_id: str) -> None:
        with self.store.connect() as conn:
            conn.execute(
                """UPDATE cost_attempts SET state = 'uncertain'
                   WHERE run_id = %s AND state = 'reserved'""", (run_id,)
            )


class PostgresInvestigationService(Stage06Service):
    """Stage 06 public semantics backed by PostgreSQL; no memory fallback."""

    def __init__(self, *, dsn: str, **kwargs):
        super().__init__(**kwargs)
        self.store = PostgresStore(dsn)
        if not self.store.check():
            raise ServiceError("persistence_unavailable")
        self.storage_mode = "postgres"
        self.approval_ttl = self.approval.ttl
        self._checkpointer = None
        self.approval = None
        if isinstance(self.runner, Stage05ReliableRunner):
            self.cost_ledger = PersistentCostLedger(
                self.store, stop=Decimal(str(self.runner.limits.api_stop_cny)),
                budget=Decimal(str(self.runner.limits.api_budget_cny)),
            )
            self.runner.cost = self.cost_ledger

    @classmethod
    def from_config(cls, config_path="configs/stage05.yaml", *, dsn: str, **kwargs):
        return super().from_config(config_path, dsn=dsn, **kwargs)

    def readiness(self) -> ReadinessStatus:
        status = super().readiness()
        components = dict(status.components)
        components["postgres"] = self.store.check()
        return ReadinessStatus(
            status="ready" if all(components.values()) else "not_ready", components=components,
        )

    @staticmethod
    def _snapshot_data(record: InvestigationRecord, view: InvestigationView) -> tuple[dict, dict]:
        snapshot = record.model_copy(deep=True)
        # Called only after full live validation, or after a checked restore.
        # Preserve the original attestation when approval state changes.
        if snapshot.persisted_validation is None and (snapshot.versions.score_mode == "xgboost_only"
                                                     or snapshot.versions.report_protocol is not None):
            snapshot.persisted_validation = PersistedValidation.for_validated_result(
                snapshot.request, snapshot.versions, snapshot.result)
        snapshot.result = snapshot.result.model_copy(update={"graph_run": None})
        snapshot.public_view = view.model_dump(mode="json")
        snapshot.idempotent_replay = False
        return snapshot.model_dump(mode="json"), view.model_dump(mode="json")

    def _workflow(self, saver, *, at: datetime, record: InvestigationRecord):
        remaining = record.expires_at - at
        if remaining <= timedelta(0):
            raise ServiceError("run_expired")
        return Stage05ApprovalWorkflow(
            checkpointer=saver, clock=lambda: at,
            ttl=min(self.approval_ttl, remaining),
        )

    def _check_checkpoint(self, workflow: Stage05ApprovalWorkflow,
                          record: InvestigationRecord, *, allow_terminal: bool = False) -> None:
        if record.approval is None or record.approval.payload is None or record.session_id is None:
            return
        try:
            state = workflow._snapshot(record.session_id)
            payload = ApprovalPayload.model_validate(state.values["payload"])
            expected = record.approval.payload
            if (payload != expected or
                (not allow_terminal and state.values.get("status") != record.approval.status) or
                (record.approval.status == "pending" and not allow_terminal
                 and state.next != ("await_approval",)) or
                (record.approval.status in ("approved", "rejected", "expired") and state.next)):
                raise ValueError("checkpoint binding mismatch")
        except (KeyError, ValueError, TypeError) as error:
            raise ServiceError("persistence_unavailable") from error

    def _record_from_row(self, row: dict) -> InvestigationRecord:
        if row["storage_schema_version"] != SCHEMA_VERSION or row["mode"] != "postgres":
            raise ServiceError("persistence_unavailable")
        if row["record_json"] is None or row["public_view_json"] is None:
            raise ServiceError("persistence_unavailable")
        try:
            record = InvestigationRecord.model_validate(row["record_json"])
            view = InvestigationView.model_validate(row["public_view_json"])
            if (record.public_view != row["public_view_json"] or view.run_id != record.run_id
                or view.report_sha256 != (record.report_sha256 if record.approval else None)
                or view.versions != record.versions or record.report_sha256 != row["report_sha256"]
                or record.session_id != row["session_id"] or record.expires_at != row["expires_at"]
                or record.created_request_id != row["created_request_id"]
                or _fingerprint(record.request.model_dump(mode="json")) != row["request_fingerprint"]
                or ((record.approval.payload.expires_at if record.approval else None)
                    != row["approval_expires_at"])):
                raise ValueError("stored view binding mismatch")
            if record.approval:
                self._check_approval_report(record, versions=record.versions,
                                            persisted_validation=record.persisted_validation)
            else:
                self._validate_result(record.request, record.result, versions=record.versions,
                                      persisted_validation=record.persisted_validation)
            return record
        except ServiceError:
            raise
        except (ValueError, TypeError, KeyError) as error:
            raise ServiceError("persistence_unavailable") from error

    def _publish_prepared(self, run_id: str, record: InvestigationRecord) -> InvestigationRecord:
        self._validate_result(record.request, record.result, versions=record.versions,
                              persisted_validation=record.persisted_validation)
        if record.session_id:
            if record.result.report_run is None:
                raise ServiceError("approval_checkpoint_failed")
            with self.store.saver() as saver:
                workflow = self._workflow(saver, at=self._now(), record=record)
                approval = workflow.start(record.result.report_run, record.session_id)
                if approval.status != "pending" or approval.payload is None:
                    raise ServiceError("approval_checkpoint_failed")
                if approval.payload.report_sha256 != record.report_sha256:
                    raise ServiceError("report_hash_mismatch")
                record.approval = approval
                self._check_checkpoint(workflow, record)
        view = project_investigation(record)
        data, public = self._snapshot_data(record, view)
        self.store.publish(run_id, data, public)
        return record

    def run(self, raw_request: Mapping[str, object] | Stage05Request, *,
            request_id: str | None = None, idempotency_key: str | None = None) -> InvestigationRecord:
        if self._closed:
            raise ServiceError("service_stopped")
        request = Stage05Request.model_validate(raw_request)
        if request.dataset_source != self.dataset_source:
            raise ServiceError("dataset_source_mismatch")
        if idempotency_key is not None and _KEY_PATTERN.fullmatch(idempotency_key) is None:
            raise ServiceError("invalid_request")
        run_id = f"run-{self.instance_id}-{uuid4().hex}"
        key_hash = hashlib.sha256(idempotency_key.encode("ascii")).hexdigest() if idempotency_key else None
        fingerprint = _fingerprint(request.model_dump(mode="json"))
        claimed_id, state = self.store.claim(
            run_id=run_id, fingerprint=fingerprint, key_hash=key_hash, request_id=request_id,
            ttl=self.ttl, max_runs=self.max_runs,
        )
        if state == "published":
            replay = self.get(claimed_id, request_id=request_id)
            replay.idempotent_replay = True
            return replay
        if state != "new":
            if claimed_id.startswith(f"run-{self.instance_id}-"):
                raise ServiceError("persistence_unavailable")
            row = self.store.row(claimed_id)
            if state == "preparing" and row and row["record_json"] is not None:
                record = InvestigationRecord.model_validate(row["record_json"])
                try:
                    return self._publish_prepared(claimed_id, record)
                except Exception as error:
                    raise ServiceError("persistence_unavailable") from error
            self.store.mark_recovery_required(claimed_id)
            raise ServiceError("persistence_unavailable")
        row = self.store.row(run_id)
        context = (self.cost_ledger.run_context(run_id)
                   if isinstance(self.cost_ledger, PersistentCostLedger) else nullcontext())
        try:
            try:
                with context:
                    result = ReliableRun.model_validate(self.runner.run(request.model_dump(mode="json")))
            finally:
                if isinstance(self.cost_ledger, PersistentCostLedger):
                    self.cost_ledger.mark_uncertain(run_id)
            versions = self._versions_for_request(request)
            digest = self._validate_result(request, result, versions=versions)
            session_id = (f"session-{run_id}" if result.status == "verified"
                          and result.report_run is not None else None)
            record = InvestigationRecord(
                run_id=run_id, session_id=session_id, request=request, status=result.status,
                evidence_state=_evidence_state(result.status), report_sha256=digest,
                created_request_id=request_id, approval_request_ids={1: request_id}
                if session_id and request_id else {},
                versions=versions, result=result, approval=None,
                created_at=row["created_at"], expires_at=row["expires_at"],
            )
            view = project_investigation(record)
            data, public = self._snapshot_data(record, view)
            self.store.save_prepared(run_id, data, public)
            return self._publish_prepared(run_id, record)
        except ServiceError:
            raise
        except Exception as error:
            self.store.mark_recovery_required(run_id)
            raise ServiceError("persistence_unavailable") from error

    def get(self, run_id: str, *, request_id: str | None = None) -> InvestigationRecord:
        if self._closed:
            raise ServiceError("service_stopped")
        with self.store.connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT * FROM investigation_runs WHERE run_id = %s FOR UPDATE", (run_id,)
                ).fetchone()
                if row is None:
                    raise ServiceError("run_not_found")
                if row["expires_at"] <= conn.execute(
                    "SELECT clock_timestamp() AS now"
                ).fetchone()["now"] or row["storage_state"] == "expired":
                    raise ServiceError("run_expired")
                if row["storage_state"] != "published":
                    raise ServiceError("persistence_unavailable")
                record = self._record_from_row(row)
                if record.approval is not None:
                    at = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
                    with self.store.saver() as saver:
                        workflow = self._workflow(saver, at=at, record=record)
                        self._check_checkpoint(workflow, record)
                        if record.approval.status == "pending":
                            outcome = workflow.start(record.result.report_run, record.session_id)
                            if outcome.status != "pending":
                                record.approval = outcome
                                if request_id:
                                    record.approval_request_ids.setdefault(len(outcome.audit), request_id)
                                view = project_investigation(record)
                                data, public = self._snapshot_data(record, view)
                                conn.execute(
                                    """UPDATE investigation_runs SET record_json = %s,
                                       public_view_json = %s WHERE run_id = %s""",
                                    (Jsonb(data), Jsonb(public), run_id),
                                )
                                event = outcome.audit[-1]
                                conn.execute(
                                    """INSERT INTO approval_events
                                       (run_id, sequence, kind, request_id, event_json)
                                       VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                                    (run_id, event.sequence, event.kind, request_id,
                                     Jsonb(event.model_dump(mode="json"))),
                                )
                return record.model_copy(deep=True)

    def decide(self, run_id: str, decision: ApprovalDecision, *,
               request_id: str | None = None) -> InvestigationRecord:
        preliminary = self.store.row(run_id)
        if preliminary and preliminary["storage_state"] == "published":
            try:
                self.get(run_id, request_id=request_id)
            except ServiceError as error:
                if error.code != "persistence_unavailable":
                    raise
                latest = self.store.row(run_id)
                if latest is None or latest["storage_state"] != "decision_applying":
                    raise
        canonical = ApprovalDecision.model_validate({
            key: getattr(decision, key) for key in ApprovalDecision.model_fields
        })
        decision_hash = _fingerprint(canonical.model_dump(mode="json"))
        with self.store.connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT * FROM investigation_runs WHERE run_id = %s FOR UPDATE", (run_id,)
                ).fetchone()
                if row is None:
                    raise ServiceError("run_not_found")
                record = self._record_from_row(row)
                if record.approval is None or record.session_id is None:
                    raise ServiceError("approval_not_available")
                if (canonical.session_id != record.session_id
                    or canonical.transaction_id != record.request.transaction_id
                    or canonical.report_sha256 != record.report_sha256):
                    raise ServiceError("approval_binding_mismatch")
                if record.approval.status == "expired":
                    raise ServiceError("approval_expired")
                if row["decision_fingerprint"]:
                    if row["decision_fingerprint"] != decision_hash:
                        raise ServiceError("approval_conflict")
                    if row["storage_state"] == "published":
                        replay = record.model_copy(deep=True)
                        replay.idempotent_replay = True
                        return replay
                else:
                    now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
                    if row["approval_expires_at"] <= now:
                        raise ServiceError("approval_expired")
                    conn.execute(
                        """UPDATE investigation_runs SET storage_state = 'decision_applying',
                           decision_fingerprint = %s, decision_request_id = %s,
                           decision_claimed_at = %s WHERE run_id = %s""",
                        (decision_hash, request_id, now, run_id),
                    )
        try:
            with self.store.connect() as conn:
                with conn.transaction():
                    row = conn.execute(
                        "SELECT * FROM investigation_runs WHERE run_id = %s FOR UPDATE", (run_id,)
                    ).fetchone()
                    if row["decision_fingerprint"] != decision_hash:
                        raise ServiceError("approval_conflict")
                    if row["storage_state"] == "published":
                        return self._record_from_row(row)
                    if row["storage_state"] != "decision_applying":
                        raise ServiceError("persistence_unavailable")
                    record = self._record_from_row(row)
                    with self.store.saver() as saver:
                        workflow = self._workflow(saver, at=row["decision_claimed_at"], record=record)
                        self._check_checkpoint(workflow, record, allow_terminal=True)
                        outcome = workflow.resume(canonical.model_dump(mode="json"),
                                                  record.result.report_run)
                        if outcome.status == "error":
                            raise ServiceError({
                                "already_finalized": "approval_conflict",
                                "decision_binding_mismatch": "approval_binding_mismatch",
                                "report_changed": "report_hash_mismatch",
                            }.get(outcome.issue_code, "persistence_unavailable"))
                        if outcome.status not in ("approved", "rejected"):
                            raise ServiceError("approval_expired")
                        record.approval = outcome
                        if row["decision_request_id"]:
                            record.approval_request_ids.setdefault(len(outcome.audit),
                                                                   row["decision_request_id"])
                        self._check_checkpoint(workflow, record)
                    view = project_investigation(record)
                    data, public = self._snapshot_data(record, view)
                    conn.execute(
                        """UPDATE investigation_runs SET storage_state = 'published',
                           record_json = %s, public_view_json = %s WHERE run_id = %s""",
                        (Jsonb(data), Jsonb(public), run_id),
                    )
                    event = outcome.audit[-1]
                    conn.execute(
                        """INSERT INTO approval_events(run_id, sequence, kind, request_id, event_json)
                           VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                        (run_id, event.sequence, event.kind, row["decision_request_id"],
                         Jsonb(event.model_dump(mode="json"))),
                    )
                    return record.model_copy(deep=True)
        except ServiceError:
            raise
        except Exception as error:
            raise ServiceError("persistence_unavailable") from error

    def close(self) -> None:
        self._closed = True
        self.store.close()

