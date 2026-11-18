"""Synthetic predecessor ledgers only; never reads or writes the live ledger."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from graphrisk.agent import autonomous_session, full_case_execution
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_providers import cloud_usage, reservation_micro
from graphrisk.agent.judgment_v12_daily_quotes import Provider
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
from extensions.case_qa_v1.budget import CAPS, HISTORY_SQL, LIMITS
from extensions.case_qa_live_v1 import parent as live_parent
from extensions.case_qa_live_v1.parent import PinnedParent
import web_trial_protocol as original
import web_trial_recovery as recovery_module


def write(path, value):
    path.write_text(canonical(value), encoding="utf-8")
    return path


def make_parent(tmp_path, *, approved=False):
    """Small exact old-grant/unknown/H2 fixture; old pricing intentionally stale."""
    ledger = tmp_path / "synthetic.sqlite3"
    old_profile = {"max_output_tokens": 1024, "deepseek": {"endpoint": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-flash", "pricing_verified_date": "2000-01-01", "context_limit": 131072,
        "peak_cny_per_million": {"cache_hit": "0.04", "cache_miss": "2", "output": "8"}}}
    profile_path = write(tmp_path / "old-profile.json", old_profile)
    source = tmp_path / "source"
    for folder in ("src", "api", "scripts"):
        (source / folder).mkdir(parents=True)
    identity = canonical(["deepseek-flash", "fixture-fp"])
    route9 = {"campaign": "route9-fixture", "source_root": str(source), "source_inventory": {},
              "identities": {"deepseek": identity}, "episodes": [{"session_id": "pending-local", "provider": "local"}]}
    route9_path = write(tmp_path / "old-route9.json", route9)
    with sqlite3.connect(ledger) as db:
        for sql in (
            "CREATE TABLE model_attempts(attempt_id TEXT PRIMARY KEY,provider TEXT,state TEXT,reserved_micro_cny INTEGER)",
            "CREATE TABLE execution_requests(attempt_id TEXT PRIMARY KEY,request_key TEXT,phase TEXT,packet_sha TEXT,accounted INTEGER,receipt_sha TEXT,receipt_path TEXT,result_code TEXT,identity TEXT,usage_json TEXT,integrity_stop INTEGER DEFAULT 0)",
            "CREATE TABLE provider_identity(provider TEXT PRIMARY KEY,identity TEXT)",
            "CREATE TABLE autonomous_campaigns(campaign_id TEXT PRIMARY KEY,manifest_sha TEXT,cap_each INTEGER,group_key TEXT)",
            "CREATE TABLE autonomous_sessions(session_id TEXT PRIMARY KEY,campaign_id TEXT,manifest_sha TEXT,provider TEXT,max_calls INTEGER,deadline REAL,checkpoint_sha TEXT,checkpoint_path TEXT)",
            "CREATE TABLE autonomous_phases(phase TEXT PRIMARY KEY,session_id TEXT,slot INTEGER)",
            "CREATE TABLE execution_phases(phase TEXT PRIMARY KEY,manifest_sha TEXT,cloud_cap INTEGER,local_cap INTEGER)",
            "CREATE TABLE web_trial_grants(campaign_id TEXT PRIMARY KEY,config_path TEXT,config_sha TEXT,approval_path TEXT,approval_sha TEXT)",
            "CREATE TABLE web_trial_packets(phase TEXT PRIMARY KEY,wrapper_json TEXT)",
        ):
            db.execute(sql)
        db.execute("INSERT INTO provider_identity VALUES('deepseek',?)", (identity,))
        for index, provider in enumerate(("deepseek", "local")):
            db.execute("INSERT INTO model_attempts VALUES(?,?,'settled',0)", (f"old-{index}", provider))
            db.execute("INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,0)",
                       (f"old-{index}", f"old-{index}", "old", "a" * 64))
        baseline = db.execute(HISTORY_SQL).fetchall()
        identities = db.execute("SELECT provider,identity FROM provider_identity ORDER BY provider").fetchall()
    formatter = object.__new__(Provider)
    formatter.provider, formatter.profile = "deepseek", old_profile
    packets, cases = [], []
    for index in range(6):
        packet = {"logical_key": "0", "source_scope_sha256": digest(["scope", index]),
                  "scope_private": {"transaction_id": f"synthetic-{index}"}, "view": {"synthetic": index}}
        packets.append(packet)
        cases.append({"alias": f"W{index}", "transaction_id": f"synthetic-{index}", "scope_sha256": packet["source_scope_sha256"],
            "packet_sha256": digest(packet), "payload_sha256": digest(formatter.payload(packet)), "profile_sha256": digest(old_profile)})
    old_cfg = {"fixture": True, "version": original.VERSION, "campaign": "old-web", "group": original.GROUP,
        "ledger_path": str(ledger), "route9_execution_path": str(route9_path), "route9_execution_sha256": file_hash(route9_path),
        "source_root": str(source), "source_inventory": {},
        "baseline": {"count": 2, "provider_counts": {"deepseek": 1, "local": 1}, "rows_sha256": digest(baseline),
            "unknown_rows_sha256": digest([]), "integrity_rows_sha256": digest([]), "provider_identity_rows_sha256": digest(identities)},
        "files": {str(route9_path): file_hash(route9_path), str(profile_path): file_hash(profile_path)},
        "extension_files": {str(Path(original.__file__).resolve()): file_hash(Path(original.__file__))},
        "profile_path": str(profile_path), "profile_sha256": digest(old_profile), "expected_identity": identity,
        "cases": cases, "route9_pending_sessions": ["pending-local"],
        "caps": {"absolute": original.ABSOLUTE, "web_calls": 6, "web_cost_micro_cny": 5_000_000,
            "route9_remaining_calls": 5, "pause_micro_cny": 80_000_000, "total_cost_micro_cny": 100_000_000}}
    old_path = write(tmp_path / "old-config.json", old_cfg)
    old_approval = write(tmp_path / "old-approval.json", {"outbound_authorized": True, "config_sha256": file_hash(old_path),
        "max_web_requests": 6, "max_cost_micro_cny": 5_000_000, "route9_resume_protocol_authorized": True,
        "question_item_id": "fixture", "user_answer": "fixture"})
    with sqlite3.connect(ledger) as db:
        grant = ("old-web", str(old_path), file_hash(old_path), str(old_approval), file_hash(old_approval))
        db.execute("INSERT INTO web_trial_grants VALUES(?,?,?,?,?)", grant)
        db.execute("INSERT INTO autonomous_campaigns VALUES(?,?,6,?)", ("old-web", file_hash(old_path), original.GROUP))
        for index in range(2):
            wrapper = {"profile_sha256": digest(old_profile), "packets": [packets[index]], "session": f"web-{index}"}
            phase, attempt = f"loop-fixture-{index}", f"attempt-{index}"
            key = digest([digest(wrapper), "deepseek", "0"])
            reserve = reservation_micro(old_profile)
            state = "unknown" if index == 0 else "settled"
            db.execute("INSERT INTO autonomous_sessions VALUES(?,?,?,'deepseek',1,99999999999,NULL,NULL)", (f"web-{index}", "old-web", digest(wrapper)))
            db.execute("INSERT INTO autonomous_phases VALUES(?,?,0)", (phase, f"web-{index}"))
            db.execute("INSERT INTO execution_phases VALUES(?,?,1,0)", (phase, digest(wrapper)))
            db.execute("INSERT INTO web_trial_packets VALUES(?,?)", (phase, canonical(wrapper)))
            db.execute("INSERT INTO model_attempts VALUES(?,'deepseek',?,?)", (attempt, state, reserve))
            if index == 0:
                db.execute("INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted,result_code) VALUES(?,?,?,?,?,'provider_connection_unknown')",
                    (attempt, key, phase, cases[index]["payload_sha256"], reserve))
            else:
                body = {"model": "deepseek-flash", "system_fingerprint": "fixture-fp", "usage": {"prompt_tokens": 1,
                    "completion_tokens": 1, "total_tokens": 2, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1}}
                usage, cost = cloud_usage(body, old_profile)
                receipt = write(tmp_path / "H2.json", {"attempt_id": attempt, "payload_sha256": cases[index]["payload_sha256"],
                    "profile_sha256": digest(old_profile), "body": body})
                db.execute("INSERT INTO execution_requests VALUES(?,?,?,?,?,?,?,?,?,?,0)",
                    (attempt, key, phase, cases[index]["payload_sha256"], cost, file_hash(receipt), str(receipt), "ok", identity, canonical(usage)))
        unknown = db.execute(HISTORY_SQL).fetchall()[2]
    recovery_cfg = {"version": recovery_module.VERSION, "original_config_path": str(old_path), "original_config_sha256": file_hash(old_path),
        "original_approval_path": str(old_approval), "original_approval_sha256": file_hash(old_approval), "original_grant_sha256": digest(grant),
        "caps": {"web_calls": 6, "web_cost_micro_cny": 5_000_000, "remaining_web_calls": 5, "route9_remaining_calls": 5},
        "files": {str(Path(recovery_module.__file__).resolve()): file_hash(Path(recovery_module.__file__))},
        "unknown": {"attempt_id": unknown[0], "row": list(unknown), "row_sha256": digest(unknown), "phase": unknown[5],
            "request_key": unknown[4], "payload_sha256": unknown[6], "accounted_micro_cny": unknown[7], "campaign": "old-web",
            "case_alias": cases[0]["alias"], "packet_sha256": cases[0]["packet_sha256"], "profile_sha256": digest(old_profile)}}
    recovery_path = write(tmp_path / "old-recovery.json", recovery_cfg)
    recovery_approval = write(tmp_path / "old-recovery-approval.json", {"recovery_config_sha256": file_hash(recovery_path),
        "unknown_attempt_id": unknown[0], "exact_unknown_quarantine_authorized": True, "max_remaining_web_requests": 5,
        "preserve_unknown": True, "no_h1_retry": True, "question_item_id": "fixture", "user_answer": "fixture"})
    profile = deepcopy(old_profile)
    profile["deepseek"].update({"context_limit": 1048576, "documented_revision": "fixture-revision", "pricing_verified_date": live_parent.today()})
    price = {"version": "case-qa-official-pricing-v1", "verified_date": live_parent.today(), "timezone": "Asia/Hong_Kong",
        "source_url": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/", "model": "deepseek-flash",
        "documented_revision": "fixture-revision", "currency": "CNY", "unit": "per_million_tokens",
        "peak_cny_per_million": profile["deepseek"]["peak_cny_per_million"], "reservation_context_limit": 1048576,
        "max_output_tokens": 1024, "single_request_reservation_micro_cny": reservation_micro(profile)}
    pricing_path = write(tmp_path / "new-pricing.json", price)
    cfg = {"version": live_parent.VERSION, "purpose": "case_qa", "fixture": True, "campaign": "case-qa-synthetic",
        "caps": deepcopy(CAPS), "limits": deepcopy(LIMITS), "ledger_path": str(ledger), "profile": profile, "cases": [{"case_alias": "qa-1"}, {"case_alias": "qa-2"}],
        "pricing_path": str(pricing_path), "pricing_sha256": file_hash(pricing_path),
        "files": {str(Path(live_parent.__file__).resolve()): file_hash(Path(live_parent.__file__))}, "parent": {}}
    for key, path in {"config": old_path, "approval": old_approval, "recovery_config": recovery_path, "recovery_approval": recovery_approval}.items():
        cfg["parent"].update({key + "_path": str(path), key + "_sha256": file_hash(path)})
    config_path = write(tmp_path / "new-config.json", cfg)
    approval = {"version": live_parent.APPROVAL_VERSION, "outbound_authorized": True, "campaign": cfg["campaign"],
        "recipient": "DeepSeek official API", "purpose": "case_qa", "config_sha256": file_hash(config_path), "max_requests": 6,
        "max_cost_micro_cny": 5_000_000, "current_price_contract_authorized": True, "parent_price_addendum_authorized": True,
        "preserve_parent_history": True, "caps": cfg["caps"], "dynamic_scope": {"question_chars": 2000, "history_completed_pairs": 3,
            "context_bytes": 32768, "purpose": "case_qa", "cases_sha256": digest(cfg["cases"])}, "question_item_id": "fixture", "user_answer": "fixture"}
    approval_path = write(tmp_path / "new-approval.json", approval)
    parent = PinnedParent(config_path, approval_path if approved else None)
    return parent, ledger, approval_path


def test_preflight_missing_qa_approval_is_readonly_and_preserves_stale_parent(tmp_path):
    parent, ledger, _ = make_parent(tmp_path)
    before = ledger.read_bytes()
    hooks = (full_case_execution.guard, full_case_execution.history_rows, full_case_execution.identity_for_attempt, autonomous_session.guard_session_reservation)
    status = parent.preflight()
    assert status["status"] == "approval_required" and not status["case_qa_authorized"]
    assert status["global_calls"] == 4 and status["web_calls"] == 2 and status["web_remaining"] == 4
    assert status["route9_remaining_call_cap"] == 5 and status["quarantined_case_retry_allowed"] is False
    assert status["quarantined_hold_micro_cny"] == reservation_micro(original.read(parent.cfg["profile_path"]))
    assert original.read(parent.cfg["profile_path"])["deepseek"]["pricing_verified_date"] == "2000-01-01"
    assert ledger.read_bytes() == before
    assert hooks == (full_case_execution.guard, full_case_execution.history_rows, full_case_execution.identity_for_attempt, autonomous_session.guard_session_reservation)
    with pytest.raises(ExecutionStop, match="case_qa_approval_required"):
        parent.authorize()


def test_qa_specific_approval_can_adopt_new_current_pricing_without_old_profile_mutation(tmp_path):
    parent, ledger, _ = make_parent(tmp_path, approved=True)
    before = ledger.read_bytes()
    assert parent.authorize()["purpose"] == "case_qa"
    assert parent.preflight()["status"] == "authorized_case_qa"
    assert ledger.read_bytes() == before
    assert reservation_micro(parent.qa_config["profile"]) == 2105344


@pytest.mark.parametrize("method", ["install", "uninstall", "register_campaign", "register_packet", "guard_campaign"])
def test_new_parent_cannot_install_hooks_or_authorize_old_scope(tmp_path, method):
    parent, ledger, _ = make_parent(tmp_path, approved=True)
    before = ledger.read_bytes()
    with pytest.raises(ExecutionStop, match="qa_parent_readonly_old_purpose_forbidden"):
        getattr(parent, method)()
    assert ledger.read_bytes() == before


@pytest.mark.parametrize("field,bad", [("purpose", "report"), ("parent_price_addendum_authorized", False),
    ("current_price_contract_authorized", False), ("preserve_parent_history", False), ("recipient", "other"),
    ("max_requests", 7), ("max_cost_micro_cny", 5000001), ("config_sha256", "a" * 64), ("dynamic_scope", {}),
    ("question_item_id", ""), ("user_answer", ""), ("caps", {})])
def test_all_new_scope_authorization_constraints_are_required(tmp_path, field, bad):
    parent, _, approval_path = make_parent(tmp_path, approved=True)
    note = original.read(approval_path)
    note[field] = bad
    write(approval_path, note)
    with pytest.raises(ExecutionStop, match="case_qa_approval_required"):
        parent.authorize()


@pytest.mark.parametrize("tamper", ["unknown_hold", "unknown_state", "unknown_receipt", "grant", "wrapper", "h2_receipt", "baseline", "unclassified"])
def test_history_authentication_keeps_all_predecessor_checks(tmp_path, tamper):
    parent, ledger, _ = make_parent(tmp_path)
    if tamper == "h2_receipt":
        (tmp_path / "H2.json").write_text("{}", encoding="utf-8")
    else:
        with sqlite3.connect(ledger) as db:
            if tamper == "unknown_hold":
                db.execute("UPDATE execution_requests SET accounted=accounted+1 WHERE attempt_id='attempt-0'")
            elif tamper == "unknown_state":
                db.execute("UPDATE model_attempts SET state='settled' WHERE attempt_id='attempt-0'")
            elif tamper == "unknown_receipt":
                db.execute("UPDATE execution_requests SET receipt_path='forbidden' WHERE attempt_id='attempt-0'")
            elif tamper == "grant":
                db.execute("UPDATE web_trial_grants SET approval_sha='wrong'")
            elif tamper == "wrapper":
                db.execute("UPDATE web_trial_packets SET wrapper_json='{}' WHERE phase='loop-fixture-0'")
            elif tamper == "baseline":
                db.execute("UPDATE execution_requests SET accounted=1 WHERE attempt_id='old-0'")
            else:
                db.execute("INSERT INTO model_attempts VALUES('alien','deepseek','unknown',1)")
                db.execute("INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES('alien','alien','alien','alien',1)")
    with pytest.raises((ExecutionStop, KeyError)):
        parent.preflight()


def test_only_new_price_date_expires_without_rewriting_old_price(tmp_path, monkeypatch):
    parent, ledger, _ = make_parent(tmp_path, approved=True)
    before = ledger.read_bytes()
    monkeypatch.setattr(live_parent, "today", lambda: "2099-01-01")
    status = parent.preflight()
    assert status["status"] == "pricing_refresh_required" and status["price_current"] is False
    assert status["global_calls"] == 4 and ledger.read_bytes() == before
    with pytest.raises(ExecutionStop, match="qa_pricing_refresh_required"):
        parent.authorize()


@pytest.mark.parametrize("binding", ["config", "approval", "recovery_config", "recovery_approval"])
def test_parent_file_binding_changes_are_detected(tmp_path, binding):
    parent, _, _ = make_parent(tmp_path)
    path = Path(parent.qa_config["parent"][binding + "_path"])
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ExecutionStop):
        parent.preflight()
