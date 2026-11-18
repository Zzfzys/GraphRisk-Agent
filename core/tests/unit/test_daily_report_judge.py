"""Current day-end fields, traceable scores and controlled whole-report degradation."""
from copy import deepcopy
import socket

import pytest
from test_autonomous_tool_contracts import scope, index, registry
from test_autonomous_action_providers import profile
from test_daily_narrative import daily_scope
from test_judgment_v12_daily_review import daily_review
from test_judgment_v3 import good
from graphrisk.agent import daily_report_judge as judge, judgment_v12_daily_quotes as daily
from graphrisk.agent.upgrade_contract import canonical, digest
import prepare_daily_judge_controls as controls


@pytest.fixture
def whole_sources(daily_review, monkeypatch):
    packet, bundle, original, p = daily_review
    p = {**p, 'max_prompt_utf8_bytes': 48_000}
    packet = daily.prepare_packet(original, bundle, p)
    def blocked(*args, **kwargs): raise AssertionError('offline mapping must not connect')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(judge.Provider, '__init__', blocked)
    sources = {key: (deepcopy(packet), bundle, good()) for key in ('H1', 'H2', 'M1', 'M2', 'L1', 'L2')}
    return sources, p


def verdict(item, event=None, *, scores=None, status='Pass'):
    reason = {'reason': '人工机制响应，不能当作模型真实能力。', 'anchors': ['/rationale/text', 'context']}
    return {'case': item['case'], 'facts': {**reason, 'status': 'Fail' if event else status},
        'defects': [event] if event else [],
        'scores': {d: {**reason, 'score': (scores or {}).get(d, 2)} for d in judge.DIMENSIONS},
        'risk_level_review': {**reason, 'status': status},
        'roles': [{'path': p, 'role': 'alternative', 'reason': reason['reason'], 'anchors': [p]} for p in item['role_paths']]}


def fixture_predictions(groups, gold, mode='perfect'):
    predictions = {}
    for group, row in zip(groups, gold):
        values = []
        for item in group['packet']['view']['reports']:
            is_control = group['id'] + '-' + item['case'] == row['control_id']
            event = None
            if mode == 'perfect' and is_control and row['kind'] == 'fact':
                event = {'path': row['path'], 'quote': row['quote'], 'rule_id': row['rule_id'],
                    'reason': '人工构造错误事件，仅检验协议。', 'anchors': [row['path'], 'context']}
            scores = {row['dimension']: 1} if mode == 'perfect' and is_control and row['kind'] == 'quality' else {}
            v = verdict(item, event, scores=scores, status='Pending' if mode == 'pending' else 'Pass')
            if scores: v['scores'][row['dimension']]['anchors'] = [row['path'], 'context']
            values.append(v)
        result = judge.parse(canonical({'verdicts': values}), group['packet'])
        predictions.update({group['id'] + '-' + v.case: v for v in result.verdicts})
    return predictions


def test_deterministic_single_leaf_full_reports_are_blinded(whole_sources):
    sources, profile = whole_sources
    before = digest({k: [p, d] for k, (p, _, d) in sources.items()})
    groups, gold, bindings = controls.prepare(sources, profile)
    assert len(groups) == len(gold) == len(bindings) == 8
    assert digest(controls.prepare(sources, profile)) == digest((groups, gold, bindings))
    assert digest({k: [p, d] for k, (p, _, d) in sources.items()}) == before
    assert len({tuple(b['order']) for b in bindings}) == 2
    for group in groups:
        text = canonical(group['payload'])
        assert all(term not in text for term in ('source_case', 'original_id', 'control_id', 'native_guard_issues', 'isFraud'))
        for item in group['packet']['view']['reports']:
            assert set(item['candidate']) == set(good())
            assert '/next_check/purpose' in item['anchors'] and '/next_check/decision_use' in item['anchors']
            assert 'rendered_check' not in item


