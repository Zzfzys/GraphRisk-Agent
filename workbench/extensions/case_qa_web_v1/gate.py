"""Read-only web continuation gate around the already registered compact budget."""
from pathlib import Path
import sqlite3

from extensions.case_qa_live_v3.budget import QA_SQL
from extensions.case_qa_v1.budget import HISTORY_SQL, read
from extensions.case_qa_v1.sessions import SessionError
from graphrisk.agent.upgrade_contract import digest, file_hash

VERSION = "case-qa-web-resume-v1"
APPROVAL_VERSION = "case-qa-web-resume-approval-v1"
NOTICES = {
    "approval_required": "问答已停批，等待本次网页接续授权；已有记录仍可查看。",
    "quota_exhausted": "本次网页剩余的一次问答已占用，不能再发起新请求；已有记录仍可查看。",
    "ledger_unknown_paused": "请求结果尚未确认，保留预约并暂停新发送；请查看已有记录，不会自动重发。",
    "wrong_case": "本次仅允许M1案剩余的一次问答，其他案件仅可查看。",
    "verification_failed": "问答接续材料或账本核验未通过，已暂停发送；已有记录仍可查看。",
}


def _check(value):
    if not value:
        raise ValueError("verification_failed")


class WebResumeGate:
    def __init__(self, manifest_path, budget):
        self.path, self.budget = Path(manifest_path).resolve(), budget
        self.sha = file_hash(self.path) if self.path.is_file() else None

    def _status(self):
        _check(self.sha is not None and file_hash(self.path) == self.sha)
        manifest, budget = read(self.path), self.budget
        _check(manifest.get("version") == VERSION
               and Path(manifest["config_path"]).resolve() == budget.config_path
               and manifest["config_sha256"] == budget.config_sha == file_hash(budget.config_path)
               and manifest["allowed_case_alias"] == budget.config["cases"][0]["case_alias"])
        for field, limit in (("max_new_requests", 1), ("max_requests", 6), ("max_cost_micro_cny", 5_000_000)):
            _check(type(manifest.get(field)) is int and manifest[field] == limit)
        files = manifest["files"]
        _check(type(files) is dict and files.get(str(Path(__file__).resolve())) == file_hash(Path(__file__)))
        for name, sha in files.items():
            path = Path(name)
            _check(path.is_absolute() and path.is_file() and file_hash(path) == sha)
        baseline = manifest["baseline"]
        ids = baseline["attempt_ids"]
        _check(type(ids) is list and len(ids) == len(set(ids)) == 5 and all(type(i) is str for i in ids))
        with sqlite3.connect(budget.path.as_uri() + "?mode=ro", uri=True) as db:
            # _status authenticates the existing grant and the complete predecessor
            # partition; no new grant or reservation is created here.
            usage = budget._status(db)
            entries = db.execute(QA_SQL).fetchall()
            rows = db.execute(HISTORY_SQL).fetchall()
        old_entries = [r for r in entries if r[0] in ids]
        old_rows = [r for r in rows if r[0] in ids]
        _check([r[0] for r in old_entries] == ids and len(old_rows) == 5
               and digest(old_entries) == baseline["request_rows_sha256"]
               and digest(old_rows) == baseline["history_rows_sha256"]
               and all(r[2] == "settled" and r[-1] == 0 for r in old_rows))
        extra = [r for r in entries if r[0] not in ids]
        allowed = manifest["allowed_case_alias"]
        _check(len(extra) <= 1 and all(r[4] == digest(budget.config["cases"][0]) for r in extra))
        result = {"allowed_case_alias": allowed, "new_requests": len(extra),
                  "remaining_new_requests": 1 - len(extra), "aggregate_requests": len(entries)}
        if usage["block_reason"] == "ledger_unknown_paused":
            return result, "ledger_unknown_paused"
        if extra:
            return result, "quota_exhausted"
        _check(usage["block_reason"] is None)
        approval_path = Path(manifest["resume_approval_path"])
        _check(approval_path.is_absolute() and approval_path.resolve() != budget.approval_path)
        if not approval_path.is_file():
            return result, "approval_required"
        note = read(approval_path)
        _check(note.get("version") == APPROVAL_VERSION and note.get("manifest_sha256") == self.sha
               and note.get("recipient") == "DeepSeek official API" and note.get("outbound_authorized") is True
               and note.get("preserve_predecessor_results") is True)
        for field, limit in (("max_new_requests", 1), ("max_requests", 6), ("max_cost_micro_cny", 5_000_000)):
            _check(type(note.get(field)) is int and note[field] == limit)
        _check(all(type(note.get(k)) is str and note[k].strip() for k in ("question_item_id", "user_answer")))
        return result, None

    def status(self):
        try:
            result, reason = self._status()
        except Exception:
            result, reason = {}, "verification_failed"
        return {**result, "can_send": reason is None, "read_only": reason is not None,
                "state": "ready" if reason is None else "paused", "reason": reason,
                "error_code": None if reason is None else "qa_disabled",
                "notice": "仅可发送M1案剩余的一次问答。" if reason is None else NOTICES[reason]}

    def require(self, case_alias):
        status = self.status()
        if not status["can_send"] or case_alias != status["allowed_case_alias"]:
            raise SessionError("qa_disabled")
        return status
