"""Whole-report outbound scope, authenticated first responses and consumed approval."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import shutil

import httpx
import pytest
from test_autonomous_action_providers import profile, cloud_body
from test_rank_successor import ready
from test_daily_reference_comparison_execution import comparison_ready, answer_source
from test_daily_reference_comparison import paired_ready
from test_reference_boundary_execution import boundary_ready
from test_judge_control_execution import judge_ready
from test_daily_report_successor import write
from test_daily_report_judge import verdict
from test_judgment_v3 import good
from graphrisk.agent import daily_judge_execution as execution, rank_successor as rs
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import digest, file_hash
import run_daily_judge as cli

APPROVAL = 'Fixture explicit approval of eight new whole-narrative paired review payloads, five yuan'
PARENT_CHECK = execution.check_parent


@pytest.fixture
def whole_ready(judge_ready, monkeypatch):
    budget, store, old, old_path, tmp = judge_ready
    monkeypatch.setattr(execution, 'check_parent', lambda db, cfg: None)
    monkeypatch.setattr(cli, 'date', rs.date)
    with budget.transaction() as db:
        for i in range(12):
            key = 'prior-judge-' + str(i)
            db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)', (key, 'deepseek', 'settled', 1))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) '
                'VALUES(?,?,?,?,?)', (key, key, 'old-phase', digest(key), 1))
    cfg = deepcopy(old)
    cfg.update(version=rs.DAILY_JUDGE_VERSION, campaign='whole-eight', max_requests=8,
        deepseek_before=1438, attempts_before=1461, parent_campaign=old['campaign'],
        parent_contract_path=str(old_path), parent_contract_sha=file_hash(old_path),
        case_keys=execution.KEYS[:], cases=[], payloads=[], output=str(tmp / 'whole-results'), judge_calls=8)
    p = execution.read(cfg['profile_path'])
    cfg['profile_path'] = str(write(tmp / 'whole-profile.json', p))
    source = Path(cfg['source_root'])
    rel = 'src/graphrisk/agent/daily_report_judge.py'; target = source / rel
    target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(Path(execution.judge.__file__), target)
    cfg['source_python_sha256'][rel] = file_hash(target); cfg['files'][str(target)] = file_hash(target)
    freeze = {'packets_sha256': {}, 'payloads_sha256': {}}; gold = []
    for i, key in enumerate(execution.KEYS):
        candidate = good()
        candidate['rationale']['text'] = key + candidate['rationale']['text']
        refs = {x for path, x in execution.judge.leaves(candidate).items() if '/refs/' in path}
        facts = [[r, '人工证据，只检验机制'] for r in sorted(refs)]
        anchors = sorted([*execution.judge.leaves(candidate), *refs, 'G1', 'context'])
        roles = ['/' + n + '/' + str(j) + '/text' for n in ('counterevidence', 'hypotheses') for j in range(len(candidate[n]))]
        items = [{'case': c, 'candidate': candidate, 'evidence': {'facts': facts, 'policies': [],
            'gap_explanations': {'G1': '尚未核实支付同意'}}, 'anchors': anchors, 'role_paths': roles} for c in ('C1', 'C2')]
        packet = execution.judge.packet_for(items, p)
        case = {'case_id': key, 'position': i,
            'packet_path': str(write(tmp / f'{key}-whole-packet.json', packet)),
            'payload_path': str(tmp / f'{key}-whole-payload.json')}
        _, payload = execution.packet_payload(case, p); write(Path(case['payload_path']), payload)
        cfg['cases'].append(case); cfg['payloads'].append(digest(payload))
        freeze['packets_sha256'][key] = digest(packet); freeze['payloads_sha256'][key] = digest(payload)
        gold.append({'id': key, 'source_case': ('H1','H2','M1','M2','L1','L2','H1','M2')[i],
            'original_id': key + '-C1', 'control_id': key + '-C2', 'kind': 'fact' if i < 4 else 'quality',
            'path': '/rationale/text', 'quote': candidate['rationale']['text'],
            **({'rule_id': 'unperformed_check'} if i < 4 else {'dimension': execution.judge.DIMENSIONS[i-4]})})
        for field in ('packet_path', 'payload_path'): cfg['files'][case[field]] = file_hash(Path(case[field]))
    cfg['gold_path'] = str(write(tmp / 'whole-gold.json', gold)); freeze['gold_sha256'] = file_hash(Path(cfg['gold_path']))
    cfg['offline_freeze_path'] = str(write(tmp / 'whole-freeze.json', freeze))
    cfg['offline_result_path'] = str(write(tmp / 'whole-offline.json', {
        'version': 'route8-daily-whole-controls-v1', 'pairs': 8, 'whole_versions': 16,
        'new_calls': 0, 'source_python_sha256': {rel: file_hash(target)}}))
    for key in ('profile_path', 'gold_path', 'offline_freeze_path', 'offline_result_path'):
        cfg['files'][cfg[key]] = file_hash(Path(cfg[key]))
    return budget, store, cfg, write(tmp / 'whole-execution.json', cfg), tmp


def transport(posts, *, invalid_first=False, failure=None):
    def respond(request):
        posts.append(json.loads(request.content))
        if failure == 'unknown': raise httpx.ReadTimeout('fixture only', request=request)
        items = json.loads(posts[-1]['messages'][1]['content'])['reports']
        values = [verdict(item) for item in items]
        if invalid_first and len(posts) == 1: values[0]['scores']['analysis']['anchors'] = ['F999']
        return httpx.Response(200, json=cloud_body({'verdicts': values},
            fp='changed-fp' if failure == 'identity' else 'new-fp'))
    return httpx.MockTransport(respond)


def test_readonly_requires_new_approval_and_preserves_ledger(whole_ready, monkeypatch):
    budget, _, _, path, _ = whole_ready; before = file_hash(budget.path)
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: pytest.fail('read-only'))
    assert cli.run(path)['max_new_judgments'] == 8 and file_hash(budget.path) == before
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    with pytest.raises(ExecutionStop, match='explicit_approval'): cli.run(path, live=True)
    with budget.transaction() as db: old = db.execute('SELECT authorization FROM rank_successors LIMIT 1').fetchone()[0]
    with pytest.raises(ExecutionStop, match='old_registered_authorization'):
        cli.run(path, live=True, approval_note=old)


def test_eight_first_responses_keep_invalid_denominator_and_do_not_resend(whole_ready, monkeypatch):
    budget, _, cfg, path, _ = whole_ready
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1'); monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: budget)
    posts = []; result = cli.run(path, live=True, approval_note=APPROVAL, transport=transport(posts, invalid_first=True))
    assert result['new_requests_this_run'] == len(posts) == 8
    evaluation = execution.evaluate_closed(path)
    assert evaluation['coverage'] == 14/16 and not evaluation['numeric_development_gate']
    snap = budget.snapshot(); assert (snap['deepseek_attempts'], snap['total_attempts']) == (1446, 1469)
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: pytest.fail('closed no budget'))
    assert cli.run(path, live=True, approval_note=APPROVAL)['new_requests_this_run'] == 0
    assert len(posts) == 8 and budget.snapshot() == snap
    saved = execution.read(Path(cfg['output']) / 'W01-result.json')
    assert saved['provider_result']['code'] == 'invalid_model_action'


@pytest.mark.parametrize('failure,reason', [('unknown','unknowns_changed'), ('identity','stops_changed')])
def test_unknown_or_identity_change_stops_and_cannot_be_retried(whole_ready, monkeypatch, failure, reason):
    budget, _, _, path, _ = whole_ready
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1'); monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: budget)
    posts = []
    with pytest.raises(ExecutionStop): cli.run(path, live=True, approval_note=APPROVAL, transport=transport(posts, failure=failure))
    assert len(posts) == 1 and budget.snapshot()['deepseek_attempts'] == 1439
    with pytest.raises(ExecutionStop, match=reason): cli.run(path, live=True, approval_note=APPROVAL, transport=transport(posts))
    assert len(posts) == 1


@pytest.mark.parametrize('key,value', [('max_requests',9), ('cap_micro_cny',5_000_001),
    ('attempts_before',1462), ('judge_calls',0), ('repairs',1), ('pricing_date','2026-11-13')])
def test_new_scope_cannot_widen_old_groups(whole_ready, key, value):
    _, store, cfg, _, tmp = whole_ready
    bad = deepcopy(cfg); bad[key] = value
    with pytest.raises(ExecutionStop): rs.read_contract(write(tmp / 'bad-whole.json', bad))
    assert rs.bounds({'version':rs.JUDGE_CONTROL_VERSION}) == (12,5_000_000,rs.GROUP_JUDGE_CONTROL)
    with pytest.raises(ExecutionStop, match='invalid_daily_judge_cap'):
        store.campaign('too-many', 'a'*64, cap_each=9, group_key=rs.GROUP_DAILY_JUDGE)


def test_saved_high_scores_cannot_replace_raw_first_response(whole_ready, monkeypatch):
    budget, _, cfg, path, _ = whole_ready
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1'); monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: budget)
    cli.run(path, live=True, approval_note=APPROVAL, transport=transport([]))
    with budget.transaction() as db:
        saved, _ = execution.verify_saved_result(db, cfg, cfg['campaign'], 0)
        value = execution.read(saved); value['provider_result']['action']['verdicts'][0]['scores']['analysis']['score'] = 1
        write(Path(saved), value)
        db.execute('UPDATE rank_successor_results SET result_sha=? WHERE campaign_id=? AND position=0',
            (file_hash(Path(saved)), cfg['campaign']))
        with pytest.raises(ExecutionStop, match='saved_result_binding'):
            execution.verify_saved_result(db, cfg, cfg['campaign'], 0)


@pytest.mark.parametrize('target', ['packet','gold'])
def test_gold_and_candidates_cannot_change_after_freeze(whole_ready, target):
    _, _, cfg, _, tmp = whole_ready
    path = Path(cfg['gold_path'] if target == 'gold' else cfg['cases'][0]['packet_path']); value = execution.read(path)
    if target == 'gold': value[0]['rule_id'] = 'permission_overreach'
    else: value['view']['reports'][0]['candidate']['rationale']['text'] = 'changed only locally'
    write(path, value); cfg['files'][str(path)] = file_hash(path)
    with pytest.raises(ExecutionStop): rs.read_contract(write(tmp / 'tampered-whole.json', cfg))


def test_real_parent_checker_rejects_unregistered_parent(whole_ready):
    budget, _, cfg, _, _ = whole_ready
    with budget.transaction() as db:
        with pytest.raises(ExecutionStop, match='parent_binding'): PARENT_CHECK(db, cfg)
