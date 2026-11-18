"""Scoped successor identity for QA; never rewrites historical provider identity."""
from __future__ import annotations

from pathlib import Path
import re

from extensions.case_qa_live_v1.budget import LiveCaseQABudget
from extensions.case_qa_v1.budget import LIMITS, read, require
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_http import post_one
from graphrisk.agent.autonomous_providers import cloud_usage, credentials
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash

REVISION = "case-qa-live-identity-v2"
VERSION = "case-qa-live-v1"
IDENTITY_SQL = "SELECT identity FROM provider_identity WHERE provider='deepseek'"


class _IdentityView:
    """Only the fixture-era final identity predicate uses the scoped successor."""
    def __init__(self, db, identity):
        self.db, self.identity, self.projected = db, identity, 0

    def execute(self, sql, parameters=()):
        if sql == IDENTITY_SQL:
            require(not parameters, "qa_identity_projection_invalid")
            self.projected += 1
            return self.db.execute("SELECT ?",(self.identity,))
        return self.db.execute(sql,parameters)


class LiveCaseQABudgetV2(LiveCaseQABudget):
    def _validate_config(self, path=None, contract=None):
        super()._validate_config(path,contract)
        require(self.config.get("execution_revision") == REVISION, "qa_execution_revision_invalid")
        for path in (Path(__file__).resolve(),Path(__file__).with_name("runtime.py").resolve()):
            require(path.is_file() and self.config["files"].get(str(path)) == file_hash(path), "qa_source_binding_missing")

    def _authorization(self):
        authorization = super()._authorization()
        self._authorized_sha256 = authorization
        return authorization

    def _status(self, db, value=None):
        # Parent history validation still reads the unchanged complete old identity table.
        view = _IdentityView(db,self.config["expected_identity"])
        result = super()._status(view,value)
        require(view.projected == 1, "qa_identity_projection_invalid")
        return result

    def _partition(self, db):
        rows,qa,web,route9 = super()._partition(db)
        for row in qa:
            if row[8] is not None:
                require(read(row[9]).get("execution_revision") == REVISION, "qa_receipt_changed")
        return rows,qa,web,route9

    def settle(self, attempt, *, cost_micro, receipt_sha, receipt_path,
               identity, usage, result_code, integrity_stop=False):
        # This is accounting for an already sent request, not permission to send.
        # A midnight rollover or authorization revocation must not lose its receipt.
        require(type(cost_micro) is int and cost_micro >= 0 and type(receipt_sha) is str
                and re.fullmatch(r"[0-9a-f]{64}",receipt_sha), "invalid_settlement")
        self._validate_config()
        with self.transaction() as db:
            self._check_registration(db)
            self._check_request_links(db)
            rows,qa,web,route9 = self._partition(db)
            authorization = getattr(self,"_authorized_sha256",None)
            require(authorization is not None and db.execute("SELECT * FROM case_qa_grants_v1 WHERE campaign_id=?",(self.config["campaign"],)).fetchone()
                    == (self.config["campaign"],str(self.config_path),self.config_sha,authorization), "qa_grant_changed")
            matches = [r for r in qa if r[0] == attempt]
            require(len(matches) == 1, "qa_settlement_scope_invalid")
            row = matches[0]
            require(row[1] == "deepseek" and row[2] in ("reserved","unknown"), "invalid_settlement_transition")
            path = Path(receipt_path).resolve()
            require(row[8:10] == (receipt_sha,str(path)) and path.is_file() and file_hash(path) == receipt_sha, "qa_receipt_changed")
            body = read(path)["body"]
            actual_usage,actual_cost = cloud_usage(body,self.config["profile"])
            actual_identity = canonical([body.get("model"),body.get("system_fingerprint")])
            require(cost_micro == actual_cost and canonical(usage) == canonical(actual_usage)
                    and identity == actual_identity, "qa_accounting_changed")
            violation = bool(integrity_stop or self._hard(body,actual_usage,actual_cost,row[3]))
            db.execute("UPDATE model_attempts SET state='settled' WHERE attempt_id=?",(attempt,))
            db.execute("UPDATE execution_requests SET accounted=?,receipt_sha=?,receipt_path=?,result_code=?,identity=?,usage_json=?,integrity_stop=? WHERE attempt_id=?",
                       (cost_micro,receipt_sha,str(path),result_code,identity,canonical(actual_usage),int(violation),attempt))
        if violation:
            raise ExecutionStop("backend_revision_or_reservation_changed")

    def generate(self, ticket, packet):
        self._validate_config()
        payload = self.payload(packet)
        with self.transaction() as db:
            self._grant(db)
            self._partition(db)
            row = db.execute("SELECT q.packet_json,q.request_key,q.phase,q.reservation,q.transport_started,a.state,r.receipt_path FROM case_qa_requests_v1 q JOIN model_attempts a USING(attempt_id) JOIN execution_requests r USING(attempt_id) WHERE q.attempt_id=?",(ticket["attempt_id"],)).fetchone()
            require(row and row[:4] == (canonical(packet),ticket["request_key"],ticket["phase"],ticket["reserved_micro_cny"])
                    and ticket["payload_sha256"] == digest(payload) and ticket["mode"] == "live_case_qa", "qa_ticket_changed")
            replay = row[4] == 1
            if not replay:
                require(row[5] == "reserved", "qa_request_state_invalid")
                db.execute("UPDATE case_qa_requests_v1 SET transport_started=1 WHERE attempt_id=?",(ticket["attempt_id"],))
        if replay:
            receipt = read(row[6]) if row[5] == "settled" and row[6] else None
            audit = self._audit(ticket,attempts=0,replayed=True)
            ok = audit["state"] == "settled" and audit["result_code"] == "ok" and not audit["integrity_stop"]
            return {"raw":self._raw(receipt["body"]) if receipt else None, "audit":audit,
                    "error_code":None if ok else "qa_saved_request_unresolved"}
        try:
            # Resolve credentials only after authorization, durable reservation and send marker.
            key = (self.credential() if self.credential else "fixture-key-not-real") if self.fixture else credentials(Path(self.config["runtime_root"]))
            require(type(key) is str and bool(re.fullmatch(r"[A-Za-z0-9_-]{8,256}",key)), "official_credentials_unconfigured")
        except Exception:
            self.unknown(ticket["attempt_id"],"qa_credentials_unavailable")
            return {"raw":None, "audit":self._audit(ticket,attempts=0), "error_code":"qa_credentials_unavailable"}
        try:
            body = post_one(self.config["profile"]["deepseek"]["endpoint"],payload,
                            headers={"Authorization":"Bearer "+key}, timeout=self.config["profile"]["timeout_seconds"],
                            transport=self.transport, proxy=None if self.fixture else "http://127.0.0.1:7890")
        except Exception:
            self.unknown(ticket["attempt_id"],"qa_transport_unknown")
            return {"raw":None, "audit":self._audit(ticket,attempts=1), "error_code":"qa_transport_unknown"}
        raw = self._raw(body)
        receipt = {"version":VERSION, "attempt_id":ticket["attempt_id"], "payload_sha256":digest(payload),
                   "profile_sha256":digest(self.config["profile"]), "execution_revision":REVISION, "body":body,
                   "transport":"httpx.MockTransport" if self.fixture else "official_https_via_127.0.0.1:7890",
                   "fixture":self.fixture}
        directory = self.config_path.parent/"receipts"
        path = directory/(ticket["attempt_id"]+".json")
        try:
            directory.mkdir(parents=True,exist_ok=True)
            with path.open("x",encoding="utf-8",newline="\n") as handle:
                handle.write(canonical(receipt))
            self.attach_receipt(ticket["attempt_id"],file_hash(path),str(path))
        except Exception:
            self.unknown(ticket["attempt_id"],"qa_receipt_save_failed")
            return {"raw":raw, "audit":self._audit(ticket,attempts=1), "error_code":"qa_receipt_save_failed"}
        try:
            usage,cost = cloud_usage(body,self.config["profile"])
            require(type(body["usage"].get("completion_tokens_details",{})) is dict, "qa_usage_unknown")
        except Exception:
            self.unknown(ticket["attempt_id"],"qa_usage_unknown")
            return {"raw":raw, "audit":self._audit(ticket,attempts=1), "error_code":"qa_usage_unknown"}
        identity = canonical([body.get("model"),body.get("system_fingerprint")])
        choices = body.get("choices")
        hard = self._hard(body,usage,cost,ticket["reserved_micro_cny"])
        valid = (type(choices) is list and len(choices) == 1 and type(choices[0]) is dict and choices[0].get("finish_reason") == "stop"
                 and type(choices[0].get("message")) is dict and choices[0]["message"].get("role") == "assistant"
                 and not choices[0]["message"].get("tool_calls") and type(raw) is str and len(raw.encode("utf-8")) <= LIMITS["max_output_bytes"])
        code = "qa_identity_or_context_violation" if hard else "ok" if valid else "qa_output_invalid"
        try:
            self.settle(ticket["attempt_id"],cost_micro=cost,receipt_sha=file_hash(path),receipt_path=str(path),
                                       identity=identity,usage=usage,result_code=code,integrity_stop=hard)
        except ExecutionStop:
            code = "budget_integrity_stop"
        return {"raw":raw, "audit":self._audit(ticket,attempts=1), "error_code":None if code == "ok" else code}
