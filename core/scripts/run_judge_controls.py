"""Read-only by default. Live mode requires new approval of twelve frozen payloads."""
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
from graphrisk.agent.judge_control_execution import (
    check, check_authorization, evaluate_closed, judge, read, require, saved_value, verify_saved_result,
)
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash, load_contract


def run(config_path, *, live=False, approval_note=None, transport=None):
    cfg, preview = check(config_path)
    if not live: return preview
    require(os.getenv('GRAPHRISK_UPGRADE_LIVE') == '1' and isinstance(approval_note, str)
        and len(approval_note.strip()) >= 10, 'new_explicit_approval_required')
    ledger = Path(cfg['runtime']) / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    sha = file_hash(Path(config_path))
    with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as db:
        check_authorization(db, cfg, approval_note)
        registered = db.execute('SELECT contract_path,contract_sha,authorization,closed FROM rank_successors '
            'WHERE campaign_id=?', (cfg['campaign'],)).fetchone()
        if registered:
            require(Path(registered[0]).resolve() == Path(config_path).resolve()
                and registered[1:3] == (sha, approval_note), 'approval_changed')
            if registered[3]:
                return {'status': 'closed_verified_no_new_requests', 'saved_first_judgments': 12,
                        'new_requests_this_run': 0}
    require(date.today().isoformat() == cfg['pricing_date'], 'refresh_pricing_before_new_requests')
    budget = InvestigationBudget(ledger, load_contract(Path(cfg['source_root']) /
        'configs/agent_investigation_experiment.json'), mode='live')
    store = SessionStore(budget)
    if registered is None:
        store.campaign(cfg['campaign'], sha, cap_each=12, group_key=successor.GROUP_JUDGE_CONTROL)
        successor.register(budget, config_path, approval_note)
    output, profile = Path(cfg['output']), read(cfg['profile_path'])
    output.mkdir(parents=True, exist_ok=True)
    fresh = 0
    for i, case in enumerate(cfg['cases']):
        path = output / (case['case_id'] + '-result.json')
        with budget.transaction() as db:
            prior = verify_saved_result(db, cfg, cfg['campaign'], i)
        if prior: continue
        require(not path.exists(), 'unregistered_result_no_resend')
        session = cfg['campaign'] + '-C' + str(i + 1)
        deadline = store.open(session, cfg['campaign'], {'campaign': cfg['campaign'], 'position': i,
            'payload_sha': cfg['payloads'][i]}, 'deepseek', calls=1, seconds=600)
        packet = read(case['packet_path'])
        wrapper = {'profile_sha256': digest(profile), 'packets': [packet], 'session': session}
        phase = store.register_packet(session, 0, digest(wrapper), 'deepseek')
        provider = judge.Provider('deepseek', profile=profile, budget=budget, campaign=wrapper,
            campaign_sha=digest(wrapper), phase=phase, output=output / 'receipts',
            runtime=Path(cfg['runtime']), live=True, remaining=lambda: deadline - time.time(), transport=transport)
        response = provider.generate('0')
        with path.open('x', encoding='utf-8') as stream:
            stream.write(canonical(saved_value(case, response)) + '\n')
        successor.record(budget, cfg['campaign'], i, response['attempt_id'], path)
        with budget.transaction() as db:
            require(verify_saved_result(db, cfg, cfg['campaign'], i) is not None, 'new_result_binding')
        fresh += 1
        print(canonical({'position': i + 1, 'total': 12, 'case_id': case['case_id'], 'code': response['code']}), flush=True)
    successor.close(budget, cfg['campaign'])
    return {'status': 'twelve_first_judgments_closed', 'saved_first_judgments': 12,
        'new_requests_this_run': fresh, 'automatic_retries': 0, 'repairs': 0,
        'secondary_judge_calls': 0, 'semantic_reading': 'Pending'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--approval-note')
    parser.add_argument('--evaluate', action='store_true', help='Read-only, authenticated closed-batch scoring')
    args = parser.parse_args()
    if args.evaluate and args.live: parser.error('evaluate and live are separate actions')
    print(canonical(evaluate_closed(args.config) if args.evaluate else
                    run(args.config, live=args.live, approval_note=args.approval_note)))
