"""Read-only by default; the two unique case-reference selections need new approval."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date
import os
from pathlib import Path
import sqlite3
import time

from graphrisk.agent import rank_successor as successor
from graphrisk.agent.autonomous_budget import InvestigationBudget
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.daily_reference_comparison_execution import (
    ReferenceAnswerProvider, answer_for, check, check_authorization, read, require,
    service_for, verified_results_by_query, verify_saved_result,
)
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash, load_contract


def _summary(results):
    return {'status': 'first_selections_saved_closed_and_verified', 'first_selections': len(results),
            'schema_valid': sum(result['provider_result']['code'] == 'ok' for result in results),
            'unique_model_selections': 2, 'paired_cases': 6, 'report_body_generations': 0,
            'automatic_retries': 0, 'repairs': 0, 'paid_judge_calls': 0,
            'semantic_support_review': 'Pending'}


def run(config_path, *, live=False, approval_note=None):
    cfg, preflight = check(config_path)
    if not live:
        return preflight
    if (os.getenv('GRAPHRISK_UPGRADE_LIVE') != '1' or not isinstance(approval_note, str)
            or len(approval_note.strip()) < 10):
        raise ValueError('explicit live flag, environment and new specific two-selection approval required')
    runtime, source = Path(cfg['runtime']), Path(cfg['source_root'])
    ledger = runtime / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    contract_sha = file_hash(Path(config_path))
    # Reject all old approvals before even constructing a live budget or SessionStore.
    with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as db:
        check_authorization(db, cfg, approval_note)
        registered = db.execute('SELECT contract_path,contract_sha,authorization,closed FROM rank_successors '
                                'WHERE campaign_id=?', (cfg['campaign'],)).fetchone()
        if registered:
            require(Path(registered[0]).resolve() == Path(config_path).resolve()
                    and registered[1:3] == (contract_sha, approval_note), 'approval_changed')
            if registered[3]:
                verified_results_by_query(db, cfg, contract_sha)
                return _summary([read(Path(cfg['output']) / (key + '-result.json'))
                                 for key in cfg['case_keys']])
    require(cfg['pricing_date'] == date.today().isoformat(), 'refresh_pricing_before_new_requests')
    budget = InvestigationBudget(ledger, load_contract(source / 'configs/agent_investigation_experiment.json'),
                                 mode='live')
    store = SessionStore(budget)
    if registered is None:
        store.campaign(cfg['campaign'], contract_sha, cap_each=2, group_key=successor.GROUP_CASE_REFERENCE)
        successor.register(budget, config_path, approval_note)
    output = Path(cfg['output'])
    output.mkdir(parents=True, exist_ok=True)
    profile, service, results = read(cfg['profile_path']), service_for(cfg), []
    for position, case in enumerate(cfg['cases']):
        saved_path = output / (case['case_id'] + '-result.json')
        with budget.transaction() as db:
            prior = verify_saved_result(db, cfg, cfg['campaign'], position)
        if prior:
            require(Path(prior[0]).resolve() == saved_path.resolve(), 'saved_path_changed')
            results.append(read(saved_path))
            continue
        require(not saved_path.exists(), 'unregistered_saved_result_requires_review')
        session = cfg['campaign'] + '-C' + str(position + 1)
        deadline = store.open(session, cfg['campaign'], {'campaign': cfg['campaign'], 'position': position,
                              'payload_sha': cfg['payloads'][position]}, 'deepseek', calls=1, seconds=600)
        packet = read(case['packet_path'])
        wrapper = {'profile_sha256': digest(profile), 'packets': [packet], 'session': session}
        phase = store.register_packet(session, 0, digest(wrapper), 'deepseek')
        provider = ReferenceAnswerProvider('deepseek', profile=profile, budget=budget, campaign=wrapper,
            campaign_sha=digest(wrapper), phase=phase, output=output / 'receipts', runtime=runtime,
            live=True, remaining=lambda: deadline - time.time())
        # Unknown responses and identity stops propagate immediately; there is no retry path.
        response = provider.generate('0')
        value = {'case_id': case['case_id'], 'provider_result': response,
                 'answer': answer_for(service, packet, response), 'first_draft': True,
                 'provider_receipt_bound': True, 'semantic_support_review': 'Pending',
                 'automatic_retries': 0, 'repairs': 0, 'paid_judge_calls': 0}
        with saved_path.open('x', encoding='utf-8') as target:
            target.write(canonical(value) + '\n')
        successor.record(budget, cfg['campaign'], position, response['attempt_id'], saved_path)
        with budget.transaction() as db:
            require(verify_saved_result(db, cfg, cfg['campaign'], position) is not None,
                    'new_saved_result_not_verified')
        results.append(value)
        print(canonical({'position': position + 1, 'total': 2, 'code': response['code']}), flush=True)
    successor.close(budget, cfg['campaign'])
    with budget.transaction() as db:
        verified_results_by_query(db, cfg, contract_sha)
    return _summary(results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--approval-note')
    args = parser.parse_args()
    print(canonical(run(args.config, live=args.live, approval_note=args.approval_note)))


if __name__ == '__main__':
    main()
