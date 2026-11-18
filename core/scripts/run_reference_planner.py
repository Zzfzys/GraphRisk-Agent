"""Read-only by default; send exact public query-planning payloads only with scoped approval."""
from __future__ import annotations
import argparse
from datetime import date
import os
from pathlib import Path
import time

from graphrisk.agent import rank_successor as successor
from graphrisk.agent.autonomous_budget import InvestigationBudget
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.reference_planner_execution import ReferencePlannerProvider, check, read, require, verify_saved_result
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash, load_contract


def run(config_path, *, live=False, approval_note=None):
    cfg, preflight = check(config_path)
    if not live:
        return preflight
    if os.getenv('GRAPHRISK_UPGRADE_LIVE') != '1' or not isinstance(approval_note, str) or len(approval_note.strip()) < 10:
        raise ValueError('explicit live flag, environment and scoped query approval note required')
    if cfg['pricing_date'] != date.today().isoformat():
        raise ValueError('refresh official pricing and freeze a new contract before any new live request')
    runtime, source = Path(cfg['runtime']), Path(cfg['source_root'])
    budget = InvestigationBudget(runtime / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3',
                                 load_contract(source / 'configs/agent_investigation_experiment.json'), mode='live')
    store = SessionStore(budget)
    with budget.transaction() as db:
        registered = db.execute('SELECT contract_path,contract_sha,authorization,closed FROM rank_successors '
                                'WHERE campaign_id=?', (cfg['campaign'],)).fetchone()
    if registered is None:
        store.campaign(cfg['campaign'], file_hash(config_path), cap_each=cfg['max_requests'], group_key=successor.bounds(cfg)[2])
        successor.register(budget, config_path, approval_note)
    else:
        require(Path(registered[0]).resolve() == Path(config_path).resolve()
                and registered[1:3] == (file_hash(config_path), approval_note), 'approval_changed')
    output = Path(cfg['output'])
    output.mkdir(parents=True, exist_ok=True)
    profile = read(cfg['profile_path'])
    results = []
    for position, item in enumerate(cfg['cases']):
        saved_path = output / (item['case_id'] + '-result.json')
        with budget.transaction() as db:
            prior = verify_saved_result(db, cfg, cfg['campaign'], position)
        if prior:
            require(Path(prior[0]) == saved_path, 'saved_path_changed')
            results.append(read(saved_path))
            continue
        require(registered is None or registered[3] == 0, 'closed_missing_result_no_resend')
        require(not saved_path.exists(), 'unregistered_saved_result_requires_review')
        session = cfg['campaign'] + '-C' + str(position + 1)
        deadline = store.open(session, cfg['campaign'], {'campaign': cfg['campaign'], 'position': position,
                              'payload_sha': cfg['payloads'][position]}, 'deepseek', calls=1, seconds=600)
        packet = read(item['packet_path'])
        wrapper = {'profile_sha256': digest(profile), 'packets': [packet], 'session': session}
        phase = store.register_packet(session, 0, digest(wrapper), 'deepseek')
        provider = ReferencePlannerProvider('deepseek', profile=profile, budget=budget, campaign=wrapper,
                    campaign_sha=digest(wrapper), phase=phase, output=output / 'receipts', runtime=runtime,
                    live=True, remaining=lambda: deadline - time.time())
        response = provider.generate('0')
        value = {'case_id': item['case_id'], 'provider_result': response, 'first_plan': True,
                 'automatic_retries': 0, 'repairs': 0, 'paid_judge_calls': 0}
        with saved_path.open('x', encoding='utf-8') as target:
            target.write(canonical(value) + '\n')
        successor.record(budget, cfg['campaign'], position, response['attempt_id'], saved_path)
        results.append(value)
        print(canonical({'position': position + 1, 'total': cfg['max_requests'], 'code': response['code']}), flush=True)
    if registered is None or not registered[3]:
        successor.close(budget, cfg['campaign'])
    return {'status': 'plans_saved', 'first_plans': len(results), 'valid_plans': sum(r['provider_result']['code'] == 'ok' for r in results),
            'automatic_retries': 0, 'repairs': 0, 'answer_quality': 'NotRun'}


