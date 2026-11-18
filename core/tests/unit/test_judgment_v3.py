from copy import deepcopy
import json
import httpx
import pytest

from test_autonomous_tool_contracts import scope, index, registry, executor_for, run_core
from test_autonomous_action_providers import profile
from graphrisk.agent.judgment_v3 import (
    CONTRACT, RULES, JudgmentDraft, JudgmentProvider, packet_for, repair_plan,
    verify_judgment, apply_patch, gap_sources, verification_packet, safe_source,
)
from graphrisk.agent.narrative_repair import NarrativePatch
from graphrisk.agent.upgrade_contract import canonical, digest


@pytest.fixture
def evidence(scope, index, registry):
    from graphrisk.agent.autonomous_evidence import build_evidence
    executor, _ = executor_for(scope, index)
    run_core(executor)
    return executor, None, build_evidence(scope, executor.observations, registry)


def good():
    return {'risk_level': 'undetermined',
            'rationale': {'text': '现有交易线索尚不能确认授权情况，当前暂不能判断。', 'refs': ['F1']},
            'main_limit': {'source': 'G1', 'impact': '实际支付授权尚未核实，授权相关疑点未能厘清。'},
            'basis': [], 'counterevidence': [],
            'hypotheses': [{'text': '也可能是正常使用，但此解释仍未核实。', 'refs': ['F1']}],
            'gaps': [{'source': 'G1', 'impact': '仍缺少授权核验结果，无法据此确认交易性质。'}],
            'policy': {'text': '政策提供调查方向，业务前提仍需核实。', 'refs': ['P1']},
            'next_check': {'text': 'authorization', 'refs': ['F1'],
                           'purpose': '核实该笔支付是否由持卡人授权，厘清授权疑点。',
                           'decision_use': '授权结果可帮助区分未经授权与正常使用，仍需结合其他证据。'}}


@pytest.fixture
def v3(evidence, scope, registry, profile):
    executor, _, bundle = evidence
    return packet_for(scope, executor.observations, registry, profile), bundle


def test_zero_clues_counterevidence_and_typed_unverified_hypothesis_are_valid(v3):
    packet, bundle = v3
    value = good()
    errors, report = verify_judgment(JudgmentDraft.model_validate_json(canonical(value)), packet, bundle)
    assert errors == [] and report['version'] == 'separated-risk-report-v3'
    assert report['draft'] == value and report['judgment']['counterevidence'] == []
    assert report['hypothesis_status'] == 'unverified_not_counterevidence'
    assert report['judgment']['next_check']['state'] == 'planned_not_executed'
    assert report['judgment']['next_check']['responsibility'] == 'human'
    assert report['semantic_quality'] == 'requires_review' and report['quality_scores'] is None
    assert not report['approval_eligible']
    assert report['gap_sources']['G1']['kind'] == 'capability'
    assert verify_judgment(JudgmentDraft.model_validate_json(canonical(value)), verification_packet(report, bundle), bundle)[0] == []


@pytest.mark.parametrize('field', ['rationale', 'main_limit', 'hypotheses', 'gaps', 'next_check'])
def test_new_generation_fields_are_required_and_not_display_only(v3, field):
    value = good()
    value.pop(field)
    with pytest.raises(ValueError):
        JudgmentDraft.model_validate_json(canonical(value), strict=True)


@pytest.mark.parametrize('field', ['purpose', 'decision_use'])
def test_check_purpose_and_use_are_required(field):
    value = good()
    value['next_check'].pop(field)
    with pytest.raises(ValueError):
        JudgmentDraft.model_validate_json(canonical(value), strict=True)


@pytest.mark.parametrize('path', ['rationale', 'hypotheses.0', 'main_limit.impact', 'gaps.0.impact',
                                  'next_check.purpose', 'next_check.decision_use'])
