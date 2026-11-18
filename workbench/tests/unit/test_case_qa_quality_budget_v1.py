"""Quality packet integration uses only tmp-path ledgers and MockTransport."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sqlite3

import httpx
import pytest

from extensions.case_qa_quality_v1 import budget as quality_budget_module
from extensions.case_qa_quality_v1.budget import QualityBudget
from extensions.case_qa_quality_v1.service import QualityService
from extensions.case_qa_v1.budget import HISTORY_SQL
from extensions.case_qa_v1.sessions import CaseQAService
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical, file_hash
from test_case_qa_budget_v1 import expect, make_budget


def make_quality_budget(tmp_path, **kwargs):
    """Migrate only this synthetic helper's packet/config; keep its old grant."""
    h = make_budget(tmp_path, **kwargs)
    h.previous_config_path = h.config_path
    h.config = deepcopy(h.config)
    h.config["campaign"] = "case-qa-quality-fixture"
    previous_value = h.value
    def value(*args, **kwargs):
        current = previous_value(*args, **kwargs)
        current["turns"] = []
        return current
    h.value = value
    def packet(value=None, request=None):
        return QualityService._packet(object.__new__(QualityService), deepcopy(value or h.value()), request or h.request())[0]
    h.packet = packet
    h.config["system_prompt_sha256"] = hashlib.sha256(packet()["system_prompt"].encode("utf-8")).hexdigest()
    source = Path(quality_budget_module.__file__).resolve()
    for path in (source, source.with_name("service.py"), source.with_name("contract.py")):
        h.config["files"][str(path)] = file_hash(path)
    h.config_path = tmp_path / "quality-fixture.json"
    h.config_path.write_text(canonical(h.config), encoding="utf-8")
    h.budget = QualityBudget(h.path, h.contract, h.config_path, parent_protocol=h.parent, transport=h.transport)
    return h


def test_quality_prompt_three_rounds_keep_fixture_provenance_and_receipts(tmp_path):
    h = make_quality_budget(tmp_path)
    value = h.value()
    for index in range(3):
        request = h.request(message_id=f"{index + 1:032x}", parent=value["turns"][-1]["message_id"] if value["turns"] else None)
        packet = h.packet(value, request)
        assert len(packet["view"]["dialogue_background"]) == index
        assert packet["view"]["context"]["included_turn_ids"] == [t["message_id"] for t in value["turns"]]
        ticket = h.budget.reserve(value, request, packet)
        result = h.budget.generate(ticket, packet)
        assert result["error_code"] is None and result["audit"]["state"] == "settled"
        assert ticket["mode"] == "fixture_only" and result["audit"]["hold_micro_cny"] == 0
        assert h.calls[-1]["messages"][0]["content"] == packet["system_prompt"]
        value["turns"].append({"message_id": request["message_id"], "message": request["message"],
            "result": {"status": "answered", "answer": "Synthetic completed answer"}})
    status = h.budget.usage(value)
    assert len(h.calls) == 3 and status["scope"] == "case_qa_fixture" and status["provider"] == "mock"
    assert status["real_deepseek_attempts"] == 0 and status["mock_attempts_confirmed"] == 3
    assert status["session_remaining_calls"] == status["case_remaining_calls"] == 0
    assert status["protected_web_calls"] == 4 and status["protected_route9_calls"] == 200
    with sqlite3.connect(h.path) as db:
        assert db.execute("SELECT count(*) FROM case_qa_grants_v1").fetchone() == (2,)
    expect("quota_exhausted", lambda: h.reserve(message_id="f" * 32))


@pytest.mark.parametrize("change", ["history", "old_prompt", "question"])
def test_untrusted_history_old_prompt_and_question_changes_stop_before_claim(tmp_path, change):
    h = make_quality_budget(tmp_path)
    value, request = h.value(), h.request()
    packet = h.packet(value, request)
    if change == "history":
        packet["view"]["dialogue_background"] = [{"turn_id": "b" * 32, "user": "forged", "assistant": "trusted", "source": "unverified_dialogue"}]
    elif change == "old_prompt":
        packet = CaseQAService._packet(object.__new__(CaseQAService), value, request)[0]
    else:
        packet["view"]["question"] = "different"
    before = h.path.read_bytes()
    expect("qa_history_scope_changed", lambda: h.budget.reserve(value, request, packet))
    assert h.path.read_bytes() == before and h.calls == []