def test_mechanism_rejects_all_pass_pending_missing_and_accepts_authored_fixture(whole_sources):
    groups, gold, _ = controls.prepare(*whole_sources)
    for mode in ('all_pass', 'pending', 'perfect'):
        result = judge.evaluate(gold, fixture_predictions(groups, gold, mode))
        assert result['numeric_development_gate'] == (mode == 'perfect')
        assert result['reference_eligible'] is False
    assert judge.evaluate(gold, {})['coverage'] == 0
    assert judge.evaluate(gold, {})['original_quality_pass'] == 0


@pytest.mark.parametrize('change', ['quote', 'pointer', 'anchor', 'order', 'roles', 'duplicate_key', 'fact_status'])
def test_full_review_rejects_unbound_or_incoherent_output(whole_sources, change):
    groups, gold, _ = controls.prepare(*whole_sources)
    packet, row = groups[2]['packet'], gold[2]
    values = [verdict(item) for item in packet['view']['reports']]
    idx = int(row['control_id'][-1]) - 1
    event = {'path': row['path'], 'quote': row['quote'], 'rule_id': row['rule_id'],
             'reason': '人工定位，待机制核验。', 'anchors': [row['path'], 'context']}
    values[idx] = verdict(packet['view']['reports'][idx], event)
    if change == 'quote': values[idx]['defects'][0]['quote'] = 'NOT_IN_REPORT'
    if change == 'pointer': values[idx]['defects'][0]['path'] = '/evidence/facts/0'
    if change == 'anchor': values[idx]['scores']['actionability']['anchors'] = ['F999']
    if change == 'order': values.reverse()
    if change == 'roles': values[idx]['roles'] = []
    if change == 'fact_status': values[idx]['facts']['status'] = 'Pass'
    text = canonical({'verdicts': values})
    if change == 'duplicate_key': text = text.replace('"case":"C1"', '"case":"C1","case":"C1"')
    with pytest.raises(ValueError): judge.parse(text, packet)


def test_confirmed_error_veto_is_separate_from_compatibility_and_scores(whole_sources):
    groups, gold, _ = controls.prepare(*whole_sources)
    values = fixture_predictions(groups, gold)
    v = values[gold[0]['original_id']]
    note = {'category': 'review_required', 'code': 'numeric_wording_review'}
    assert judge.assess(v, compatibility_notes=[note])['status'] == 'Pass'
    assert judge.assess(v, confirmed_errors=[{'code': 'confirmed_wrong_amount'}])['status'] == 'Fail'
    failed = v.model_copy(update={'risk_level_review': v.risk_level_review.model_copy(update={'status': 'Fail'})})
    assert judge.assess(failed)['status'] == 'Fail'
    known = {r['original_id']: {'confirmed_errors': [{'code': 'confirmed_wrong_amount'}]} for r in gold}
    assert judge.evaluate(gold, values, known_reviews=known)['original_quality_pass'] == 0


def test_context_and_packet_catalog_cannot_be_widened(whole_sources):
    groups, _, _ = controls.prepare(*whole_sources)
    packet = groups[0]['packet']
    with pytest.raises(Exception, match='context_limit'):
        judge.packet_for(packet['view']['reports'], {'max_prompt_utf8_bytes': 100})
    bad = deepcopy(packet['view']['reports']); bad[0]['gold'] = 'pass'
    with pytest.raises(Exception, match='exact_pair'): judge.packet_for(bad, whole_sources[1])
    bad = deepcopy(packet['view']['reports']); bad[0]['anchors'].append('F999')
    with pytest.raises(Exception, match='anchor_catalog'): judge.packet_for(bad, whole_sources[1])


def test_permission_overreach_and_execution_are_distinct_before_new_outputs():
    assert controls.SPECS[2][-1] == 'unperformed_check'
    assert controls.SPECS[3][-1] == 'permission_overreach'
    assert 'purpose和decision_use也是模型撰写' in judge.PROMPT
    # Import also verifies the new execution verifier refers to the old immutable parent helper.
    from graphrisk.agent import daily_judge_execution as execution
    assert execution.verify_parent_result.__module__ == 'graphrisk.agent.judge_control_execution'
