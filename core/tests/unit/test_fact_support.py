"""Positive/negative witnesses for the experimental fact support contract."""
from copy import deepcopy

import pytest

from test_judgment_v13 import candidate
from test_judgment_v3 import evidence
from test_autonomous_tool_contracts import index, registry, scope
from test_autonomous_action_providers import profile
from graphrisk.agent import judgment_v13 as v13
from graphrisk.agent.upgrade_contract import digest
from test_autonomous_tool_contracts import executor_for, run_core
from graphrisk.agent.autonomous_evidence import build_evidence


def check(candidate, text, refs, field='rationale'):
    value, packet, bundle = candidate
    value = deepcopy(value)
    item = {'text': text, 'refs': refs}
    value[field] = [item] if field in ('basis', 'hypotheses', 'counterevidence') else item
    original = deepcopy(value)
    issues, report = v13.verify(v13.parse(value, packet), packet, bundle)
    assert original == value
    return issues, report


@pytest.mark.parametrize('text,refs', [
    ('目标交易金额17.0，尚需人工核查授权。', ['F1']),
    ('目标交易金额17.0已记录，尚需人工核查授权。', ['F1']),
    ('本笔交易金额为17，尚需人工核查授权。', ['F1']),
    ('此前24小时匿名匹配历史交易数为17，不能据此确认授权。', ['F3']),
    ('此前1小时按匿名身份规则匹配的历史交易数为17。', ['F2']),
    ('此前7天匿名匹配历史交易数为17。', ['F4']),
    ('主评分为0.8，是未校准信号，不是欺诈概率。', ['F8']),
    ('图分数为0.2，是未校准的实验信号，不是欺诈概率。', ['F16']),
    ('已返回样本中共享设备字段的交易数为2，不证明同人。', ['F13']),
    ('历史均值、波动及金额与历史均值之差均未取得。', ['F5', 'F6', 'F7']),
    ('金额与历史均值之差缺失，当前无法评估金额偏离。', ['F7']),
    ('历史均值缺失，不能说明交易安全。', ['F5']),
])
def test_supported_statements_can_pass_without_rewriting_model_prose(candidate, text, refs):
    issues, report = check(candidate, text, refs)
    assert issues == []
    assert report['draft']['rationale']['text'] == text
    assert report['fact_support']['contract'] == 'fact-support-v1'
    assert report['fact_support']['scope'] == 'declared_claim_families_only'
    assert report['semantic_quality'] == 'requires_review'
    assert report['approval_eligible'] is False


@pytest.mark.parametrize('text,refs,code', [
    ('目标交易金额18.0，尚需核查授权。', ['F1'], 'fact_claim_value_mismatch'),
    ('目标交易金额17.0美元，尚需核查授权。', ['F1'], 'fact_claim_currency_unverified'),
    ('目标交易金额17.0，尚需核查授权。', ['F8'], 'fact_claim_citation_required'),
    ('此前24小时匿名匹配历史交易数为18。', ['F3'], 'fact_claim_value_mismatch'),
    ('此前24小时匿名匹配历史交易数为17。', ['F2'], 'fact_claim_citation_required'),
    ('历史均值、波动及金额与历史均值之差均未取得。', ['F5', 'F7'], 'fact_claim_citation_required'),
    ('历史均值已取得，可用于比较。', ['F5'], 'fact_claim_availability_mismatch'),
    ('此前24小时历史均值为0。', ['F5'], 'fact_claim_unavailable'),
    ('此前24小时历史金额波动为0。', ['F6'], 'fact_claim_unavailable'),
    ('此前24小时金额与历史均值之差为0。', ['F7'], 'fact_claim_unavailable'),
    ('主评分为0.7，是未校准信号，不是欺诈概率。', ['F8'], 'fact_claim_value_mismatch'),
    ('主评分为0.8。', ['F8'], 'score_uncalibrated_context_required'),
    ('本笔欺诈概率为0.8。', ['F8'], 'fact_claim_probability_unsupported'),
    ('已返回样本中共享设备字段的交易数为3。', ['F13'], 'fact_claim_value_mismatch'),
    ('已返回样本中共享设备字段的交易数为2。', ['F14'], 'fact_claim_citation_required'),
    ('持卡人个人历史存在匹配记录。', ['F3'], 'heuristic_identity_claim_requires_review'),
    ('历史均值缺失，说明交易安全。', ['F5'], 'missing_evidence_cannot_establish_safety'),
])
def test_wrong_or_unsupported_statements_cannot_be_released(candidate, text, refs, code):
    issues, report = check(candidate, text, refs)
    assert code in {row['code'] for row in issues}
    assert report is None


