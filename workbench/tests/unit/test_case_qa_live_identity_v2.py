"""Identity-successor regression checks; synthetic copies and MockTransport only."""
from copy import deepcopy
from pathlib import Path
import json
import sqlite3

import httpx
import pytest

from extensions.case_qa_live_v2.budget import IDENTITY_SQL, REVISION, LiveCaseQABudgetV2, _IdentityView
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_providers import cloud_usage
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
from test_case_qa_budget_v1 import FixtureParent, expect
from test_case_qa_live_budget_v1 import make_live, claim


class IdentityParent(FixtureParent):
    def __init__(self, rows, identities):
        super().__init__(rows)
        self.identities = identities

    def _partition(self,db):
        identities = db.execute("SELECT provider,identity FROM provider_identity ORDER BY provider").fetchall()
        assert identities == self.identities, "historical identity changed"
        return super()._partition(db)


def make_v2(tmp_path, *, approved=True, register=True, handler=None):
    h = make_live(tmp_path,approved=True,handler=handler)
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE provider_identity SET identity=? WHERE provider='deepseek'",(canonical(["deepseek-flash","old-fixture-identity"]),))
        identities = db.execute("SELECT provider,identity FROM provider_identity ORDER BY provider").fetchall()
        h.grants_before = db.execute("SELECT * FROM case_qa_grants_v1").fetchall()
    h.parent = IdentityParent(h.parent.rows,identities)
    h.config = deepcopy(h.config)
    h.config.update(campaign="case-qa-live-identity-test-v2",execution_revision=REVISION)
    root = Path(__file__).parents[2]
    for relative in ("extensions/case_qa_live_v2/budget.py","extensions/case_qa_live_v2/runtime.py"):
        path = (root/relative).resolve()
        h.config["files"][str(path)] = file_hash(path)
    h.config_path = tmp_path/"config-v2.json"
    h.config_path.write_text(canonical(h.config),encoding="utf-8")
    h.approval_path = tmp_path/"APPROVED-v2.json"
    h.approval.update(campaign=h.config["campaign"],config_sha256=file_hash(h.config_path))
    if approved:
        h.approval_path.write_text(canonical(h.approval),encoding="utf-8")
    h.budget = LiveCaseQABudgetV2(h.path,h.contract,h.config_path,parent_protocol=h.parent,
                               approval_path=h.approval_path,transport=h.transport,fixture=True)
    if register:
        h.budget.register()
    h.identities = identities
    return h


def identities(h):
    with sqlite3.connect(h.path.as_uri()+"?mode=ro",uri=True) as db:
        return db.execute("SELECT provider,identity FROM provider_identity ORDER BY provider").fetchall()


def receipt(h,result):
    path = Path(result["audit"]["receipt_path"])
    value = json.loads(path.read_text(encoding="utf-8"))
    usage = value["body"]["usage"]
    return {"cost_micro":result["audit"]["accounted_micro_cny"],"receipt_sha":file_hash(path),"receipt_path":str(path),
            "identity":canonical([value["body"]["model"],value["body"]["system_fingerprint"]]),
            "usage":{k:usage[k] for k in ("prompt_tokens","completion_tokens","total_tokens","prompt_cache_hit_tokens","prompt_cache_miss_tokens")},
            "result_code":"ok"}


def test_successor_identity_settles_without_altering_old_table_or_old_grants(tmp_path):
    h = make_v2(tmp_path)
    assert h.budget.usage(h.value())["block_reason"] is None
    ticket,packet = claim(h)
    result = h.budget.generate(ticket,packet)
    assert result["error_code"] is None and result["audit"]["state"] == "settled" and not result["audit"]["integrity_stop"]
    assert result["audit"]["cost_estimate_micro_cny"] == 280 and len(h.calls) == 1
    assert identities(h) == h.identities
    with sqlite3.connect(h.path.as_uri()+"?mode=ro",uri=True) as db:
        grants = db.execute("SELECT * FROM case_qa_grants_v1").fetchall()
        assert all(old in grants for old in h.grants_before)
    stored = json.loads(Path(result["audit"]["receipt_path"]).read_text(encoding="utf-8"))
    assert stored["execution_revision"] == REVISION
    h.budget.generate(ticket,packet)
    assert len(h.calls) == 1


