"""Live executor checks use synthetic ledgers and exact MockTransport only."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import httpx
import pytest

from extensions.case_qa_live_v1.budget import VERSION, RATES, LiveCaseQABudget, dynamic_scope
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical, file_hash
from test_case_qa_budget_v1 import make_budget, expect


def make_live(tmp_path, *, approved=False, register=False, handler=None):
    h = make_budget(tmp_path,handler=handler)
    c = deepcopy(h.config)
    c.update(version=VERSION,campaign="case-qa-live-test",runtime_root=str(tmp_path))
    c["profile"]["pricing_scope"] = "official_peak_verified_today"
    c["profile"]["deepseek"].update(context_limit=1048576,documented_revision="DeepSeek-V4.1-Flash-1017",peak_cny_per_million=RATES)
    source = Path(__file__).parents[2]/"extensions/case_qa_live_v1/budget.py"
    c["files"][str(source.resolve())] = file_hash(source)
    pricing = tmp_path/"PRICING.json"
    pricing.write_text(canonical({"version":"case-qa-official-pricing-v1", "verified_date":datetime.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat(),
        "source_url":"https://api-docs.deepseek.com/zh-cn/quick_start/pricing/", "timezone":"Asia/Hong_Kong", "model":"deepseek-flash",
        "documented_revision":"DeepSeek-V4.1-Flash-1017", "peak_cny_per_million":RATES, "reservation_context_limit":1048576,
        "max_output_tokens":1024, "single_request_reservation_micro_cny":2_105_344}),encoding="utf-8")
    c.update(pricing_path=str(pricing),pricing_sha256=file_hash(pricing))
    h.config,h.config_path = c,tmp_path/"live-config.json"
    h.config_path.write_text(canonical(c),encoding="utf-8")
    h.approval_path = tmp_path/"OUTBOUND-APPROVED.json"
    h.approval = {"version":"case-qa-live-approval-v1", "campaign":c["campaign"], "purpose":"case_qa", "config_sha256":file_hash(h.config_path),
        "outbound_authorized":True, "recipient":"DeepSeek official API", "question_item_id":"fixture-approval-not-real",
        "user_answer":"synthetic approval only", "caps":c["caps"], "dynamic_scope":dynamic_scope(c), "parent_price_addendum_authorized":True,
        "max_requests":6, "max_cost_micro_cny":5_000_000, "current_price_contract_authorized":True, "preserve_parent_history":True}
    if approved:
        h.approval_path.write_text(canonical(h.approval),encoding="utf-8")
    h.credential_reads = []
    def credential():
        h.credential_reads.append(1)
        return "fixture-credential-value"
    h.budget = LiveCaseQABudget(h.path,h.contract,h.config_path,parent_protocol=h.parent,
        approval_path=h.approval_path,transport=h.transport,credential=credential,fixture=True)
    if register:
        h.budget.register()
    return h


def claim(h, *, case_index=0, session="1"*32, message_id="a"*32):
    value = h.value(case_index,session)
    value["turns"] = []
    request = h.request(message_id)
    packet = h.packet(value,request)
    return h.budget.reserve(value,request,packet),packet


def test_pending_constructor_usage_and_failed_registration_are_readonly(tmp_path):
    h = make_live(tmp_path)
    before = h.path.read_bytes()
    result = h.budget.usage(h.value())
    assert h.path.read_bytes() == before and not h.credential_reads and not h.calls
    assert result["provider"] == "deepseek" and result["scope"] == "live_case_qa"
    assert result["block_reason"] == "case_qa_not_authorized"
    assert result["real_deepseek_attempts"] is None and result["unresolved_slots"] is None and result["batch_unresolved_slots"] is None
    assert all(result[k] is None for k in ("session_remaining_calls","case_remaining_calls","batch_remaining_calls","cost_estimate_cny","unsettled_hold_cny"))
    expect("case_qa_not_authorized",h.budget.register)
    assert h.path.read_bytes() == before


def test_registered_scope_real_labels_and_receipt_settlement(tmp_path):
    h = make_live(tmp_path,approved=True,register=True)
    assert not h.credential_reads
    ticket,packet = claim(h)
    assert ticket["mode"] == "live_case_qa" and ticket["reserved_micro_cny"] == 2_105_344
    assert h.budget.usage(h.value())["unsettled_hold_cny"] == "2.105344"
    result = h.budget.generate(ticket,packet)
    assert result["error_code"] is None and result["audit"]["mode"] == "live_case_qa"
    assert result["audit"]["cost_estimate_micro_cny"] == 280 and h.credential_reads == [1]
    usage = h.budget.usage(h.value())
    assert usage["real_deepseek_attempts"] == 1 and usage["mock_attempts_confirmed"] == 0
    assert usage["scope"] == "live_case_qa" and usage["authorization"] == "approved_case_qa"
    assert usage["budget_status"] == "shared_ledger" and usage["cost_estimate_cny"] == "0.000280"
    receipt = json.loads(Path(result["audit"]["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["version"] == VERSION and receipt["fixture"] is True
    assert "fixture-credential-value" not in canonical(receipt)
    replay = h.budget.generate(ticket,packet)
    assert replay["audit"]["transport_attempts"] == 0 and len(h.calls) == 1 and h.credential_reads == [1]


@pytest.mark.parametrize("field,value",[("outbound_authorized",False),("purpose","report"),("max_requests",7),
    ("max_cost_micro_cny",6_000_000),("current_price_contract_authorized",False),("parent_price_addendum_authorized",False),
    ("preserve_parent_history",False),("question_item_id",""),("user_answer","")])
def test_incomplete_approval_never_registers_or_sends(tmp_path,field,value):
    h = make_live(tmp_path,approved=True)
    h.approval[field] = value
    h.approval_path.write_text(canonical(h.approval),encoding="utf-8")
    before = h.path.read_bytes()
    expect("case_qa_not_authorized",h.budget.register)
    assert h.path.read_bytes() == before and not h.calls and not h.credential_reads


def test_dynamic_case_history_approval_and_grant_bytes_are_bound(tmp_path):
    h = make_live(tmp_path,approved=True,register=True)
    h.approval["dynamic_scope"]["cases_sha256"] = "f"*64
    h.approval_path.write_text(canonical(h.approval),encoding="utf-8")
    expect("case_qa_not_authorized",lambda:claim(h))
    h.approval["dynamic_scope"] = dynamic_scope(h.config)
    h.approval["user_answer"] = "changed synthetic approval"
    h.approval_path.write_text(canonical(h.approval),encoding="utf-8")
    expect("qa_grant_changed",lambda:claim(h))
    assert not h.calls


def test_scope_payload_must_match_persisted_history(tmp_path):
    h = make_live(tmp_path,approved=True,register=True)
    value = h.value()
    value["turns"] = []
    request = h.request()
    packet = h.packet(value,request)
    packet["view"]["dialogue_background"].append({"turn_id":"b"*32,"user":"made up previous turn","assistant":"invented answer","source":"unverified_dialogue"})
    expect("qa_history_scope_changed",lambda:h.budget.reserve(value,request,packet))
    assert not h.calls


@pytest.mark.parametrize("failure",["timeout","usage","identity"])
def test_unknown_or_identity_change_stops_without_retry(tmp_path,failure):
    h = make_live(tmp_path,approved=True,register=True)
    if failure == "timeout":
        def handler(request):
            raise httpx.ReadTimeout("private diagnostic is not retained")
        h.handler = handler
    elif failure == "usage":
        h.body["usage"] = {}
    else:
        h.body["system_fingerprint"] = "different"
    ticket,packet = claim(h)
    result = h.budget.generate(ticket,packet)
    assert result["error_code"] and len(h.calls) == 1
    state = h.budget.usage(h.value())
    assert state["block_reason"] in {"ledger_unknown_paused","budget_integrity_stop"}
    assert state["real_deepseek_attempts"] == 1
    h.budget.generate(ticket,packet)
    assert len(h.calls) == 1
    expect(state["block_reason"],lambda:claim(h,case_index=1,session="2"*32))


def test_credential_failure_keeps_slot_but_reports_no_http_attempt(tmp_path):
    h = make_live(tmp_path,approved=True,register=True)
    def credential():
        raise ValueError("secret diagnostic")
    h.budget.credential = credential
    ticket,packet = claim(h)
    result = h.budget.generate(ticket,packet)
    assert result["audit"]["transport_attempts"] == 0 and not h.calls
    assert result["error_code"] == "qa_credentials_unavailable"
    usage = h.budget.usage(h.value())
    assert usage["real_deepseek_attempts"] == 0 and usage["unsettled_hold_cny"] == "2.105344"
    assert usage["block_reason"] == "ledger_unknown_paused"


def test_approved_price_date_or_file_drift_blocks_readonly(tmp_path):
    h = make_live(tmp_path,approved=True,register=True)
    pricing = Path(h.config["pricing_path"])
    pricing.write_text(pricing.read_text()+" ",encoding="utf-8")
    before = h.path.read_bytes()
    expect("qa_pricing_changed",h.budget.usage)
    assert h.path.read_bytes() == before and not h.calls


def test_unreviewed_transport_cannot_construct_live_or_fixture(tmp_path):
    h = make_live(tmp_path)
    before = h.path.read_bytes()
    expect("qa_transport_not_authorized",lambda:LiveCaseQABudget(h.path,h.contract,h.config_path,parent_protocol=h.parent,fixture=True))
    expect("qa_transport_not_authorized",lambda:LiveCaseQABudget(h.path,h.contract,h.config_path,parent_protocol=h.parent,transport=h.transport))
    assert h.path.read_bytes() == before and not h.calls


def test_register_is_idempotent_and_never_reads_credentials(tmp_path):
    h = make_live(tmp_path,approved=True,register=True)
    before = h.path.read_bytes()
    h.budget.register()
    assert h.path.read_bytes() == before and not h.credential_reads and not h.calls
    with sqlite3.connect(h.path) as db:
        assert db.execute("SELECT count(*) FROM case_qa_grants_v1 WHERE campaign_id=?",(h.config["campaign"],)).fetchone() == (1,)


def test_revoked_approval_does_not_claim_old_unresolved_requests_are_zero(tmp_path):
    h = make_live(tmp_path,approved=True,register=True)
    ticket,packet = claim(h)
    h.approval_path.unlink()
    before = h.path.read_bytes()
    state = h.budget.usage(h.value())
    assert state["block_reason"] == "case_qa_not_authorized"
    assert state["real_deepseek_attempts"] is None and state["unresolved_slots"] is None and state["batch_unresolved_slots"] is None
    assert state["unsettled_hold_cny"] is None and h.path.read_bytes() == before
    expect("case_qa_not_authorized",lambda:h.budget.generate(ticket,packet))
    assert not h.calls


def test_old_frozen_price_allows_readonly_constructor_but_never_send(tmp_path):
    h = make_live(tmp_path,approved=True)
    price_path = Path(h.config["pricing_path"])
    quote = json.loads(price_path.read_text(encoding="utf-8"))
    quote["verified_date"] = (datetime.now(ZoneInfo("Asia/Hong_Kong")).date()-timedelta(days=1)).isoformat()
    price_path.write_text(canonical(quote),encoding="utf-8")
    h.config["pricing_sha256"] = file_hash(price_path)
    h.config_path.write_text(canonical(h.config),encoding="utf-8")
    h.approval["config_sha256"] = file_hash(h.config_path)
    h.approval_path.write_text(canonical(h.approval),encoding="utf-8")
    before = h.path.read_bytes()
    budget = LiveCaseQABudget(h.path,h.contract,h.config_path,parent_protocol=h.parent,
        approval_path=h.approval_path,transport=h.transport,fixture=True)
    expect("qa_pricing_refresh_required",lambda:budget.usage(h.value()))
    expect("qa_pricing_refresh_required",budget.register)
    assert h.path.read_bytes() == before and not h.calls and not h.credential_reads
