"""Presentation checks use authored records, not extra model calls or new gold."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from test_daily_reference_comparison import paired_ready, selected_results
from test_reference_answers import answer_source
from test_autonomous_action_providers import profile
from graphrisk.agent.upgrade_contract import file_hash
from graphrisk.serving import reference_presentation as presentation
from graphrisk.serving.daily_reference_comparison import render_comparison


def test_source_note_is_bound_to_exact_content_and_keeps_both_limits(monkeypatch):
    text = 'Authored whole saved fixture; never a real legal excerpt.'
    digest = sha256(text.encode()).hexdigest()
    monkeypatch.setattr(presentation, 'R1_CONTENT', digest)
    source = {'ref': 'R1', 'content_sha256': digest, 'text': text, 'payment_rail': 'credit_card'}
    answer = {'status': 'reference_excerpt', 'sources': [source,
        {'ref': 'R2', 'content_sha256': 'a'*64, 'text': 'EFT background', 'payment_rail': 'eft'}]}
    short = '\n'.join(presentation.summary_lines(answer))
    assert '程序整理' in short and '拟就未授权使用申诉追责' in short
    assert '不能仅因缺少某一项材料自动拒绝' in short and '允许合理终止调查' in short
    assert 'R2（电子资金转账）' in short and '分开查阅' in short
    assert '不表示本案已核实或已执行' in short and '历史有效期仍需核实' in short
    source['text'] += ' replaced'
    assert '资料要点' not in '\n'.join(presentation.summary_lines(answer))
    source['text'], source['payment_rail'] = text, 'eft'
    assert '资料要点' not in '\n'.join(presentation.summary_lines(answer))


@pytest.mark.parametrize('status,reason,expected', [
    ('insufficient_evidence', 'missing_case_facts', '个案条件不足'),
    ('insufficient_evidence', 'no_answer_in_sources', '当前资料不足以回答'),
    ('out_of_scope', 'scope_mismatch', '所问条件与资料范围不符'),
    ('conflicting_evidence', 'unresolved_conflict', '尚未确定优先适用关系'),
    ('not_evaluated', None, '尚未完成本案问题回答选择'),
    ('error', None, '不是最终拒答或低风险结论')])
def test_distinct_answer_states_are_not_hidden_or_called_success(status, reason, expected):
    answer = {'status': status, 'sources': [], 'draft': {'reason': reason} if reason else None}
    assert expected in '\n'.join(presentation.summary_lines(answer))
    assert '已回答或正确拒答' in '\n'.join(presentation.summary_lines(None))
    assert '完整节选与出处可重建' not in '\n'.join(presentation.summary_lines(answer))


def compact_ready(paired_ready):
    paired, path, service = paired_ready
    results = selected_results(paired, service)
    old = path.parent.parent / 'old-rendered'
    render_comparison(path, old, verified_results_by_query=results, expected_sha256=file_hash(path))
    return paired, path, results, old / 'rendered-manifest.json'


def test_compact_copy_preserves_six_bodies_and_all_selected_sources_and_metadata(paired_ready):
    paired, path, results, old = compact_ready(paired_ready)
    protected = {str(p): p.read_bytes() for p in path.parent.rglob('*') if p.is_file()}
    output = path.parent.parent / 'compact'
    before = deepcopy(results)
    manifest = presentation.render_compact_comparison(path, old, output,
        verified_results_by_query=results, expected_sha256=file_hash(path))
    assert manifest['new_model_calls'] == 0 and manifest['user_reading'] == 'Pending'
    assert len(manifest['cases']) == 6 and results == before
    for row, original in zip(manifest['cases'], paired['cases']):
        raw = Path(row['path']).read_bytes()
        body = Path(original['a_path']).read_bytes()
        assert raw[:len(body)] == body
        appendix = raw[len(body):].decode('utf-8')
        assert '../references/' + row['query_id'] + '.md' in appendix
        assert '../technical/' + row['query_id'] + '.json' in appendix
        assert 'SHA256' not in appendix and 'missing_case_facts' not in appendix
        assert row['appendix_characters'] < 400
    for key, result in results.items():
        full = (output / f'references/{key}.md').read_text('utf-8')
        assert json.loads((output / f'technical/{key}.json').read_text('utf-8')) == result
        for source in result['answer']['sources']:
            assert source['text'] in full and source['source_url'] in full
        if not result['answer']['sources']:
            assert '本次选择未引用原文' in full
    assert all(Path(name).read_bytes() == raw for name, raw in protected.items())
    with pytest.raises(ValueError, match='output_already_exists'):
        presentation.render_compact_comparison(path, old, output,
            verified_results_by_query=results, expected_sha256=file_hash(path))


@pytest.mark.parametrize('mutation', ['receipt_mapping', 'body', 'source', 'inside_original'])
def test_changed_inputs_do_not_generate_a_plausible_short_appendix(paired_ready, mutation):
    paired, path, results, old = compact_ready(paired_ready)
    output = path.parent.parent / 'compact'
    if mutation == 'receipt_mapping':
        results['Q1']['packet_sha256'] = results['Q2']['packet_sha256']
    elif mutation == 'body':
        target = Path(paired['cases'][0]['a_path'])
        target.write_bytes(b'changed')
    elif mutation == 'source':
        results['Q1']['answer']['sources'][0]['text'] = 'changed but plausible'
    else:
        output = path.parent / 'new-inside-old'
    with pytest.raises(ValueError):
        presentation.render_compact_comparison(path, old, output,
            verified_results_by_query=results, expected_sha256=file_hash(path))
    assert not output.exists()
