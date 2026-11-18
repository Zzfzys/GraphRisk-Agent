"""Offline daily candidate boundaries; no provider requests or report repairs."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from test_autonomous_tool_contracts import scope, index, registry, executor_for, run_core
from test_autonomous_action_providers import profile
from test_daily_narrative import daily_scope
from test_judgment_v3 import good

from graphrisk.agent import judgment_v3 as base
from graphrisk.agent import judgment_v12_daily_review as candidate
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.report_generation_profile import quality_report_profile
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
from graphrisk.serving.daily_narrative import configure_daily_narrative


@pytest.fixture
def daily_review(daily_scope, index, registry, profile):
    def two_history_rows(name, result):
        if name == 'get_transaction_features':
            result = deepcopy(result)
            result['data'].update(tx_count_last_1h=2, tx_count_last_24h=2, tx_count_last_7d=2)
        return result
    executor, requests = executor_for(daily_scope, index, mutate=two_history_rows)
    run_core(executor)
    assert len(requests) == 6
    bundle = build_evidence(daily_scope, executor.observations, registry)
    report_profile = quality_report_profile(profile)
    original = base.packet_for(daily_scope, executor.observations, registry, report_profile)
    before = deepcopy(original)
    packet = candidate.prepare_packet(original, bundle, report_profile)
    assert original == before
    return packet, bundle, original, report_profile


def refs_for(packet, bundle, metric, window=None):
    return {ref for ref, identifier in packet['refs_private'].items()
            if any(f.fact_id == identifier and f.metric == metric and
                   (window is None or f.window_seconds == window) for f in bundle.facts)}


def test_prepare_is_isolated_and_default_contract_remains_v12(daily_review, profile):
    packet, _, original, _ = daily_review
    assert original['judgment_rules_version'] == base.RULES
    assert original['view']['narrative_contract'] == base.CONTRACT
    assert original['generation_variant'] == base.DAY_END_VARIANT
    assert packet['judgment_rules_version'] == candidate.RULES != base.RULES
    assert packet['view']['narrative_contract'] == candidate.CONTRACT
    assert packet['view']['reference_contract']['examples_are_evidence'] is False
    delegate = configure_daily_narrative(SimpleNamespace(profile=profile), narrative_factory=None,
        report_contract=base.CONTRACT)
    assert delegate.report_contract == base.CONTRACT
    assert not delegate.allow_investigation and delegate.max_narrative_calls == 1


def test_valid_gap_sources_are_distinct_from_fact_reference_namespace(daily_review):
    packet, bundle, _, _ = daily_review
    value = good()
    parsed = candidate.parse(value, packet, bundle)
    assert parsed.main_limit.source == 'G1'
    assert not candidate.issues_for(value, packet, bundle)
    changed = deepcopy(value)
    changed['next_check']['refs'] = ['F1', 'G1']
    issues = candidate.issues_for(changed, packet, bundle)
    assert any(item['code'] == 'reference_namespace_mismatch' for item in issues)
    with pytest.raises(ActionFailure, match='daily_review_reference_contract'):
        candidate.parse(changed, packet, bundle)
    with pytest.raises(ValidationError):
        candidate.Draft.model_validate_json(canonical(changed), strict=True)


@pytest.mark.parametrize('ref_kind', ['unknown', 'hidden'])
def test_unknown_and_hidden_fact_citations_are_rejected(daily_review, ref_kind):
    packet, bundle, _, _ = daily_review
    visible = {row[0] for row in packet['view']['facts']} | {p['ref'] for p in packet['view']['policies']}
    hidden = sorted(set(packet['refs_private']) - visible)
    assert hidden, 'the existing plain program-scope presentation hides scope-only references'
    value = good()
    value['rationale']['refs'] = ['F999'] if ref_kind == 'unknown' else [hidden[0]]
    issues = candidate.issues_for(value, packet, bundle)
    assert any(item['code'] == 'unknown_or_hidden_citation' for item in issues)
    with pytest.raises(ActionFailure, match='daily_review_reference_contract'):
        candidate.parse(value, packet, bundle)


def test_l1_all_windows_and_amount_variation_require_complete_metric_citations(daily_review):
    packet, bundle, _, _ = daily_review
    counts = refs_for(packet, bundle, 'history_transaction_count')
    mean = refs_for(packet, bundle, 'history_amount_mean')
    variation = refs_for(packet, bundle, 'history_amount_population_std')
    value = good()
    value['rationale'] = {'text': '此前各窗口均有匿名匹配历史，历史金额均值与波动均未取得。',
                          'refs': sorted(refs_for(packet, bundle, 'history_transaction_count', 3600) | mean)}
    required = counts | mean | variation
    issues = candidate.issues_for(value, packet, bundle)
    missing = {ref for issue in issues if issue['code'] == 'declared_metric_citation_required'
               for ref in issue['missing_refs']}
    assert required - set(value['rationale']['refs']) <= missing
    assert variation <= missing
    value['rationale']['refs'] = sorted(required)
    assert not candidate.issues_for(value, packet, bundle)


@pytest.mark.parametrize('text', [
    '这些匿名匹配已经形成个人历史参照，值得进一步核验。',
    '当前记录构成个人基线，可以作为比较背景。',
    '不能确认授权情况。当前匿名匹配构成个人历史参照。',
    '这不是个人历史；但当前记录已经构成个人基线。',
    '不能确认授权，个人历史存在记录。',
    '个人历史存在记录，是否为本人待核实。',
])
def test_affirmative_identity_upgrade_requires_review_in_each_clause(daily_review, text):
    packet, bundle, _, _ = daily_review
    value = good()
    value['hypotheses'] = [{'text': text, 'refs': ['F1']}]
    issues = candidate.issues_for(value, packet, bundle)
    assert any(issue['code'] == 'heuristic_identity_wording_review_required' for issue in issues)


@pytest.mark.parametrize('text', [
    '匿名匹配不是个人历史，不能据此确认真实身份。',
    '当前记录不代表个人基线，仍需核验实际身份。',
    '个人历史是否成立尚待核实，不能直接确认身份。',
    '个人基线未经核实，现有匹配不能确认真实身份。',
])
def test_denied_or_explicitly_unverified_personal_history_is_not_affirmative(daily_review, text):
    packet, bundle, _, _ = daily_review
    value = good()
    value['hypotheses'] = [{'text': text, 'refs': ['F1']}]
    assert not candidate.issues_for(value, packet, bundle)


def test_precise_history_numbers_still_belong_to_program_cards(daily_review):
    packet, bundle, _, _ = daily_review
    count = refs_for(packet, bundle, 'history_transaction_count', 86400)
    value = good()
    value['rationale'] = {'text': '此前24小时的匿名匹配历史交易数为2，仍需审慎理解。', 'refs': sorted(count)}
    assert candidate.issues_for(value, packet, bundle)
    value['rationale'] = {'text': '此前24小时的匿名匹配历史样本有限，其解释力仍需审慎评估。',
                          'refs': sorted(count)}
    assert not candidate.issues_for(value, packet, bundle)
    issues, report = candidate.verify(candidate.parse(value, packet, bundle), packet, bundle)
    assert not issues and report


@pytest.mark.parametrize('replacement', ['target_amount', 'wrong_history_window'])
def test_qualitative_limited_history_requires_the_stated_window_count(daily_review, replacement):
    packet, bundle, _, _ = daily_review
    expected = refs_for(packet, bundle, 'history_transaction_count', 86400)
    value = good()
    value['rationale'] = {'text': '此前24小时匿名匹配历史样本有限，不能据此形成稳定比较基线。',
                          'refs': sorted(expected)}
    assert not candidate.issues_for(value, packet, bundle)
    value['rationale']['refs'] = (['F1'] if replacement == 'target_amount' else
                                 sorted(refs_for(packet, bundle, 'history_transaction_count', 3600)))
    issues = candidate.issues_for(value, packet, bundle)
    assert any(issue['code'] == 'declared_metric_citation_required' and
               expected <= set(issue['missing_refs']) for issue in issues)


def test_denial_of_a_statistical_inference_does_not_assert_that_statistic(daily_review):
    packet, bundle, _, _ = daily_review
    value = good()
    value['hypotheses'] = [{'text': '不能把历史波动当作欺诈证明。', 'refs': ['F1']}]
    assert not candidate.issues_for(value, packet, bundle)


def test_candidate_report_binds_its_own_version_and_does_not_self_award_quality(daily_review):
    packet, bundle, _, _ = daily_review
    errors, report = candidate.verify(candidate.parse(good(), packet, bundle), packet, bundle)
    assert errors == []
    assert report['version'] == 'separated-risk-report-v12-daily-review-candidate'
    assert report['contract'] == candidate.CONTRACT
    assert report['generation_variant'] == candidate.GENERATION
    assert report['judgment_rules_version'] == candidate.RULES
    assert report['report_sha256'] == digest({k: v for k, v in report.items() if k != 'report_sha256'})
    assert report['program']['scoring_context'] == bundle.scoring_context.model_dump(mode='json')
    assert report['semantic_quality'] == 'requires_review' and report['quality_scores'] is None
    assert report['approval_eligible'] is False


@pytest.mark.parametrize('field', ['draft', 'judgment'])
@pytest.mark.parametrize('operation', ['delete', 'change'])
def test_saved_report_rebuild_rejects_draft_or_display_tampering_even_after_rehash(
        daily_review, field, operation):
    packet, bundle, _, _ = daily_review
    issues, report = candidate.verify(candidate.parse(good(), packet, bundle), packet, bundle)
    assert not issues
    original = deepcopy(report)
    assert candidate.verify_saved(report, packet, bundle) == report == original
    changed = deepcopy(report)
    if operation == 'delete':
        del changed[field]['rationale']
    else:
        changed[field]['rationale']['text'] = '现有线索尚不能确认真实授权情况，需要进一步人工核实。'
    changed['report_sha256'] = digest({k: v for k, v in changed.items() if k != 'report_sha256'})
    with pytest.raises(ActionFailure, match='candidate_saved_report_changed'):
        candidate.verify_saved(changed, packet, bundle)
    assert report == original


@pytest.mark.parametrize('field', ['view', 'messages_sha256', 'generation_variant', 'response_schema'])
def test_tampered_packet_stops_before_any_draft_review(daily_review, field):
    packet, bundle, _, _ = daily_review
    changed = deepcopy(packet)
    if field == 'view':
        changed['view']['writing_plan'] = 'tampered'
    elif field == 'response_schema':
        changed['view']['response_schema']['title'] = 'tampered'
    else:
        changed[field] = 'tampered'
    with pytest.raises(ActionFailure, match='daily_review_packet_changed'):
        candidate.issues_for(good(), changed, bundle)


@pytest.mark.parametrize('field', ['rationale', 'next_check'])
@pytest.mark.parametrize('refs', [1, [{}], None, 'F1'])
def test_malformed_reference_arrays_return_schema_issue_without_crashing(daily_review, field, refs):
    packet, bundle, _, _ = daily_review
    value = good()
    value[field]['refs'] = refs
    issues = candidate.issues_for(value, packet, bundle)
    assert any(issue['code'] == 'daily_review_schema_invalid' for issue in issues)


@pytest.mark.parametrize('field', ['main_limit', 'gaps'])
def test_provider_rejects_unknown_gap_source_before_accepting_draft(daily_review, field):
    packet, _, _, _ = daily_review
    value = good()
    if field == 'gaps':
        value['gaps'][0]['source'] = 'G999'
    else:
        value['main_limit']['source'] = 'G999'
    provider = object.__new__(candidate.Provider)
    with pytest.raises(ActionFailure, match='daily_review_schema_invalid'):
        provider.check_output(canonical(value), packet)


@pytest.fixture
def offline_source_run(daily_review, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    import prepare_daily_report_candidate as offline
    def no_transport(*args, **kwargs):
        raise AssertionError('offline preparation must not initialize an HTTP client')
    monkeypatch.setattr(httpx, 'Client', no_transport)
    _, bundle, original, profile = daily_review
    source_run = tmp_path / 'old-source-run'
    def write(relative, value):
        path = source_run / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical(value), encoding='utf-8')
        return path
    originals = [write('final-six/profile.json', profile)]
    results = []
    keys = ['H1', 'H2', 'M1', 'M2', 'L1', 'L2']
    for key in keys:
        originals.extend([
            write(f'final-six/{key}-source-private.json',
                  {'scope': original['scope_private'], 'evidence': bundle.model_dump(mode='json')}),
            write(f'final-six/{key}-packet-private.json', original),
            write(f'final-six/{key}-payload.json', {'placeholder': 'saved original payload'})])
        codes = sorted({row['code'] for row in base.issues_for(good(), original, bundle)})
        path = write(f'actual-first-drafts-v1/{key}-result-private.json',
                     {'provider_result': {'action': good()}, 'native_guard_issues': codes})
        results.append({'case': key, 'result_sha256': file_hash(path)})
    write('execution-private.json', {'case_keys': keys,
          'files': {str(path): file_hash(path) for path in originals}})
    write('LIVE-EXECUTION.json', {'closed': True, 'cases': results})
    before = {path: file_hash(path) for path in source_run.rglob('*.json')}
    return offline, source_run, before


def test_offline_cli_prepares_six_payloads_without_changing_originals_or_sending(offline_source_run, tmp_path):
    offline, source_run, before = offline_source_run
    output = tmp_path / 'candidate-output'
    result = offline.prepare(source_run, output)
    assert len(result['cases']) == len(result['saved_draft_rechecks']) == 6
    assert len(list(output.glob('*-candidate-payload.json'))) == 6
    assert result['paid_calls'] == result['external_transfers'] == result['new_first_drafts_generated'] == 0
    assert result['candidate_writing_quality'] == 'NotEvaluated'
    assert result['payloads_authorized_for_sending'] is False
    assert all(file_hash(path) == expected for path, expected in before.items())


def test_offline_cli_rejects_source_hash_change_before_creating_output(offline_source_run, tmp_path):
    offline, source_run, _ = offline_source_run
    (source_run / 'final-six/H1-source-private.json').write_text('{}', encoding='utf-8')
    output = tmp_path / 'rejected-output'
    with pytest.raises(ValueError, match='original source hash changed'):
        offline.prepare(source_run, output)
    assert not output.exists()


@pytest.mark.parametrize('location', ['same', 'inside', 'ancestor', 'existing'])
def test_offline_cli_requires_a_separate_new_output_directory(offline_source_run, tmp_path, location):
    offline, source_run, before = offline_source_run
    options = {'same': source_run, 'inside': source_run / 'new-output',
               'ancestor': tmp_path, 'existing': tmp_path / 'existing-output'}
    output = options[location]
    if location == 'existing':
        output.mkdir()
        (output / 'sentinel.txt').write_text('preserve', encoding='utf-8')
    with pytest.raises(ValueError, match='separate|already exists'):
        offline.prepare(source_run, output)
    assert all(file_hash(path) == expected for path, expected in before.items())
    if location == 'inside':
        assert not output.exists()
    if location == 'existing':
        assert (output / 'sentinel.txt').read_text(encoding='utf-8') == 'preserve'
