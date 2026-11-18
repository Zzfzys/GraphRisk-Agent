"""Offline recovery checks retain the unknown and use only fixture ledgers."""
from pathlib import Path

import pytest

from graphrisk.agent import full_case_execution as formal
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import digest, file_hash
from test_web_trial_protocol import prepared, reserve, settle, stage, write
import web_trial_protocol as original
from web_trial_recovery import RecoveryTrialProtocol, VERSION
import web_trial_recovery


@pytest.fixture
def recovery(prepared):
    protocol, budget, store, profile, packets = prepared()
    campaign, phase = stage(protocol, budget, store, profile, packets[0])
    attempt = reserve(protocol, budget, campaign, phase, profile)
    budget.unknown(attempt, 'provider_connection_unknown')
    protocol.uninstall()
    with budget.transaction() as db:
        row = next(r for r in original._ORIGINAL_HISTORY(db) if r[0] == attempt)
        grant = db.execute('SELECT * FROM web_trial_grants WHERE campaign_id=?', (protocol.cfg['campaign'],)).fetchone()
    case = protocol.cfg['cases'][0]
    marker = Path(protocol.cfg['runtime']) / 'recovery-source-marker.txt'
    marker.write_text('frozen fixture', encoding='utf-8')
    cfg = {'version': VERSION, 'original_config_path': str(protocol.config_path), 'original_config_sha256': protocol.sha256,
           'original_approval_path': str(protocol.approval_path), 'original_approval_sha256': file_hash(protocol.approval_path),
           'original_grant_sha256': digest(grant),
           'unknown': {'attempt_id': attempt, 'row': list(row), 'row_sha256': digest(row), 'phase': phase,
                       'request_key': row[4], 'payload_sha256': row[6], 'accounted_micro_cny': row[7],
                       'campaign': protocol.cfg['campaign'], 'case_alias': case['alias'],
                       'profile_sha256': case['profile_sha256'], 'packet_sha256': case['packet_sha256']},
           'caps': {'web_calls': 6, 'web_cost_micro_cny': 5_000_000, 'remaining_web_calls': 5, 'route9_remaining_calls': 5},
           'files': {str(Path(web_trial_recovery.__file__).resolve()): file_hash(Path(web_trial_recovery.__file__)),
                     str(marker): file_hash(marker)}}
    config = Path(write(Path(protocol.cfg['runtime']) / 'recovery.json', cfg))
    approval = Path(write(Path(protocol.cfg['runtime']) / 'recovery-approval.json', {
        'recovery_config_sha256': file_hash(config), 'unknown_attempt_id': attempt,
        'exact_unknown_quarantine_authorized': True, 'max_remaining_web_requests': 5,
        'preserve_unknown': True, 'no_h1_retry': True, 'question_item_id': 'fixture-recovery-only', 'user_answer': 'fixture-only'}))
    def construct(approved=True):
        return RecoveryTrialProtocol(protocol.config_path, protocol.approval_path, recovery_config_path=config,
                                     recovery_approval_path=approval if approved else None)
    return construct, budget, store, profile, packets, attempt, marker


def test_pending_addendum_is_readonly_and_cannot_authorize_new_calls(recovery):
    construct, budget, store, _, _, _, _ = recovery
    before = budget.path.read_bytes()
    protocol = construct(False)
    status = protocol.status()
    assert status['status'] == 'paused_recovery_approval_pending'
    assert not status['authorized'] and status['web_calls'] == 1 and status['web_remaining'] == 5
    with pytest.raises(ExecutionStop, match='specific_approval_required'):
        protocol.register_campaign(store)
    assert before == budget.path.read_bytes()
    protocol.install()
    with pytest.raises(ExecutionStop, match='specific_approval_required'):
        budget.reserve(provider='local', request_key='future-r9', packet_sha='1' * 64,
                       phase='future-r9-phase', reserve_micro=0)
    assert before == budget.path.read_bytes()


