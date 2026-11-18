"""Small fixture ledger and MockTransport only; no real model calls."""
from copy import deepcopy
from datetime import date
from pathlib import Path
import json
import shutil

import httpx
import pytest

from test_reference_boundary_execution import boundary_ready
from test_daily_reference_comparison_execution import comparison_ready, answer_source, write
from test_daily_reference_comparison import paired_ready
from test_rank_successor import ready
from test_autonomous_action_providers import profile, cloud_body
from graphrisk.agent import judge_control_execution as execution, rank_successor as rs
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import digest, file_hash
import run_judge_controls as cli

APPROVAL = 'Specific fixture approval for twelve new paired judgments, maximum five yuan'
PARENT_CHECK = execution.check_parent


class JudgeDay(date):
    @classmethod
    def today(cls): return cls(2026, 11, 14)


@pytest.fixture
def judge_ready(boundary_ready, monkeypatch):
    budget, store, old, old_path, tmp = boundary_ready
    monkeypatch.setattr(execution, 'check_parent', lambda db, cfg: None)
    for module in (rs, execution, cli): monkeypatch.setattr(module, 'date', JudgeDay)
    with budget.transaction() as db:
        for i in range(6):
            key = 'prior-boundary-' + str(i)
            db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)', (key, 'deepseek', 'settled', 1))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) '
                'VALUES(?,?,?,?,?)', (key, key, 'old-phase', digest(key), 1))
    cfg = deepcopy(old)
    cfg.update(version=rs.JUDGE_CONTROL_VERSION, campaign='judge-twelve', max_requests=12,
        deepseek_before=1426, attempts_before=1449, parent_campaign=old['campaign'],
        parent_contract_path=str(old_path), parent_contract_sha=file_hash(old_path),
        case_keys=execution.KEYS[:], cases=[], payloads=[], output=str(tmp / 'judge-results'),
        pricing_date='2026-11-14', judge_calls=12, report_generation_calls=0, secondary_judge_calls=0)
    p = execution.read(cfg['profile_path'])
    p['deepseek']['pricing_verified_date'] = '2026-11-14'
    cfg['profile_path'] = str(write(tmp / 'judge-profile.json', p))
    source = Path(cfg['source_root'])
    rel = 'src/graphrisk/agent/judge_events_v13.py'
    target = source / rel
    target.parent.mkdir(parents=True)
    shutil.copyfile(Path(execution.judge.__file__), target)
    cfg['source_python_sha256'][rel] = file_hash(target)
    cfg['files'][str(target)] = file_hash(target)
    freeze = {'packets_sha256': {}, 'payloads_sha256': {}}
    gold = []
    for i, key in enumerate(execution.KEYS):
        items = [{'case': case, 'candidate': {'rationale': {'text': key + '历史缺失，因此交易正常。' if case == 'C1'
                   else key + '历史缺失，不能判定安全。', 'refs': ['F1']}}} for case in ('C1', 'C2')]
        packet = execution.judge.packet_for(items, p)
        case = {'case_id': key, 'position': i,
            'packet_path': str(write(tmp / f'{key}-judge-packet.json', packet)),
            'payload_path': str(tmp / f'{key}-judge-payload.json')}
        packet, payload = execution.packet_payload(case, p)
        write(Path(case['payload_path']), payload)
        cfg['cases'].append(case)
        cfg['payloads'].append(digest(payload))
        freeze['packets_sha256'][key] = digest(packet)
        freeze['payloads_sha256'][key] = digest(payload)
        gold += [{'id': key + '-C1', 'verdict': 'defect', 'kind': 'claim', 'path': '/rationale/text',
                  'quote': items[0]['candidate']['rationale']['text'], 'rule_id': 'missing_as_normal'},
                 {'id': key + '-C2', 'verdict': 'pass'}]
        for field in ('packet_path', 'payload_path'): cfg['files'][case[field]] = file_hash(Path(case[field]))
    cfg['gold_path'] = str(write(tmp / 'local-judge-gold.json', gold))
    freeze['gold_sha256'] = file_hash(Path(cfg['gold_path']))
    cfg['offline_freeze_path'] = str(write(tmp / 'old-offline-freeze.json', freeze))
    cfg['offline_result_path'] = str(write(tmp / 'old-offline-result.json', {
        'version': 'route8-offline-controlled-excerpts-v1', 'pairs': 12, 'excerpts': 24,
        'new_calls': 0, 'source_python_sha256': {rel: file_hash(target)}}))
    for key in ('profile_path', 'gold_path', 'offline_freeze_path', 'offline_result_path'):
        cfg['files'][cfg[key]] = file_hash(Path(cfg[key]))
    path = write(tmp / 'judge-execution.json', cfg)
    return budget, store, cfg, path, tmp


def transport(posts, *, invalid_first=False, failure=None):
    def respond(request):
        posts.append(json.loads(request.content))
        if failure == 'unknown': raise httpx.ReadTimeout('fixture only', request=request)
        view = json.loads(posts[-1]['messages'][1]['content'])
        values = []
        for item in view['reports']:
            bad = item['case'] == 'C1'
            dim = {'score': 1, 'reason': '仅为机制测试，不是模型实测。'}
            values.append({'case': item['case'], 'verdict': 'defect' if bad else 'pass',
                'defects': [{'kind': 'claim', 'path': '/rationale/text', 'rule_id': 'missing_as_normal',
                    'quote': 'not in candidate' if invalid_first and len(posts) == 1 else
                        item['candidate']['rationale']['text']}] if bad else [],
                'reason': '人工构造首个评审响应。',
                **{name: dim for name in ('specificity', 'analysis', 'actionability', 'readability')}})
        return httpx.Response(200, json=cloud_body({'verdicts': values},
            fp='changed-fp' if failure == 'identity' else 'new-fp'))
    return httpx.MockTransport(respond)