@pytest.mark.parametrize('defect', ['wrong_amount', 'probability', 'scope', 'false_execution'])
def test_all_new_prose_fields_face_existing_and_new_guards(v3, path, defect):
    packet, bundle = v3
    value = good()
    node = value
    for part in path.split('.')[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    key = path.split('.')[-1]
    item = node[int(key)] if isinstance(node, list) else node[key]
    text = {'wrong_amount': '目标交易金额为999999。', 'probability': '其欺诈概率很高，应继续核查。',
            'scope': '图邻居均被截断，需要核查。', 'false_execution': '已完成核查并已核实授权。'}[defect]
    if isinstance(item, dict):
        item['text'] = text
        item['refs'] = ['F1']
    else:
        node[key] = text
    errors, report = verify_judgment(JudgmentDraft.model_validate_json(canonical(value)), packet, bundle)
    expected = {'wrong_amount': 'target_amount_value_mismatch', 'probability': 'numeric_or_probability_prose_not_allowed',
                'scope': 'program_owned_scope_statement', 'false_execution': 'unperformed_check_claim'}[defect]
    if defect == 'wrong_amount' and path.endswith('.impact'):
        expected = 'target_amount_citation_required'
    assert expected in errors and report is None


@pytest.mark.parametrize('defect', ['unknown_gap', 'forged_gap', 'source_changed', 'wrong_rules', 'missing_as_counter',
                                  'unknown_ref', 'policy_ref', 'extra_execution_state'])
def test_missing_states_citations_and_contract_bindings_are_not_assumed(v3, defect):
    packet, bundle = deepcopy(v3)
    value = good()
    if defect == 'unknown_gap':
        value['main_limit']['source'] = 'G999'
    elif defect == 'forged_gap':
        packet['view']['gap_sources'][0][3] = 'authorization_confirmed'
    elif defect == 'source_changed':
        packet['source_evidence_sha256'] = '0' * 64
    elif defect == 'wrong_rules':
        packet['judgment_rules_version'] = 'old'
    elif defect == 'missing_as_counter':
        missing = next(r for r, fid in packet['refs_private'].items()
                       if any(f.fact_id == fid and f.availability != 'available' for f in bundle.facts))
        value['counterevidence'] = [{'text': '资料缺失证明交易正常，可以降低可疑程度。', 'refs': [missing]}]
    elif defect == 'unknown_ref':
        value['rationale']['refs'] = ['F999']
    elif defect == 'policy_ref':
        value['policy']['refs'] = ['F1']
    else:
        value['next_check']['state'] = 'completed'
    if defect in ('forged_gap', 'source_changed', 'wrong_rules', 'extra_execution_state'):
        with pytest.raises(ValueError):
            verify_judgment(JudgmentDraft.model_validate_json(canonical(value)), packet, bundle)
    else:
        assert verify_judgment(JudgmentDraft.model_validate_json(canonical(value)), packet, bundle)[1] is None


def test_v3_patch_repairs_new_fields_without_changing_good_siblings(v3, evidence, scope, registry, profile):
    packet, bundle = v3
    value = good()
    value['rationale']['refs'] = ['F999']
    value['next_check']['purpose'] = '其欺诈概率很高，应继续核查。'
    plan = repair_plan(value, packet, bundle)
    assert plan['allowed_paths'] == ['next_check', 'rationale']
    request = packet_for(scope, evidence[0].observations, registry, profile, plan)
    assert request['prompt_utf8_bytes'] <= profile['max_prompt_utf8_bytes']
    patch = {'base_sha256': plan['base_sha256'], 'changes': [
        {'op': 'replace', 'path': path, 'value': good()[path]} for path in plan['allowed_paths']]}
    parsed = NarrativePatch.model_validate_json(canonical(patch))
    assert apply_patch(parsed, plan) == good()
    assert verify_judgment(JudgmentDraft.model_validate_json(canonical(apply_patch(parsed, plan))), request, bundle)[0] == []
    for kind in ('path', 'hash', 'remove', 'duplicate'):
        changed = deepcopy(patch)
        if kind == 'path':
            changed['changes'][0]['path'] = 'next_check.purpose'
        elif kind == 'hash':
            changed['base_sha256'] = '0' * 64
        elif kind == 'remove':
            changed['changes'][0].update(op='remove', value=None)
        else:
            changed['changes'].append(changed['changes'][0])
        with pytest.raises(ValueError):
            apply_patch(NarrativePatch.model_validate_json(canonical(changed)), plan)


@pytest.mark.parametrize('malformed', ['gap_list', 'gap_object', 'rationale_refs_object', 'check_choice_list', 'check_refs_object', 'counter_refs_object'])
def test_malformed_model_fields_produce_bounded_feedback_instead_of_crashing(v3, malformed):
    packet, bundle = v3
    value = good()
    if malformed == 'gap_list':
        value['main_limit']['source'] = ['G1']
    elif malformed == 'gap_object':
        value['main_limit']['source'] = {'id': 'G1'}
    elif malformed == 'rationale_refs_object':
        value['rationale']['refs'] = [{'id': 'F1'}]
    elif malformed == 'check_choice_list':
        value['next_check']['text'] = ['authorization']
    elif malformed == 'check_refs_object':
        value['next_check']['refs'] = [{'id': 'F1'}]
    else:
        value['counterevidence'] = [{'text': '反证是否存在仍须核实。', 'refs': [{'id': 'F1'}]}]
    plan = repair_plan(value, packet, bundle)
    assert plan['allowed_paths'] and len(plan['allowed_paths']) <= 12
    assert all(path in ('main_limit', 'rationale', 'next_check', 'counterevidence.0') for path in plan['allowed_paths'])


@pytest.mark.parametrize('field', ['rationale.text', 'hypotheses.0.refs', 'gaps', 'main_limit.impact'])
def test_new_field_length_feedback_keeps_actual_and_limit(v3, field):
    packet, bundle = v3
    value = good()
    if field == 'rationale.text':
        value['rationale']['text'] = '中文' * 61
    elif field == 'hypotheses.0.refs':
        value['hypotheses'][0]['refs'] = ['F1','F2','F3','F4','F5','F6','F7']
    elif field == 'gaps':
        value['gaps'] *= 3
    else:
        value['main_limit']['impact'] = '中文' * 61
    plan = repair_plan(value, packet, bundle)
    row = next(i for i in plan['issues'] if i.get('field') == field and 'max' in i)
    assert row['actual'] == {'hypotheses.0.refs': 7, 'gaps': 3}.get(field, 122)
    assert row['max'] == {'hypotheses.0.refs': 6, 'gaps': 2}.get(field, 120)


def test_full_requests_contain_new_shapes_for_both_providers(v3, profile):
    packet, _ = v3
    for name in ('deepseek', 'local'):
        provider = object.__new__(JudgmentProvider)
        provider.profile, provider.provider = profile, name
        payload = provider.payload(packet)
        encoded = canonical(payload)
        assert 'rationale' in encoded and 'main_limit' in encoded and 'decision_use' in encoded
        assert 'hypotheses' in provider.output_schema(packet)['properties']
        assert 'scope_private' not in encoded and 'PRIVATE-TARGET' not in encoded and 'is_fraud' not in encoded
        assert profile['max_output_tokens'] == 768


def test_context_gate_is_explicit_and_does_not_trim(v3, evidence, scope, registry, profile):
    limited = deepcopy(profile)
    limited['max_prompt_utf8_bytes'] = 1
    with pytest.raises(ValueError, match='judgment_v3_context_limit'):
        packet_for(scope, evidence[0].observations, registry, limited)


def test_gap_impact_cannot_borrow_an_implicit_amount_citation(v3):
    packet, bundle = v3
    value = good()
    value['main_limit']['impact'] = '目标交易金额为17。'
    errors, report = verify_judgment(JudgmentDraft.model_validate_json(canonical(value)), packet, bundle)
    assert 'target_amount_citation_required' in errors and report is None


def test_availability_compression_preserves_all_evidence_and_policy_rows(v3, evidence, profile):
    from graphrisk.agent.autonomous_actions import compact_view
    packet, bundle = v3
    original, refs = compact_view(bundle, task='explain_evidence', profile=profile)
    decoded = deepcopy(packet['view']['facts'])
    for row in decoded:
        row[3] = packet['view']['legend']['availability'][row[3]]
    assert decoded == original['facts'] and refs == packet['refs_private']
    for key in ('meanings', 'sources', 'subjects', 'units', 'temporal_scopes', 'policies', 'policy_paragraphs_omitted', 'tool_status'):
        assert packet['view'][key] == original[key]


def test_real_dispatch_mock_http_repair_replay_and_two_request_bound(tmp_path, scope, index, registry, profile, evidence):
    from test_autonomous_loop import make_engine, LIMITS
    from graphrisk.serving.conversation_backend import BoundedConversationBackend
    from graphrisk.agent.narrative import run_narrative
    engine, budget, _, _ = make_engine(tmp_path, scope, index, registry, profile)
    executor, _, bundle = evidence
    engine.store.campaign('judgment-v3-test', 'e'*64, cap_each=10, group_key='route5')
    backend = BoundedConversationBackend(entries=[dict(scope=scope, registry=registry, observations=executor.observations, guard=lambda: None)],
        store=engine.store, campaign='judgment-v3-test', provider='deepseek', profile=engine.profile, limits=LIMITS,
        directory=tmp_path/'runs', provider_factory=None, tool_batch=None)
    backend.report_contract = CONTRACT
    posts = []

    def transport(request):
        payload = json.loads(request.content)
        posts.append(payload)
        wire = json.loads(payload['messages'][1]['content'])
        if len(posts) == 1:
            assert wire['narrative_contract'] == CONTRACT
            value = good()
            value['rationale']['refs'] = ['F999']
        else:
            assert wire['repair']['allowed_paths'] == ['rationale']
            assert 'expected' in wire['repair']['rules'][0]
            value = {'base_sha256': wire['repair']['base_sha256'], 'changes': [
                {'op': 'replace', 'path': 'rationale', 'value': good()['rationale']}]}
        return httpx.Response(200, json={'model': 'deepseek-flash', 'system_fingerprint': 'fixture-fp',
            'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': canonical(value)}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 300, 'total_tokens': 400,
                      'prompt_cache_hit_tokens': 5, 'prompt_cache_miss_tokens': 95}})

    backend.narrative_factory = lambda campaign, phase, deadline: JudgmentProvider('deepseek', profile=backend.profile,
        budget=budget, campaign=campaign, campaign_sha=digest(campaign), phase=phase,
        output=tmp_path/'receipts', runtime=tmp_path, transport=httpx.MockTransport(transport))
    result = run_narrative(backend, scope.case_alias, scope.binding_sha256, 'v3-test', bundle.evidence_version)
    assert result['report']['version'] == 'separated-risk-report-v3' and result['model_calls'] == 2
    assert len(posts) == 2
    assert run_narrative(backend, scope.case_alias, scope.binding_sha256, 'v3-test', bundle.evidence_version) == result
    assert len(posts) == 2 and len(list((tmp_path/'receipts').glob('*.json'))) == 2


