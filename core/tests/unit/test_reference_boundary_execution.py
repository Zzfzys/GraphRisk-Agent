"""Mock HTTP only: six new slots cannot reopen old approvals or retry failures."""
from copy import deepcopy
from pathlib import Path
import json

import httpx
import pytest

from test_daily_reference_comparison_execution import comparison_ready, write, ExecutionDay
from test_daily_reference_comparison import paired_ready
from test_rank_successor import ready
from test_autonomous_action_providers import profile, cloud_body
from test_daily_reference_comparison_execution import answer_source
from graphrisk.agent import rank_successor as rs, reference_boundary_execution as execution
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import file_hash, digest
import run_reference_boundary as cli


APPROVAL = 'New explicit fixture approval for six frozen boundary probes, maximum five yuan'


@pytest.fixture
def boundary_ready(comparison_ready, monkeypatch):
    budget, store, old, old_path, tmp = comparison_ready
    monkeypatch.setattr(execution, 'check_parent', lambda db, cfg: None)
    monkeypatch.setattr(execution, 'date', ExecutionDay)
    monkeypatch.setattr(cli, 'date', ExecutionDay)
    with budget.transaction() as db:
        for key in ('closed-prior-first', 'closed-prior-second'):
            db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)', (key, 'deepseek', 'settled', 1))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) '
                       'VALUES(?,?,?,?,?)', (key, key, 'old-phase', digest(key), 1))
    cfg = deepcopy(old)
    cfg.update(version=rs.BOUNDARY_VERSION, campaign='boundary-six', max_requests=6,
        deepseek_before=1420, attempts_before=1443, parent_campaign=old['campaign'],
        parent_contract_path=str(old_path), parent_contract_sha=file_hash(old_path),
        case_keys=execution.CASE_KEYS[:], cases=[], payloads=[], output=str(tmp / 'boundary-results'))
    for i, (key, kind) in enumerate(zip(execution.CASE_KEYS, execution.KINDS)):
        query = 'Authored review report missing fixture ' + key
        request = {'query': query}
        if kind == 'answer':
            request.update(context={'as_of': '2026-11-06'}, purpose='reference_explanation',
                top_k=5, planner='rules', supplied_plan=None, version_label=None)
        case = {'case_id': key, 'position': i, 'kind': kind, 'data_kind': 'public_reference',
            'manifest_path': cfg['manifest_path'], 'corpus_version': cfg['corpus_version'],
            'request_path': str(write(tmp / f'{key}-request.json', request)),
            'packet_path': str(tmp / f'{key}-packet.json'), 'payload_path': str(tmp / f'{key}-payload.json')}
        packet, payload = execution.prepare_case(case, execution.read(cfg['profile_path']))
        write(Path(case['packet_path']), packet)
        write(Path(case['payload_path']), payload)
        cfg['cases'].append(case)
        cfg['payloads'].append(digest(payload))
        for field in ('request_path', 'packet_path', 'payload_path'):
            cfg['files'][case[field]] = file_hash(Path(case[field]))
    path = write(tmp / 'boundary-execution.json', cfg)
    return budget, store, cfg, path, tmp


def mock_transport(posts, *, invalid_first=False, failure=None):
    def respond(request):
        payload = json.loads(request.content)
        posts.append(payload)
        if failure == 'unknown':
            raise httpx.ReadTimeout('fixture timeout', request=request)
        view = json.loads(payload['messages'][1]['content'])
        action = {'queries': ['wrong question' if invalid_first and len(posts) == 1 else
                              view['original_question']]} if 'public_source_catalog' in view else {
            'decision': 'cite_reference', 'reason': 'reference_explanation', 'citation_ids': ['R1']}
        return httpx.Response(200, json=cloud_body(action, fp='changed-fp' if failure == 'identity' else 'new-fp'))
    return httpx.MockTransport(respond)


def test_readonly_and_old_approval_cannot_construct_a_live_budget(boundary_ready, monkeypatch):
    budget, _, _, path, _ = boundary_ready
    before = file_hash(budget.path)
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: pytest.fail('no live budget'))
    assert cli.run(path)['max_new_requests'] == 6
    assert file_hash(budget.path) == before
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    with pytest.raises(ExecutionStop, match='new_explicit_approval_required'):
        cli.run(path, live=True)
    with budget.transaction() as db:
        prior = db.execute('SELECT authorization FROM rank_successors LIMIT 1').fetchone()[0]
    with pytest.raises(ExecutionStop, match='old_registered_authorization'):
        cli.run(path, live=True, approval_note=prior)


