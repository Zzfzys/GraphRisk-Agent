"""Offline pairing of six accepted drafts with two public-reference selections.

No provider is constructed, no receipt is authenticated and no ledger is opened.
Only the controlled execution caller may supply ``verified_results_by_query``
after verifying its closed campaign and original receipts. Gold and reading
reviews stay local and never enter the two outbound payloads.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re

from graphrisk.agent.public_references import PublicReferenceRetriever, load_reference_corpus
from graphrisk.agent.reference_answer_execution import ReferenceAnswerProvider
from graphrisk.agent.reference_answers import ReferenceAnswerService
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
from graphrisk.serving.daily_reference import CATALOG, CATALOG_VERSION, build_case_report_package


VERSION = 'daily-reference-paired-comparison-v1'
RENDER_VERSION = 'daily-reference-paired-render-v1'
CASES = ('H1', 'H2', 'M1', 'M2', 'L1', 'L2')
TASKS = {'Q1': 'risk_assessment', 'Q2': 'policy_conditions'}
DENOMINATORS = {'model_selections': 2, 'report_displays': 6, 'paired_readings': 6}


def _require(condition, code):
    if not condition:
        raise ValueError('daily_reference_comparison_' + code)


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _bytes(value):
    return canonical(value).encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _write_new(files):
    _require(all(not path.exists() for path in files), 'output_already_exists')
    for path, raw in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)


def _reading_template():
    return {'schema_version': 'daily-reference-paired-reading-v1',
            'denominator': 6, 'shared_unique_selections': 2,
            'comparison_choices': ['更好', '相同', '更差'],
            'cases': [{'case_id': key, 'status': 'Pending', 'comparison': None,
                       'next_check_clearer': None, 'reading_burden': None,
                       'misleading_case_fact': None, 'misleading_applicability_or_authority': None,
                       'notes': ''} for key in CASES]}


def _date(value):
    return date.fromisoformat(value).strftime('%Y年%m月%d日') if value else '未核实'


def _fence(text, language=''):
    fence = '`' * max(3, 1 + max((len(run) for run in re.findall(r'`+', text)), default=0))
    return fence + language + '\n' + text + '\n' + fence


def _appendix(case, packet, answer, selection_status):
    gaps = sorted({gap for row in packet['view']['references'] for gap in row['missing_facts']})
    lines = ['\n\n---\n\n## 独立参考资料附录（不改动以上原稿）',
        f"案件：{case['case_id']}；独立问题：{case['query_id']}；任务：{case['task']}",
        f"原稿全文 SHA256：{case['body_sha256']}",
        f"本案绑定 SHA256：{case['case_binding_sha256']}",
        f"原政策核查基准日：{_date(case['policy_as_of'])}；不据此认定资料当日有效。",
        f"问题：{packet['request']['query']}",
        f"检索状态：{packet['status']}；选择状态：{selection_status}。",
        '法域、支付工具、消费者账户性质、授权、获益及通知条件均未由本案材料核实。',
        '匿名卡片、设备字段和风险档不能补足这些条件；本附录不改变风险档，也不授予退款、冻结或拒付权限。',
        '条件缺口（来自检索元数据，不是已满足条件）：' + ('、'.join(gaps) or '未取得可检查的条件元数据。'),
        '原稿用户阅读已接受；原生检查问题及原 report=None 状态保留，不是新版正式门禁通过。']
    if answer is None:
        lines.append('尚未取得可展示的真实模型选择；不是已通过的回答或正确拒答。')
    else:
        lines += [answer['message'],
            f"模型选择：{answer['draft']['decision']}；理由：{answer['draft']['reason']}。",
            '完整节选与出处可重建核对；是否充分回答问题仍待独立阅读，不能等同本案规则适用。']
        for source in answer['sources']:
            lines += [f"### {source['ref']} · 所选完整资料块",
                f"资料抓取日：{_date(source['retrieved_on'])}；已核实生效起日：{_date(source['verified_from'])}；终日：{_date(source['verified_to'])}。",
                _fence(canonical({key: value for key, value in source.items() if key != 'text'}), 'json'),
                _fence(source['text'])]
        if not answer['sources']:
            lines.append('本选择未引用资料块；检索到背景材料不等于它们支持本案结论。')
    return ('\n\n'.join(lines) + '\n').encode('utf-8')


def _construct(accepted_root, output_root, profile_path, manifest_path, corpus_version):
    accepted_root, output_root = Path(accepted_root).resolve(), Path(output_root).resolve()
    profile_path, manifest_path = Path(profile_path).resolve(), Path(manifest_path).resolve()
    _require(not output_root.is_relative_to(accepted_root), 'original_materials_are_read_only')
    acceptance_path, audit_path = accepted_root / 'USER-READING-ACCEPTANCE.json', accepted_root / 'audit-final.json'
    old_manifest_path = accepted_root / 'six-payloads/review-manifest.json'
    acceptance, audit, old_manifest = map(_read, (acceptance_path, audit_path, old_manifest_path))
    _require(acceptance['user_reading_feedback'] == 'Accepted' and acceptance['cases'] == list(CASES)
             and acceptance['report_version'] == 'v12-daily-supported-quotes-v2'
             and acceptance['historical_machine_results_preserved'] is True
             and acceptance['execution_audit_sha256'] == file_hash(audit_path)
             and audit['closed'] is True and audit['generation'] == acceptance['report_version'], 'original_acceptance')
    selected = {item['review_case']: item for item in old_manifest['selected_cases']}
    _require(set(selected) == set(CASES), 'original_six_cases')
    corpus, _ = load_reference_corpus(manifest_path, corpus_version)
    service = ReferenceAnswerService(PublicReferenceRetriever(manifest_path, corpus_version))
    profile = _read(profile_path)
    _require(type(profile.get('max_prompt_utf8_bytes')) is int
             and 0 < profile['max_prompt_utf8_bytes'] <= 24_000, 'explicit_prompt_limit')
    formatter = object.__new__(ReferenceAnswerProvider)
    formatter.provider, formatter.profile = 'deepseek', profile
    inputs = [acceptance_path, audit_path, old_manifest_path, profile_path, manifest_path,
              *(manifest_path.parent / doc.snapshot_file for doc in corpus.documents)]
    files, queries, cases = {}, {}, []
    for index, key in enumerate(CASES):
        source_path = accepted_root / f'six-payloads/{key}-source-private.json'
        old_packet_path = accepted_root / f'six-payloads/{key}-packet-private.json'
        old_payload_path = accepted_root / f'six-payloads/{key}-payload.json'
        result_path = accepted_root / f'actual-first-drafts-v1/{key}-result-private.json'
        body_path = accepted_root / f'first-draft-reading/{key}.md'
        source, result, body = _read(source_path), _read(result_path), body_path.read_bytes()
        body.decode('utf-8')  # Preserve bytes, but reject a non-UTF8 reading artifact.
        scope, evidence, action = source['scope'], source['evidence'], result['provider_result']['action']
        old_packet = _read(old_packet_path)
        band = ('high', 'medium', 'low')[index // 2]
        _require(acceptance['accepted_result_sha256'][key] == file_hash(result_path)
                 and audit['artifacts'][str(body_path)] == _sha(body)
                 and selected[key]['scope_sha256'] == digest(scope)
                 and selected[key]['packet_sha256'] == digest(old_packet)
                 and selected[key]['payload_file_sha256'] == file_hash(old_payload_path)
                 and Path(selected[key]['payload_path']).resolve() == old_payload_path
                 and old_packet['source_scope_sha256'] == digest(scope)
                 and old_packet['source_evidence_sha256'] == digest(evidence)
                 and old_packet['source_observations_sha256'] == [digest(json.loads(row['result_json']))
                                                               for row in source['observations']]
                 and selected[key]['risk_level'] == band
                 and scope['case_alias'] == evidence['case_alias']
                 and scope['policy_as_of'] == evidence['policy_as_of'] == '2026-11-06'
                 and scope['scoring_context'] == evidence['scoring_context']
                 and scope['scoring_context']['risk_level'] == band
                 and result['case'] == key and result['first_draft'] is True
                 and result['provider_result']['code'] == 'ok' and result['report'] is None
                 and action['next_check']['text'] == 'authorization', 'original_case_binding')
        query_id = 'Q1' if index % 2 == 0 else 'Q2'
        task = TASKS[query_id]
        package = build_case_report_package(service, task=task, turn_id=digest([VERSION, key])[:32],
            case_alias=evidence['case_alias'], case_binding_sha256=digest(scope), evidence=evidence, report=None)
        _require(package['report_sha256'] is None and package['selection_status'] == 'not_evaluated', 'pending_package')
        packet = package['reference']['packet']
        request, payload = packet['request'], formatter.payload(packet)
        _require(request['context'] == {'as_of': '2026-11-06', 'jurisdiction': None,
                                        'payment_rail': None, 'facts': {}}
                 and packet['prompt_utf8_bytes'] <= profile['max_prompt_utf8_bytes'], 'outbound_context')
        if query_id not in queries:
            query = {'case_id': query_id, 'position': len(queries), 'task': task,
                     'question_id': CATALOG[task]['question_id'], 'consumed_by_cases': []}
            for name, value in (('request', request), ('packet', packet), ('payload', payload)):
                path, raw = output_root / f'queries/{query_id}-{name}.json', _bytes(value)
                query.update({name + '_path': str(path), name + '_sha256': digest(value),
                              name + '_file_sha256': _sha(raw)})
                files[path] = raw
            queries[query_id] = query
        query = queries[query_id]
        _require(all(query[name + '_sha256'] == digest(value) for name, value in
                     (('request', request), ('packet', packet), ('payload', payload))), 'deduplication_changed')
        query['consumed_by_cases'].append(key)
        a_path, pending_path = output_root / f'A/{key}.md', output_root / f'B-pending/{key}.md'
        case = {'case_id': key, 'query_id': query_id, 'task': task, 'case_alias': evidence['case_alias'],
                'policy_as_of': evidence['policy_as_of'], 'evidence_version': evidence['evidence_version'],
                'source_path': str(source_path), 'source_sha256': file_hash(source_path),
                'original_packet_path': str(old_packet_path), 'original_packet_sha256': file_hash(old_packet_path),
                'scope_sha256': digest(scope), 'evidence_sha256': digest(evidence),
                'result_path': str(result_path), 'result_sha256': file_hash(result_path),
                'action_sha256': digest(action), 'body_path': str(body_path), 'body_sha256': _sha(body),
                'model_risk_band': band, 'draft_risk_level': action['risk_level'],
                'native_guard_issues': result['native_guard_issues'], 'original_report': None,
                'original_human_quality_review': result['human_quality_review'],
                'user_reading_feedback': acceptance['user_reading_feedback'],
                'acceptance_sha256': file_hash(acceptance_path), 'audit_final_sha256': file_hash(audit_path),
                'request_sha256': digest(request), 'packet_sha256': digest(packet), 'payload_sha256': digest(payload),
                'a_path': str(a_path), 'a_sha256': _sha(body)}
        case['case_binding_sha256'] = digest(case)
        pending = body + _appendix(case, packet, None, 'not_evaluated')
        case.update(b_pending_path=str(pending_path), b_pending_sha256=_sha(pending))
        files[a_path], files[pending_path] = body, pending
        inputs.extend((source_path, old_packet_path, old_payload_path, result_path, body_path))
        cases.append(case)
    _require(len(queries) == len({item['payload_sha256'] for item in queries.values()}) == 2,
             'exactly_two_unique_requests')
    reading_path = output_root / 'reading-template.json'
    files[reading_path] = _bytes(_reading_template())
    manifest = {'schema_version': VERSION, 'accepted_root': str(accepted_root), 'output_root': str(output_root),
        'profile_path': str(profile_path), 'profile_sha256': file_hash(profile_path),
        'profile_canonical_sha256': digest(profile), 'manifest_path': str(manifest_path),
        'manifest_sha256': file_hash(manifest_path), 'corpus_version': corpus_version,
        'catalog_version': CATALOG_VERSION, 'catalog_sha256': digest(CATALOG),
        'acceptance_path': str(acceptance_path), 'acceptance_sha256': file_hash(acceptance_path),
        'audit_final_path': str(audit_path), 'audit_final_sha256': file_hash(audit_path),
        'denominators': dict(DENOMINATORS), 'queries': list(queries.values()), 'cases': cases,
        'reading_template_path': str(reading_path), 'reading_status': 'Pending',
        'model_selection_status': 'not_evaluated', 'paid_calls_in_preparation': 0,
        'original_report_status_preserved': True, 'formal_report_pass_claimed': False,
        'outbound_role': 'two_public_questions_and_complete_reference_blocks_only',
        'out_of_scope_real_case_evaluation': 'not_available_unknown_context',
        'files': {str(path): file_hash(path) for path in inputs}}
    manifest['files'].update({str(path): _sha(raw) for path, raw in files.items()})
    return manifest, files


def prepare_comparison(accepted_root, output_root, *, profile_path, manifest_path, corpus_version):
    """Write new local artifacts exclusively; never send, reserve or mutate originals."""
    manifest, files = _construct(accepted_root, output_root, profile_path, manifest_path, corpus_version)
    files[Path(manifest['output_root']) / 'paired-manifest.json'] = _bytes(manifest)
    _write_new(files)
    return manifest


def verify_comparison(path, *, expected_sha256=None):
    """Reconstruct local bindings. Execution callers must pin expected_sha256."""
    path = Path(path)
    _require(expected_sha256 is None or file_hash(path) == expected_sha256, 'manifest_changed')
    saved = _read(path)
    _require(saved.get('schema_version') == VERSION, 'manifest_version')
    expected, files = _construct(*(saved[key] for key in
        ('accepted_root', 'output_root', 'profile_path', 'manifest_path', 'corpus_version')))
    _require(saved == expected and path.resolve() == Path(saved['output_root']) / 'paired-manifest.json', 'manifest_binding')
    _require(all(Path(name).is_file() and file_hash(Path(name)) == sha for name, sha in saved['files'].items())
             and all(path.read_bytes() == raw for path, raw in files.items()), 'frozen_file_changed')
    return saved


def _rendered(manifest, output_root, verified_results_by_query):
    """The supplied mapping is not a proof of receipt authentication in this layer."""
    _require(set(verified_results_by_query) == {'Q1', 'Q2'}, 'two_caller_verified_results_required')
    output_root = Path(output_root).resolve()
    _require(not output_root.is_relative_to(Path(manifest['accepted_root']))
             and output_root != Path(manifest['output_root']), 'new_render_directory_required')
    service = ReferenceAnswerService(PublicReferenceRetriever(manifest['manifest_path'], manifest['corpus_version']))
    by_query = {item['case_id']: item for item in manifest['queries']}
    files, rows = {}, []
    for query_id, result in verified_results_by_query.items():
        query = by_query[query_id]
        _require(result['query_id'] == query_id and result['packet_sha256'] == query['packet_sha256']
                 and result['payload_sha256'] == query['payload_sha256']
                 and isinstance(result['execution_binding'], dict) and bool(result['execution_binding']), 'result_binding')
        answer = result['answer']
        if result['result_code'] == 'ok':
            _require(answer is not None and answer['draft'] is not None
                     and answer['packet_sha256'] == query['packet_sha256'], 'result_answer')
            service.verify_saved(answer)
        else:
            _require(answer is None, 'failed_result_has_answer')
    for case in manifest['cases']:
        result = verified_results_by_query[case['query_id']]
        packet = _read(by_query[case['query_id']]['packet_path'])
        body = Path(case['a_path']).read_bytes()
        status = result['answer']['status'] if result['answer'] else 'selection_failed:' + result['result_code']
        rendered = body + _appendix(case, packet, result['answer'], status)
        path = output_root / f"B/{case['case_id']}.md"
        files[path] = rendered
        rows.append({'case_id': case['case_id'], 'query_id': case['query_id'],
            'case_binding_sha256': case['case_binding_sha256'], 'body_sha256': case['body_sha256'],
            'b_path': str(path), 'b_sha256': _sha(rendered), 'body_byte_length': len(body),
            'answer_sha256': result['answer']['answer_sha256'] if result['answer'] else None,
            'selection_status': status, 'original_body_preserved': True})
    reading_path = output_root / 'reading-template.json'
    files[reading_path] = _bytes(_reading_template())
    rendered_manifest = {'schema_version': RENDER_VERSION, 'paired_manifest_sha256': file_hash(
        Path(manifest['output_root']) / 'paired-manifest.json'), 'output_root': str(output_root),
        'denominators': dict(DENOMINATORS), 'cases': rows, 'verified_results_by_query': verified_results_by_query,
        'selection_authentication': 'caller_supplied_receipt_authentication_not_performed_in_this_module',
        'semantic_support': 'requires_independent_reading', 'reading_status': 'Pending',
        'reading_template_path': str(reading_path), 'paid_calls_in_render': 0,
        'files': {str(path): _sha(raw) for path, raw in files.items()}}
    return rendered_manifest, files


def render_comparison(manifest_path, output_root, *, verified_results_by_query, expected_sha256):
    """Append full source blocks after receipt authentication by the execution caller."""
    manifest = verify_comparison(manifest_path, expected_sha256=expected_sha256)
    rendered, files = _rendered(manifest, output_root, verified_results_by_query)
    files[Path(rendered['output_root']) / 'rendered-manifest.json'] = _bytes(rendered)
    _write_new(files)
    return rendered


def evaluate_comparison(manifest_path, rendered_manifest_path=None, *, expected_sha256=None,
                        verified_results_by_query=None, query_reviews=None, readings=None):
    """Keep mechanical checks, two semantic reviews and six reading votes separate."""
    manifest = verify_comparison(manifest_path, expected_sha256=expected_sha256)
    result = {'schema_version': 'daily-reference-paired-evaluation-v1', 'denominators': dict(DENOMINATORS),
              'model': {'completed_valid': 0, 'expected_branch_matches': 0, 'semantic_review': 'Pending'},
              'display': {'body_and_binding_pass': 0},
              'reading': {'status': 'Pending', 'reviewed': 0, '更好': 0, '相同': 0, '更差': 0},
              'not_evaluated': ['fraud_recall', 'false_positive_rate', 'primary_draft_writing_improvement',
                                'real_case_out_of_scope_accuracy', 'new_formal_report_gate_pass']}
    if rendered_manifest_path is None:
        _require(verified_results_by_query is None and query_reviews is None and readings is None, 'render_required_before_review')
        return result
    _require(verified_results_by_query is not None, 'caller_verified_results_required')
    saved = _read(rendered_manifest_path)
    expected, files = _rendered(manifest, saved['output_root'], verified_results_by_query)
    _require(saved == expected and all(path.is_file() and path.read_bytes() == raw for path, raw in files.items()),
             'rendered_artifact_changed')
    result['display']['body_and_binding_pass'] = 6
    for key, row in verified_results_by_query.items():
        if row['result_code'] == 'ok':
            result['model']['completed_valid'] += 1
            draft = row['answer']['draft']
            expected_branch = ('cite_reference', 'reference_explanation') if key == 'Q1' else (
                'insufficient_evidence', 'missing_case_facts')
            result['model']['expected_branch_matches'] += (draft['decision'], draft['reason']) == expected_branch
    if query_reviews is not None:
        _require(set(query_reviews) == {'Q1', 'Q2'} and all(row['status'] in ('Pending', 'Pass', 'Fail')
                 and isinstance(row['notes'], str) for row in query_reviews.values()), 'query_review_fields')
        _require(all(row['status'] != 'Pass' or verified_results_by_query[key]['result_code'] == 'ok'
                     for key, row in query_reviews.items()), 'failed_selection_cannot_pass_review')
        result['model'].update(semantic_review='Completed' if all(row['status'] != 'Pending'
            for row in query_reviews.values()) else 'Pending',
            semantic_pass=sum(row['status'] == 'Pass' for row in query_reviews.values()))
    if readings is not None:
        _require(set(readings) == set(CASES), 'six_readings_required')
        for row in readings.values():
            _require(row['status'] in ('Pending', 'Reviewed') and isinstance(row['notes'], str), 'reading_fields')
            if row['status'] == 'Reviewed':
                _require(row['comparison'] in ('更好', '相同', '更差')
                    and type(row['next_check_clearer']) is bool
                    and type(row['reading_burden']) is int and 0 <= row['reading_burden'] <= 3
                    and type(row['misleading_case_fact']) is bool
                    and type(row['misleading_applicability_or_authority']) is bool, 'completed_reading_fields')
                result['reading']['reviewed'] += 1
                result['reading'][row['comparison']] += 1
            else:
                _require(all(row[key] is None for key in ('comparison', 'next_check_clearer', 'reading_burden',
                    'misleading_case_fact', 'misleading_applicability_or_authority')), 'pending_reading_has_verdict')
        result['reading']['status'] = 'Completed' if result['reading']['reviewed'] == 6 else 'Pending'
        result['reading']['misleading_case_fact_count'] = sum(row['misleading_case_fact'] is True for row in readings.values())
        result['reading']['misleading_applicability_or_authority_count'] = sum(
            row['misleading_applicability_or_authority'] is True for row in readings.values())
    return result
