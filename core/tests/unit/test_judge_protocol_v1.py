"""Small offline contract tests: typed localization, path grammar and scoring."""
from copy import deepcopy
import json

import pytest

from graphrisk.agent import judge_events_v13 as judge
from graphrisk.agent.upgrade_contract import canonical, digest

PROTOCOL = 'localized-judge-v13-events-v1'
RULE = 'unsupported_citation'
QUOTE = '历史波动缺失'


@pytest.fixture
def packet():
    candidate = {'rationale': {'text': QUOTE + '，不能判断金额异常。', 'refs': ['F5']},
                 'basis': [{'text': QUOTE + '。', 'refs': ['F5']}],
                 'policy': {'text': '政策前提尚未测量。', 'refs': ['P1']}}
    return {'schema_version': PROTOCOL, 'view': {'reports': [
        {'case': 'C1', 'candidate': candidate},
        {'case': 'C2', 'candidate': {'rationale': {'text': '建议人工核查授权。', 'refs': ['F1']}}}]}}


def verdict(case, events):
    dim = {'score': 1, 'reason': '仅为离线协议测试。'}
    return {'case': case, 'verdict': 'defect' if events else 'pass', 'defects': events,
            'reason': '仅为离线构造的定位样例。',
            **{key: dim for key in ('specificity', 'analysis', 'actionability', 'readability')}}


def content(event):
    return canonical({'verdicts': [verdict('C1', [event]), verdict('C2', [])]})


def claim(path='rationale.text', quote=QUOTE, rule=RULE):
    return {'kind': 'claim', 'path': path, 'quote': quote, 'rule_id': rule}


def reference(path='rationale.refs[0]', ref='F5'):
    return {'kind': 'reference', 'path': path, 'ref_id': ref, 'rule_id': RULE}


@pytest.mark.parametrize('path,expected', [
    ('rationale.text', '/rationale/text'),
    ('candidate.rationale.text', '/rationale/text'),
    ('/rationale/text', '/rationale/text'),
    ('reports[0].candidate.rationale.text', '/rationale/text'),
    ('reports.0.candidate.rationale.text', '/rationale/text'),
    ('/reports/0/candidate/rationale/text', '/rationale/text'),
    ('basis[0].text', '/basis/0/text'),
    ('basis.0.text', '/basis/0/text'),
])
def test_claim_paths_normalize_without_changing_quote_or_input(packet, path, expected):
    before = digest(packet)
    raw = content(claim(path))
    event = judge.parse(raw, packet).verdicts[0].defects[0]
    assert event.kind == 'claim' and event.path == expected and event.quote == QUOTE
    assert digest(packet) == before
    assert path in raw


@pytest.mark.parametrize('path', [
    'rationale.refs.0', 'rationale.refs[0]', '/rationale/refs/0',
    'reports[0].candidate.rationale.refs[0]', '/reports/0/candidate/rationale/refs/0',
])
def test_short_reference_ids_use_the_reference_event(packet, path):
    event = judge.parse(content(reference(path)), packet).verdicts[0].defects[0]
    assert event.kind == 'reference' and event.ref_id == 'F5'
    assert event.path == '/rationale/refs/0'


def test_policy_reference_and_short_claim_have_distinct_types(packet):
    event = judge.parse(content(reference('policy.refs[0]', 'P1')), packet).verdicts[0].defects[0]
    assert event.ref_id == 'P1'
    event = judge.parse(content(claim(quote='波')), packet).verdicts[0].defects[0]
    assert event.quote == '波'  # Structurally localized, not evidence of semantic accuracy.


@pytest.mark.parametrize('path', [
    'reports[1].candidate.rationale.text', '/reports/1/candidate/rationale/text',
    'basis[-1].text', 'basis[01].text', 'basis[99].text',
    "rationale['text']", 'rationale..text', 'basis[0].__class__',
    '/rationale/~1text', '/rationale/text/', '/rationale//text',
])
def test_unsafe_ambiguous_missing_or_other_case_paths_are_rejected(packet, path):
    with pytest.raises(ValueError):
        judge.parse(content(claim(path)), packet)


@pytest.mark.parametrize('event', [
    claim('rationale.refs[0]', 'F5'),
    reference('rationale.text'),
    claim(quote='原文不存在的句子'),
    reference(ref='F6'),
    claim(quote=''),
    reference(ref='F0'),
    reference(ref='G1'),
    {**claim(), 'ref_id': 'F5'},
    {**reference(), 'quote': QUOTE},
    {**claim(), 'kind': 'invented'},
])
def test_kind_quote_reference_and_field_shape_cannot_be_mixed(packet, event):
    with pytest.raises(ValueError):
        judge.parse(content(event), packet)


def test_case_order_and_invalid_group_are_not_silently_salvaged(packet):
    raw = {'verdicts': [verdict('C2', []), verdict('C1', [claim()])]}
    with pytest.raises(ValueError):
        judge.parse(canonical(raw), packet)
    with pytest.raises(ValueError):
        judge.parse(content(claim(quote='伪造引句')), packet)
    result = judge.calibration(gold(), {})
    assert result['coverage'] == 0 and result['pending_or_missing'] == 2
    assert not result['reference_eligible']


def gold():
    return [{'id': 'bad', 'verdict': 'defect', **claim('/rationale/text')},
            {'id': 'clean', 'verdict': 'pass'}]


def predictions(event):
    return {'bad': {'verdict': 'defect', 'defects': [event]},
            'clean': {'verdict': 'pass', 'defects': []}}