def test_readonly_no_old_authorization_or_budget_creation(judge_ready, monkeypatch):
    budget, _, _, path, _ = judge_ready
    before = file_hash(budget.path)
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: pytest.fail('must remain read-only'))
    assert cli.run(path)['max_new_judgments'] == 12
    assert file_hash(budget.path) == before
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    with pytest.raises(ExecutionStop, match='new_explicit_approval_required'): cli.run(path, live=True)
    with budget.transaction() as db: old = db.execute('SELECT authorization FROM rank_successors LIMIT 1').fetchone()[0]
    with pytest.raises(ExecutionStop, match='old_registered_authorization'):
        cli.run(path, live=True, approval_note=old)


@pytest.mark.parametrize('key,value', [('max_requests', 13), ('max_requests', 12.0),
    ('cap_micro_cny', 5_000_001), ('deepseek_before', 1427), ('attempts_before', 1450),
    ('judge_calls', 0), ('secondary_judge_calls', 1), ('pricing_date', '2026-11-13')])
def test_exact_new_scope_cannot_widen_old_groups(judge_ready, key, value):
    _, store, cfg, _, tmp = judge_ready
    bad = deepcopy(cfg); bad[key] = value
    with pytest.raises(ExecutionStop): rs.read_contract(write(tmp / 'bad-judge.json', bad))
    assert rs.bounds({'version': rs.BOUNDARY_VERSION}) == (6, 5_000_000, rs.GROUP_BOUNDARY)
    assert rs.bounds({'version': rs.CASE_REFERENCE_VERSION}) == (2, 5_000_000, rs.GROUP_CASE_REFERENCE)
    with pytest.raises(ExecutionStop, match='invalid_judge_control_cap'):
        store.campaign('too-many', 'a'*64, cap_each=13, group_key=rs.GROUP_JUDGE_CONTROL)


def test_twelve_first_results_keep_invalid_denominator_and_close_without_resend(judge_ready, monkeypatch):
    budget, _, cfg, path, _ = judge_ready
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: budget)
    posts = []
    outcome = cli.run(path, live=True, approval_note=APPROVAL, transport=transport(posts, invalid_first=True))
    assert outcome['new_requests_this_run'] == len(posts) == 12
    assert execution.read(Path(cfg['output']) / 'B01-result.json')['provider_result']['code'] == 'invalid_model_action'
    evaluated = execution.evaluate_closed(path)
    assert evaluated['metrics']['pending_or_missing'] == 2 and evaluated['metrics']['tp'] == 11
    assert not evaluated['metrics']['reference_eligible']
    snap = budget.snapshot()
    assert (snap['deepseek_attempts'], snap['total_attempts'], snap['unresolved_attempts'], snap['integrity_stops']) == (1438, 1461, 3, 1)
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: pytest.fail('closed replay has no budget'))
    assert cli.run(path, live=True, approval_note=APPROVAL)['new_requests_this_run'] == 0
    assert len(posts) == 12 and budget.snapshot() == snap


@pytest.mark.parametrize('failure,code', [('unknown', 'unknowns_changed'), ('identity', 'stops_changed')])
def test_new_unknown_or_identity_change_stops_after_one_attempt(judge_ready, monkeypatch, failure, code):
    budget, _, _, path, _ = judge_ready
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: budget)
    posts = []
    with pytest.raises(ExecutionStop): cli.run(path, live=True, approval_note=APPROVAL, transport=transport(posts, failure=failure))
    assert len(posts) == 1 and budget.snapshot()['deepseek_attempts'] == 1427
    with pytest.raises(ExecutionStop, match=code): cli.run(path, live=True, approval_note=APPROVAL, transport=transport(posts))
    assert len(posts) == 1


def test_saved_verdict_cannot_replace_original_receipt(judge_ready, monkeypatch):
    budget, _, cfg, path, _ = judge_ready
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: budget)
    cli.run(path, live=True, approval_note=APPROVAL, transport=transport([]))
    with budget.transaction() as db:
        saved, _ = execution.verify_saved_result(db, cfg, cfg['campaign'], 0)
        value = execution.read(saved)
        value['provider_result']['action']['verdicts'][0]['reason'] = 'Changed plausible verdict'
        write(Path(saved), value)
        db.execute('UPDATE rank_successor_results SET result_sha=? WHERE campaign_id=? AND position=0',
            (file_hash(Path(saved)), cfg['campaign']))
        with pytest.raises(ExecutionStop, match='saved_result_binding'):
            execution.verify_saved_result(db, cfg, cfg['campaign'], 0)


@pytest.mark.parametrize('target', ['packet', 'gold'])
def test_rehashed_payload_or_local_gold_change_is_rejected(judge_ready, target):
    _, _, cfg, _, tmp = judge_ready
    p = Path(cfg['cases'][0]['packet_path'] if target == 'packet' else cfg['gold_path'])
    value = execution.read(p)
    if target == 'packet': value['view']['reports'][0]['candidate']['rationale']['text'] = 'new candidate'
    else: value[0]['rule_id'] = 'scope_or_model'
    write(p, value); cfg['files'][str(p)] = file_hash(p)
    with pytest.raises(ExecutionStop, match='packet_reconstruction' if target == 'packet' else 'gold_changed'):
        rs.read_contract(write(tmp / 'changed-judge.json', cfg))


def test_unregistered_parent_is_not_accepted_by_real_parent_checker(judge_ready):
    budget, _, cfg, _, _ = judge_ready
    with budget.transaction() as db:
        with pytest.raises(ExecutionStop, match='parent_binding'): PARENT_CHECK(db, cfg)
