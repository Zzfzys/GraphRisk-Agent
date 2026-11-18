"""Read-only receipt-authenticated answer selection scoring; semantic reading stays pending."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sqlite3

from pydantic import ValidationError

from graphrisk.agent.public_references import load_reference_corpus
from graphrisk.agent.reference_answers import ReferenceAnswerDraft
from graphrisk.agent.reference_answer_execution import check, read, require, verify_saved_result
from graphrisk.agent.upgrade_contract import canonical, file_hash


def score_case(case: dict, saved: dict | None) -> dict:
    """Score labels/coverage only; this function does not authenticate model origin."""
    saved = saved if isinstance(saved, dict) else {}
    response = saved.get('provider_result') or {}
    answer = saved.get('answer')
    answer = answer if isinstance(answer, dict) else None
    code = response.get('code', 'no_response')
    draft = None
    if code == 'ok':
        try:
            draft = ReferenceAnswerDraft.model_validate_json(canonical(response.get('action')), strict=True)
        except (ValidationError, ValueError, TypeError):
            pass
    valid = draft is not None
    positive = case['kind'] == 'positive_reference_explanation'
    sources = answer.get('sources', []) if answer else []
    selected = {(source['doc_id'], source['section']) for source in sources}
    gold = {(source['doc_id'], source['section']) for source in case['gold_sections']}
    integrity = bool(valid and answer and answer.get('source_integrity') == 'verified_saved_excerpt')
    decision_match = bool(valid and draft.decision == case['expected_decision'])
    reason_match = bool(valid and draft.reason == case['expected_reason'])
    status_match = bool(answer and answer.get('status') == case['expected_status'])
    # Negative gold describes potentially relevant background, not citations a
    # correct refusal must select. Only positives require complete target selection.
    complete = bool(gold and gold <= selected) if positive else None
    target_pass = bool(valid and decision_match and reason_match and status_match and integrity
                       and (complete if positive else True))
    return {'case_id': case['case_id'], 'kind': case['kind'], 'question': case['query'],
            'purpose': case['purpose'], 'context': case['context'], 'provider_code': code,
            'expected_decision': case['expected_decision'], 'expected_reason': case['expected_reason'],
            'expected_status': case['expected_status'], 'gold_sections': case['gold_sections'],
            'first_draft': response.get('action'), 'schema_valid': valid,
            'decision_match': decision_match, 'reason_match': reason_match, 'status_match': status_match,
            'source_integrity_verified': integrity,
            'source_integrity_error': bool(valid and not integrity),
            'selected_gold_count': len(gold & selected), 'gold_count': len(gold),
            'positive_all_gold_selected': complete,
            'structural_target_pass': target_pass,
            'positive_over_refusal': bool(positive and valid and draft.decision != 'cite_reference'),
            'positive_incomplete_selection': bool(positive and valid and draft.decision == 'cite_reference' and not complete),
            'no_answer_wrong_citation': bool(case['kind'] == 'negative_no_answer' and valid and draft.decision == 'cite_reference'),
            'application_unsafe_citation': bool(case['purpose'] == 'transaction_application' and valid
                                                and draft.decision == 'cite_reference'),
            'scope_branch_mismatch': bool(case['kind'] == 'negative_scope_mismatch' and valid
                                          and not (decision_match and reason_match)),
            'missing_response': code in ('no_response', 'provider_connection_unknown'),
            'invalid_output': bool(not valid and code not in ('no_response', 'provider_connection_unknown')),
            'answer': answer, 'human_reading': 'Pending', 'semantic_correctness': 'NotEvaluated',
            'model_origin': 'not_authenticated_by_pure_scorer'}


def summarize(rows: list[dict]) -> dict:
    positive = [row for row in rows if row['kind'] == 'positive_reference_explanation']
    negative = [row for row in rows if row['kind'] != 'positive_reference_explanation']
    counts = {key + '_count': sum(bool(row[key]) for row in rows) for key in
              ('schema_valid', 'structural_target_pass', 'source_integrity_verified', 'source_integrity_error',
               'positive_over_refusal', 'positive_incomplete_selection', 'no_answer_wrong_citation',
               'application_unsafe_citation', 'scope_branch_mismatch', 'missing_response', 'invalid_output')}
    return {'case_count': len(rows), 'positive_count': len(positive), 'negative_count': len(negative), **counts,
            'positive_complete_target_count': sum(row['structural_target_pass'] for row in positive),
            'positive_complete_target_rate': sum(row['structural_target_pass'] for row in positive) / len(positive) if positive else None,
            'negative_expected_branch_count': sum(row['structural_target_pass'] for row in negative),
            'negative_expected_branch_rate': sum(row['structural_target_pass'] for row in negative) / len(negative) if negative else None,
            'denominator_policy': 'all_predeclared_cases_including_missing_or_invalid_output',
            'human_reading': 'Pending', 'semantic_correctness': 'NotEvaluated',
            'report_quality': 'NotRun', 'business_applicability': 'NotDetermined'}


def _load_cases(cfg: dict) -> tuple[list[dict], str]:
    path = Path(cfg['evaluation_cases_path'])
    require(cfg['files'].get(str(path)) == file_hash(path), 'evaluation_labels_not_bound')
    suite = read(path)
    require(suite.get('schema_version') == 'route7-answer-support-queries-v3'
            and suite.get('corpus_version') == cfg['corpus_version'] and suite.get('blind_test') is False,
            'evaluation_suite_scope')
    cases, public = suite['cases'], read(cfg['suite_path'])['cases']
    require(len(cases) == 12 and [case['case_id'] for case in cases] == cfg['case_keys']
            and [case['case_id'] for case in public] == cfg['case_keys'], 'evaluation_case_order')
    require(sum(case['kind'] == 'positive_reference_explanation' for case in cases) == 6,
            'evaluation_positive_count')
    _, chunks = load_reference_corpus(cfg['manifest_path'], cfg['corpus_version'])
    available = {(chunk['document'].doc_id, chunk['span'].section) for chunk in chunks}
    for case, request in zip(cases, public):
        require(all(case[key] == request[key] for key in ('query', 'context', 'purpose')),
                'evaluation_request_changed')
        require(case['kind'] in ('positive_reference_explanation', 'negative_no_answer',
                                'negative_case_undetermined', 'negative_scope_mismatch'), 'evaluation_kind')
        gold = {(source['doc_id'], source['section']) for source in case['gold_sections']}
        require(len(gold) == len(case['gold_sections']) and gold <= available, 'evaluation_gold')
        require(case['kind'] != 'positive_reference_explanation' or bool(gold), 'positive_needs_gold')
    return cases, file_hash(path)


def _block(text: str) -> str:
    fence = '`' * max(3, 1 + max((len(run) for run in re.findall(r'`+', text)), default=0))
    return fence + 'text\n' + text + '\n' + fence


def render_reading(report: dict) -> str:
    lines = ['# 真实首份回答选择：待人工阅读', '',
             '来源已通过共享账本、闭批与原收据校验。以下指标仅检查分支、引用完整性和预标目标覆盖；内容支持仍待阅读。', '',
             _block(canonical(report['summary'])), '']
    for row in report['cases']:
        lines += ['## ' + row['case_id'], '', _block(row['question']), '',
                  '首个选择（没有重试或修复）：', '', _block(canonical(row['first_draft'])), '',
                  f"provider_code={row['provider_code']}；结构目标通过={row['structural_target_pass']}；human_reading=Pending。", '']
        answer = row['answer']
        if not answer:
            lines += ['没有可核验的受控回答，不能算正确拒答。', '']
            continue
        lines += [answer['message'], '']
        selected_ids = {source['ref'] for source in answer['sources']}
        groups = [('所选完整服务器来源（不生成新摘要）', answer['sources']),
                  ('其余检索背景（不是答案支持，供核查拒答及遗漏）',
                   [source for source in answer['retrieved_sources'] if source['ref'] not in selected_ids])]
        for label, sources in groups:
            lines += [label + '：', '']
            if not sources:
                lines += ['无。', '']
            for source in sources:
                lines += [f"### {source['ref']} · {source['section']}", '',
                          f"[官方原网址]({source['source_url']})", '',
                          _block(canonical({key: source[key] for key in
                                ('doc_id', 'section', 'source_url', 'snapshot_sha256', 'content_sha256',
                                 'char_start', 'char_end', 'offset_basis', 'version_label')})), '',
                          _block(source['text']), '']
        lines += ['人工检查：所选正文是否回答原问，关键条件/例外是否完整，是否有无谓拒答或范围错用？', '']
    return '\n'.join(lines) + '\n'


def evaluate_completed(config_path: Path, output_dir: Path) -> dict:
    """No registration, live transport, budget mutation or alternate ledger is used."""
    output_dir = Path(output_dir)
    json_path, md_path = output_dir / 'scores.json', output_dir / 'READING.md'
    require(not json_path.exists() and not md_path.exists(), 'evaluation_output_exists')
    cfg, preflight = check(config_path)
    require(cfg['max_requests'] == 12 and preflight['used_in_batch'] == 12, 'evaluation_incomplete_batch')
    cases, suite_hash = _load_cases(cfg)
    ledger = Path(cfg['runtime']) / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    rows, provenance = [], []
    with sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True) as db:
        closed = db.execute('SELECT contract_sha,closed FROM rank_successors WHERE campaign_id=?',
                            (cfg['campaign'],)).fetchone()
        require(closed == (file_hash(config_path), 1), 'evaluation_closed_contract_binding')
        for position, case in enumerate(cases):
            saved = verify_saved_result(db, cfg, cfg['campaign'], position)
            require(saved is not None, 'evaluation_missing_paid_result')
            result = read(saved[0])
            row = score_case(case, result)
            row['model_origin'] = 'authenticated_shared_ledger_receipt'
            row['result_sha256'] = saved[1]
            rows.append(row)
            provenance.append({'case_id': case['case_id'], 'attempt_id': result['provider_result']['attempt_id'],
                               'result_sha256': saved[1], 'provider_code': result['provider_result']['code']})
    report = {'schema_version': 'route7-reference-answer-evaluation-v1', 'status': 'closed_batch_structurally_scored',
              'contract_sha256': file_hash(config_path), 'evaluation_suite_sha256': suite_hash,
              'public_suite_sha256': file_hash(Path(cfg['suite_path'])), 'corpus_version': cfg['corpus_version'],
              'evaluator_sha256': file_hash(Path(__file__)), 'model_provenance': provenance,
              'summary': summarize(rows), 'cases': rows, 'human_reading': 'Pending',
              'limits': ['These engineering-authored questions are visible to the team, not an independent blind test.',
                         'Human English translations and one preparation clarification isolate answer selection; they do not measure retrieval improvement.',
                         'Exact sources and selected gold coverage do not prove semantic entailment or complete useful answers.',
                         'Negative gold is reference background and is not required as a refusal citation.',
                         'The model chooses a bounded branch and complete excerpts, not free-form legal or fraud conclusions.',
                         'Invalid output retains the original denominator; no automatic fallback, repair or paid judge is used.']}
    reading = render_reading(report)
    output_dir.mkdir(parents=True, exist_ok=True)
    with json_path.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(canonical(report) + '\n')
    with md_path.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(reading)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_completed(args.config, args.output_dir)
    print(canonical({'status': report['status'], 'output_dir': str(args.output_dir), 'summary': report['summary']}))


if __name__ == '__main__':
    main()
