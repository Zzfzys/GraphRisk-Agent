from copy import deepcopy
from types import SimpleNamespace
import socket

import pytest

import evaluate_route8_offline as offline
from graphrisk.agent import judge_events_v13 as judge
from graphrisk.agent.upgrade_contract import canonical, digest


@pytest.fixture
def sources(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('offline control preparation must not connect or construct clients')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(offline.Provider, '__init__', blocked)
    monkeypatch.setattr(judge.Provider, '__init__', blocked)
    refs = {r: 'local-' + r for r in ('F1', 'F5', 'F8', 'F13', 'F15', 'F16', 'P1')}
    view = {'facts': [['F1', '交易金额：10', 'target_transaction', None, 'target_event', 'available']],
            'legend': {}, 'policies': [], 'gap_explanations': {'G1': '支付同意尚未核实'},
            'score_roles': {}, 'score_time_contract': 'day_end',
            'provider': 'NEVER_SEND_PROVIDER', 'isFraud': 'NEVER_SEND_LABEL',
            'gold': 'NEVER_SEND_GOLD', 'previous_score': 'NEVER_SEND_SCORE'}
    packet = {'view': view, 'refs_private': refs,
              'scope_private': {'transaction_id': 'NEVER_SEND_ID'}}
    return {cid: (deepcopy(packet), SimpleNamespace(facts=[SimpleNamespace(metric='transaction_amount', value=10)]),
                  {'rationale': {'text': '原稿不可被修改。', 'refs': ['F1']}}) for cid in ('H1', 'L2')}


def test_preparation_is_deterministic_blinded_and_does_not_mutate_sources(sources):
    before = repr(sources)
    groups, gold, private = offline.prepare_controls(sources)
    assert repr(sources) == before
    assert digest(offline.prepare_controls(sources)) == digest((groups, gold, private))
    assert len(groups) == 12 and len(gold) == 24
    assert sum(r['verdict'] == 'defect' for r in gold) == 12
    assert len({tuple(r['order']) for r in private}) == 2
    for group in groups:
        payload = canonical(group['payload'])
        assert all(marker not in payload for marker in
                   ('NEVER_SEND_', 'source_case', 'source_root', 'expected_local_error', 'clean', 'bad'))
        assert group['packet']['schema_version'] == judge.PROTOCOL


def test_protocol_control_rejects_all_pass_wrong_rule_and_pending(sources):
    groups, gold, _ = offline.prepare_controls(sources)
    all_pass = offline.replay_fixture(groups, gold, 'all_pass')['metrics']
    wrong = offline.replay_fixture(groups, gold, 'wrong_rule')['metrics']
    pending = offline.replay_fixture(groups, gold, 'pending')['metrics']
    perfect = offline.replay_fixture(groups, gold, 'perfect')['metrics']
    assert all_pass['fn'] == 12 and all_pass['tp'] == 0 and not all_pass['reference_eligible']
    assert wrong['tp'] == 0 and wrong['fn'] == 12 and not wrong['reference_eligible']
    assert pending['pending_or_missing'] == 24 and pending['coverage'] == 0 and not pending['reference_eligible']
    assert perfect['tp'] == perfect['tn'] == 12 and perfect['reference_eligible']
    assert offline.replay_fixture(groups, gold, 'perfect')['source'] == 'authored_protocol_fixture_not_real_model'


def test_existing_output_is_rejected_before_any_ledger_access(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('existing output must not read a source or ledger')
    monkeypatch.setattr(offline, 'read', blocked)
    with pytest.raises(ValueError, match='new_output_directory'):
        offline.main_run(tmp_path / 'missing', tmp_path)


def test_reference_and_claim_controls_remain_distinct(sources):
    groups, gold, private = offline.prepare_controls(sources)
    for row in gold:
        if row.get('kind') == 'reference':
            assert row['path'] == '/rationale/refs/0' and row['ref_id'] == 'F8'
            assert 'quote' not in row
        elif row['verdict'] == 'defect':
            assert row['kind'] == 'claim' and row['path'] == '/rationale/text' and row['quote']
