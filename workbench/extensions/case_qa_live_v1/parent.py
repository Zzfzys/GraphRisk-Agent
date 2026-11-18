"""Read-only predecessor authentication for separately approved case-QA sends.

No hooks are installed. Old prices remain historical; only a new case-QA
approval can adopt this separately pinned current-price contract.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_providers import reservation_micro
from graphrisk.agent.upgrade_contract import digest, file_hash
from extensions.case_qa_v1.budget import CAPS, LIMITS
import web_trial_protocol as original
from web_trial_recovery import RecoveryTrialProtocol

VERSION = "case-qa-live-v1"
APPROVAL_VERSION = "case-qa-live-approval-v1"


def require(ok, code):
    if not ok:
        raise ExecutionStop(code)


def today():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


class PinnedParent(RecoveryTrialProtocol):
    """Reuse predecessor history checks without authorizing old-purpose sends."""

    def __init__(self, config_path, approval_path=None):
        self.qa_config_path = Path(config_path).resolve()
        self.qa_config = original.read(self.qa_config_path)
        self.qa_config_sha256 = file_hash(self.qa_config_path)
        self.qa_approval_path = Path(approval_path).resolve() if approval_path else None
        self._validate_qa_files()
        parent = self.qa_config["parent"]
        super().__init__(parent["config_path"], parent["approval_path"],
                         recovery_config_path=parent["recovery_config_path"],
                         recovery_approval_path=parent["recovery_approval_path"])
        require(Path(self.qa_config["ledger_path"]).resolve() == Path(self.cfg["ledger_path"]).resolve(),
                "qa_parent_ledger_changed")
        require(bool(self.qa_config.get("fixture")) == bool(self.cfg.get("fixture")), "qa_parent_mode_changed")

    def _validate_qa_files(self):
        cfg = self.qa_config
        require(file_hash(self.qa_config_path) == self.qa_config_sha256
                and original.read(self.qa_config_path) == cfg, "qa_config_changed")
        require(cfg.get("version") == VERSION and cfg.get("purpose") == "case_qa", "qa_parent_scope_invalid")
        require(cfg.get("caps") == CAPS and cfg.get("limits") == LIMITS, "qa_caps_changed")
        require(type(cfg.get("parent")) is dict, "qa_parent_binding_changed")
        parent = cfg["parent"]
        for key in ("config", "approval", "recovery_config", "recovery_approval"):
            path = Path(parent[key + "_path"])
            require(path.is_file() and file_hash(path) == parent[key + "_sha256"], "qa_parent_binding_changed")
        require(type(cfg.get("files")) is dict
                and cfg["files"].get(str(Path(__file__).resolve())) == file_hash(Path(__file__)), "qa_source_binding_missing")
        for path, sha in cfg["files"].items():
            require(Path(path).is_file() and file_hash(Path(path)) == sha, "qa_bound_file_changed")
        pricing_path = Path(cfg["pricing_path"])
        require(pricing_path.is_file() and file_hash(pricing_path) == cfg["pricing_sha256"], "qa_pricing_binding_changed")
        price = original.read(pricing_path)
        profile = cfg["profile"]
        require(price.get("version") == "case-qa-official-pricing-v1"
                and price.get("timezone") == "Asia/Hong_Kong"
                and price.get("source_url") == "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
                and price.get("currency") == "CNY" and price.get("unit") == "per_million_tokens"
                and profile["deepseek"]["endpoint"] == "https://api.deepseek.com/chat/completions"
                and price.get("model") == profile["deepseek"]["model"] == "deepseek-flash"
                and price.get("documented_revision") == profile["deepseek"]["documented_revision"]
                and price.get("peak_cny_per_million") == profile["deepseek"]["peak_cny_per_million"]
                and price.get("reservation_context_limit") == profile["deepseek"]["context_limit"] == 1048576
                and price.get("max_output_tokens") == profile["max_output_tokens"] == 1024
                and price.get("verified_date") == profile["deepseek"]["pricing_verified_date"]
                and price.get("single_request_reservation_micro_cny") == reservation_micro(profile)
                and 0 < reservation_micro(profile) <= CAPS["batch_micro_cny"], "qa_pricing_contract_invalid")

    def _validate_files(self):
        # Retain original source inventory, bindings, caps and exact H1 checks.
        super()._validate_files()
        self._validate_qa_files()
        if not self.cfg.get("fixture"):
            require(len(self.cfg["source_inventory"]) == 311
                    and len(self.cfg["extension_files"]) == 9
                    and len(self.recovery_cfg["files"]) == 10, "qa_parent_inventory_changed")

    def _historical_approval(self):
        """Exactly the original two approval predicates, without redating them."""
        self._validate_files()
        require(self.approval_path is not None and self.approval_path.is_file(), "qa_parent_approval_required")
        note = original.read(self.approval_path)
        require(note.get("outbound_authorized") is True
                and note.get("config_sha256") == self.sha256
                and note.get("max_web_requests") == 6
                and note.get("max_cost_micro_cny") == 5_000_000
                and note.get("route9_resume_protocol_authorized") is True
                and isinstance(note.get("question_item_id"), str) and bool(note["question_item_id"].strip())
                and isinstance(note.get("user_answer"), str) and bool(note["user_answer"].strip()), "qa_parent_approval_required")
        require(self.recovery_approval_path is not None and self.recovery_approval_path.is_file(), "qa_parent_recovery_approval_required")
        recovery = original.read(self.recovery_approval_path)
        require(recovery.get("recovery_config_sha256") == self.recovery_sha256
                and recovery.get("unknown_attempt_id") == self.recovery_cfg["unknown"]["attempt_id"]
                and recovery.get("exact_unknown_quarantine_authorized") is True
                and recovery.get("max_remaining_web_requests") == 5
                and recovery.get("preserve_unknown") is True and recovery.get("no_h1_retry") is True
                and isinstance(recovery.get("question_item_id"), str) and bool(recovery["question_item_id"].strip())
                and isinstance(recovery.get("user_answer"), str) and bool(recovery["user_answer"].strip()), "qa_parent_recovery_approval_required")
        return note, recovery

    def authorize(self):
        """Only the new QA scope may adopt this current-price addendum."""
        self._historical_approval()
        cfg = self.qa_config
        require(self.qa_approval_path is not None and self.qa_approval_path.is_file(), "case_qa_approval_required")
        note = original.read(self.qa_approval_path)
        require(note.get("version") == APPROVAL_VERSION
                and note.get("outbound_authorized") is True
                and note.get("campaign") == cfg["campaign"]
                and note.get("recipient") == "DeepSeek official API"
                and note.get("purpose") == "case_qa"
                and note.get("config_sha256") == self.qa_config_sha256
                and type(note.get("max_requests")) is int and note["max_requests"] == 6
                and type(note.get("max_cost_micro_cny")) is int and note["max_cost_micro_cny"] == 5_000_000
                and note.get("current_price_contract_authorized") is True
                and note.get("parent_price_addendum_authorized") is True
                and note.get("preserve_parent_history") is True
                and note.get("caps") == cfg["caps"]
                and note.get("dynamic_scope") == {"question_chars": 2000, "history_completed_pairs": 3,
                    "context_bytes": 32768, "purpose": "case_qa", "cases_sha256": digest(cfg["cases"])}
                and isinstance(note.get("question_item_id"), str) and bool(note["question_item_id"].strip())
                and isinstance(note.get("user_answer"), str) and bool(note["user_answer"].strip()), "case_qa_approval_required")
        require(original.read(cfg["pricing_path"])["verified_date"] == today(), "qa_pricing_refresh_required")
        return note

    def _grant(self, db, *, required=True):
        # A historical grant is evidence, never a capability for the new scope.
        self._historical_approval()
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='web_trial_grants'").fetchone()
        row = db.execute("SELECT config_path,config_sha,approval_path,approval_sha FROM web_trial_grants WHERE campaign_id=?",
                         (self.cfg["campaign"],)).fetchone() if exists else None
        if row is None:
            require(not required, "qa_parent_grant_missing")
            return None
        require(row == (str(self.config_path), self.sha256, str(self.approval_path), file_hash(self.approval_path)), "qa_parent_grant_changed")
        require(db.execute("SELECT manifest_sha,cap_each,group_key FROM autonomous_campaigns WHERE campaign_id=?",
                           (self.cfg["campaign"],)).fetchone() == (self.sha256, 6, original.GROUP), "qa_parent_campaign_changed")
        return row

    def _unknown(self, db, rows):
        # Full frozen Recovery._unknown predicate; replace only its direct old
        # date authorize call with historical approval authentication.
        self._historical_approval()
        cfg = self.recovery_cfg
        unknown = cfg["unknown"]
        found = [r for r in rows if r[0] == unknown["attempt_id"]]
        require(len(found) == 1 and digest(found[0]) == unknown["row_sha256"], "qa_parent_unknown_row_changed")
        row = found[0]
        require(rows[self.cfg["baseline"]["count"]] == row and list(row) == unknown["row"]
                and row[1:3] == ("deepseek", "unknown") and row[8:10] == (None, None)
                and row[10:] == ("provider_connection_unknown", None, None, 0)
                and row[3] == row[7] == unknown["accounted_micro_cny"]
                and row[3] == reservation_micro(original.read(self.cfg["profile_path"]))
                and row[4] == unknown["request_key"] and row[5] == unknown["phase"]
                and row[6] == unknown["payload_sha256"], "qa_parent_unknown_scope_changed")
        grant = db.execute("SELECT * FROM web_trial_grants WHERE campaign_id=?", (self.cfg["campaign"],)).fetchone()
        require(grant is not None and digest(grant) == cfg["original_grant_sha256"]
                and grant == (self.cfg["campaign"], str(self.config_path), self.sha256,
                              str(self.approval_path), file_hash(self.approval_path)), "qa_parent_original_grant_changed")
        bound = db.execute("SELECT s.campaign_id,c.group_key,s.provider,s.max_calls,p.slot "
                           "FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) "
                           "JOIN autonomous_campaigns c USING(campaign_id) WHERE p.phase=?", (row[5],)).fetchone()
        require(bound == (self.cfg["campaign"], original.GROUP, "deepseek", 1, 0)
                and unknown["campaign"] == self.cfg["campaign"], "qa_parent_unknown_campaign_changed")
        require(db.execute("SELECT manifest_sha,cap_each,group_key FROM autonomous_campaigns WHERE campaign_id=?",
                           (self.cfg["campaign"],)).fetchone() == (self.sha256, 6, original.GROUP), "qa_parent_original_campaign_changed")
        case = self._case(payload_sha=row[6])
        require(case["alias"] == unknown["case_alias"] and case["packet_sha256"] == unknown["packet_sha256"]
                and case["profile_sha256"] == unknown["profile_sha256"], "qa_parent_quarantined_case_changed")
        self._verify_web_request(db, row[5], row[4], row[6], case)
        return row

    def preflight(self):
        """Full read-only history check, available before outbound approval."""
        self._historical_approval()
        with sqlite3.connect(Path(self.cfg["ledger_path"]).as_uri() + "?mode=ro", uri=True) as db:
            rows, web, route9 = self._partition(db)
        authorized, reason = True, None
        try:
            self.authorize()
        except ExecutionStop as error:
            if str(error) not in {"case_qa_approval_required", "qa_pricing_refresh_required"}:
                raise
            authorized, reason = False, str(error)
        current_price = original.read(self.qa_config["pricing_path"])["verified_date"] == today()
        return {"status": "authorized_case_qa" if authorized else "approval_required" if reason == "case_qa_approval_required" else "pricing_refresh_required",
                "case_qa_authorized": authorized, "block_reason": reason,
                "protocol_sha256": self.sha256, "recovery_sha256": self.recovery_sha256,
                "case_qa_config_sha256": self.qa_config_sha256,
                "global_calls": len(rows), "global_provider_counts": {p: sum(r[1] == p for r in rows) for p in ("deepseek", "local")},
                "global_accounted_micro_cny": sum(r[7] for r in rows),
                "web_calls": len(web), "web_remaining": 6 - len(web),
                "route9_calls_after_baseline": len(route9),
                "route9_remaining_call_cap": self.cfg["caps"]["route9_remaining_calls"] - len(route9),
                "quarantined_unknown_attempt_id": self.recovery_cfg["unknown"]["attempt_id"],
                "quarantined_hold_micro_cny": self.recovery_cfg["unknown"]["accounted_micro_cny"],
                "quarantined_case_retry_allowed": False, "full_ledger_rows_sha256": digest(rows),
                "price_current": current_price, "old_profile_unchanged": True,
                "old_purpose_send_enabled": False, "hooks_installed": False,
                "read_only": True, "new_requests": 0}

    def status(self):
        return self.preflight()

    def _deny_old_scope(self, *args, **kwargs):
        raise ExecutionStop("qa_parent_readonly_old_purpose_forbidden")

    install = uninstall = register_campaign = register_packet = guard_campaign = _deny_old_scope
