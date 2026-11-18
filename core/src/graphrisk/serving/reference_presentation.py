"""Short reading notes; full, unmodified evidence remains a separate artifact.

These are program-authored presentation notes, never new model answers. Callers
must authenticate the saved answer before using either presentation.
"""
from hashlib import sha256
from pathlib import Path

from graphrisk.agent.upgrade_contract import canonical, file_hash
from graphrisk.serving.daily_reference_comparison import (
    _fence, _write_new, evaluate_comparison, verify_comparison,
)


R1_CONTENT = '72dd48626a6fa25daf3fe348837d51f29479cd4d0ef15c8a86b4d02954d8abc2'
STATES = {'reference_excerpt': '可查看完整参考节选', 'insufficient_evidence': '资料或条件不足',
          'out_of_scope': '资料范围不符', 'conflicting_evidence': '可能存在冲突，待核对',
          'not_evaluated': '尚未评价，不是最终拒答', 'error': '资料不可用，不是无答案结论'}
RAILS = {'credit_card': '信用卡', 'eft': '电子资金转账'}


def summary_lines(answer):
    """Use a reviewed Chinese note only for its exact saved R1 content hash."""
    if answer is None:
        return ['尚未取得有效模型选择，不能把本轮记为已回答或正确拒答。']
    status = answer['status']
    lines = ['资料分支：' + STATES[status]]
    sources = answer.get('sources', [])
    if status == 'reference_excerpt':
        known = [s for s in sources if s.get('content_sha256') == R1_CONTENT
                 and sha256(s['text'].encode('utf-8')).hexdigest() == R1_CONTENT
                 and s.get('payment_rail') == 'credit_card']
        if known:
            lines += [
                '资料要点（程序整理，' + known[0]['ref'] + '）：信用卡发卡方拟就未授权使用申诉追责时，应合理调查，可合理要求配合；'
                '不能仅因缺少某一项材料自动拒绝。若因持卡人不配合特定请求导致信息不足、又无其他确认事实，资料允许合理终止调查。',
                '核查参考：可按适用场景核对购买习惯、收货地点、签名及授权关系；这些是可选方向，不表示本案已核实或已执行。']
            extras = [s for s in sources if s not in known]
            if extras:
                lines.append('其他所选背景：' + '、'.join(
                    s['ref'] + '（' + RAILS.get(s.get('payment_rail'), '适用场景待核实') + '）'
                    for s in extras) + '，与信用卡场景分开查阅。')
        else:
            lines.append(f'已选择{len(sources)}段参考资料；本页不自动概括未复核的条款，请按需查看完整原文。')
        lines.append('资料仅供参考；本案适用条件及历史有效期仍需核实，不改变原风险判断。')
    elif status == 'insufficient_evidence':
        reason = (answer.get('draft') or {}).get('reason')
        lines.append('个案条件不足，暂不能认定责任规则适用；仍需核实支付工具、适用地区和授权等事实。'
                     if reason == 'missing_case_facts' else
                     '当前资料不足以回答所问结论；不能以背景条款代替答案。')
        lines.append('资料不足不代表交易安全，也不改变原风险判断。')
    elif status == 'not_evaluated':
        lines.append('尚未完成本案问题回答选择；检索命中只是待核背景，不能当作回答依据。')
    else:
        lines.append({'out_of_scope': '所问条件与资料范围不符，需另核适用资料。',
                      'conflicting_evidence': '可能冲突的内容需分别核对，尚未确定优先适用关系。',
                      'error': '来源或检索暂不可用；这不是最终拒答或低风险结论。'}[status])
    return lines


def full_source_lines(source):
    """Keep the complete text and readable scope/URL separate from technical JSON."""
    return [source['ref'] + ' · ' + source['section'],
            '资料场景：' + RAILS.get(source.get('payment_rail'), '待核实') + '；仅供参考，未认定本案适用。',
            '官方来源：' + source['source_url'], '资料版本：' + source['version_label'],
            '采集日期：' + (source.get('retrieved_on') or '未注明') + '；生效区间：' + (
                '未核实' if source['date_status'] == 'unverified' else
                str(source.get('verified_from')) + ' 至 ' + str(source.get('verified_to'))),
            _fence(source['text'])]


def render_compact_comparison(paired_path, original_rendered_path, output_root, *,
                              verified_results_by_query, expected_sha256):
    """Authenticate receipts in the caller; recheck the old pair and every answer here."""
    paired = verify_comparison(paired_path, expected_sha256=expected_sha256)
    evaluate_comparison(paired_path, original_rendered_path, expected_sha256=expected_sha256,
                        verified_results_by_query=verified_results_by_query)
    output = Path(output_root).resolve()
    if any(output.is_relative_to(Path(paired[key]).resolve()) for key in ('accepted_root', 'output_root')):
        raise ValueError('compact_output_must_be_separate_from_original_materials')
    files, rows = {}, []
    for query_id, result in verified_results_by_query.items():
        answer = result['answer']
        full = ['# 完整参考资料 · ' + query_id,
                '以下为原模型选择的全部完整节选，未删改或改选；中文要点是程序整理，不能代替原文。']
        if answer is None:
            full.append('本次没有有效模型选择；未生成回答依据。')
        else:
            full.append('原核查问题：' + answer['question'])
            full.append('原核查基准日：' + answer['context']['as_of'] + '，不据此认定资料当日有效。')
            for source in answer['sources']:
                full.extend(full_source_lines(source))
            if not answer['sources']:
                full.append('本次选择未引用原文；完整选择记录见技术信息，不把未选背景冒充回答依据。')
        files[output / f'references/{query_id}.md'] = ('\n\n'.join(full) + '\n').encode('utf-8')
        files[output / f'technical/{query_id}.json'] = (canonical(result) + '\n').encode('utf-8')
    for case in paired['cases']:
        key, query_id = case['case_id'], case['query_id']
        body = Path(case['a_path']).read_bytes()
        appendix = '\n\n---\n\n## 参考资料简要说明\n\n' + '\n\n'.join(
            summary_lines(verified_results_by_query[query_id]['answer']))
        appendix += (f'\n\n[完整原文与出处](../references/{query_id}.md) · '
                     f'[技术信息](../technical/{query_id}.json)\n')
        path = output / f'C/{key}.md'
        files[path] = body + appendix.encode('utf-8')
        rows.append({'case_id': key, 'query_id': query_id, 'original_body_sha256': case['body_sha256'],
            'original_body_bytes': len(body), 'path': str(path),
            'appendix_characters': len(appendix), 'original_body_preserved': True})
    manifest = {'schema_version': 'daily-reference-compact-presentation-v1',
        'paired_manifest_sha256': expected_sha256,
        'original_rendered_manifest_sha256': file_hash(Path(original_rendered_path)),
        'presentation_only': True, 'model_selections_reused': 2, 'new_model_calls': 0,
        'selection_authentication': 'caller_verified_receipts_not_authenticated_in_this_module',
        'summary_kind': 'program_authored_note_for_exact_saved_source_hash',
        'summary_source_sha256': R1_CONTENT, 'cases': rows, 'user_reading': 'Pending',
        'files': {str(path): sha256(raw).hexdigest() for path, raw in files.items()}}
    files[output / 'compact-manifest.json'] = (canonical(manifest) + '\n').encode('utf-8')
    _write_new(files)
    return manifest