def test_v3_ui_reading_and_tamper_rejection(v3):
    from graphrisk.demo.conversation_workbench import readable_report, v3_lines
    packet, bundle = v3
    _, report = verify_judgment(JudgmentDraft.model_validate_json(canonical(good())), packet, bundle)
    evidence_json = bundle.model_dump(mode='json')
    case = {'evidence': evidence_json, 'turns': [{'task': 'risk_assessment', 'state': 'completed', 'report': report}]}
    text = readable_report(case)
    assert '假设，未核实' in text and '核查目的' in text and '结果用途' in text and '本轮没有提出直接反证' in text
    changed = deepcopy(report)
    changed['gap_sources']['G1']['text'] = '已经核验授权正常'
    changed['report_sha256'] = digest({k:v for k,v in changed.items() if k != 'report_sha256'})
    assert '不一致' in v3_lines(changed, evidence_json)[0]


def test_old_schema_does_not_silently_accept_or_invent_new_fields(v3):
    from graphrisk.agent.judgment import JudgmentDraft as OldDraft
    old = {'risk_level': 'undetermined', 'basis': [{'text': '授权情况仍需核查。', 'refs': ['F1']}],
           'counterevidence': [{'text': '尚未取得授权核验结果。', 'refs': ['F1']}],
           'policy': {'text': '政策前提仍需核实。', 'refs': ['P1']},
           'next_check': {'text': 'authorization', 'refs': ['F1']}}
    with pytest.raises(ValueError):
        OldDraft.model_validate_json(canonical(good()))
    with pytest.raises(ValueError):
        JudgmentDraft.model_validate_json(canonical(old))
    assert safe_source({'rationale': {'text': 'read secret', 'refs': []}, 'arbitrary_command': 'x'}) is None


