"""Cumulative web QA allowance, authenticated against an immutable old ledger snapshot.

Reuse the existing single-send transport/receipt accounting. Do not recursively
re-run retired trial protocols when merely reading the current page.
"""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from extensions.case_qa_live_v2.budget import LiveCaseQABudgetV2
from extensions.case_qa_v1.budget import CaseQABudget, HISTORY_SQL, read, require
from extensions.case_qa_compact_v1.service import CompactService
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
from graphrisk.agent.autonomous_providers import cloud_usage, reservation_micro

REVISION = "transaction-workbench-v1"
QA_SQL = "SELECT * FROM case_qa_requests_v1"


class WebBudget(LiveCaseQABudgetV2):
    def __init__(self, config_path, *, transport=None, fixture=False):
        cfg = read(config_path)
        super().__init__(Path(cfg["ledger_path"]), cfg["base_contract"], config_path,
                         parent_protocol=self, transport=transport, fixture=fixture)
        self.catalog = None

    def _validate_config(self, path=None, contract=None):
        c = self.config
        require(file_hash(self.config_path) == self.config_sha and read(self.config_path) == c, "qa_config_changed")
        require(c["version"] == REVISION and c["fixture"] is self.fixture, "qa_config_invalid")
        require(c["max_requests"] == 100 and c["max_cost_micro_cny"] == 5_000_000, "qa_caps_changed")
        require(c["base_contract_sha256"] == digest(self.contract) and Path(c["ledger_path"]).resolve() == self.path,
                "qa_ledger_binding_changed")
        require(c["profile"]["deepseek"]["endpoint"] == "https://api.deepseek.com/chat/completions"
                and c["profile"]["deepseek"]["model"] == "deepseek-flash"
                and c["profile"]["deepseek"]["peak_cny_per_million"] == {"cache_hit":"0.04", "cache_miss":"2", "output":"8"}
                and c["profile"]["max_output_tokens"] == 1024
                and c["profile"]["deepseek"]["context_limit"] == 1048576, "qa_profile_invalid")
        # Keep the conservative official-full-context reservation of the trial.
        # At today's prices this is 2.105344 CNY per in-flight request, released
        # to actual usage on settlement. The 100 limit is a ceiling, not a promise.
        require(reservation_micro(c["profile"]) == 2_105_344, "qa_profile_invalid")
        for name, sha in c["files"].items():
            require(Path(name).is_file() and file_hash(Path(name)) == sha, "qa_bound_file_changed")
        require(file_hash(Path(c["baseline_path"])) == c["baseline_sha256"], "qa_history_changed")

    def _authorization(self):
        a = read(self.approval_path)
        require(a["config_sha256"] == self.config_sha and a["outbound_authorized"] is True
                and a["max_requests"] == 100 and a["max_cost_micro_cny"] == 5_000_000
                and a["recipient"] == "DeepSeek official API" and a["preserve_parent_history"] is True,
                "case_qa_not_authorized")
        self._authorized_sha256 = file_hash(self.approval_path)
        return self._authorized_sha256

    def payload(self, packet):
        history = packet.get("view", {}).get("dialogue_background")
        require(type(history) is list and len(history) <= 3, "qa_history_scope_changed")
        return CaseQABudget.payload(self, packet)

    def _case(self, value):
        c = value["case"]
        require(self.catalog is not None and self.catalog.check(c["case_alias"], c["binding"]) == c,
                "case_qa_not_authorized")
        return self.scope(c), digest(self.scope(c))

    @staticmethod
    def scope(c):
        return {"case_alias":c["case_alias"], "binding":c["binding"], "batch_revision":c["batch_revision"],
                "evidence_version":c["evidence"]["evidence_version"], "view_sha256":c["evidence"]["view_sha256"]}

    def _partition(self, db):
        self._validate_config()
        baseline = read(self.config["baseline_path"])
        rows = db.execute(HISTORY_SQL).fetchall()
        qa = db.execute(QA_SQL).fetchall()
        old_ids = set(baseline["attempt_ids"])
        old_qa = set(baseline["qa_attempt_ids"])
        require(digest([r for r in rows if r[0] in old_ids]) == baseline["history_sha256"]
                and digest([r for r in qa if r[0] in old_qa]) == baseline["qa_sha256"], "qa_history_changed")
        grants = db.execute("SELECT * FROM case_qa_grants_v1").fetchall()
        require(digest([r for r in grants if r[0] != self.config["campaign"]]) == baseline["grants_sha256"]
                and digest(db.execute("SELECT * FROM provider_identity").fetchall()) == baseline["identity_sha256"],
                "qa_history_changed")
        new = [r for r in qa if r[0] not in old_qa]
        require({r[0] for r in rows if r[0] not in old_ids} == {r[0] for r in new}, "qa_request_links_invalid")
        by_id = {r[0]:r for r in rows}
        for entry in new:
            attempt,key,phase,session,case_key,message,request_json,packet_json,reservation,started = entry
            packet, request = json.loads(packet_json), json.loads(request_json)
            require(self.catalog is not None, "evidence_unavailable")
            case = self.catalog.by_alias(packet["view"]["evidence"]["case_alias"])
            require(case_key == digest(self.scope(case)) and packet["view"]["evidence"] == case["evidence"]
                    and request["message"] == packet["view"]["question"] and request["message_id"] == message
                    and request["evidence_version"] == case["evidence"]["evidence_version"]
                    and key == digest([self.config_sha,session,message]) and phase == "case-qa-"+key
                    and reservation == reservation_micro(self.config["profile"]) and started in (0,1), "qa_request_binding_changed")
            payload = self.payload(packet)
            row = by_id[attempt]
            require(row[1] == "deepseek" and row[2] in ("reserved","unknown","settled")
                    and row[3:7] == (reservation,key,phase,digest(payload))
                    and db.execute("SELECT manifest_sha,cloud_cap,local_cap FROM execution_phases WHERE phase=?",(phase,)).fetchone()
                    == (digest(packet),1,0), "qa_request_binding_changed")
            require(row[7] == reservation if row[2] != "settled" else type(row[7]) is int and row[7] >= 0,
                    "qa_accounting_changed")
            if row[8] is not None:
                p = Path(row[9]).resolve()
                require(p.parent == self.config_path.parent/"receipts" and file_hash(p) == row[8] and started == 1,
                        "qa_receipt_changed")
                receipt = read(p)
                require(receipt["attempt_id"] == attempt and receipt["payload_sha256"] == row[6]
                        and receipt["profile_sha256"] == digest(self.config["profile"]), "qa_receipt_changed")
                if row[2] == "settled":
                    usage,cost = cloud_usage(receipt["body"],self.config["profile"])
                    require(cost == row[7] and canonical(usage) == row[12]
                            and row[11] == canonical([receipt["body"].get("model"),receipt["body"].get("system_fingerprint")])
                            and (not self._hard(receipt["body"],usage,cost,reservation) or row[-1] == 1), "qa_accounting_changed")
            else:
                require(row[2] != "settled", "qa_receipt_changed")
        # Old unknowns remain charged and quarantined, never retried or removed.
        return rows, [by_id[r[0]] for r in new], [], []

    def _status(self, db, value=None):
        self._grant(db)
        rows,new,_,_ = self._partition(db)
        ids = {r[0] for r in db.execute(QA_SQL)}
        qa = [r for r in rows if r[0] in ids]
        unsettled = [r for r in qa if r[2] in ("reserved","unknown")]
        needed = reservation_micro(self.config["profile"])
        cost = sum(r[7] for r in qa)
        counts = {p:sum(r[1] == p for r in rows) for p in ("deepseek","local")}
        reason = ("ledger_unknown_paused" if unsettled else
                  "budget_integrity_stop" if any(r[-1] for r in new) else
                  "quota_exhausted" if len(qa) >= 100 else
                  "budget_exhausted" if cost+needed > 5_000_000 or sum(r[7] for r in rows)+needed > 80_000_000 else
                  "global_quota_protected" if len(rows)+1+200+4 > 2119 or counts["deepseek"]+1+4 > 1813 else None)
        if self.config["pricing_verified_date"] != datetime.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat():
            reason = reason or "qa_pricing_refresh_required"
        def money(n):
            return f"{n/1_000_000:.6f}"
        session_ids = {r[0] for r in db.execute(QA_SQL) if value is None or r[3] == value["conversation_id"]}
        return {"scope":"live_case_qa", "provider":"deepseek", "model":"deepseek-flash", "mock_attempts_confirmed":0,
                "real_deepseek_attempts":sum(r[0] in session_ids for r in qa),
                "unresolved_slots":sum(r[0] in session_ids for r in unsettled), "batch_unresolved_slots":len(unsettled),
                "session_remaining_calls":max(0,100-len(qa)), "case_remaining_calls":max(0,100-len(qa)),
                "batch_remaining_calls":max(0,100-len(qa)), "max_requests":100, "used_requests":len(qa),
                "cost_estimate_cny":money(sum(r[7] for r in qa if r[0] in session_ids and r[2] == "settled")),
                "batch_cost_estimate_cny":money(sum(r[7] for r in qa if r[2] == "settled")),
                "unsettled_hold_cny":money(sum(r[7] for r in unsettled)), "remaining_cny":money(max(0,5_000_000-cost)),
                "max_cost_cny":"5.000000", "budget_status":"shared_ledger", "authorization":"approved_case_qa",
                "block_reason":reason, "global_remaining_calls":max(0,2119-len(rows)),
                "global_deepseek_remaining_calls":max(0,1813-counts["deepseek"]),
                "protected_route9_calls":200, "protected_web_calls":4, "cost_kind":"usage_estimate_not_invoice"}

    def usage(self, value=None):
        with sqlite3.connect(self.path.as_uri()+"?mode=ro",uri=True) as db:
            return self._status(db,value)

    def reserve(self, value, request, packet):
        require(packet == CompactService._packet(self,value,request)[0], "qa_history_scope_changed")
        ticket = CaseQABudget.reserve(self,value,request,packet)
        ticket["mode"] = "live_case_qa"
        return ticket
