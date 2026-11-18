"""One bounded quality successor; authenticate old and new QA without rewriting either."""
from pathlib import Path
import re

from extensions.case_qa_live_v1.budget import dynamic_scope
from extensions.case_qa_live_v2.budget import LiveCaseQABudgetV2
from extensions.case_qa_quality_v1.service import QualityService
from extensions.case_qa_v1.budget import CaseQABudget, HISTORY_SQL, read, require
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash

ADAPTER_REVISION = "case-qa-live-quality-v3"
ANSWER_REVISION = "case-qa-quality-v1"
QA_SQL = "SELECT * FROM case_qa_requests_v1"
ORDER = " ORDER BY a.rowid"


class _ScopeView:
    """Only the exact QA/history reads are partitioned; real sums/counts pass through."""
    def __init__(self, db, own, excluded, quarantined=None):
        self.db, self.own, self.excluded = db, tuple(own), tuple(excluded)
        self.quarantined = quarantined
        self.history_reads, self.qa_reads = 0, 0

    def execute(self, sql, parameters=()):
        parameters = tuple(parameters)
        if sql == QA_SQL:
            require(not parameters, "qa_scope_projection_invalid")
            self.qa_reads += 1
            if self.excluded:
                sql += " WHERE attempt_id NOT IN (" + ",".join("?" for _ in self.excluded) + ")"
                return self.db.execute(sql, self.excluded)
            return self.db.execute(sql)
        forms = [(HISTORY_SQL, ())]
        if self.own:
            tail = " WHERE a.attempt_id NOT IN (" + ",".join("?" for _ in self.own) + ")"
            forms.append((HISTORY_SQL.replace(ORDER, tail + ORDER), self.own))
        if self.quarantined is not None:
            tail = " WHERE a.attempt_id<>?"
            forms.append((HISTORY_SQL.replace(ORDER, tail + ORDER), (self.quarantined,)))
            if self.own:
                tail += " AND a.attempt_id NOT IN (" + ",".join("?" for _ in self.own) + ")"
                forms.append((HISTORY_SQL.replace(ORDER, tail + ORDER), (self.quarantined,) + self.own))
        for expected, wanted in forms:
            if sql == expected:
                require(parameters == wanted, "qa_scope_projection_invalid")
                self.history_reads += 1
                if self.excluded:
                    condition = "a.attempt_id NOT IN (" + ",".join("?" for _ in self.excluded) + ")"
                    sql = sql.replace(ORDER, (" AND " if " WHERE " in sql else " WHERE ") + condition + ORDER)
                    return self.db.execute(sql, parameters + self.excluded)
                return self.db.execute(sql, parameters)
        return self.db.execute(sql, parameters)

    def verified(self):
        require(self.history_reads == (3 if self.quarantined is not None else 2)
                and self.qa_reads == 1, "qa_scope_projection_invalid")