def test_projection_is_exact_and_old_identity_history_is_still_verified(tmp_path):
    h = make_v2(tmp_path)
    with sqlite3.connect(h.path.as_uri()+"?mode=ro",uri=True) as db:
        view = _IdentityView(db,h.config["expected_identity"])
        assert view.execute("SELECT provider,identity FROM provider_identity ORDER BY provider").fetchall() == h.identities
        assert view.projected == 0
        assert view.execute(IDENTITY_SQL).fetchone() == (h.config["expected_identity"],)
        assert view.projected == 1
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE provider_identity SET identity='tampered' WHERE provider='deepseek'")
    before = h.path.read_bytes()
    with pytest.raises(AssertionError,match="historical identity changed"):
        h.budget.usage()
    assert h.path.read_bytes() == before and not h.calls


@pytest.mark.parametrize("failure",["identity","usage","timeout","context","output_usage_over_reservation"])
def test_new_violations_keep_receipt_or_hold_and_never_retry(tmp_path,failure):
    h = make_v2(tmp_path)
    if failure == "identity":
        h.body["system_fingerprint"] = "third-unapproved-identity"
    elif failure == "usage":
        h.body["usage"] = {}
    elif failure == "timeout":
        def failed(request):
            raise httpx.ReadTimeout("private text not emitted")
        h.handler = failed
    elif failure == "context":
        h.body["usage"].update(prompt_tokens=1048576,prompt_cache_miss_tokens=1048576,total_tokens=1048586)
    else:
        # Cache-miss 1M input plus 2048 output costs more than the 1024-output reservation.
        h.body["usage"].update(prompt_tokens=1048576,prompt_cache_miss_tokens=1048576,
                               completion_tokens=2048,total_tokens=1050624)
    ticket,packet = claim(h)
    result = h.budget.generate(ticket,packet)
    assert result["error_code"] and len(h.calls) == 1
    assert h.budget.usage(h.value())["block_reason"] in {"ledger_unknown_paused","budget_integrity_stop"}
    assert identities(h) == h.identities
    h.budget.generate(ticket,packet)
    assert len(h.calls) == 1


def test_old_attempt_cannot_be_settled_through_qa_scope(tmp_path):
    h = make_v2(tmp_path)
    before = h.path.read_bytes()
    expect("qa_settlement_scope_invalid",lambda:h.budget.settle("old-000000",cost_micro=0,receipt_sha="a"*64,
        receipt_path=str(tmp_path/"not-a-receipt"),identity=h.config["expected_identity"],usage={},result_code="ok"))
    assert h.path.read_bytes() == before and not h.calls


def test_repeated_settlement_and_different_cost_cannot_rewrite_result(tmp_path):
    h = make_v2(tmp_path)
    ticket,packet = claim(h)
    result = h.budget.generate(ticket,packet)
    details = receipt(h,result)
    before = h.path.read_bytes()
    expect("invalid_settlement_transition",lambda:h.budget.settle(ticket["attempt_id"],**details))
    details["cost_micro"] += 1
    expect("invalid_settlement_transition",lambda:h.budget.settle(ticket["attempt_id"],**details))
    assert h.path.read_bytes() == before


def test_receipt_tamper_blocks_readonly_and_never_changes_identity(tmp_path):
    h = make_v2(tmp_path)
    ticket,packet = claim(h)
    result = h.budget.generate(ticket,packet)
    path = Path(result["audit"]["receipt_path"])
    path.write_text(path.read_text()+" ",encoding="utf-8")
    before = h.path.read_bytes()
    expect("qa_receipt_changed",h.budget.usage)
    assert h.path.read_bytes() == before and identities(h) == h.identities


def test_revision_is_explicit_and_new_source_is_pinned(tmp_path):
    h = make_v2(tmp_path)
    h.config["execution_revision"] = "v1"
    h.config_path.write_text(canonical(h.config),encoding="utf-8")
    expect("qa_execution_revision_invalid",lambda:LiveCaseQABudgetV2(h.path,h.contract,h.config_path,
        parent_protocol=h.parent,approval_path=h.approval_path,transport=h.transport,fixture=True))


