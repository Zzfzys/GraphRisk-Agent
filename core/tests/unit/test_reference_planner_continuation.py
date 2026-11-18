"""A failed first request consumes its allowance; only the untouched 23 may run."""
from copy import deepcopy
import json
from pathlib import Path
import socket

import httpx
import pytest

from test_reference_planner_execution import planner_ready, AUTHORIZATION
from test_daily_quotes_execution import quotes_ready
from test_daily_candidate_execution import candidate_ready, reserve
from test_daily_report_successor import daily_ready, write
from test_rank_successor import ready
from test_autonomous_tool_contracts import scope, index, registry
from test_autonomous_action_providers import profile, cloud_body
from test_public_references import source
from graphrisk.agent import rank_successor as rs, reference_planner_execution as planning
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent import autonomous_http
from graphrisk.agent.upgrade_contract import digest, file_hash
import run_reference_planner as cli


PROXY = 'http://127.0.0.1:7890'
FAILED_RESERVATION = 2_129_920
REMAINING_CAP = 2_870_080


@pytest.fixture
def continuation_ready(planner_ready):
    budget, store, parent, parent_path, tmp = planner_ready
    failed = reserve(planner_ready, reserve_micro=FAILED_RESERVATION)
    budget.unknown(failed, 'provider_connection_unknown')
    rs.close(budget, parent['campaign'])
    authorization = write(tmp / 'original-authorization.json', {
        'contract_file_sha256': file_hash(parent_path), 'maximum_requests': 24, 'cap_cny': 5,
        'automatic_retries': 0, 'repairs': 0, 'paid_judges': 0, 'user_text': AUTHORIZATION})
    provider_profile = planning.read(parent['profile_path'])
    provider_profile['approved_loopback_proxy'] = PROXY
    profile_path = write(tmp / 'continuation-profile.json', provider_profile)
    cfg = deepcopy(parent)
    cfg.update(version=rs.PLANNER_CONTINUATION_VERSION, campaign='public-planner-remaining-23',
        max_requests=23, cap_micro_cny=REMAINING_CAP, deepseek_before=1383, attempts_before=1406,
        parent_campaign=parent['campaign'], parent_contract_path=str(parent_path),
        parent_contract_sha=file_hash(parent_path), profile_path=str(profile_path),
        original_authorization_path=str(authorization), original_authorization_sha256=file_hash(authorization),
        prior_unknown_attempt_id=failed, prior_unknown_reserved_micro_cny=FAILED_RESERVATION,
        case_keys=parent['case_keys'][1:], payloads=parent['payloads'][1:],
        cases=[{**case, 'position': position} for position, case in enumerate(parent['cases'][1:])],
        output=str(tmp / 'continuation-plans'))
    cfg['files'].update({str(path): file_hash(path) for path in (authorization, profile_path, parent_path)})
    with budget.transaction() as db:
        cfg['historical_unknowns'] = rs.rows(db, "a.state IN ('reserved','unknown')")
        cfg['historical_stops'] = rs.rows(db, 'r.integrity_stop=1')
    path = write(tmp / 'continuation-contract.json', cfg)
    store.campaign(cfg['campaign'], file_hash(path), cap_each=23, group_key=rs.GROUP_REFERENCE_PLANNER_CONT)
    rs.register(budget, path, AUTHORIZATION)
    return budget, store, cfg, path, tmp


@pytest.mark.parametrize('mutation', ['failed_question', 'new_allowance', 'reset_cost', 'original_authorization'])
def test_continuation_cannot_reissue_failure_or_expand_original_approval(continuation_ready, mutation):
    _, _, original, _, tmp = continuation_ready
    cfg = deepcopy(original)
    if mutation == 'failed_question':
        parent = planning.read(cfg['parent_contract_path'])
        cfg['case_keys'][0], cfg['payloads'][0] = parent['case_keys'][0], parent['payloads'][0]
        cfg['cases'][0] = parent['cases'][0]
    elif mutation == 'new_allowance':
        cfg['max_requests'] = 24
    elif mutation == 'reset_cost':
        cfg['cap_micro_cny'] = 5_000_000
    else:
        path = Path(cfg['original_authorization_path'])
        value = planning.read(path)
        value['contract_file_sha256'] = 'f' * 64
        write(path, value)
        cfg['files'][str(path)] = cfg['original_authorization_sha256'] = file_hash(path)
    with pytest.raises(ExecutionStop):
        rs.read_contract(write(tmp / 'changed-continuation.json', cfg))