@pytest.mark.parametrize('key,value', [('max_requests', 7), ('max_requests', 6.0),
    ('cap_micro_cny', 5_000_001), ('deepseek_before', 1421), ('attempts_before', 1444),
    ('pricing_date', '2026-11-12')])
def test_allocation_is_exact_and_prior_scopes_stay_fixed(boundary_ready, key, value):
    _, store, cfg, _, tmp = boundary_ready
    bad = deepcopy(cfg)
    bad[key] = value
    with pytest.raises(ExecutionStop):
        rs.read_contract(write(tmp / 'bad.json', bad))
    assert rs.bounds({'version': rs.CASE_REFERENCE_VERSION}) == (2, 5_000_000, rs.GROUP_CASE_REFERENCE)
    assert rs.bounds({'version': rs.ANSWER_VERSION}) == (12, 5_000_000, rs.GROUP_REFERENCE_ANSWER)
    with pytest.raises(ExecutionStop, match='invalid_reference_boundary_cap'):
        store.campaign('bad-scope', 'a'*64, cap_each=7, group_key=rs.GROUP_BOUNDARY)


def test_six_mixed_first_results_close_and_replay_without_retry(boundary_ready, monkeypatch):
    budget, _, cfg, path, _ = boundary_ready
    posts = []
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: budget)
    result = cli.run(path, live=True, approval_note=APPROVAL,
                     transport=mock_transport(posts, invalid_first=True))
    assert len(posts) == result['new_requests_this_run'] == 6
    with budget.transaction() as db:
        assert db.execute('SELECT closed FROM rank_successors WHERE campaign_id=?', (cfg['campaign'],)).fetchone() == (1,)
        assert all(execution.verify_saved_result(db, cfg, cfg['campaign'], i) for i in range(6))
    first = execution.read(Path(cfg['output']) / 'P0-result.json')
    assert first['provider_result']['code'] == 'invalid_model_action' and first['selection'] is None
    snap = budget.snapshot()
    assert (snap['deepseek_attempts'], snap['total_attempts']) == (1426, 1449)
    assert (snap['unresolved_attempts'], snap['integrity_stops']) == (3, 1)
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: pytest.fail('closed result replay is read-only'))
    assert cli.run(path, live=True, approval_note=APPROVAL)['new_requests_this_run'] == 0
    assert len(posts) == 6 and budget.snapshot() == snap


@pytest.mark.parametrize('failure,code', [('unknown', 'unknowns_changed'), ('identity', 'stops_changed')])
def test_new_unknown_or_identity_change_stops_without_resending(boundary_ready, monkeypatch, failure, code):
    budget, _, _, path, _ = boundary_ready
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: budget)
    posts = []
    with pytest.raises(ExecutionStop):
        cli.run(path, live=True, approval_note=APPROVAL, transport=mock_transport(posts, failure=failure))
    assert len(posts) == 1 and budget.snapshot()['deepseek_attempts'] == 1421
    with pytest.raises(ExecutionStop, match=code):
        cli.run(path, live=True, approval_note=APPROVAL, transport=mock_transport(posts))
    assert len(posts) == 1


def test_saved_answer_cannot_replace_the_original_receipt_selection(boundary_ready, monkeypatch):
    budget, _, cfg, path, _ = boundary_ready
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: budget)
    cli.run(path, live=True, approval_note=APPROVAL, transport=mock_transport([]))
    with budget.transaction() as db:
        saved_path, _ = execution.verify_saved_result(db, cfg, cfg['campaign'], 2)
        value = execution.read(saved_path)
        value['selection']['sources'][0]['text'] = 'Replaced quote'
        write(Path(saved_path), value)
        db.execute('UPDATE rank_successor_results SET result_sha=? WHERE campaign_id=? AND position=2',
                   (file_hash(Path(saved_path)), cfg['campaign']))
        with pytest.raises(ExecutionStop, match='saved_result_binding'):
            execution.verify_saved_result(db, cfg, cfg['campaign'], 2)


def test_rehashed_packet_and_extra_source_file_are_rejected(boundary_ready):
    _, _, cfg, _, tmp = boundary_ready
    packet_path = Path(cfg['cases'][3]['packet_path'])
    packet = execution.read(packet_path)
    packet['view']['context']['jurisdiction'] = 'US'
    write(packet_path, packet)
    cfg['files'][str(packet_path)] = file_hash(packet_path)
    with pytest.raises(ExecutionStop, match='outbound_changed'):
        rs.read_contract(write(tmp / 'bad-packet.json', cfg))
