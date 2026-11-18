"""Compact-answer successor: authenticate old three and new three in separate scopes."""
from pathlib import Path
import re

from extensions.case_qa_live_v1.budget import dynamic_scope
from extensions.case_qa_live_v2.budget import LiveCaseQABudgetV2
from extensions.case_qa_live_v3.budget import _ScopeView, QA_SQL, ORDER
from extensions.case_qa_short_v1.budget import ShortBudget
from extensions.case_qa_v1.budget import CaseQABudget, HISTORY_SQL, read, require
from graphrisk.agent.upgrade_contract import digest, file_hash
from .service import CompactService

REVISION = "case-qa-compact-v1"
ADAPTER_REVISION = "case-qa-compact-live-v1"
_HISTORY_HEAD = HISTORY_SQL.removesuffix(ORDER)
_NOT_IN = r" NOT IN \(\?(?:,\?)*\)"
_HISTORY_CLAUSE = r"a\.attempt_id(?:<>\?|" + _NOT_IN + r")"
_QA_CLAUSE = r"attempt_id" + _NOT_IN
_HISTORY_QUERY = re.compile(re.escape(_HISTORY_HEAD) + r"(?: WHERE " + _HISTORY_CLAUSE
    + r"(?: AND " + _HISTORY_CLAUSE + r")*)?" + re.escape(ORDER))
_QA_QUERY = re.compile(re.escape(QA_SQL) + r"(?: WHERE " + _QA_CLAUSE + r"(?: AND " + _QA_CLAUSE + r")*)?")


class _FilteredRows:
    def __init__(self, cursor, excluded):
        self.cursor, self.excluded = cursor, frozenset(excluded)

    def fetchall(self):
        return [row for row in self.cursor.fetchall() if row[0] not in self.excluded]

    def fetchone(self):
        while (row := self.cursor.fetchone()) is not None:
            if row[0] not in self.excluded:
                return row
        return None


class _ResultProjection:
    """Two frozen query families only; execute SQL unchanged, then filter verified new IDs."""
    def __init__(self, db, excluded):
        self.db, self.excluded = db, tuple(excluded)
        self.history_reads, self.qa_reads = 0, 0

    def execute(self, sql, parameters=()):
        parameters = tuple(parameters)
        history, qa = _HISTORY_QUERY.fullmatch(sql), _QA_QUERY.fullmatch(sql)
        if history or qa:
            require(sql.count("?") == len(parameters) and all(type(p) is str for p in parameters),
                    "qa_result_projection_invalid")
            self.history_reads += int(bool(history))
            self.qa_reads += int(bool(qa))
            return _FilteredRows(self.db.execute(sql, parameters), self.excluded)
        require(not sql.startswith(_HISTORY_HEAD) and not sql.startswith(QA_SQL), "qa_result_projection_invalid")
        return self.db.execute(sql, parameters)

    def verified(self):
        require(self.history_reads > 0 and self.qa_reads > 0, "qa_result_projection_invalid")