class LiveQualityBudget(LiveCaseQABudgetV2):
    def _validate_config(self, path=None, contract=None):
        super()._validate_config(path, contract)
        c = self.config
        require(c.get("adapter_revision") == ADAPTER_REVISION and c.get("answer_revision") == ANSWER_REVISION,
                "qa_quality_revision_invalid")
        root = Path(__file__).resolve().parents[2]
        for relative in ("extensions/case_qa_live_v3/budget.py", "extensions/case_qa_live_v3/runtime.py",
                         "extensions/case_qa_live_v3/service.py", "extensions/case_qa_quality_v1/service.py",
                         "extensions/case_qa_quality_v1/contract.py"):
            source = (root / relative).resolve()
            require(source.is_file() and c["files"].get(str(source)) == file_hash(source), "qa_source_binding_missing")
        p = c.get("predecessors")
        require(type(p) is dict and set(p) == {"v1", "v2", "attempt_ids", "request_rows_sha256", "history_rows_sha256"},
                "qa_predecessor_binding_invalid")
        require(type(p["attempt_ids"]) is list and len(p["attempt_ids"]) == 1
                and type(p["attempt_ids"][0]) is str and re.fullmatch(r"[0-9a-f]{32}", p["attempt_ids"][0]),
                "qa_predecessor_binding_invalid")
        for key in ("request_rows_sha256", "history_rows_sha256"):
            require(type(p[key]) is str and re.fullmatch(r"[0-9a-f]{64}", p[key]), "qa_predecessor_binding_invalid")
        campaigns = {c["campaign"]}
        for label in ("v1", "v2"):
            previous = p[label]
            for key in ("config", "approval"):
                source = Path(previous[key + "_path"])
                require(source.is_file() and file_hash(source) == previous[key + "_sha256"], "qa_predecessor_binding_changed")
            old = read(previous["config_path"])
            require(old["campaign"] not in campaigns and Path(old["ledger_path"]).resolve() == self.path
                    and old.get("fixture") is self.fixture, "qa_predecessor_scope_changed")
            campaigns.add(old["campaign"])
            for key in ("cases", "caps", "limits", "expected_identity", "base_contract_sha256", "parent_bindings"):
                require(old[key] == c[key], "qa_predecessor_scope_changed")
            # Validate every historical approval predicate, but do not demand
            # that a frozen predecessor's pricing date be refreshed for reading.
            note = read(previous["approval_path"])
            require(note.get("version") == "case-qa-live-approval-v1" and note.get("campaign") == old["campaign"]
                    and note.get("purpose") == "case_qa" and note.get("config_sha256") == previous["config_sha256"]
                    and note.get("outbound_authorized") is True and note.get("recipient") == "DeepSeek official API"
                    and note.get("caps") == old["caps"] and note.get("dynamic_scope") == dynamic_scope(old)
                    and note.get("max_requests") == 6 and note.get("max_cost_micro_cny") == 5_000_000
                    and note.get("parent_price_addendum_authorized") is True and note.get("current_price_contract_authorized") is True
                    and note.get("preserve_parent_history") is True
                    and type(note.get("question_item_id")) is str and bool(note["question_item_id"].strip())
                    and type(note.get("user_answer")) is str and bool(note["user_answer"].strip()), "qa_predecessor_approval_invalid")

    def _authorization(self):
        authorization = super()._authorization()
        note = read(self.approval_path)
        require(note.get("max_new_requests") == 5 and note.get("preserve_predecessor_results") is True,
                "case_qa_not_authorized")
        return authorization

    def _historical_grants(self, db):
        for label in ("v1", "v2"):
            p = self.config["predecessors"][label]
            old = read(p["config_path"])
            require(db.execute("SELECT * FROM case_qa_grants_v1 WHERE campaign_id=?", (old["campaign"],)).fetchone()
                    == (old["campaign"], str(Path(p["config_path"]).resolve()), p["config_sha256"], p["approval_sha256"]),
                    "qa_predecessor_grant_changed")

    def _partition(self, db):
        self._validate_config()
        self._historical_grants(db)
        all_rows = db.execute(HISTORY_SQL).fetchall()
        entries = db.execute(QA_SQL).fetchall()
        old_ids = tuple(self.config["predecessors"]["attempt_ids"])
        old_entries = [r for r in entries if r[0] in old_ids]
        old_rows = [r for r in all_rows if r[0] in old_ids]
        require(len(old_entries) == len(old_rows) == 1
                and digest(old_entries) == self.config["predecessors"]["request_rows_sha256"]
                and digest(old_rows) == self.config["predecessors"]["history_rows_sha256"]
                and old_rows[0][2] == "settled" and old_rows[0][-1] == 0, "qa_predecessor_request_changed")
        new_ids = tuple(r[0] for r in entries if r[0] not in old_ids)
        p = self.config["predecessors"]["v2"]
        old_budget = LiveCaseQABudgetV2(self.path, self.contract, p["config_path"],
            parent_protocol=self.parent_protocol, approval_path=p["approval_path"], transport=self.transport, fixture=self.fixture)
        recovery = getattr(self.parent_protocol, "recovery_cfg", None)
        h1 = recovery["unknown"]["attempt_id"] if recovery else None
        previous_view = _ScopeView(db, old_ids, new_ids, h1)
        previous, previous_qa, previous_web, previous_route9 = old_budget._partition(previous_view)
        previous_view.verified()
        current_view = _ScopeView(db, new_ids, old_ids, h1)
        current, current_qa, web, route9 = LiveCaseQABudgetV2._partition(self, current_view)
        current_view.verified()
        require(previous == [r for r in all_rows if r[0] not in new_ids]
                and current == [r for r in all_rows if r[0] not in old_ids]
                and previous_web == web and previous_route9 == route9
                and [r[0] for r in previous_qa] == list(old_ids)
                and [r[0] for r in current_qa] == list(new_ids), "qa_scope_projection_invalid")
        combined = set(old_ids + new_ids)
        return all_rows, [r for r in all_rows if r[0] in combined], web, route9

    def reserve(self, value, request, packet):
        require(packet == QualityService._packet(self, value, request)[0], "qa_history_scope_changed")
        ticket = CaseQABudget.reserve(self, value, request, packet)
        ticket["mode"] = "live_case_qa"
        return ticket

    def settle(self, attempt, **kwargs):
        require(attempt not in self.config["predecessors"]["attempt_ids"], "qa_settlement_scope_invalid")
        return super().settle(attempt, **kwargs)
