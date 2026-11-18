"""Headless formal-report presentation; provider fixtures use local MockTransport."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from graphrisk.agent import public_references, reference_answers
from graphrisk.agent.narrative import run_narrative
from graphrisk.agent.upgrade_contract import digest
from graphrisk.demo import conversation_workbench as ui
from graphrisk.serving import daily_reference
from test_autonomous_tool_contracts import scope, index, registry
from test_autonomous_action_providers import profile
from test_daily_narrative import daily_scope, make_backend


BACKGROUND = 'Authored background only. A returned reference is not a supported answer or an applicable case finding.'


@pytest.fixture
def formal_value(tmp_path, daily_scope, index, registry, profile):
    backend, _, bundle, posts = make_backend(tmp_path, daily_scope, index, registry, profile)
    result = run_narrative(backend, daily_scope.case_alias, daily_scope.binding_sha256,
                           'formal-ui-mock-first-draft', bundle.evidence_version)
    assert result['report'] and result['report']['version'] == ui.DAILY_QUOTES_VERSION
    assert len(posts) == 1
    return {'conversation_id': 'formal-ui-conversation', 'case_alias': daily_scope.case_alias,
        'case_reference_binding_sha256': daily_scope.binding_sha256,
        'scoring_context': bundle.scoring_context.model_dump(mode='json'),
        'evidence': bundle.model_dump(mode='json'), 'turns': [
            {'turn_id': 'formal-ui-turn', 'task': 'risk_assessment', 'state': 'completed',
             'report': result['report'], 'actual_model_calls': 1, 'actual_tool_calls': 0}]}


@pytest.fixture
def package_view(formal_value, monkeypatch):
    value = deepcopy(formal_value)
    source = {'ref': 'R1', 'doc_id': 'AUTHORED-REFERENCE', 'section': 'authored-section',
        'text': BACKGROUND, 'source_url': 'https://www.consumerfinance.gov/rules-policy/regulations/1005/2/',
        'version_label': 'authored-version', 'snapshot_sha256': 'a' * 64, 'content_sha256': 'b' * 64,
        'char_start': 0, 'char_end': len(BACKGROUND), 'date_status': 'unverified',
        'offset_basis': 'unicode_code_points_in_saved_excerpt', 'missing_facts': ['effective_interval'],
        'unmet_conditions': []}
    value['turns'][0]['case_reference_root'] = {'fixture_root': True}
    value['turns'][0]['report_package'] = {'version': 'daily-case-reference-report-package-v1',
        'selection_status': 'not_evaluated', 'retrieval_status': 'ok',
        'reference': {'answer': {'question': '本轮待核的公开资料问题', 'status': 'not_evaluated',
                                'sources': [], 'retrieved_sources': [source]}}}
    calls = []
    service = object()

    def retriever(manifest, expected_version):
        assert (manifest, expected_version) == ('trusted-manifest.json', 'f' * 64)
        return 'trusted-retriever'

    def answer_service(given):
        assert given == 'trusted-retriever'
        return service

    def verify(given_value, given_turn, given_service):
        assert given_service is service
        assert given_value['case_reference_binding_sha256'] == value['case_reference_binding_sha256']
        assert given_turn['case_reference_root'] == {'fixture_root': True}
        calls.append(given_turn['turn_id'])
        return deepcopy(given_turn['report_package'])

    monkeypatch.setenv('GRAPHRISK_PUBLIC_REFERENCE_MANIFEST', 'trusted-manifest.json')
    monkeypatch.setenv('GRAPHRISK_PUBLIC_REFERENCE_VERSION', 'f' * 64)
    monkeypatch.setattr(public_references, 'PublicReferenceRetriever', retriever)
    monkeypatch.setattr(reference_answers, 'ReferenceAnswerService', answer_service)
    monkeypatch.setattr(daily_reference, 'verify_case_report_package', verify)
    return value, calls


def test_formal_quotes_dispatch_uses_its_shared_verifier_not_legacy_v3(formal_value, monkeypatch):
    original = deepcopy(formal_value)
    monkeypatch.setattr(ui, 'v3_lines', lambda *args: pytest.fail('quotes must not use the legacy v3 checker'))
    text = ui.readable_report(formal_value)
    assert '日终调查阅读版（模型原稿保持不变）' in text
    assert formal_value['turns'][0]['report']['judgment']['rationale']['text'] in text
    assert 'XGBoost 模型调查优先级（试用）：高' in text
    assert '本轮未调用大模型' not in text and '尚未生成 Agent 综合研判' not in text
    assert formal_value == original


def test_legacy_v3_still_dispatches_to_its_original_renderer(formal_value, monkeypatch):
    formal_value['turns'][0]['report']['version'] = 'separated-risk-report-v3'
    calls = []
    monkeypatch.setattr(ui, 'v3_lines', lambda report, evidence: calls.append(report) or ['legacy renderer retained'])
    monkeypatch.setattr(ui, 'daily_quotes_lines', lambda *args: pytest.fail('legacy is not the quotes contract'))
    assert 'legacy renderer retained' in ui.readable_report(formal_value)
    assert calls == [formal_value['turns'][0]['report']]


def test_pending_case_appendix_is_bound_and_combined_without_editing_report(package_view):
    value, calls = package_view
    original = deepcopy(value)
    turn = value['turns'][0]
    appendix = ui.case_reference_lines(value, turn)
    assert any('尚未完成本案问题回答选择' in line for line in appendix)
    assert any('待核背景' in line for line in appendix)
    assert BACKGROUND not in appendix
    assert BACKGROUND in ui.case_reference_lines(value, turn, details=True)
    text = ui.readable_report(value)
    assert '\n\n'.join(appendix) in text
    assert value == original and calls
    assert turn['report_package']['reference']['answer']['sources'] == []


@pytest.mark.parametrize('failure', ['unconfigured', 'rejected'])
def test_package_service_failure_hides_quotes_but_not_verified_main_report(package_view, monkeypatch, failure):
    value, _ = package_view
    if failure == 'unconfigured':
        monkeypatch.delenv('GRAPHRISK_PUBLIC_REFERENCE_MANIFEST')
        value['turns'][0]['report_package']['manifest_path'] = 'browser-supplied-manifest.json'
    else:
        monkeypatch.setattr(daily_reference, 'verify_case_report_package',
                            lambda *args: (_ for _ in ()).throw(ValueError('changed package binding')))
    text = ui.readable_report(value)
    assert '暂停展示包内引文' in text and BACKGROUND not in text
    assert value['turns'][0]['report']['judgment']['rationale']['text'] in text


def test_combined_daily_export_only_appends_the_displayed_report_turn_package(package_view):
    value, _ = package_view
    old = deepcopy(value['turns'][0])
    old['turn_id'] = 'older-turn'
    old['report_package']['reference']['answer']['question'] = '旧轮次核对问题，不属于最新正文'
    value['turns'].insert(0, old)
    text = ui.readable_report(value, include_reference_details=True)
    assert '本轮待核的公开资料问题' in text
    assert '旧轮次核对问题，不属于最新正文' not in text


def test_no_report_still_exposes_only_the_pending_reference_state(package_view):
    value, _ = package_view
    value['turns'][0]['report'] = None
    value['turns'][0]['outcome'] = 'rejected'
    text = ui.readable_report(value)
    assert '尚未生成 Agent 综合研判' in text and '尚未完成本案问题回答选择' in text
    assert '当前判断：' not in text


@pytest.mark.parametrize('tampered', [False, True])
def test_headless_refresh_and_download_never_post_and_hide_unverified_formal_text(package_view, monkeypatch, tampered):
    value, _ = package_view
    if tampered:
        report = value['turns'][0]['report']
        report['judgment']['rationale']['text'] = '伪造的正式报告全文不能显示'
        report['report_sha256'] = digest({key: item for key, item in report.items() if key != 'report_sha256'})
    original = deepcopy(value)
    requests = []

    def api(method, path, **kwargs):
        requests.append((method, path))
        assert method == 'GET', 'refresh/download must not invoke a report or model'
        return ({'batches': []} if path == '/api/v1/risk-batches' else deepcopy(value)), None

    monkeypatch.setattr(ui, 'api_call', api)
    app = AppTest.from_string('from graphrisk.demo.conversation_workbench import render_workbench\nrender_workbench()')
    app.session_state['conversation_view'] = deepcopy(value)
    app.run(timeout=15)
    assert not app.exception
    assert any(node.proto.label == '下载本轮公开资料附录' for node in app.get('download_button'))
    assert any('尚未完成本案问题回答选择' in node.value for node in app.text)
    if tampered:
        assert any('暂停展示研判全文' in node.value for node in app.text)
        assert '伪造的正式报告全文不能显示' not in ui.readable_report(value)
    else:
        assert any(value['turns'][0]['report']['judgment']['rationale']['text'] in node.value for node in app.text)
    assert not any(BACKGROUND in str(node.value) for node in app.json)
    assert not any('伪造的正式报告全文不能显示' in str(node.value) for node in app.json)
    next(button for button in app.button if button.label == '刷新已有会话').click().run(timeout=15)
    app.run(timeout=15)
    assert not app.exception and all(method == 'GET' for method, path in requests)
    assert value == original and app.session_state['conversation_view'] == original
