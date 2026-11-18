"""Approved case QA on the shared ledger; construction and preflight are read only."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

import httpx

from extensions.case_qa_v1.budget import CAPS, LIMITS, CaseQABudget, read, require
from extensions.case_qa_v1.sessions import CaseQAService
from graphrisk.agent.autonomous_budget import ExecutionStop, InvestigationBudget
from graphrisk.agent.autonomous_http import post_one
from graphrisk.agent.autonomous_providers import cloud_usage, credentials, reservation_micro
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash

VERSION = "case-qa-live-v1"
EXPECTED_IDENTITY = canonical(["deepseek-flash", "bc19ba97cbe016479903222bb1578db6"])
RATES = {"cache_hit":"0.04", "cache_miss":"2", "output":"8"}


def dynamic_scope(config):
    return {"question_chars":2000, "history_completed_pairs":3, "context_bytes":32768,
            "purpose":"case_qa", "cases_sha256":digest(config["cases"])}


class LiveCaseQABudget(CaseQABudget):
    def __init__(self, path, base_contract, config_path, *, parent_protocol,
                 approval_path=None, transport=None, credential=None, fixture=False):
        self.path, self.contract = Path(path).resolve(), base_contract
        self.mode = "fixture" if fixture else "live"
        self.fixture, self.transport = fixture, transport
        self.parent_protocol = parent_protocol
        self.config_path = Path(config_path).resolve()
        self.config, self.config_sha = read(self.config_path), file_hash(self.config_path)
        self.approval_path = Path(approval_path).resolve() if approval_path else self.config_path.parent/"OUTBOUND-APPROVED.json"
        self.credential = credential
        require(type(fixture) is bool and ((fixture and type(transport) is httpx.MockTransport)
                or (not fixture and transport is None and credential is None)), "qa_transport_not_authorized")
        require(callable(getattr(parent_protocol,"_partition",None)), "qa_parent_binding_changed")
        self._validate_config()
        require(self.path.is_file(), "existing_registered_budget_required")
        try:
            with sqlite3.connect(self.path.as_uri()+"?mode=ro",uri=True) as db:
                self._check_registration(db)
                self._check_request_links(db)
                require(db.execute("SELECT version,mode FROM execution_settings WHERE id=1").fetchone()
                        == (2,self.mode), "execution_ledger_mode_or_version_changed")
        except sqlite3.Error:
            raise ExecutionStop("execution_ledger_incomplete_no_reset") from None

    def _validate_config(self, path=None, contract=None):
        c = self.config
        require(file_hash(self.config_path) == self.config_sha and read(self.config_path) == c, "qa_config_changed")
        require(c.get("version") == VERSION and c.get("purpose") == "case_qa" and c.get("fixture") is self.fixture,
                "qa_config_invalid")
        require(isinstance(c.get("campaign"),str) and re.fullmatch(r"case-qa-[A-Za-z0-9_-]{1,80}",c["campaign"]), "qa_config_invalid")
        require(Path(c["ledger_path"]).resolve() == self.path and c["base_contract_sha256"] == digest(self.contract), "qa_ledger_binding_changed")
        require(c["caps"] == CAPS and c["limits"] == LIMITS, "qa_caps_changed")
        p = c["profile"]
        require(p["deepseek"]["endpoint"] == "https://api.deepseek.com/chat/completions"
                and p["deepseek"]["model"] == "deepseek-flash" and p["deepseek"]["documented_revision"] == "DeepSeek-V4.1-Flash-1017"
                and p["deepseek"]["context_limit"] == 1048576 and p["max_output_tokens"] == 1024
                and p["deepseek"]["peak_cny_per_million"] == RATES
                and p.get("pricing_scope") == "official_peak_verified_today"
                and type(p["timeout_seconds"]) in (int,float) and 0 < p["timeout_seconds"] <= 120
                and reservation_micro(p) == 2_105_344, "qa_profile_invalid")
        require(c["expected_identity"] == EXPECTED_IDENTITY or (self.fixture and c["expected_identity"] == canonical(["deepseek-flash","fixture-v1"])), "qa_profile_invalid")
        require(type(c["system_prompt_sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}",c["system_prompt_sha256"]), "qa_profile_invalid")
        require(type(c["cases"]) is list and len(c["cases"]) == 2 and len({x["case_alias"] for x in c["cases"]}) == 2, "case_qa_not_authorized")
        require(type(c.get("files")) is dict and c["files"].get(str(Path(__file__).resolve())) == file_hash(Path(__file__)), "qa_source_binding_missing")
        for name,sha in c["files"].items():
            require(Path(name).is_file() and file_hash(Path(name)) == sha,"qa_bound_file_changed")
        require(type(c.get("parent_bindings")) is dict and bool(c["parent_bindings"]), "qa_parent_binding_changed")
        for name,sha in c["parent_bindings"].items():
            require(Path(name).is_file() and file_hash(Path(name)) == sha,"qa_parent_binding_changed")
        pricing = Path(c["pricing_path"])
        require(pricing.is_file() and file_hash(pricing) == c["pricing_sha256"], "qa_pricing_changed")
        quote = read(pricing)
        require(quote.get("version") == "case-qa-official-pricing-v1"
                and quote.get("source_url") == "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
                and quote.get("timezone") == "Asia/Hong_Kong"
                and type(quote.get("verified_date")) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}",quote["verified_date"]), "qa_pricing_changed")
        require(quote.get("model") == p["deepseek"]["model"] and quote.get("documented_revision") == p["deepseek"]["documented_revision"]
                and quote.get("peak_cny_per_million") == RATES and quote.get("reservation_context_limit") == 1048576
                and quote.get("max_output_tokens") == 1024 and quote.get("single_request_reservation_micro_cny") == 2_105_344, "qa_pricing_changed")

    def _authorization(self):
        require(read(self.config["pricing_path"])["verified_date"] == datetime.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat(),
                "qa_pricing_refresh_required")
        require(self.approval_path.is_file(), "case_qa_not_authorized")
        a = read(self.approval_path)
        c = self.config
        require(a.get("version") == "case-qa-live-approval-v1" and a.get("campaign") == c["campaign"]
                and a.get("purpose") == "case_qa" and a.get("config_sha256") == self.config_sha
                and a.get("outbound_authorized") is True and a.get("recipient") == "DeepSeek official API"
                and a.get("caps") == c["caps"] and a.get("dynamic_scope") == dynamic_scope(c)
                and a.get("max_requests") == 6 and a.get("max_cost_micro_cny") == 5_000_000
                and a.get("parent_price_addendum_authorized") is True and a.get("current_price_contract_authorized") is True
                and a.get("preserve_parent_history") is True
                and type(a.get("question_item_id")) is str and bool(a["question_item_id"].strip())
                and type(a.get("user_answer")) is str and bool(a["user_answer"].strip()), "case_qa_not_authorized")
        if not self.fixture:
            require(callable(getattr(self.parent_protocol,"authorize",None)), "qa_parent_binding_changed")
            self.parent_protocol.authorize()
        return file_hash(self.approval_path)

    def register(self):
        """The only new-purpose registration entry; call after concrete authorization."""
        self._validate_config()
        authorization = self._authorization()
        with self.transaction() as db:
            self._check_registration(db)
            self._check_request_links(db)
            self._partition(db)
            db.execute("CREATE TABLE IF NOT EXISTS case_qa_grants_v1 (campaign_id TEXT PRIMARY KEY, config_path TEXT NOT NULL, config_sha TEXT NOT NULL, authorization TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS case_qa_requests_v1 (attempt_id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, phase TEXT UNIQUE NOT NULL, session_id TEXT NOT NULL, case_key TEXT NOT NULL, message_id TEXT NOT NULL, request_json TEXT NOT NULL, packet_json TEXT NOT NULL, reservation INTEGER NOT NULL, transport_started INTEGER NOT NULL DEFAULT 0)")
            wanted = (self.config["campaign"],str(self.config_path),self.config_sha,authorization)
            old = db.execute("SELECT * FROM case_qa_grants_v1 WHERE campaign_id=?",(wanted[0],)).fetchone()
            require(old is None or old == wanted, "qa_grant_changed")
            if old is None:
                db.execute("INSERT INTO case_qa_grants_v1 VALUES (?,?,?,?)",wanted)
        return self.usage()

    def _grant(self, db):
        self._validate_config()
        authorization = self._authorization()
        require(db.execute("SELECT 1 FROM sqlite_master WHERE name='case_qa_grants_v1'").fetchone(), "case_qa_not_authorized")
        require(db.execute("SELECT * FROM case_qa_grants_v1 WHERE campaign_id=?",(self.config["campaign"],)).fetchone()
                == (self.config["campaign"],str(self.config_path),self.config_sha,authorization), "qa_grant_changed")

    def usage(self, value=None):
        self._validate_config()
        try:
            self._authorization()
        except ExecutionStop as error:
            if str(error) != "case_qa_not_authorized":
                raise
            return {"scope":"live_case_qa", "provider":"deepseek", "model":"deepseek-flash", "real_deepseek_attempts":None,
                    "mock_attempts_confirmed":0, "unresolved_slots":None, "batch_unresolved_slots":None,
                    "session_remaining_calls":None, "case_remaining_calls":None, "batch_remaining_calls":None,
                    "cost_estimate_cny":None, "batch_cost_estimate_cny":None, "unsettled_hold_cny":None,
                    "budget_status":"authorization_required", "authorization":"not_configured", "block_reason":"case_qa_not_authorized",
                    "cost_kind":"usage_estimate_not_invoice"}
        return super().usage(value)

    def _partition(self, db):
        rows,qa,web,route9 = super()._partition(db)
        for row in qa:
            if row[8] is not None:
                receipt = read(row[9])
                require(receipt.get("version") == VERSION and receipt.get("fixture") is self.fixture
                        and receipt.get("transport") == ("httpx.MockTransport" if self.fixture else "official_https_via_127.0.0.1:7890"),
                        "qa_receipt_changed")
        return rows,qa,web,route9

    def _status(self, db, value=None):
        result = super()._status(db,value)
        result.update(scope="live_case_qa", provider="deepseek", budget_status="shared_ledger",
                      authorization="approved_case_qa", real_deepseek_attempts=result["mock_attempts_confirmed"], mock_attempts_confirmed=0)
        return result

    def reserve(self, value, request, packet):
        # Bind history to the trusted persisted conversation, not arbitrary supplied prose.
        require(packet == CaseQAService._packet(self,value,request)[0], "qa_history_scope_changed")
        ticket = super().reserve(value,request,packet)
        ticket["mode"] = "live_case_qa"
        return ticket

    def payload(self, packet):
        view = packet.get("view",{})
        require(set(view) == {"question","evidence","dialogue_background","context"}, "qa_packet_invalid")
        history = view["dialogue_background"]
        require(type(history) is list and len(history) <= 3 and all(type(t) is dict
                and set(t) == {"turn_id","user","assistant","source"} and t["source"] == "unverified_dialogue"
                and type(t["user"]) is str and len(t["user"]) <= 2000 and type(t["assistant"]) is str for t in history), "qa_history_scope_changed")
        return super().payload(packet)

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
                   "profile_sha256":digest(self.config["profile"]), "body":body,
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
            InvestigationBudget.settle(self,ticket["attempt_id"],cost_micro=cost,receipt_sha=file_hash(path),receipt_path=str(path),
                                       identity=identity,usage=usage,result_code=code,integrity_stop=hard)
        except ExecutionStop:
            code = "budget_integrity_stop"
        return {"raw":raw, "audit":self._audit(ticket,attempts=1), "error_code":None if code == "ok" else code}
