"""Web resume permission is separate from the old grant; all ledgers are fixtures."""
from pathlib import Path
import sqlite3

import httpx
import pytest

from extensions.case_qa_web_v1 import gate as module
from extensions.case_qa_web_v1.gate import WebResumeGate
from extensions.case_qa_live_v3.budget import QA_SQL
from extensions.case_qa_v1.budget import HISTORY_SQL
from extensions.case_qa_v1.sessions import SessionError
from graphrisk.agent.upgrade_contract import digest, file_hash
from test_case_qa_compact_budget_v1 import make_compact, reserve, write


def make_gate(tmp_path, *, approved=True):
    h = make_compact(tmp_path)
    for number in (41, 42):
        ticket, packet, _, _ = reserve(h, case_index=1, number=number)
        assert h.budget.generate(ticket, packet)["error_code"] is None
    with sqlite3.connect(h.path) as db:
        entries = db.execute(QA_SQL).fetchall()
        ids = [r[0] for r in entries]
        rows = [r for r in db.execute(HISTORY_SQL).fetchall() if r[0] in ids]
    directory = tmp_path / "web"
    directory.mkdir()
    h.manifest_path = directory / "manifest.json"
    h.resume_path = directory / "OUTBOUND-APPROVED.json"
    h.bound_source = directory / "ui.py"
    h.bound_source.write_text("# fixture web UI\n", encoding="utf-8")
    h.manifest = {"version": module.VERSION, "config_path": str(h.config_path),
        "config_sha256": file_hash(h.config_path), "files": {
            str(Path(module.__file__).resolve()): file_hash(Path(module.__file__)),
            str(h.bound_source): file_hash(h.bound_source)},
        "baseline": {"attempt_ids": ids, "request_rows_sha256": digest(entries), "history_rows_sha256": digest(rows)},
        "allowed_case_alias": h.cases[0]["case_alias"], "max_new_requests": 1, "max_requests": 6,
        "max_cost_micro_cny": 5_000_000, "resume_approval_path": str(h.resume_path)}
    write(h.manifest_path, h.manifest)
    h.resume = {"version": module.APPROVAL_VERSION, "manifest_sha256": file_hash(h.manifest_path),
        "recipient": "DeepSeek official API", "outbound_authorized": True, "max_new_requests": 1,
        "max_requests": 6, "max_cost_micro_cny": 5_000_000, "preserve_predecessor_results": True,
        "question_item_id": "synthetic-web-resume", "user_answer": "synthetic one-call approval"}
    if approved:
        write(h.resume_path, h.resume)
    h.gate = WebResumeGate(h.manifest_path, h.budget)
    h.calls.clear()
    return h


def closed(h):
    assert not h.gate.status()["can_send"]
    with pytest.raises(SessionError, match="qa_disabled"):
        h.gate.require(h.cases[0]["case_alias"])


def test_missing_resume_permission_is_read_only_without_registering_or_writing(tmp_path):
    h = make_gate(tmp_path, approved=False)
    before = h.path.read_bytes()
    assert h.gate.status()["reason"] == "approval_required"
    closed(h)
    assert h.path.read_bytes() == before and not h.calls


def test_approved_gate_allows_only_m1_then_one_reservation_exhausts_even_across_sessions(tmp_path):
    h = make_gate(tmp_path)
    before = h.path.read_bytes()
    assert h.gate.require(h.cases[0]["case_alias"])["remaining_new_requests"] == 1
    with pytest.raises(SessionError, match="qa_disabled"):
        h.gate.require(h.cases[1]["case_alias"])
    assert h.path.read_bytes() == before and not h.calls
    ticket, packet, _, _ = reserve(h, case_index=0, number=80, session="7" * 32)
    assert h.gate.status()["reason"] == "ledger_unknown_paused"
    closed(h)
    assert h.budget.generate(ticket, packet)["error_code"] is None
    assert h.gate.status()["reason"] == "quota_exhausted"
    closed(h)
    assert len(h.calls) == 1


@pytest.mark.parametrize("change", ["source", "old_request", "old_receipt", "baseline", "approval_cap", "approval_binding"])
def test_material_or_frozen_history_changes_fail_closed(tmp_path, change):
    h = make_gate(tmp_path)
    if change == "source":
        h.bound_source.write_text("# changed\n", encoding="utf-8")
    elif change == "old_request":
        with sqlite3.connect(h.path) as db:
            db.execute("UPDATE case_qa_requests_v1 SET request_json='{}' WHERE attempt_id=?",
                       (h.manifest["baseline"]["attempt_ids"][0],))
    elif change == "old_receipt":
        h.old_receipts[0].write_text("{}", encoding="utf-8")
    elif change == "baseline":
        h.manifest["baseline"]["history_rows_sha256"] = "f" * 64
        write(h.manifest_path, h.manifest)
    else:
        h.resume["max_new_requests" if change == "approval_cap" else "manifest_sha256"] = 2 if change == "approval_cap" else "f" * 64
        write(h.resume_path, h.resume)
    before = h.path.read_bytes()
    assert h.gate.status()["reason"] == "verification_failed"
    closed(h)
    assert h.path.read_bytes() == before and not h.calls


def test_unknown_extra_request_cannot_be_resumed_or_resent(tmp_path):
    h = make_gate(tmp_path)
    def timeout(request):
        raise httpx.ReadTimeout("synthetic response unknown")
    h.handler = timeout
    ticket, packet, _, _ = reserve(h, case_index=0, number=81)
    result = h.budget.generate(ticket, packet)
    assert result["error_code"] == "qa_transport_unknown"
    assert result["audit"]["state"] == "unknown" and len(h.calls) == 1
    before = h.path.read_bytes()
    assert h.gate.status()["reason"] == "ledger_unknown_paused"
    closed(h)
    assert h.path.read_bytes() == before and len(h.calls) == 1
