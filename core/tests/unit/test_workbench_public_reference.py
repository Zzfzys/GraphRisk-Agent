"""Small mocked sidecars test presentation; real receipt verification has separate tests."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from graphrisk.agent import reference_answer_cache
from graphrisk.demo import conversation_workbench as ui


QUOTE = ('Authored complete reference: do not automatically deny solely for a missing report. '
         'However, lack of other confirming facts may permit ending review. <script>inert fixture</script>')
URL = 'https://www.consumerfinance.gov/rules-policy/regulations/1026/12/'
TRUSTED_CACHE = 'trusted-deployment-reference-cache.json'


@pytest.fixture
def value():
    return {'conversation_id': 'test-conversation', 'case_alias': 'authored-case',
        'reference_case_binding_sha256': 'c' * 64,
        'evidence': {'facts': [], 'policies': [], 'evidence_version': 'authored-evidence'},
        'turns': [{'task': 'investigate', 'state': 'completed', 'actual_model_calls': 0, 'actual_tool_calls': 0,
                   'report': {'version': 'authored-unrecognized-report', 'report_sha256': 'unchanged-report-hash'}}],
        'public_reference': {'version': 'daily-public-reference-sidecar-v1',
            'case_binding_sha256': 'c' * 64,
            'context_role': 'public_question_scope_not_case_facts',
            'reference_provenance': {'reviewer_type': 'independent_assistant_reading',
                'reading_status': 'acceptable_for_reference', 'user_acceptance': 'Pending'},
            'answer': {'question': 'Fixed public question about a missing report.', 'status': 'reference_excerpt',
                'message': '完整服务器节选；不作个案责任结论。', 'purpose': 'reference_explanation',
                'context': {'as_of': '2026-11-12', 'facts': {}},
                'semantic_support': 'requires_review', 'source_integrity': 'verified_saved_excerpt',
                'sources': [{'ref': 'R1', 'doc_id': 'AUTHORED-ONLY', 'section': 'authored-comment',
                    'text': QUOTE, 'source_url': URL, 'version_label': 'authored-version',
                    'snapshot_sha256': 'a' * 64, 'content_sha256': 'b' * 64,
                    'char_start': 12, 'char_end': 12 + len(QUOTE),
                    'offset_basis': 'unicode_code_points_in_saved_excerpt', 'date_status': 'unverified',
                    'missing_facts': ['effective_interval'], 'unmet_conditions': []}]}}}


@pytest.fixture
def checked(monkeypatch):
    calls = []

    def verify(value, cache_path):
        calls.append(cache_path)
        assert cache_path == TRUSTED_CACHE
        assert value['reference_case_binding_sha256'] == value['public_reference']['case_binding_sha256']
        return deepcopy(value['public_reference'])

    monkeypatch.setenv('GRAPHRISK_PUBLIC_REFERENCE_CACHE', TRUSTED_CACHE)
    monkeypatch.setattr(reference_answer_cache, 'verify_reference_sidecar', verify)
    return calls


def daily(value):
    ctx = {'temporal_scope': 'relative_day_end', 'relative_day': 2, 'batch_end_second': 259200,
        'available_through_second': 259200, 'batch_id': 'authored-day-2', 'revision': 1,
        'model_version': 'authored-daily-xgb', 'risk_level': 'high', 'score': .8,
        'calibrated_probability': False}
    value['scoring_context'] = deepcopy(ctx)
    value['evidence']['scoring_context'] = deepcopy(ctx)
    return value


def test_absent_reference_keeps_existing_output_and_does_not_verify(value, checked):
    del value['public_reference']
    assert ui.public_reference_lines(value) == []
    assert '公开资料参考附录' not in ui.readable_report(value)
    assert checked == []


def test_browser_path_is_not_a_cache_configuration_and_unverified_quotes_stay_hidden(value, monkeypatch):
    value['public_reference']['cache_path'] = 'browser-supplied-path.json'
    monkeypatch.delenv('GRAPHRISK_PUBLIC_REFERENCE_CACHE', raising=False)
    monkeypatch.setattr(reference_answer_cache, 'verify_reference_sidecar',
                        lambda *a, **k: pytest.fail('must not use a browser-supplied cache path'))
    lines = ui.public_reference_lines(value)
    assert '暂停展示' in ''.join(lines) and QUOTE not in ''.join(lines)
    assert QUOTE not in ui.readable_report(value)


def test_display_and_both_export_returns_append_only_verified_complete_sources(value, checked):
    for view in (deepcopy(value), daily(deepcopy(value))):
        original = deepcopy(view)
        base = {key: deepcopy(item) for key, item in view.items() if key != 'public_reference'}
        baseline = ui.readable_report(base)
        lines = ui.public_reference_lines(view, details=True)
        exported = ui.readable_report(view, include_reference_details=True)
        assert exported == baseline + '\n\n' + '\n\n'.join(lines)
        for expected in (QUOTE, URL, 'authored-comment', 'authored-version', 'a' * 64, 'b' * 64,
                         'Unicode字符位置：[12,', '固定公开问题', '本案已核实事实',
                         '仍须结合具体问题阅读核对', '已核对保存节选与绑定记录',
                         '不新增模型调用', '用户验收仍待单独确认'):
            assert expected in exported
        for raw_code in ('public_question_scope_not_case_facts', 'requires_review', 'verified_saved_excerpt',
                         'unicode_code_points_in_saved_excerpt', 'effective_interval'):
            assert raw_code not in exported
        assert '用户验收通过' not in exported and view == original
    assert checked and all(path == TRUSTED_CACHE for path in checked)


@pytest.mark.parametrize('status,label', [
    ('insufficient_evidence', '资料或条件不足'), ('out_of_scope', '资料范围不符'),
    ('not_evaluated', '尚未评价'), ('error', '资料不可用'), ('conflicting_evidence', '可能存在冲突')])
def test_reference_state_is_separate_from_the_model_band(value, checked, status, label):
    daily(value)
    value['public_reference']['answer'].update(status=status, sources=[])
    assert any('资料分支：' in line and label in line for line in ui.public_reference_lines(value))
    assert '模型调查优先级（试用）：高' in ui.readable_report(value)
    assert QUOTE not in ui.readable_report(value)


def test_rerun_revalidates_and_does_not_reuse_a_previously_accepted_quote(value, checked, monkeypatch):
    assert QUOTE in ui.readable_report(value, include_reference_details=True)

    def changed(*args, **kwargs):
        raise ValueError('authored fixture: source or receipt changed')

    monkeypatch.setattr(reference_answer_cache, 'verify_reference_sidecar', changed)
    text = ui.readable_report(value)
    assert '暂停展示' in text and QUOTE not in text
    assert QUOTE not in ui.readable_report(value, include_reference_details=True)


def test_default_reading_is_short_and_complete_sources_remain_available(value, checked):
    original = deepcopy(value)
    summary = ui.readable_report(value)
    full = ui.readable_report(value, include_reference_details=True)
    assert '资料分支：可查看完整参考节选' in summary
    assert '完整原文和技术信息可按需查看' in summary
    assert QUOTE not in summary and 'a' * 64 not in summary and URL not in summary
    assert QUOTE in full and URL in full and 'a' * 64 in full
    assert value == original and len(checked) >= 2


@pytest.mark.parametrize('verified', [True, False])
def test_workbench_refresh_only_gets_and_never_mutates_report_or_leaks_raw_sidecar(value, checked, monkeypatch, verified):
    original = deepcopy(value)
    requests = []

    def api(method, path, **kwargs):
        requests.append((method, path))
        if method != 'GET':
            pytest.fail('refresh or reference display must not submit or invoke a model')
        return ({'batches': []} if path == '/api/v1/risk-batches' else deepcopy(value)), None

    if not verified:
        monkeypatch.setattr(reference_answer_cache, 'verify_reference_sidecar',
                            lambda *a, **k: (_ for _ in ()).throw(ValueError('changed reference')))
    monkeypatch.setattr(ui, 'api_call', api)
    app = AppTest.from_string('from graphrisk.demo.conversation_workbench import render_workbench\nrender_workbench()')
    app.session_state['conversation_view'] = deepcopy(value)
    app.run(timeout=10)
    assert not app.exception
    assert any(node.proto.label == '下载公开资料参考附录' for node in app.get('download_button'))
    details = next(node for node in app.expander if node.label == '查看完整原文与技术信息（公开资料）')
    assert details.proto.expanded is False
    summary = next(node for node in app.expander if node.label == '公开资料参考附录')
    assert summary.proto.expanded is True
    text = '\n'.join(node.value for node in app.text)
    assert (QUOTE in text) is verified
    assert not any(QUOTE in str(node.value) for node in app.json)
    assert not any(QUOTE in str(node.value) for node in app.markdown)
    next(button for button in app.button if button.label == '刷新已有会话').click().run(timeout=10)
    assert not app.exception
    app.run(timeout=10)
    assert not app.exception and all(method == 'GET' for method, path in requests)
    assert app.session_state['conversation_view'] == original and value == original