def test_strict_match_requires_the_rule_not_just_the_right_location():
    wrong = predictions(claim('/rationale/text', rule='unperformed_check'))
    result = judge.calibration(gold(), wrong)
    assert result['tp'] == 0 and result['fn'] == 1
    assert result['coverage'] == 1 and result['kappa'] == 1
    assert result['sensitivity_requires_correct_rule'] is True
    assert result['metrics_version'] == 'localized-judge-metrics-v1'
    assert not result['reference_eligible']


def test_short_quote_does_not_match_an_entire_gold_claim():
    result = judge.calibration(gold(), predictions(claim('/rationale/text', quote='波')))
    assert result['tp'] == 0 and result['fn'] == 1
    assert not result['reference_eligible']


def test_quote_containing_the_gold_anchor_is_accepted_but_wrong_path_is_not():
    result = judge.calibration(gold(), predictions(claim('rationale.text', quote=QUOTE + '，不能判断金额异常。')))
    assert result['tp'] == 1 and result['reference_eligible']
    result = judge.calibration(gold(), predictions(claim('basis[0].text')))
    assert result['tp'] == 0 and result['fn'] == 1


def test_reference_match_requires_exact_id_and_path():
    reference_gold = [{'id': 'bad', 'verdict': 'defect', **reference('rationale.refs.0')},
                      {'id': 'clean', 'verdict': 'pass'}]
    result = judge.calibration(reference_gold, predictions(reference('/rationale/refs/0')))
    assert result['tp'] == 1 and result['reference_eligible']
    result = judge.calibration(reference_gold, predictions(reference('/rationale/refs/0', 'F6')))
    assert result['tp'] == 0 and not result['reference_eligible']


@pytest.mark.parametrize('invalid', [
    None, {}, {'verdict': 'pending', 'defects': []},
    {'verdict': 'invalid', 'defects': []},
    {'verdict': 'pass', 'defects': [claim()]},
    {'verdict': 'defect', 'defects': []},
    {'verdict': 'defect', 'defects': [{**claim(), 'rule_id': 'invented'}]},
    {'verdict': 'defect', 'defects': [claim('rationale.refs.0', 'F5')]},
])
def test_missing_pending_and_invalid_predictions_stay_in_the_denominator(invalid):
    result = judge.calibration(gold(), {'bad': invalid, 'clean': {'verdict': 'pass', 'defects': []}})
    assert result['coverage'] == 0.5 and result['pending_or_missing'] == 1
    assert result['tp'] == 0 and result['fn'] == 1 and result['tn'] == 1
    assert result['kappa'] is None and not result['reference_eligible']


def test_factory_and_provider_expose_the_versioned_typed_schema(packet):
    reports = packet['view']['reports']
    generated = judge.packet_for(reports, {'max_prompt_utf8_bytes': 20000})
    assert generated['schema_version'] == PROTOCOL
    assert generated['view']['event_contract_version'] == PROTOCOL
    schema = generated['view']['response_schema']
    assert 'ClaimDefect' in schema['$defs'] and 'ReferenceDefect' in schema['$defs']
    client = judge.Provider.__new__(judge.Provider)
    parsed = client.check_output(content(reference()), generated)
    assert parsed.verdicts[0].defects[0].ref_id == 'F5'
    assert client.output_schema(generated) == schema
    assert '未校准' in client.system_prompt(generated)
    assert 'unperformed_check' in client.system_prompt(generated)
    with pytest.raises(ValueError):
        judge.packet_for(reports, {'max_prompt_utf8_bytes': 100})


def test_new_provider_refuses_legacy_packet_but_read_only_legacy_decoder_survives(packet):
    client = judge.Provider.__new__(judge.Provider)
    legacy = deepcopy(packet); legacy['schema_version'] = 'localized-judge-v13'
    with pytest.raises(ValueError):
        client.system_prompt(legacy)
    raw = {'path': 'reports[0].candidate.rationale.text', 'span_text': QUOTE, 'rule_id': RULE}
    decoded = judge.parse(content(raw), legacy)
    assert decoded.verdicts[0].defects[0].path == 'rationale.text'


def test_metrics_do_not_drop_unknown_ids_or_malformed_gold():
    with pytest.raises(ValueError):
        judge.calibration(gold(), {'foreign': {'verdict': 'pass', 'defects': []}})
    malformed = gold(); malformed[0]['rule_id'] = 'invented'
    with pytest.raises(ValueError):
        judge.calibration(malformed, {})


@pytest.mark.parametrize('valid', [True, False])
def test_mock_http_preserves_raw_event_and_replays_without_another_request(packet, tmp_path, valid):
    from test_autonomous_action_providers import profile as profile_fixture, ledger, transport_for, cloud_body
    profile = {**profile_fixture.__wrapped__(), 'max_prompt_utf8_bytes': 20000}
    generated = judge.packet_for(packet['view']['reports'], profile)
    campaign = {'profile_sha256': digest(profile), 'packets': [generated]}
    budget = ledger(tmp_path, campaign)
    posts = []
    raw = content(reference() if valid else claim(quote='并非原文的引句'))
    client = judge.Provider('deepseek', profile=profile, budget=budget, campaign=campaign,
                            campaign_sha=digest(campaign), phase='pilot', output=tmp_path/'receipts',
                            runtime=tmp_path, transport=transport_for(profile, posts,
                            mutate=lambda _: cloud_body(json.loads(raw))))
    first = client.generate('0')
    assert first['code'] == ('ok' if valid else 'invalid_model_action')
    if valid:
        assert first['action']['verdicts'][0]['defects'][0]['path'] == '/rationale/refs/0'
    saved = json.loads(next((tmp_path/'receipts').glob('*.json')).read_text(encoding='utf-8'))
    assert saved['body']['choices'][0]['message']['content'] == raw
    replay = client.generate('0')
    assert replay['replayed_without_model_request'] and replay['code'] == first['code']
    assert len(posts) == 1 and budget.snapshot()['total_attempts'] == 1