class CompactBudget(LiveCaseQABudgetV2):
    def _validate_config(self, path=None, contract=None):
        super()._validate_config(path, contract)
        c = self.config
        require(c.get("adapter_revision") == ADAPTER_REVISION
                and c.get("answer_revision") == c.get("prompt_revision") == REVISION, "qa_compact_revision_invalid")
        for name in ("budget.py", "runtime.py", "service.py", "contract.py"):
            source = Path(__file__).with_name(name).resolve()
            require(source.is_file() and c["files"].get(str(source)) == file_hash(source), "qa_source_binding_missing")
        p = c.get("predecessor_scope")
        require(type(p) is dict and set(p) == {"config_path", "config_sha256", "approval_path", "approval_sha256",
                "attempt_ids", "request_rows_sha256", "history_rows_sha256"}, "qa_predecessor_binding_invalid")
        require(type(p["attempt_ids"]) is list and len(p["attempt_ids"]) == len(set(p["attempt_ids"])) == 3
                and all(type(a) is str and re.fullmatch(r"[0-9a-f]{32}", a) for a in p["attempt_ids"]),
                "qa_predecessor_binding_invalid")
        for key in ("request_rows_sha256", "history_rows_sha256"):
            require(type(p[key]) is str and re.fullmatch(r"[0-9a-f]{64}", p[key]), "qa_predecessor_binding_invalid")
        for key in ("config", "approval"):
            source = Path(p[key + "_path"])
            require(source.is_file() and file_hash(source) == p[key + "_sha256"], "qa_predecessor_binding_changed")
        old = read(p["config_path"])
        require(old["campaign"] != c["campaign"] and Path(old["ledger_path"]).resolve() == self.path
                and old.get("fixture") is self.fixture
                and Path(p["config_path"]).resolve().parent != self.config_path.parent, "qa_predecessor_scope_changed")
        for key in ("cases", "caps", "limits", "expected_identity", "base_contract_sha256", "parent_bindings"):
            require(old[key] == c[key], "qa_predecessor_scope_changed")
        v3 = read(old["predecessor_scope"]["config_path"])
        ancestors = [old, v3] + [read(v3["predecessors"][label]["config_path"]) for label in ("v1", "v2")]
        for previous in ancestors:
            for key in ("conversation_path", "trial_result_path"):
                require(key not in previous or Path(previous[key]).resolve() != Path(c[key]).resolve(), "qa_predecessor_path_shared")
        note = read(p["approval_path"])
        require(note.get("version") == "case-qa-live-approval-v1" and note.get("campaign") == old["campaign"]
                and note.get("purpose") == "case_qa" and note.get("config_sha256") == p["config_sha256"]
                and note.get("outbound_authorized") is True and note.get("recipient") == "DeepSeek official API"
                and note.get("caps") == old["caps"] and note.get("dynamic_scope") == dynamic_scope(old)
                and note.get("max_requests") == 6 and note.get("max_cost_micro_cny") == 5_000_000
                and note.get("max_new_requests") == 4 and note.get("preserve_predecessor_results") is True
                and note.get("parent_price_addendum_authorized") is True and note.get("current_price_contract_authorized") is True
                and note.get("preserve_parent_history") is True
                and type(note.get("question_item_id")) is str and bool(note["question_item_id"].strip())
                and type(note.get("user_answer")) is str and bool(note["user_answer"].strip()), "qa_predecessor_approval_invalid")

    def _authorization(self):
        authorization = super()._authorization()
        note = read(self.approval_path)
        require(type(note.get("max_new_requests")) is int and note["max_new_requests"] == 3
                and note.get("preserve_predecessor_results") is True, "case_qa_not_authorized")
        return authorization

    def _partition(self, db):
        self._validate_config()
        p = self.config["predecessor_scope"]
        old_config = read(p["config_path"])
        require(db.execute("SELECT * FROM case_qa_grants_v1 WHERE campaign_id=?", (old_config["campaign"],)).fetchone()
                == (old_config["campaign"], str(Path(p["config_path"]).resolve()), p["config_sha256"], p["approval_sha256"]),
                "qa_predecessor_grant_changed")
        all_rows, entries = db.execute(HISTORY_SQL).fetchall(), db.execute(QA_SQL).fetchall()
        old_ids = tuple(p["attempt_ids"])
        old_entries, old_rows = ([r for r in rows if r[0] in old_ids] for rows in (entries, all_rows))
        require(len(old_entries) == len(old_rows) == 3 and [r[0] for r in old_entries] == list(old_ids)
                and digest(old_entries) == p["request_rows_sha256"] and digest(old_rows) == p["history_rows_sha256"]
                and all(r[2] == "settled" and r[-1] == 0 for r in old_rows)
                and old_entries[0][4] == old_entries[1][4] != old_entries[2][4], "qa_predecessor_request_changed")
        new_ids = tuple(r[0] for r in entries if r[0] not in old_ids)
        recovery = getattr(self.parent_protocol, "recovery_cfg", None)
        h1 = recovery["unknown"]["attempt_id"] if recovery else None
        # Authenticate every prospective exclusion before allowing it to disappear
        # from the predecessor's read view. Unknown non-QA rows are never excluded.
        current = _ScopeView(db, new_ids, old_ids, h1)
        present, current_qa, web, route9 = LiveCaseQABudgetV2._partition(self, current)
        current.verified()
        historical = _ResultProjection(db, [r[0] for r in current_qa])
        old_budget = ShortBudget(self.path, self.contract, p["config_path"], parent_protocol=self.parent_protocol,
            approval_path=p["approval_path"], transport=self.transport, fixture=self.fixture)
        previous, previous_qa, previous_web, previous_route9 = old_budget._partition(historical)
        historical.verified()
        require(previous == [r for r in all_rows if r[0] not in new_ids]
                and present == [r for r in all_rows if r[0] not in old_ids]
                and [r[0] for r in previous_qa] == list(old_ids)
                and [r[0] for r in current_qa] == list(new_ids)
                and previous_web == web and previous_route9 == route9, "qa_result_projection_invalid")
        qa_ids = set(old_ids + new_ids)
        return all_rows, [r for r in all_rows if r[0] in qa_ids], web, route9

    def reserve(self, value, request, packet):
        require(packet == CompactService._packet(self, value, request)[0], "qa_history_scope_changed")
        ticket = CaseQABudget.reserve(self, value, request, packet)
        ticket["mode"] = "live_case_qa"
        return ticket

    def settle(self, attempt, **kwargs):
        require(attempt not in self.config["predecessor_scope"]["attempt_ids"], "qa_settlement_scope_invalid")
        return super().settle(attempt, **kwargs)
