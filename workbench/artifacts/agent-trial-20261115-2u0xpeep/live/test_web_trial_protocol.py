"""Small offline shared-ledger fixtures; never use the real execution ledger."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import sqlite3

import pytest

from graphrisk.agent import full_case_execution as formal
from graphrisk.agent.autonomous_budget import InvestigationBudget, ExecutionStop
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.autonomous_providers import cloud_usage, reservation_micro
from graphrisk.agent.judgment_v12_daily_quotes import Provider
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash, load_contract, register_budget
import web_trial_protocol as trial

SOURCE = Path(formal.__file__).resolve().parents[3]


def write(path, value):
    path.write_text(canonical(value), encoding='utf-8')
    return str(path)


@pytest.fixture
def prepared(tmp_path):
    def build(*, initial_cost=0):
        contract = load_contract(SOURCE / 'configs/agent_investigation_experiment.json')
        ledger = tmp_path / 'fixture.sqlite3'
        register_budget(ledger, contract)
        budget = InvestigationBudget(ledger, contract, mode='fixture')
        store = SessionStore(budget)
        with budget.transaction() as db:
            for i in range(1469):
                provider = 'deepseek' if i < 1446 else 'local'
                state = 'unknown' if i < 3 else 'settled'
                name = 'history-' + str(i)
                db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)', (name, provider, state, 1))
                db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted,integrity_stop) '
                           'VALUES(?,?,?,?,?,?)', (name, name, 'history', '1' * 64, initial_cost if i == 0 else 0, int(i == 3)))
            db.execute('INSERT INTO provider_identity VALUES(?,?)', ('deepseek', canonical(['deepseek-flash', 'old-fp'])))
            rows = trial._ORIGINAL_HISTORY(db)
            identities = db.execute('SELECT provider,identity FROM provider_identity ORDER BY provider').fetchall()
        source = tmp_path / 'source'
        for folder in ('src', 'api', 'scripts'):
            (source / folder).mkdir(parents=True)
        identity = canonical(['deepseek-flash', 'fixture-fp'])
        route9 = {'campaign': 'route9-fixture', 'source_root': str(source), 'source_inventory': {},
                  'identities': {'deepseek': identity}, 'history_sha256': digest(rows),
                  'episodes': [{'session_id': 'pending-local', 'provider': 'local'}]}
        route9_path = Path(write(tmp_path / 'r9.json', route9))
        profile = json.loads((SOURCE / 'configs/agent_investigation_providers.json').read_text(encoding='utf-8'))
        profile['deepseek']['pricing_verified_date'] = date.today().isoformat()
        profile_path = Path(write(tmp_path / 'profile.json', profile))
        packets, cases = [], []
        formatter = object.__new__(Provider)
        formatter.provider, formatter.profile = 'deepseek', profile
        for i in range(6):
            packet = {'logical_key': '0', 'source_scope_sha256': digest(['scope', i]),
                      'scope_private': {'transaction_id': 'fixture-' + str(i)}, 'view': {'fixture': i}}
            packets.append(packet)
            cases.append({'alias': 'W' + str(i + 1), 'transaction_id': 'fixture-' + str(i),
                          'scope_sha256': packet['source_scope_sha256'], 'packet_sha256': digest(packet),
                          'payload_sha256': digest(formatter.payload(packet)), 'profile_sha256': digest(profile)})
        cfg = {'fixture': True, 'version': trial.VERSION, 'campaign': 'web-fixture', 'group': trial.GROUP,
               'runtime': str(tmp_path), 'ledger_path': str(ledger), 'route9_execution_path': str(route9_path),
               'route9_execution_sha256': file_hash(route9_path), 'source_root': str(source), 'source_inventory': {},
               'baseline': {'count': len(rows), 'provider_counts': {'deepseek': 1446, 'local': 23},
                            'rows_sha256': digest(rows), 'unknown_rows_sha256': digest([r for r in rows if r[2] == 'unknown']),
                            'integrity_rows_sha256': digest([r for r in rows if r[-1]]),
                            'provider_identity_rows': identities, 'provider_identity_rows_sha256': digest(identities)},
               'files': {str(route9_path): file_hash(route9_path), str(profile_path): file_hash(profile_path)},
               'extension_files': {str(Path(trial.__file__).resolve()): file_hash(Path(trial.__file__))},
               'profile_path': str(profile_path), 'profile_sha256': digest(profile), 'expected_identity': identity,
               'cases': cases, 'route9_pending_sessions': ['pending-local'],
               'caps': {'web_calls': 6, 'web_cost_micro_cny': 5_000_000, 'route9_remaining_calls': 5,
                        'absolute': deepcopy(trial.ABSOLUTE), 'pause_micro_cny': 80_000_000, 'total_cost_micro_cny': 100_000_000}}
        config = Path(write(tmp_path / 'web.json', cfg))
        approval = Path(write(tmp_path / 'approval.json', {'outbound_authorized': True,
                        'config_sha256': file_hash(config), 'max_web_requests': 6, 'max_cost_micro_cny': 5_000_000,
                        'route9_resume_protocol_authorized': True, 'question_item_id': 'fixture-only', 'user_answer': 'fixture-only'}))
        protocol = trial.TrialProtocol(config, approval)
        return protocol, budget, store, profile, packets
    yield build
    if trial._ACTIVE:
        trial._ACTIVE.uninstall()


def stage(protocol, budget, store, profile, packet, *, session='web-session'):
    protocol.install()
    protocol.register_campaign(store)
    campaign = {'profile_sha256': digest(profile), 'packets': [packet], 'session': session}
    store.open(session, protocol.cfg['campaign'], {'fixture': session}, 'deepseek', calls=1)
    phase = store.register_packet(session, 0, digest(campaign), 'deepseek')
    protocol.register_packet(budget, phase, campaign, profile)
    protocol.guard_campaign(campaign, phase, profile)
    return campaign, phase


def reserve(protocol, budget, campaign, phase, profile):
    case = protocol._case(packet_sha=digest(campaign['packets'][0]))
    return budget.reserve(provider='deepseek', request_key=digest([digest(campaign), 'deepseek', '0']),
                          packet_sha=case['payload_sha256'], phase=phase, reserve_micro=reservation_micro(profile))


def settle(protocol, budget, attempt, payload_sha, profile):
    body = {'model': 'deepseek-flash', 'system_fingerprint': 'fixture-fp',
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2,
                      'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 1}}
    usage, cost = cloud_usage(body, profile)
    receipt = Path(protocol.cfg['runtime']) / (attempt + '.json')
    write(receipt, {'attempt_id': attempt, 'payload_sha256': payload_sha,
                    'profile_sha256': digest(profile), 'body': body})
    budget.settle(attempt, cost_micro=cost, receipt_sha=file_hash(receipt), receipt_path=str(receipt),
                  identity=protocol.cfg['expected_identity'], usage=usage, result_code='invalid_model_action')


def test_readonly_pending_and_missing_approval_do_not_register(prepared):
    protocol, budget, store, _, _ = prepared()
    before = budget.path.read_bytes()
    pending = trial.TrialProtocol(protocol.config_path)
    assert pending.status()['status'] == 'approval_pending'
    with pytest.raises(ExecutionStop, match='specific_approval_required'):
        pending.register_campaign(store)
    assert budget.path.read_bytes() == before


def test_shared_reserve_settle_partition_preserves_original_route9_history(prepared):
    protocol, budget, store, profile, packets = prepared()
    campaign, phase = stage(protocol, budget, store, profile, packets[0])
    attempt = reserve(protocol, budget, campaign, phase, profile)
    settle(protocol, budget, attempt, protocol.cfg['cases'][0]['payload_sha256'], profile)
    status = protocol.status()
    assert status['global_calls'] == 1470 and status['web_calls'] == 1
    assert status['global_provider_counts'] == {'deepseek': 1447, 'local': 23}
    with budget.transaction() as db:
        assert formal.check_history(db, protocol.route9) == []
        assert len(trial._ORIGINAL_HISTORY(db)) == 1470
        assert db.execute("SELECT identity FROM provider_identity WHERE provider='deepseek'").fetchone()[0] != protocol.cfg['expected_identity']
    with pytest.raises(ExecutionStop, match='case_already_requested'):
        stage(protocol, budget, store, profile, packets[0], session='new-session-same-case')
    assert budget.snapshot()['total_attempts'] == 1470


def test_six_distinct_cases_use_shared_cap_once_each(prepared):
    protocol, budget, store, profile, packets = prepared()
    for i, packet in enumerate(packets):
        campaign, phase = stage(protocol, budget, store, profile, packet, session='case-' + str(i))
        attempt = reserve(protocol, budget, campaign, phase, profile)
        settle(protocol, budget, attempt, protocol.cfg['cases'][i]['payload_sha256'], profile)
    assert protocol.status()['web_remaining'] == 0
    with pytest.raises(ExecutionStop, match='case_already_requested'):
        stage(protocol, budget, store, profile, packets[0], session='seventh')
    assert budget.snapshot()['total_attempts'] == 1475


@pytest.mark.parametrize('change', ['unknown', 'unclassified', 'identity', 'source', 'packet', 'receipt'])
def test_unverified_rows_or_tampering_stop_before_further_reservation(prepared, change):
    protocol, budget, store, profile, packets = prepared()
    campaign, phase = stage(protocol, budget, store, profile, packets[0])
    if change == 'packet':
        broken = deepcopy(campaign)
        broken['packets'][0]['view']['fixture'] = 999
        with pytest.raises(ExecutionStop):
            protocol.guard_campaign(broken, phase, profile)
        return
    if change == 'source':
        Path(protocol.cfg['source_root'], 'src/new.py').write_text('changed', encoding='utf-8')
    elif change == 'receipt':
        attempt = reserve(protocol, budget, campaign, phase, profile)
        settle(protocol, budget, attempt, protocol.cfg['cases'][0]['payload_sha256'], profile)
        Path(protocol.cfg['runtime'], attempt + '.json').write_text('{}', encoding='utf-8')
    else:
        with budget.transaction() as db:
            if change == 'identity':
                db.execute("UPDATE provider_identity SET identity='changed'")
            else:
                state = 'unknown' if change == 'unknown' else 'settled'
                db.execute("INSERT INTO model_attempts VALUES('unapproved','deepseek',?,1)", (state,))
                db.execute("INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES('unapproved','unapproved','alien','alien',1)")
    with pytest.raises(ExecutionStop):
        reserve(protocol, budget, campaign, phase, profile)


def test_pending_request_blocks_both_purposes_without_erasing_unknown(prepared):
    protocol, budget, store, profile, packets = prepared()
    campaign, phase = stage(protocol, budget, store, profile, packets[0])
    attempt = reserve(protocol, budget, campaign, phase, profile)
    budget.unknown(attempt, 'fixture_timeout')
    with pytest.raises(ExecutionStop, match='new_unresolved_attempt'):
        protocol.status()
    with budget.transaction() as db:
        with pytest.raises(ExecutionStop, match='new_unresolved_attempt'):
            formal.check_history(db, protocol.route9)
    assert budget.snapshot()['unresolved_attempts'] == 4


def test_shared_global_money_pause_is_not_bypassed_by_new_grant(prepared):
    protocol, budget, store, profile, packets = prepared(initial_cost=79_999_999)
    campaign, phase = stage(protocol, budget, store, profile, packets[0])
    with pytest.raises(ExecutionStop, match='pause_for_budget_reconciliation'):
        reserve(protocol, budget, campaign, phase, profile)
    assert budget.snapshot()['total_attempts'] == 1469