@pytest.mark.parametrize('text', [
    '目标交易金额17.0，另有18笔待核查。',
    '目标交易金额17.0e0，尚需核查。',
    '此前48小时匿名匹配历史交易数为17。',
])
def test_unsupported_numeric_remainder_requires_review_instead_of_being_masked(candidate, text):
    issues, report = check(candidate, text, ['F1', 'F3'])
    assert 'fact_support_review_required' in {row['code'] for row in issues}
    assert report is None


def test_correct_repeated_numbers_are_warnings_not_hard_errors(candidate):
    issues, report = check(candidate, '目标交易金额17.0已记录，尚需核查授权。', ['F1'])
    assert issues == []
    assert any(row['code'] == 'verified_fact_repeated' for row in report['fact_support']['warnings'])
    witness = report['fact_support']['witnesses'][0]
    assert witness['metric'] == 'transaction_amount'
    assert witness['subject'] == 'target_transaction'
    assert witness['unit'] == 'dataset_amount_unit'
    assert witness['window_seconds'] is None
    assert witness['query_ref'] and witness['source_version']


@pytest.mark.parametrize('field', ['rationale', 'basis', 'hypotheses', 'counterevidence', 'policy'])
def test_checks_apply_to_every_model_owned_cited_field(candidate, field):
    issues, report = check(candidate, '目标交易金额18.0，尚需核查授权。', ['F1'], field)
    assert 'fact_claim_value_mismatch' in {row['code'] for row in issues}
    assert report is None


def test_verified_observation_in_hypotheses_still_requires_role_review(candidate):
    issues, report = check(candidate, '已返回样本中共享设备字段的交易数为2。', ['F13'], 'hypotheses')
    assert 'measured_claim_in_hypothesis_requires_review' in {row['code'] for row in issues}
    assert report is None


@pytest.mark.parametrize('changed', [
    {'value': 19.0}, {'subject': 'verified_customer'}, {'unit': 'USD'},
    {'window_seconds': 3600}, {'temporal_scope': 'strictly_before_target'},
    {'source_version': 'f' * 64}, {'model_version': 'invented'}, {'query_ref': 'f' * 64},
])
def test_witness_provenance_cannot_be_replaced_even_if_source_hash_is_resealed(candidate, changed):
    value, packet, bundle = candidate
    fact = next(f for f in bundle.facts if f.metric == 'transaction_amount')
    altered = fact.model_copy(update=changed)
    bundle = bundle.model_copy(update={'facts': tuple(altered if f is fact else f for f in bundle.facts)})
    packet = deepcopy(packet)
    # Changing only the value and re-sealing the bundle does not recreate trusted
    # tool observations. Source ID binding is checked against the packet witness.
    packet['source_evidence_sha256'] = digest(bundle.model_dump(mode='json'))
    value = deepcopy(value)
    value['rationale'] = {'text': '目标交易金额17.0，尚需核查授权。', 'refs': ['F1']}
    issues = v13.issues_for(value, packet, bundle)
    assert 'fact_support_source_invalid' in {row['code'] for row in issues}


def test_stale_bundle_or_mutated_contract_fails_closed(candidate):
    value, packet, bundle = candidate
    changed = deepcopy(packet)
    changed['source_evidence_sha256'] = 'f' * 64
    assert 'fact_support_source_invalid' in {row['code'] for row in v13.issues_for(value, changed, bundle)}
    changed = deepcopy(packet)
    changed['view']['fact_support_contract']['version'] = 'invented'
    with pytest.raises(ValueError, match='fact_support_contract_changed'):
        v13.issues_for(value, changed, bundle)