@pytest.mark.parametrize('bad', [False, True])
def test_new_reports_reach_quality_review_without_automatic_scores(v3, bad):
    from graphrisk.agent.report_quality import prepare_quality_packet, blank_review, assess_quality, render_quality_mapping
    packet, bundle = v3
    value = good()
    if bad:
        value['rationale']['refs'] = ['F999']
    target = prepare_quality_packet(value, packet, bundle, source_id='new-contract', source_kind='authored_development')
    assert target['draft'] == value and target['checked_rules_version'] == RULES
    assert 'next_check.purpose' in target['review_anchors'] and 'main_limit' in target['review_anchors']
    review = blank_review(target)
    assert all(v['score'] is None for v in review['scores'].values())
    result = assess_quality(target, review)
    assert result['status'] == ('Fail' if bad else 'Pending') and result['total'] is None
    assert value['next_check']['purpose'] in render_quality_mapping(target)
    assert value['next_check']['decision_use'] in render_quality_mapping(target)
    assert target['process_context']['kind'] == 'not_supplied'
    review['reviewer'] = {'kind': 'assistant_development', 'reviewer_id': 'authored', 'version': 'example-v1'}
    review['facts'] = {'status': 'Pass', 'reason': '仅用于机械汇总测试的人工预置判断。', 'anchors': ['rationale']}
    for name in review['scores']:
        review['scores'][name] = {'score': 2, 'reason': '人工预置锚点，不是模型或本人评分。', 'anchors': ['next_check.purpose']}
    for row in review['roles']:
        row.update(role='alternative', reason='人工示意将该句视为尚未核实的可能解释。')
    assert assess_quality(target, review)['status'] == ('Fail' if bad else 'Pass')