def evaluate_completed(config_path):
    """Authenticate all settled receipts before passing plans to the offline scorer."""
    import sqlite3
    from evaluate_public_references import evaluate
    cfg, preflight = check(config_path)
    require(preflight['used_in_batch'] == cfg['max_requests'], 'incomplete_batch')
    ledger = Path(cfg['runtime']) / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    plans, provenance, unknown_cases = {}, [], set()
    with sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT contract_sha,closed FROM rank_successors WHERE campaign_id=?', (cfg['campaign'],)).fetchone()
        require(row == (file_hash(config_path), 1), 'completed_contract_binding')
        for i, case_id in enumerate(cfg['case_keys']):
            saved = verify_saved_result(db, cfg, cfg['campaign'], i)
            require(saved is not None, 'missing_paid_result')
            value = read(saved[0])['provider_result']
            plans[case_id] = value['action'] if value['code'] == 'ok' else None
            provenance.append({'case_id': case_id, 'attempt_id': value['attempt_id'],
                               'result_sha256': saved[1], 'code': value['code'],
                               'transport_status': 'response_received', 'receipt_verified': True})
        if cfg['version'] == successor.PLANNER_CONTINUATION_VERSION:
            # check() binds the closed parent and its sole unresolved transport attempt.
            parent = read(cfg['parent_contract_path'])
            case_id = parent['case_keys'][0]
            require(case_id not in plans and len(plans) + 1 == len(parent['case_keys']), 'continuation_case_coverage')
            state = db.execute('SELECT a.state,r.result_code FROM model_attempts a '
                               'JOIN execution_requests r USING(attempt_id) WHERE a.attempt_id=?',
                               (cfg['prior_unknown_attempt_id'],)).fetchone()
            require(state == ('unknown', 'provider_connection_unknown'), 'parent_unknown_state')
            plans[case_id] = None
            unknown_cases.add(case_id)
            provenance.insert(0, {'case_id': case_id, 'attempt_id': cfg['prior_unknown_attempt_id'],
                                 'result_sha256': None, 'code': 'provider_connection_unknown',
                                 'transport_status': 'response_unknown', 'receipt_verified': False,
                                 'parent_contract_sha256': cfg['parent_contract_sha']})
    output = Path(cfg['output']).parent / 'evaluation'
    output.mkdir(exist_ok=True)
    reports = []
    for split in ('development', 'frozen'):
        report = evaluate(Path(cfg['manifest_path']), Path(cfg['suite_path']), split, 'llm', plans)
        for case in report['cases']:
            case['transport_status'] = 'response_unknown' if case['case_id'] in unknown_cases else 'response_received'
            if case['case_id'] in unknown_cases:
                case['error_code'] = 'transport_unknown'
                case['error_source'] = 'provider_transport'
                case['error_message'] = 'The original attempt has no confirmed provider response; it was not retried.'
                case['plan_status'] = 'response_unknown'
        unknown_count = sum(case['case_id'] in unknown_cases for case in report['cases'])
        report['summary']['plan_error_count'] -= unknown_count
        report['summary']['model_invalid_plan_count'] = report['summary']['plan_error_count']
        report['summary']['transport_unknown_count'] = unknown_count
        report['summary']['model_response_case_count'] = len(report['cases']) - unknown_count
        report['summary']['pipeline_scope'] = 'all_original_authorized_cases_in_split'
        # These are the same per-case scores, with an explicitly different denominator.
        response_rows = [case for case in report['cases'] if case['case_id'] not in unknown_cases]
        response_positive = [case for case in response_rows if case['retrieval_kind'] == 'relevant']
        score_keys = [*(f'{metric}_at_{k}' for k in (1, 3, 5) for metric in ('hit', 'recall', 'all_gold')),
                      'reciprocal_rank']
        report['summary']['model_response_only'] = {
            'scope': 'confirmed_response_cases_only_not_the_full_pipeline',
            'case_count': len(response_rows), 'relevant_count': len(response_positive),
            'no_answer_count': sum(case['retrieval_kind'] == 'no_answer' for case in response_rows),
            'excluded_transport_unknown_case_ids': sorted(unknown_cases & {case['case_id'] for case in report['cases']}),
            **{key: round(sum(case[key] for case in response_positive) / len(response_positive), 6)
               if response_positive else None for key in score_keys}}
        report['summary']['llm_planner'] = 'authenticated_shared_ledger_receipts'
        report['model_provenance'] = {'contract_file_sha256': file_hash(config_path), 'receipts': provenance,
                                     'actual_paid_attempts': cfg['max_requests'] + len(unknown_cases),
                                     'actual_provider_attempts': cfg['max_requests'] + len(unknown_cases),
                                     'confirmed_model_responses': cfg['max_requests'],
                                     'transport_unknown_count': len(unknown_cases),
                                     'unknown_attempts_are_not_confirmed_billing': True,
                                     'automatic_retries': 0, 'repairs': 0}
        report['limits'].append('Full-pipeline metrics retain the original unknown attempt as a zero-recall case; '
                                'response-only scores have a different denominator and do not measure transport reliability. '
                                'Response-only comparisons must exclude the same case IDs from every baseline arm. '
                                'An unresolved transport attempt is not an invalid model plan or a confirmed billed response.')
        report['frozen_baseline_reports'] = cfg['baseline_reports']
        target = output / (split + '-llm.json')
        with target.open('x', encoding='utf-8') as handle:
            handle.write(canonical(report) + '\n')
        reports.append({'split': split, 'summary': report['summary'], 'output': str(target)})
    return {'status': 'authenticated_plans_evaluated', 'reports': reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--approval-note')
    parser.add_argument('--evaluate', action='store_true', help='score a closed scoped batch without new requests')
    args = parser.parse_args()
    if args.evaluate and (args.live or args.approval_note):
        parser.error('--evaluate cannot request live execution or approval')
    print(canonical(evaluate_completed(args.config) if args.evaluate else run(args.config, live=args.live, approval_note=args.approval_note)))


if __name__ == '__main__':
    main()