def test_aliases_cannot_borrow_same_numeric_value_from_wrong_window(candidate):
    value, packet, bundle = candidate
    packet = deepcopy(packet)
    packet['refs_private']['F3'] = packet['refs_private']['F2']
    value = deepcopy(value)
    value['rationale'] = {'text': '此前24小时匿名匹配历史交易数为17。', 'refs': ['F3']}
    issues = v13.issues_for(value, packet, bundle)
    assert 'fact_support_source_invalid' in {row['code'] for row in issues}


def test_new_packet_exposes_versioned_contract_and_refuses_old_frozen_rules(candidate):
    value, packet, bundle = candidate
    assert packet['view']['fact_support_contract']['version'] == 'fact-support-v1'
    assert packet['view']['fact_support_contract']['witnesses']
    assert packet['judgment_rules_version'].endswith('+fact-support-v1')
    packet = deepcopy(packet)
    packet['judgment_rules_version'] = 'report-quality-generation-v13-namespaced-refs+bounded-risk'
    with pytest.raises(ValueError, match='judgment_v13_rules_changed'):
        v13.issues_for(value, packet, bundle)


@pytest.mark.parametrize('suffix', ['USD', '万元', '万', '千', '倍'])
def test_numeric_suffix_cannot_change_the_verified_value_or_unit(candidate, suffix):
    issues, report = check(candidate, '目标交易金额17.0' + suffix + '，尚需核查授权。', ['F1'])
    assert report is None
    assert {row['code'] for row in issues} & {
        'fact_claim_currency_unverified', 'fact_support_review_required'}


@pytest.fixture
def available_stats(scope, index, registry, profile):
    def mutate(name, response):
        if name == 'get_transaction_features':
            response['data'].update(amount_mean_last_24h=20.0, amount_std_last_24h=2.0,
                                    amount_deviation_last_24h=-3.0)
        return response
    executor, _ = executor_for(scope, index, mutate=mutate)
    run_core(executor)
    bundle = build_evidence(scope, executor.observations, registry)
    packet = v13.packet_for(scope, executor.observations, registry,
                           {**profile, 'max_prompt_utf8_bytes': 20000})
    from test_judgment_v3 import good
    value = good(); value['gaps'] = []; value['next_check'] = {'text': 'authorization'}
    return value, packet, bundle


@pytest.mark.parametrize('text,refs', [
    ('此前24小时历史均值为20。', ['F5']),
    ('此前24小时历史金额波动为2。', ['F6']),
    ('此前24小时金额与历史均值之差为-3。', ['F7']),
    ('历史均值已取得，可用于比较。', ['F5']),
])
def test_actual_available_statistics_have_positive_witnesses(available_stats, text, refs):
    issues, report = check(available_stats, text, refs)
    assert issues == [] and report is not None


@pytest.mark.parametrize('text,refs,code', [
    ('此前24小时历史均值为0。', ['F5'], 'fact_claim_value_mismatch'),
    ('此前24小时历史金额波动为20。', ['F6'], 'fact_claim_value_mismatch'),
    ('此前24小时金额与历史均值之差为3。', ['F7'], 'fact_claim_value_mismatch'),
    ('历史均值缺失，当前无法比较。', ['F5'], 'fact_claim_availability_mismatch'),
])
def test_available_statistics_cannot_be_zeroed_swapped_or_declared_missing(available_stats, text, refs, code):
    issues, report = check(available_stats, text, refs)
    assert code in {row['code'] for row in issues}
    assert report is None


@pytest.mark.parametrize('text,refs', [
    ('此前24小时匿名匹配历史交易数为十八。', ['F3']),
    ('主评分为八成。', ['F8']),
    ('持卡人个人历史均值缺失。', ['F5']),
    ('其他交易历史均值缺失。', ['F5']),
])
def test_unrecognized_claim_value_or_subject_context_is_not_verified(candidate, text, refs):
    issues, report = check(candidate, text, refs)
    assert 'fact_support_review_required' in {row['code'] for row in issues}
    assert report is None