def test_no_policy_can_be_reported_as_null_without_inventing_a_citation(evidence, scope, registry, profile):
    from graphrisk.agent.autonomous_evidence import build_evidence
    observations = tuple(o for o in evidence[0].observations if o.call.name != 'retrieve_policy')
    bundle = build_evidence(scope, observations, registry)
    packet = packet_for(scope, observations, registry, profile)
    value = good()
    value['policy'] = None
    errors, report = verify_judgment(JudgmentDraft.model_validate_json(canonical(value)), packet, bundle)
    assert errors == [] and report['judgment']['policy'] is None
    assert any(row['kind'] == 'tool' and row['state'] == 'not_requested' for row in gap_sources(bundle, packet).values())


def test_existing_tool_direction_remains_an_unexecuted_followup_plan(v3):
    packet, bundle = v3
    value = good()
    ref = next(r for r, fid in packet['refs_private'].items() if any(f.fact_id == fid and f.metric == 'history_amount_mean' for f in bundle.facts))
    value['next_check'] = {'text': 'history_stats', 'refs': [ref],
                           'purpose': '核对历史统计缺失原因，区分无历史和工具未提供。',
                           'decision_use': '帮助判断能否进行历史比较，不能把缺失当成正常。'}
    errors, report = verify_judgment(JudgmentDraft.model_validate_json(canonical(value)), packet, bundle)
    assert errors == []
    check = report['judgment']['next_check']
    assert check['state'] == 'planned_not_executed' and check['responsibility'] == 'tool_followup_requires_runtime_check'


def test_streamlit_v3_reading_rerun_does_not_call_a_provider(v3):
    from streamlit.testing.v1 import AppTest
    packet, bundle = v3
    _, report = verify_judgment(JudgmentDraft.model_validate_json(canonical(good())), packet, bundle)
    value = {'evidence': bundle.model_dump(mode='json'), 'turns': [{'task': 'risk_assessment', 'state': 'completed',
             'report': report, 'actual_model_calls': 1, 'actual_tool_calls': 0}]}
    app = AppTest.from_string('from graphrisk.demo.conversation_workbench import show_conversation\nimport streamlit as st\nshow_conversation(st.session_state["case"])')
    app.session_state['case'] = value
    app.run()
    assert not app.exception and not app.button
    assert any('核查目的' in item.value for item in app.text)
    app.run()
    assert not app.exception and not app.button


@pytest.mark.parametrize('contract', ['unknown', CONTRACT])
def test_service_rejects_invalid_contract_selection_before_touching_ledger(tmp_path, contract):
    from serve_investigation_conversations import make_service
    with pytest.raises(ValueError, match='narrative_contract_'):
        make_service(tmp_path, report_contract=contract)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('contract', ['program-facts-judgment-v2', CONTRACT])
def test_worker_cli_propagates_contract_and_uses_its_own_directory(tmp_path, monkeypatch, contract):
    from types import SimpleNamespace
    import serve_investigation_conversations as server
    suffix = 'native-v3' if contract == CONTRACT else 'native-v1'
    directory = tmp_path/'reports/generated/agent-investigation-upgrade/route5'/suffix/'workers'
    directory.mkdir(parents=True)
    request, response = directory/'request.json', directory/'response.json'
    request.write_text(canonical({'case': 'fixture', 'observations': [], 'calls': [{'tool': 'get_transaction', 'arguments': {}}]}), encoding='utf-8')
    recorded = []

    def make_service(runtime, **kwargs):
        recorded.append(kwargs)
        return SimpleNamespace(backend=SimpleNamespace(entries={'fixture': {}}, batch=lambda *args: ([], [])))

    monkeypatch.setattr(server, 'make_service', make_service)
    monkeypatch.setattr('sys.argv', ['serve', '--runtime', str(tmp_path), '--narrative', '--report-contract', contract,
                                   '--worker-request', str(request), '--worker-response', str(response)])
    server.main()
    assert recorded == [{'worker_mode': True, 'narrative': True, 'report_contract': contract}]
    assert json.loads(response.read_text(encoding='utf-8')) == {'observations': [], 'events': []}
