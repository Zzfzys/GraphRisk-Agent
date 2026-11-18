"""Small authored records test local pairing, not real model semantic quality."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_autonomous_action_providers import profile
from test_reference_answers import answer_source
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
from graphrisk.serving import daily_reference_comparison as comparison


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value), encoding='utf-8')
    return path


@pytest.fixture
def paired_ready(tmp_path, answer_source, profile):
    """A genuine prepare/verify path with six tiny authored accepted records."""
    service, retriever, _ = answer_source
    root, output = tmp_path / 'accepted', tmp_path / 'paired'
    accepted_results, artifacts, selected = {}, {}, []
    for index, key in enumerate(comparison.CASES):
        band = ('high', 'medium', 'low')[index // 2]
        score = (0.9, 0.5, 0.1)[index // 2]
        alias = digest(key)[:32]
        context = {'contract_version': 'daily-scoring-context-v1', 'temporal_scope': 'relative_day_end',
            'relative_day': 0, 'batch_end_second': 86400, 'available_through_second': 86400,
            'batch_id': 'authored-day', 'revision': 1, 'sealed_content_sha256': 'a' * 64,
            'model_version': 'authored-model', 'model_sha256': 'b' * 64,
            'feature_protocol_sha256': 'c' * 64, 'category_maps_sha256': 'd' * 64,
            'policy_sha256': 'e' * 64, 'score': score, 'risk_level': band, 'calibrated_probability': False}
        evidence = {'schema_version': 'investigation-facts-v1', 'case_alias': alias,
            'evidence_version': digest(['evidence', key]), 'policy_as_of': '2026-11-06',
            'facts': [{'fact_id': 'F-' + digest(['score', key])[:24], 'metric': 'primary_model_score',
                'subject': 'target_transaction', 'unit': 'uncalibrated_score', 'window_seconds': None,
                'temporal_scope': 'relative_day_end', 'source_tool': 'get_risk_score', 'query_ref': 'fixture',
                'source_version': 'b' * 64, 'model_version': 'authored-model',
                'availability': 'available', 'value': score, 'missing_reason': None}],
            'policies': [], 'tool_outcomes': [], 'limitations': [], 'scoring_context': context}
        scope = {'case_alias': alias, 'transaction_id': 'PRIVATE-' + key,
                 'policy_as_of': '2026-11-06', 'scoring_context': context}
        source = {'scope': scope, 'evidence': evidence, 'observations': [
            {'name': 'fixture', 'arguments': {}, 'result_json': canonical({'private': 'PRIVATE-' + key})}]}
        write(root / f'six-payloads/{key}-source-private.json', source)
        packet = {'source_scope_sha256': digest(scope), 'source_evidence_sha256': digest(evidence),
                  'source_observations_sha256': [digest(json.loads(row['result_json']))
                                                for row in source['observations']]}
        write(root / f'six-payloads/{key}-packet-private.json', packet)
        payload_path = write(root / f'six-payloads/{key}-payload.json', {'historical_private': key})
        action = {'risk_level': 'undetermined', 'rationale': {'text': '尚未核实授权'},
                  'next_check': {'text': 'authorization'}}
        result_path = write(root / f'actual-first-drafts-v1/{key}-result-private.json', {
            'case': key, 'first_draft': True, 'provider_result': {'code': 'ok', 'action': action},
            'native_guard_issues': [{'code': 'authored_old_native_issue'}],
            'human_quality_review': 'Pending', 'report': None})
        body_path = root / f'first-draft-reading/{key}.md'
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(f'# {key} 原稿\r\n\r\nPRIVATE-{key}；未核实授权。\r\n'.encode('utf-8'))
        artifacts[str(body_path)] = file_hash(body_path)
        accepted_results[key] = file_hash(result_path)
        selected.append({'review_case': key, 'risk_level': band, 'scope_sha256': digest(scope),
            'packet_sha256': digest(packet), 'payload_path': str(payload_path),
            'payload_file_sha256': file_hash(payload_path)})
    write(root / 'six-payloads/review-manifest.json', {'selected_cases': selected})
    audit_path = write(root / 'audit-final.json', {'closed': True,
        'generation': 'v12-daily-supported-quotes-v2', 'artifacts': artifacts})
    write(root / 'USER-READING-ACCEPTANCE.json', {'user_reading_feedback': 'Accepted',
        'cases': list(comparison.CASES), 'report_version': 'v12-daily-supported-quotes-v2',
        'historical_machine_results_preserved': True, 'execution_audit_sha256': file_hash(audit_path),
        'accepted_result_sha256': accepted_results})
    cloned_profile = deepcopy(profile)
    cloned_profile['deepseek']['pricing_verified_date'] = '2026-11-13'
    cloned_profile.update(approved_loopback_proxy='http://127.0.0.1:7890', report_thinking=False,
        max_output_tokens=4096, cloud_total_token_budget=16384, max_prompt_utf8_bytes=24_000)
    profile_path = write(tmp_path / 'profile.json', cloned_profile)
    manifest = comparison.prepare_comparison(root, output, profile_path=profile_path,
        manifest_path=retriever.manifest_path, corpus_version=retriever.expected_version)
    return manifest, output / 'paired-manifest.json', service


def selected_results(manifest, service):
    """Authored choices: this helper never authenticates a real model receipt."""
    rows = {}
    for query in manifest['queries']:
        packet = json.loads(Path(query['packet_path']).read_text(encoding='utf-8'))
        draft = {'decision': 'cite_reference', 'reason': 'reference_explanation',
                 'citation_ids': ['R1']} if query['case_id'] == 'Q1' else {
                    'decision': 'insufficient_evidence', 'reason': 'missing_case_facts', 'citation_ids': []}
        rows[query['case_id']] = {'query_id': query['case_id'], 'packet_sha256': query['packet_sha256'],
            'payload_sha256': query['payload_sha256'], 'answer': service.finish(packet, draft),
            'result_code': 'ok', 'execution_binding': {'authored_fixture_not_real_receipt': True}}
    return rows


def test_preparation_reuses_two_public_requests_and_preserves_original_status(paired_ready):
    manifest, path, _ = paired_ready
    assert comparison.verify_comparison(path, expected_sha256=file_hash(path)) == manifest
    assert manifest['denominators'] == {'model_selections': 2, 'report_displays': 6, 'paired_readings': 6}
    assert [query['consumed_by_cases'] for query in manifest['queries']] == [
        ['H1', 'M1', 'L1'], ['H2', 'M2', 'L2']]
    for query in manifest['queries']:
        request = json.loads(Path(query['request_path']).read_text(encoding='utf-8'))
        assert request['context'] == {'as_of': '2026-11-06', 'jurisdiction': None,
                                      'payment_rail': None, 'facts': {}}
        payload = Path(query['payload_path']).read_text(encoding='utf-8')
        assert 'PRIVATE-' not in payload and 'authored_old_native_issue' not in payload
        assert 'expected_decision' not in payload and 'accepted_result_sha256' not in payload
    for case in manifest['cases']:
        original = Path(case['body_path']).read_bytes()
        assert Path(case['a_path']).read_bytes() == original
        pending = Path(case['b_pending_path']).read_bytes()
        assert pending.startswith(original) and b'not_evaluated' in pending
        assert case['original_report'] is None and case['user_reading_feedback'] == 'Accepted'
        assert case['native_guard_issues'] == [{'code': 'authored_old_native_issue'}]
    evaluation = comparison.evaluate_comparison(path)
    assert evaluation['model']['semantic_review'] == evaluation['reading']['status'] == 'Pending'
    assert evaluation['display']['body_and_binding_pass'] == 0


def test_preparation_never_overwrites_or_writes_inside_original_materials(paired_ready):
    manifest, _, _ = paired_ready
    arguments = {key: manifest[key] for key in ('profile_path', 'manifest_path', 'corpus_version')}
    with pytest.raises(ValueError, match='output_already_exists'):
        comparison.prepare_comparison(manifest['accepted_root'], manifest['output_root'], **arguments)
    with pytest.raises(ValueError, match='original_materials_are_read_only'):
        comparison.prepare_comparison(manifest['accepted_root'], Path(manifest['accepted_root']) / 'new', **arguments)


@pytest.mark.parametrize('change', ['body', 'evidence', 'result', 'query', 'mapping', 'source'])
def test_replay_rejects_changed_materials_and_rehashed_mapping(paired_ready, change):
    manifest, path, service = paired_ready
    case = manifest['cases'][0]
    if change == 'body':
        target = Path(case['body_path'])
        target.write_bytes(target.read_bytes() + b'changed')
    elif change == 'evidence':
        target = Path(case['source_path'])
        value = json.loads(target.read_text(encoding='utf-8'))
        value['evidence']['policy_as_of'] = '2026-11-07'
        write(target, value)
    elif change == 'result':
        target = Path(case['result_path'])
        value = json.loads(target.read_text(encoding='utf-8'))
        value['native_guard_issues'] = []
        write(target, value)
    elif change == 'query':
        target = Path(manifest['queries'][0]['packet_path'])
        value = json.loads(target.read_text(encoding='utf-8'))
        value['request']['context']['jurisdiction'] = 'US'
        write(target, value)
    elif change == 'source':
        corpus = json.loads(service.retriever.manifest_path.read_text(encoding='utf-8'))
        target = service.retriever.manifest_path.parent / corpus['documents'][0]['snapshot_file']
        target.write_bytes(target.read_bytes() + b'changed')
    else:
        manifest['cases'][0]['query_id'] = 'Q2'
        manifest['cases'][0]['case_binding_sha256'] = digest(manifest['cases'][0])
        write(path, manifest)
    with pytest.raises((ValueError, KeyError)):
        comparison.verify_comparison(path)


def test_render_appends_complete_sources_without_rewriting_body_or_claiming_review(paired_ready):
    manifest, path, service = paired_ready
    rows = selected_results(manifest, service)
    output = path.parent.parent / 'rendered'
    rendered = comparison.render_comparison(path, output,
        verified_results_by_query=rows, expected_sha256=file_hash(path))
    for case, saved in zip(manifest['cases'], rendered['cases']):
        content = Path(saved['b_path']).read_bytes()
        body = Path(case['body_path']).read_bytes()
        assert content[:len(body)] == body and saved['body_byte_length'] == len(body)
        assert case['body_sha256'].encode() in content and case['case_binding_sha256'].encode() in content
        assert '2026年11月06日'.encode() in content and '原 report=None 状态保留'.encode() in content
        for source in rows[case['query_id']]['answer']['sources']:
            assert source['text'].encode() in content
            assert source['source_url'].encode() in content and source['content_sha256'].encode() in content
    evaluation = comparison.evaluate_comparison(path, output / 'rendered-manifest.json',
        expected_sha256=file_hash(path), verified_results_by_query=rows)
    assert evaluation['display']['body_and_binding_pass'] == 6
    assert evaluation['model'] == {'completed_valid': 2, 'expected_branch_matches': 2, 'semantic_review': 'Pending'}
    assert evaluation['reading']['status'] == 'Pending'
    assert 'not_performed_in_this_module' in rendered['selection_authentication']


def test_render_rejects_different_question_or_forged_inner_authentication(paired_ready):
    manifest, path, service = paired_ready
    rows = selected_results(manifest, service)
    rows['Q1']['packet_sha256'] = rows['Q2']['packet_sha256']
    with pytest.raises(ValueError, match='result_binding'):
        comparison.render_comparison(path, path.parent.parent / 'bad-question',
            verified_results_by_query=rows, expected_sha256=file_hash(path))
    rows = selected_results(manifest, service)
    rows['Q1']['answer']['model_response_authenticated'] = True
    with pytest.raises(ValueError):
        comparison.render_comparison(path, path.parent.parent / 'bad-authentication',
            verified_results_by_query=rows, expected_sha256=file_hash(path))


def test_rendered_sidecar_cannot_legitimize_changed_body(paired_ready):
    manifest, path, service = paired_ready
    rows = selected_results(manifest, service)
    output = path.parent.parent / 'rendered'
    rendered = comparison.render_comparison(path, output,
        verified_results_by_query=rows, expected_sha256=file_hash(path))
    b_path = Path(rendered['cases'][0]['b_path'])
    b_path.write_bytes(b'changed body\n' + b_path.read_bytes())
    rendered['cases'][0]['b_sha256'] = rendered['files'][str(b_path)] = file_hash(b_path)
    write(output / 'rendered-manifest.json', rendered)
    with pytest.raises(ValueError, match='rendered_artifact_changed'):
        comparison.evaluate_comparison(path, output / 'rendered-manifest.json', verified_results_by_query=rows)


def test_failed_selection_stays_in_two_request_denominator_and_cannot_pass_review(paired_ready):
    manifest, path, service = paired_ready
    rows = selected_results(manifest, service)
    rows['Q1'].update(result_code='invalid_model_action', answer=None)
    output = path.parent.parent / 'rendered'
    rendered = comparison.render_comparison(path, output,
        verified_results_by_query=rows, expected_sha256=file_hash(path))
    assert sum(case['selection_status'].startswith('selection_failed:') for case in rendered['cases']) == 3
    evaluation = comparison.evaluate_comparison(path, output / 'rendered-manifest.json', verified_results_by_query=rows)
    assert evaluation['denominators']['model_selections'] == 2 and evaluation['model']['completed_valid'] == 1
    with pytest.raises(ValueError, match='failed_selection_cannot_pass_review'):
        comparison.evaluate_comparison(path, output / 'rendered-manifest.json', verified_results_by_query=rows,
            query_reviews={key: {'status': 'Pass', 'notes': 'authored judgement'} for key in rows})


def test_semantic_two_and_paired_reading_six_are_independent_denominators(paired_ready):
    manifest, path, service = paired_ready
    rows = selected_results(manifest, service)
    output = path.parent.parent / 'rendered'
    comparison.render_comparison(path, output, verified_results_by_query=rows, expected_sha256=file_hash(path))
    readings = {key: {'status': 'Reviewed', 'comparison': '更好', 'next_check_clearer': True,
        'reading_burden': 2, 'misleading_case_fact': False,
        'misleading_applicability_or_authority': False, 'notes': 'authored reading, not real feedback'}
        for key in comparison.CASES}
    readings['L2'].update(comparison='更差', misleading_applicability_or_authority=True)
    review = {key: {'status': 'Pass', 'notes': 'authored support check'} for key in rows}
    evaluation = comparison.evaluate_comparison(path, output / 'rendered-manifest.json',
        verified_results_by_query=rows, query_reviews=review, readings=readings)
    assert evaluation['model']['semantic_pass'] == 2
    assert evaluation['reading']['reviewed'] == 6 and evaluation['reading']['更好'] == 5
    assert evaluation['reading']['更差'] == evaluation['reading']['misleading_applicability_or_authority_count'] == 1
    assert 'fraud_recall' in evaluation['not_evaluated']
    del readings['L2']
    with pytest.raises(ValueError, match='six_readings_required'):
        comparison.evaluate_comparison(path, output / 'rendered-manifest.json',
            verified_results_by_query=rows, readings=readings)