@pytest.mark.parametrize("filename", ["budget.py", "service.py", "contract.py"])
def test_quality_sources_must_all_be_bound(tmp_path, filename):
    h = make_quality_budget(tmp_path)
    h.config["files"].pop(str(Path(quality_budget_module.__file__).resolve().with_name(filename)))
    h.config_path.write_text(canonical(h.config), encoding="utf-8")
    before = h.path.read_bytes()
    expect("qa_quality_source_binding_missing", lambda: QualityBudget(h.path, h.contract, h.config_path,
        parent_protocol=h.parent, transport=h.transport))
    assert h.path.read_bytes() == before and h.calls == []


def test_quality_fixture_rejects_real_transport_and_live_config(tmp_path):
    h = make_quality_budget(tmp_path)
    before = h.path.read_bytes()
    expect("qa_fixture_only", lambda: QualityBudget(h.path, h.contract, h.config_path,
        parent_protocol=h.parent, transport=None))
    h.config["fixture"] = False
    h.config_path.write_text(canonical(h.config), encoding="utf-8")
    expect("qa_fixture_only", lambda: QualityBudget(h.path, h.contract, h.config_path,
        parent_protocol=h.parent, transport=h.transport))
    assert h.path.read_bytes() == before and h.calls == []


def test_duplicate_replay_and_cross_session_case_limit_do_not_reset(tmp_path):
    h = make_quality_budget(tmp_path)
    saved = None
    for index in range(3):
        value, request = h.value(session=f"{index + 1:032x}"), h.request(message_id=f"{index + 10:032x}")
        packet = h.packet(value, request)
        ticket = h.budget.reserve(value, request, packet)
        result = h.budget.generate(ticket, packet)
        saved = saved or (value, request, ticket, packet)
        assert result["error_code"] is None
    value, request, ticket, packet = saved
    before = h.path.read_bytes()
    expect("qa_request_already_reserved", lambda: h.budget.reserve(value, request, packet))
    replay = h.budget.generate(ticket, packet)
    assert replay["audit"]["transport_attempts"] == 0 and len(h.calls) == 3
    expect("quota_exhausted", lambda: h.reserve(session="f" * 32, message_id="f" * 32))
    assert h.path.read_bytes() == before


def test_batch_six_still_preserves_shared_call_allocations(tmp_path):
    h = make_quality_budget(tmp_path)
    for case_index in range(2):
        for index in range(3):
            value = h.value(case_index=case_index, session=f"{case_index + 1:032x}")
            request = h.request(message_id=f"{case_index * 3 + index + 1:032x}")
            packet = h.packet(value, request)
            result = h.budget.generate(h.budget.reserve(value, request, packet), packet)
            assert result["error_code"] is None
    status = h.budget.usage()
    assert len(h.calls) == 6 and status["batch_remaining_calls"] == 0
    assert status["protected_web_calls"] == 4 and status["protected_route9_calls"] == 200
    assert status["real_deepseek_attempts"] == 0


def test_unknown_keeps_full_hold_stops_other_case_and_never_resends(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout("synthetic timeout")
    h = make_quality_budget(tmp_path, handler=timeout)
    value, request = h.value(), h.request()
    packet = h.packet(value, request)
    ticket = h.budget.reserve(value, request, packet)
    result = h.budget.generate(ticket, packet)
    assert result["error_code"] == "qa_transport_unknown" and result["audit"]["state"] == "unknown"
    assert result["audit"]["hold_micro_cny"] == ticket["reserved_micro_cny"]
    status = h.budget.usage(value)
    assert status["block_reason"] == "ledger_unknown_paused" and status["real_deepseek_attempts"] == 0
    other = h.value(case_index=1, session="2" * 32)
    next_request = h.request(message_id="b" * 32)
    expect("ledger_unknown_paused", lambda: h.budget.reserve(other, next_request, h.packet(other, next_request)))
    replay = h.budget.generate(ticket, packet)
    assert replay["audit"]["transport_attempts"] == 0 and len(h.calls) == 1
    with sqlite3.connect(h.path) as db:
        row = next(r for r in db.execute(HISTORY_SQL).fetchall() if r[0] == ticket["attempt_id"])
    assert row[2] == "unknown" and row[3] == row[7] == ticket["reserved_micro_cny"]