def pending_receipt(h):
    ticket,packet = claim(h)
    body = deepcopy(h.body)
    directory = h.config_path.parent/"receipts"
    directory.mkdir(exist_ok=True)
    path = directory/(ticket["attempt_id"]+".json")
    value = {"version":"case-qa-live-v1","execution_revision":REVISION,"fixture":True,"transport":"httpx.MockTransport",
             "attempt_id":ticket["attempt_id"],"payload_sha256":ticket["payload_sha256"],
             "profile_sha256":digest(h.config["profile"]),"body":body}
    path.write_text(canonical(value),encoding="utf-8")
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE case_qa_requests_v1 SET transport_started=1 WHERE attempt_id=?",(ticket["attempt_id"],))
    h.budget.attach_receipt(ticket["attempt_id"],file_hash(path),str(path))
    usage,cost = cloud_usage(body,h.config["profile"])
    details = {"cost_micro":cost,"receipt_sha":file_hash(path),"receipt_path":str(path),
               "identity":canonical([body["model"],body["system_fingerprint"]]),"usage":usage,"result_code":"ok"}
    return ticket,details


def test_v1_reproduces_successor_identity_pre_send_failure(tmp_path):
    h = make_live(tmp_path,approved=True,register=True)
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE provider_identity SET identity=? WHERE provider='deepseek'",(canonical(["deepseek-flash","old-fixture-identity"]),))
    before = h.path.read_bytes()
    assert h.budget.usage(h.value())["block_reason"] == "budget_integrity_stop"
    expect("budget_integrity_stop",lambda:claim(h))
    assert h.path.read_bytes() == before and not h.calls


@pytest.mark.parametrize("cause",["pricing_rollover","approval_revoked"])
def test_returned_receipt_is_accounted_after_send_permission_expires(tmp_path,cause):
    h = make_v2(tmp_path)
    ticket,details = pending_receipt(h)
    if cause == "approval_revoked":
        h.approval_path.unlink()
    else:
        def expired():
            raise ExecutionStop("qa_pricing_refresh_required")
        h.budget._authorization = expired
    h.budget.settle(ticket["attempt_id"],**details)
    with sqlite3.connect(h.path.as_uri()+"?mode=ro",uri=True) as db:
        assert db.execute("SELECT state FROM model_attempts WHERE attempt_id=?",(ticket["attempt_id"],)).fetchone() == ("settled",)
        assert db.execute("SELECT accounted,integrity_stop FROM execution_requests WHERE attempt_id=?",(ticket["attempt_id"],)).fetchone() == (280,0)
    assert identities(h) == h.identities and not h.calls
    if cause == "pricing_rollover":
        expect("qa_pricing_refresh_required",lambda:claim(h,case_index=1,session="2"*32))
    else:
        expect("case_qa_not_authorized",lambda:claim(h,case_index=1,session="2"*32))


@pytest.mark.parametrize("field",["receipt_sha","receipt_path","usage","cost_micro","identity"])
def test_scoped_settlement_rejects_mismatched_receipt_arguments(tmp_path,field):
    h = make_v2(tmp_path)
    ticket,details = pending_receipt(h)
    if field == "receipt_sha":
        details[field] = "a"*64
    elif field == "receipt_path":
        details[field] = str(tmp_path/"wrong-receipt.json")
    elif field == "usage":
        details[field] = dict(details[field],prompt_tokens=999)
    elif field == "cost_micro":
        details[field] = ticket["reserved_micro_cny"]+1
    else:
        details[field] = canonical(["deepseek-flash","third-identity"])
    before = h.path.read_bytes()
    expect("qa_receipt_changed" if field in {"receipt_sha","receipt_path"} else "qa_accounting_changed",
           lambda:h.budget.settle(ticket["attempt_id"],**details))
    assert h.path.read_bytes() == before and identities(h) == h.identities and not h.calls