def test_exact_unknown_counts_and_cost_remain_in_full_ledger_and_route9_can_check(recovery):
    construct, budget, store, profile, packets, unknown_id, _ = recovery
    protocol = construct().install()
    status = protocol.status()
    hold = status['quarantined_hold_micro_cny']
    assert status['global_calls'] == 1470 and status['web_accounted_micro_cny'] == hold
    with budget.transaction() as db:
        assert formal.check_history(db, protocol.route9) == []
    campaign, phase = stage(protocol, budget, store, profile, packets[1], session='second-case')
    attempt = reserve(protocol, budget, campaign, phase, profile)
    settle(protocol, budget, attempt, protocol.cfg['cases'][1]['payload_sha256'], profile)
    status = protocol.status()
    assert status['web_calls'] == 2 and status['web_accounted_micro_cny'] > hold
    assert budget.snapshot()['total_attempts'] == 1471 and budget.snapshot()['unresolved_attempts'] == 4
    with budget.transaction() as db:
        assert db.execute('SELECT state FROM model_attempts WHERE attempt_id=?', (unknown_id,)).fetchone() == ('unknown',)
        assert db.execute('SELECT accounted,receipt_path FROM execution_requests WHERE attempt_id=?', (unknown_id,)).fetchone() == (hold, None)
    with pytest.raises(ExecutionStop, match='case_already_requested'):
        stage(protocol, budget, store, profile, packets[0], session='never-retry-first-case')
    with pytest.raises(ExecutionStop, match='quarantined_attempt_must_remain_unknown'):
        budget.settle(unknown_id, cost_micro=0, receipt_sha='1' * 64, receipt_path='never-attach',
                      identity=protocol.cfg['expected_identity'], usage={}, result_code='ok')
    assert protocol.status()['quarantined_unknown_state'] == 'unknown'


def test_only_five_remaining_cases_can_run_and_unknown_stays_unknown(recovery):
    construct, budget, store, profile, packets, _, _ = recovery
    protocol = construct().install()
    for i in range(1, 6):
        campaign, phase = stage(protocol, budget, store, profile, packets[i], session='remaining-' + str(i))
        attempt = reserve(protocol, budget, campaign, phase, profile)
        settle(protocol, budget, attempt, protocol.cfg['cases'][i]['payload_sha256'], profile)
    assert protocol.status()['web_remaining'] == 0
    assert budget.snapshot()['total_attempts'] == 1475 and budget.snapshot()['unresolved_attempts'] == 4
    with pytest.raises(ExecutionStop, match='case_already_requested'):
        stage(protocol, budget, store, profile, packets[5], session='seventh-forbidden')


@pytest.mark.parametrize('change', ['row', 'state', 'receipt', 'grant', 'packet', 'source', 'other_unknown', 'other_integrity'])
def test_quarantine_is_exact_and_other_changes_remain_blocking(recovery, change):
    construct, budget, _, _, _, unknown_id, marker = recovery
    protocol = construct().install()
    if change == 'source':
        marker.write_text('changed', encoding='utf-8')
    else:
        with budget.transaction() as db:
            if change == 'row':
                db.execute('UPDATE execution_requests SET accounted=accounted+1 WHERE attempt_id=?', (unknown_id,))
            elif change == 'state':
                db.execute("UPDATE model_attempts SET state='settled' WHERE attempt_id=?", (unknown_id,))
            elif change == 'receipt':
                db.execute("UPDATE execution_requests SET receipt_path='unapproved' WHERE attempt_id=?", (unknown_id,))
            elif change == 'grant':
                db.execute("UPDATE web_trial_grants SET approval_sha='changed'")
            elif change == 'packet':
                db.execute("UPDATE web_trial_packets SET wrapper_json='{}'")
            else:
                db.execute("INSERT INTO model_attempts VALUES('second-alien','deepseek',?,1)", ('unknown' if change == 'other_unknown' else 'settled',))
                db.execute("INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted,integrity_stop) VALUES('second-alien','second-alien','alien','alien',1,?)", (int(change == 'other_integrity'),))
    with pytest.raises((ExecutionStop, KeyError)):
        protocol.status()
