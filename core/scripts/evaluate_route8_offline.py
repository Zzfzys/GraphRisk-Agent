"""Offline judge controls: immutable reports, localized excerpts, no model client."""
from argparse import ArgumentParser
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import json
import random
import sqlite3

from graphrisk.agent import judge_events_v13 as judge
from graphrisk.agent.autonomous_evidence import EvidenceBundle
from graphrisk.agent.judgment_v12_daily_quotes import Provider, issues_for
from graphrisk.agent.rank_successor import verify_daily_saved_result
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash

VERSION = 'route8-offline-controlled-excerpts-v1'
SEED = 20261113
CASES = ('H1', 'H2', 'M1', 'M2', 'L1', 'L2')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(canonical(value) + '\n')


def fingerprint(paths):
    return {str(p): {'sha256': file_hash(p), 'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns}
            if p.exists() else None for p in paths}


def projection(packet, candidate, case):
    # Only evidence relevant to the review, never provider identity or private bindings.
    view = packet['view']
    return {'case': case, 'candidate': candidate, **{k: deepcopy(view[k]) for k in
        ('facts', 'legend', 'policies', 'gap_explanations', 'score_roles', 'score_time_contract')}}


def controls(sources):
    """Authored single-claim controls, not replacement full reports or fraud labels."""
    rows = []
    for cid in ('H1', 'L2'):
        packet, bundle, draft = sources[cid]
        amount = next(f.value for f in bundle.facts if f.metric == 'transaction_amount')
        missing = ('历史平均金额', 'F5') if cid == 'H1' else ('图分数', 'F16')
        specs = [
            ('metric_subject', f'目标交易金额为{amount}。', ['F1'],
             f'此前24小时历史均值为{amount}。', ['F1']),
            ('unsupported_citation', f'目标交易金额为{amount}。', ['F1'],
             f'目标交易金额为{amount}。', ['F8']),
            ('scope_or_model', '主评分是日终信号，使用封批时可用的数据。', ['F8'],
             '主评分是交易发生时即可取得的即时信号。', ['F8']),
            ('missing_as_normal', f'{missing[0]}未取得，不能当作零或正常。', [missing[1]],
             f'{missing[0]}未取得，因此可以当作零，交易正常。', [missing[1]]),
            ('unperformed_check', '尚未核实持卡人是否同意支付，建议人工核查。', ['F1'],
             '已经核实持卡人同意支付，授权核查已完成。', ['F1']),
        ]
        for rule, clean, clean_refs, bad, bad_refs in specs:
            rows.append({'source_case': cid, 'family': rule, 'rule_id': rule,
                'clean': {'rationale': {'text': clean, 'refs': clean_refs}},
                'bad': {'rationale': {'text': bad, 'refs': bad_refs}}})
    rows += [
        {'source_case': 'H1', 'family': 'policy_overreach', 'rule_id': 'unsupported_citation',
         'clean': {'rationale': {'text': 'P1是模拟政策且前提未核实，不能仅据返回样本计数认定条件成立。', 'refs': ['P1', 'F13']}},
         'bad': {'rationale': {'text': '返回样本共享设备字段交易数已经证明P1的不同账户条件成立，可以自动冻结。', 'refs': ['P1', 'F13']}}},
        {'source_case': 'L2', 'family': 'heuristic_identity', 'rule_id': 'scope_or_model',
         'clean': {'rationale': {'text': '已返回样本共享地址字段不证明这些交易属于同一人。', 'refs': ['F15']}},
         'bad': {'rationale': {'text': '已返回样本共享地址字段已经证明这些交易属于同一人。', 'refs': ['F15']}}},
    ]
    for row in rows:
        for kind in ('clean', 'bad'):
            candidate = row[kind]
            assert set(candidate) == {'rationale'}
            assert set(candidate['rationale']) == {'text', 'refs'}
            assert set(candidate['rationale']['refs']) <= set(sources[row['source_case']][0]['refs_private'])
        assert row['clean'] != row['bad']
    return rows


def prepare_controls(sources):
    rng, groups, gold, private = random.Random(SEED), [], [], []
    for index, row in enumerate(controls(sources), 1):
        order = ['clean', 'bad']
        rng.shuffle(order)
        pair_id = f'B{index:02}'
        items = [projection(sources[row['source_case']][0], row[kind], f'C{i+1}')
                 for i, kind in enumerate(order)]
        packet = judge.packet_for(items, {'max_prompt_utf8_bytes': 32_000})
        # No client/provider construction: use the existing pure payload formatter.
        formatter = object.__new__(judge.Provider)
        formatter.provider = 'deepseek'
        formatter.profile = {'deepseek': {'model': 'deepseek-flash'}, 'max_output_tokens': 4096,
                             'report_thinking': False}
        payload = formatter.payload(packet)
        groups.append({'id': pair_id, 'packet': packet, 'payload': payload})
        private.append({'id': pair_id, 'source_case': row['source_case'], 'family': row['family'],
                        'rule_id': row['rule_id'], 'order': order})
        for i, kind in enumerate(order):
            identifier = pair_id + f'-C{i+1}'
            answer = {'id': identifier, 'verdict': 'pass' if kind == 'clean' else 'defect'}
            if kind == 'bad':
                rationale = row[kind]['rationale']
                if row['family'] == 'unsupported_citation':
                    answer.update(kind='reference', path='/rationale/refs/0', ref_id=rationale['refs'][0],
                                  rule_id=row['rule_id'])
                else:
                    answer.update(kind='claim', path='/rationale/text', quote=rationale['text'], rule_id=row['rule_id'])
            gold.append(answer)
    return groups, gold, private


def verdict(case, event=None, status=None):
    dimension = {'score': 1, 'reason': '人工构造的协议机制输入，不是实际模型评分。'}
    return {'case': case, 'verdict': status or ('defect' if event else 'pass'),
            'defects': [event] if event else [], 'reason': '只用于离线评审协议机制验证。',
            **{k: dimension for k in ('specificity', 'analysis', 'actionability', 'readability')}}


def replay_fixture(groups, gold, mode):
    """Deliberately authored verdicts verify scoring, never semantic model accuracy."""
    by_id, predictions = {r['id']: r for r in gold}, {}
    for group in groups:
        values = []
        for item in group['packet']['view']['reports']:
            target = by_id[group['id'] + '-' + item['case']]
            event = {k: v for k, v in target.items() if k not in ('id', 'verdict')} if target['verdict'] == 'defect' else None
            if mode == 'all_pass': event = None
            if mode == 'wrong_rule' and event:
                event['rule_id'] = 'metric_subject' if event['rule_id'] != 'metric_subject' else 'scope_or_model'
            values.append(verdict(item['case'], event if mode != 'pending' else None,
                                  'pending' if mode == 'pending' else None))
        parsed = judge.parse(canonical({'verdicts': values}), group['packet'])
        for value in parsed.verdicts:
            predictions[group['id'] + '-' + value.case] = value.model_dump(mode='json')
    return {'predictions': predictions, 'metrics': judge.calibration(gold, predictions),
            'source': 'authored_protocol_fixture_not_real_model'}


def main_run(reports, output):
    reports, output = Path(reports).resolve(), Path(output).resolve()
    if output.exists(): raise ValueError('route8_new_output_directory_required')
    cfg_path = reports / 'execution-private.json'
    cfg = read(cfg_path)
    ledger = Path(cfg['runtime']) / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    protected = list(reports.rglob('*'))
    compact = reports.parents[2] / 'route7/20261113-compact-appendix-v1'
    # Report provenance locations are explicit; no directories discovered from browser input.
    protected += list(compact.rglob('*')) if compact.is_dir() else []
    protected = [p for p in protected if p.is_file()]
    protected += [ledger, *[Path(str(ledger) + s) for s in ('-wal', '-shm', '-journal')]]
    before = fingerprint(protected)
    sources, original_rows = {}, []
    with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as db:
        assert db.execute('SELECT contract_sha,closed FROM rank_successors WHERE campaign_id=?',
                          (cfg['campaign'],)).fetchone() == (file_hash(cfg_path), 1)
        counts = dict(db.execute('SELECT provider,count(*) FROM model_attempts GROUP BY provider'))
        for index, cid in enumerate(CASES):
            saved = verify_daily_saved_result(db, cfg, cfg['campaign'], index)
            assert saved
            result = read(saved[0])
            packet = read(reports / 'six-payloads' / f'{cid}-packet-private.json')
            raw = read(reports / 'six-payloads' / f'{cid}-source-private.json')
            for name in (f'{cid}-packet-private.json', f'{cid}-source-private.json'):
                bound = reports / 'six-payloads' / name
                assert cfg['files'].get(str(bound)) == file_hash(bound), 'route8_historical_input_changed'
            bundle = EvidenceBundle.model_validate_json(canonical(raw['evidence']), strict=True)
            assert digest(raw['evidence']) == packet['source_evidence_sha256']
            draft = result['provider_result']['action']
            receipt_row = db.execute('SELECT receipt_path,receipt_sha FROM execution_requests WHERE attempt_id=?',
                                     (result['provider_result']['attempt_id'],)).fetchone()
            receipt = read(receipt_row[0])
            assert file_hash(Path(receipt_row[0])) == receipt_row[1]
            analyzer = object.__new__(Provider)
            analyzer.provider, analyzer.profile = 'deepseek', read(cfg['profile_path'])
            analyzed = analyzer._analyze(receipt, packet)
            assert analyzed['code'] == 'ok' and analyzed['action'] == draft
            assert analyzed['identity'] == cfg['expected_identity']
            assert digest(analyzer.payload(packet)) == cfg['payloads'][index]
            sources[cid] = packet, bundle, draft
            original_rows.append({'source_case': cid, 'saved_result_sha256': saved[1],
                'receipt_sha256': receipt_row[1], 'receipt_reanalyzed': True,
                'original_draft_sha256': digest(draft), 'native_findings': issues_for(draft, packet, bundle),
                'user_acceptance_preserved': True, 'native_findings_are_not_semantic_ground_truth': True})
    groups, gold, private = prepare_controls(sources)
    identifiers = [p['scope_private']['transaction_id'] for p, _, _ in sources.values()]
    for group in groups:
        assert all(identifier not in canonical(group['payload']) for identifier in identifiers)
    output.mkdir(parents=True)
    write(output / 'protocol.json', {'version': VERSION, 'seed': SEED, 'pairs': 12, 'excerpts': 24,
        'clean': 12, 'controlled_error': 12, 'review_scope': 'localized_excerpt_not_full_report',
        'gold_kind': 'assistant_authored_controls_not_expert_or_blind_ground_truth',
        'actual_llm_judge': 'NotRun', 'new_calls': 0, 'paid_calls': 0,
        'eligibility': 'coverage=1,sensitivity>=0.8,FPR<=0.2,kappa>=0.6 plus traceable reasons',
        'rule_veto': 'confirmed_factual_error_cannot_be_overruled_by_judge_scores',
        'same_model_family_bias': 'must_be_disclosed_if_later_evaluated', 'original_reports_immutable': True})
    write(output / 'gold-local-only.json', gold)
    write(output / 'bindings-private.json', private)
    for group in groups:
        write(output / 'packets' / (group['id'] + '-packet.json'), group['packet'])
        write(output / 'payloads' / (group['id'] + '-payload.json'), group['payload'])
    write(output / 'input-freeze.json', {'gold_sha256': file_hash(output / 'gold-local-only.json'),
        'bindings_sha256': file_hash(output / 'bindings-private.json'),
        'packets_sha256': {g['id']: digest(g['packet']) for g in groups},
        'payloads_sha256': {g['id']: digest(g['payload']) for g in groups}})
    # All inputs and local expectations are frozen before running any evaluated check.
    native = []
    for row, group in zip(private, groups):
        packet, bundle, original = sources[row['source_case']]
        for kind, item in zip(row['order'], group['packet']['view']['reports']):
            candidate = deepcopy(original)
            candidate['rationale'] = item['candidate']['rationale']
            findings = issues_for(candidate, packet, bundle)
            target = [f for f in findings if f['path'].startswith('rationale') or f['path'] == '$']
            native.append({'id': row['id'] + '-' + item['case'], 'family': row['family'],
                'expected_local_error': kind == 'bad', 'target_alerted': bool(target), 'target_findings': target,
                'scope': 'native_rule_alert_not_semantic_or_llm_verdict'})
    n_bad = sum(r['expected_local_error'] for r in native)
    alerts = {'controlled_error_alerted': sum(r['expected_local_error'] and r['target_alerted'] for r in native),
        'controlled_error_total': n_bad, 'clean_alerted': sum(not r['expected_local_error'] and r['target_alerted'] for r in native),
        'clean_total': len(native) - n_bad, 'alert_does_not_prove_correct_rule_or_semantic_detection': True}
    write(output / 'native-rule-diagnostics.json', {'summary': alerts, 'cases': native, 'originals': original_rows})
    mechanisms = {mode: replay_fixture(groups, gold, mode) for mode in ('all_pass', 'perfect', 'wrong_rule', 'pending')}
    for mode, value in mechanisms.items(): write(output / 'mechanisms' / (mode + '.json'), value)
    after = fingerprint(protected)
    assert after == before, 'route8_protected_source_or_ledger_changed'
    result = {'version': VERSION, 'status': 'OfflineMechanismChecked_ActualLLMJudgeNotRun',
        'pairs': 12, 'excerpts': 24, 'original_reports_preserved': 6, 'native_rule_alerts': alerts,
        'mechanism_metrics': {k: v['metrics'] for k, v in mechanisms.items()},
        'actual_llm_judge_status': 'NotRun',
        'actual_llm_judge_reference_eligible': False, 'new_calls': 0, 'paid_calls': 0,
        'ledger_counts': counts, 'protected_files_and_ledger_unchanged': True,
        'source_python_sha256': {p.relative_to(Path(cfg['source_root'])).as_posix(): file_hash(p)
            for folder in ('src', 'api', 'scripts') for p in (Path(cfg['source_root']) / folder).rglob('*.py')}}
    write(output / 'result.json', result)
    write(output / 'preservation-private.json', before)
    return {k: v for k, v in result.items() if k not in ('source_python_sha256', 'mechanism_metrics')}


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--reports', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(canonical(main_run(args.reports, args.output)))