@pytest.mark.parametrize('mutation', ['parent_open', 'unknown_deleted', 'unknown_cost_changed'])
def test_parent_must_stay_closed_and_unknown_must_remain_unchanged(continuation_ready, mutation):
    budget, _, cfg, _, _ = continuation_ready
    with budget.transaction() as db:
        if mutation == 'parent_open':
            db.execute('UPDATE rank_successors SET closed=0 WHERE campaign_id=?', (cfg['parent_campaign'],))
        elif mutation == 'unknown_deleted':
            db.execute('DELETE FROM execution_requests WHERE attempt_id=?', (cfg['prior_unknown_attempt_id'],))
            db.execute('DELETE FROM model_attempts WHERE attempt_id=?', (cfg['prior_unknown_attempt_id'],))
        else:
            db.execute('UPDATE execution_requests SET accounted=0 WHERE attempt_id=?', (cfg['prior_unknown_attempt_id'],))
    with pytest.raises(ExecutionStop):
        reserve(continuation_ready)


@pytest.mark.parametrize('proxy', ['http://127.0.0.1:7891', 'http://example.invalid:7890',
                                  'http://user:secret@127.0.0.1:7890'])
def test_arbitrary_proxy_is_rejected_before_network(proxy, monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **kw: pytest.fail('invalid proxy must fail before network'))
    with pytest.raises(ValueError):
        autonomous_http.post_one('https://api.deepseek.com/chat/completions', {}, headers={}, timeout=1, proxy=proxy)


def test_explicit_proxy_keeps_certificate_checks_and_ignores_environment(monkeypatch):
    options = {}

    class Response:
        status_code, headers = 200, {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def aiter_bytes(self):
            yield b'{}'

    class Client:
        def __init__(self, **kwargs):
            options.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(autonomous_http.httpx, 'AsyncClient', Client)
    assert autonomous_http.post_one('https://api.deepseek.com/chat/completions', {}, headers={}, timeout=1, proxy=PROXY) == {}
    assert options['proxy'] == PROXY and options.get('verify', True) is True
    assert options['trust_env'] is False and options['follow_redirects'] is False
    with pytest.raises(TypeError):
        autonomous_http.post_one('https://api.deepseek.com/chat/completions', {}, headers={}, timeout=1, proxy=PROXY, verify=False)


def test_only_remaining_23_requests_run_once_and_unknown_charge_is_preserved(continuation_ready, monkeypatch):
    budget, _, cfg, path, _ = continuation_ready
    parent = planning.read(cfg['parent_contract_path'])
    posts = []

    def handler(request):
        payload = json.loads(request.content)
        posts.append(payload)
        query = json.loads(payload['messages'][1]['content'])['original_question']
        return httpx.Response(200, json=cloud_body({'queries': [query, 'unauthorized transfer review']}, fp='new-fp'))

    provider_class = planning.ReferencePlannerProvider
    monkeypatch.setattr(cli, 'ReferencePlannerProvider', lambda *a, **kw: provider_class(*a, **kw, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(cli, 'check', lambda p: (rs.read_contract(p), {}))
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **kw: budget)
    monkeypatch.setattr(cli, 'load_contract', lambda p: budget.contract)
    monkeypatch.setattr(planning, 'post_one', lambda *a, **kw: pytest.fail('mock execution must bypass the real proxy branch'))
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    first = cli.run(path, live=True, approval_note=AUTHORIZATION)
    assert first['first_plans'] == first['valid_plans'] == len(posts) == 23
    assert [digest(payload) for payload in posts] == parent['payloads'][1:]
    assert parent['payloads'][0] not in [digest(payload) for payload in posts]
    snapshot = budget.snapshot()
    assert (snapshot['deepseek_attempts'], snapshot['total_attempts']) == (1406, 1429)
    assert (snapshot['unresolved_attempts'], snapshot['integrity_stops']) == (3, 1)
    assert cfg['cap_micro_cny'] + cfg['prior_unknown_reserved_micro_cny'] == 5_000_000
    with budget.transaction() as db:
        assert rs.rows(db, "a.state IN ('reserved','unknown')") == cfg['historical_unknowns']
        assert rs.rows(db, 'r.integrity_stop=1') == cfg['historical_stops']
        assert db.execute('SELECT state,reserved_micro_cny FROM model_attempts WHERE attempt_id=?',
                          (cfg['prior_unknown_attempt_id'],)).fetchone() == ('unknown', FAILED_RESERVATION)
    assert cli.run(path, live=True, approval_note=AUTHORIZATION) == first
    assert len(posts) == 23 and budget.snapshot() == snapshot
