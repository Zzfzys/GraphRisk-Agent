"""Public-query successor uses isolated fixture ledgers and mock HTTP only."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import httpx
import pytest

from test_daily_quotes_execution import quotes_ready
from test_daily_candidate_execution import candidate_ready, reserve
from test_daily_report_successor import daily_ready, write
from test_rank_successor import ready, settle
from test_autonomous_tool_contracts import scope, index, registry
from test_autonomous_action_providers import profile, cloud_body
from test_public_references import source
from graphrisk.agent import rank_successor as rs, reference_planner_execution as planning
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.public_references import load_reference_corpus
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
import run_reference_planner as cli


AUTHORIZATION = 'Fixture approval for these exact 24 public query-planning requests'


@pytest.fixture
def planner_ready(quotes_ready, source):
    budget, store, parent, parent_path, tmp = quotes_ready
    for position in range(6):
        attempt = reserve(quotes_ready, position)
        rs.record(budget, parent['campaign'], position, attempt, settle(quotes_ready, attempt))
    rs.close(budget, parent['campaign'])
    manifest_path, _, _ = source
    manifest, _ = load_reference_corpus(manifest_path)
    root = tmp / 'planner-source'
    source_file = root / 'src/fixture.py'
    source_file.parent.mkdir(parents=True)
    source_file.write_text('fixture = True\n', encoding='utf-8')
    budget_config = write(root / 'configs/agent_investigation_experiment.json', {'authored_fixture': True})
    provider_profile = planning.read(parent['profile_path'])
    files = {str(path): file_hash(path) for path in
             (source_file, budget_config, manifest_path, parent_path, Path(parent['profile_path']))}
    for doc in manifest.documents:
        snapshot = manifest_path.parent / doc.snapshot_file
        files[str(snapshot)] = file_hash(snapshot)
    cases = [{'case_id': f'{split[0].upper()}{number:02}', 'split': split,
              'query': f'公开测试问题 {split} {number:02}：如何调查未授权转账？',
              'context': {'as_of': '2026-11-12'}}
             for split in ('development', 'frozen') for number in range(1, 13)]
    suite_path = write(tmp / 'planner/suite.json', {
        'schema_version': 'route7-reference-queries-v1', 'blind_test': False,
        'corpus_version': manifest.corpus_version, 'cases': cases})
    files[str(suite_path)] = file_hash(suite_path)
    formatter = object.__new__(planning.ReferencePlannerProvider)
    formatter.provider, formatter.profile = 'deepseek', provider_profile
    outbound = []
    payloads = []
    for position, case in enumerate(cases):
        packet = planning.packet_for(case['query'], manifest, provider_profile)
        payload = formatter.payload(packet)
        packet_path = write(tmp / ('planner/' + case['case_id'] + '-packet.json'), packet)
        payload_path = write(tmp / ('planner/' + case['case_id'] + '-payload.json'), payload)
        files.update({str(path): file_hash(path) for path in (packet_path, payload_path)})
        payloads.append(digest(payload))
        outbound.append({'case_id': case['case_id'], 'position': position,
                         'packet_path': str(packet_path), 'payload_path': str(payload_path)})
    baselines = {}
    for split in ('development', 'frozen'):
        for planner in ('literal', 'rules'):
            path = write(tmp / f'planner/{split}_{planner}.json', {
                'split': split, 'planner': planner, 'corpus_version': manifest.corpus_version,
                'suite_sha256': file_hash(suite_path)})
            baselines[split + '_' + planner] = str(path)
            files[str(path)] = file_hash(path)
    cfg = {**deepcopy(parent), 'version': rs.PLANNER_VERSION, 'campaign': 'public-planner-24',
           'max_requests': 24, 'deepseek_before': 1382, 'attempts_before': 1405,
           'parent_campaign': parent['campaign'], 'parent_contract_path': str(parent_path),
           'parent_contract_sha': file_hash(parent_path), 'manifest_path': str(manifest_path),
           'suite_path': str(suite_path), 'corpus_version': manifest.corpus_version,
           'source_root': str(root), 'source_python_sha256': {'src/fixture.py': file_hash(source_file)},
           'case_keys': [case['case_id'] for case in cases], 'cases': outbound, 'payloads': payloads,
           'baseline_reports': baselines, 'files': files, 'prompt_sha256': digest(planning.PROMPT),
           'pricing_date': date.today().isoformat(), 'runtime': str(tmp), 'output': str(tmp / 'plans')}
    path = write(tmp / 'planner-contract.json', cfg)
    store.campaign(cfg['campaign'], file_hash(path), cap_each=24, group_key=rs.GROUP_REFERENCE_PLANNER)
    rs.register(budget, path, AUTHORIZATION)
    return budget, store, cfg, path, tmp


def mock_provider(data, *, invalid=None):
    budget, store, cfg, _, tmp = data
    packet = planning.read(cfg['cases'][0]['packet_path'])
    provider_profile = planning.read(cfg['profile_path'])
    session = cfg['campaign'] + '-C1'
    store.open(session, cfg['campaign'], {'campaign': cfg['campaign'], 'position': 0,
               'payload_sha': cfg['payloads'][0]}, 'deepseek', calls=1)
    wrapper = {'profile_sha256': digest(provider_profile), 'packets': [packet], 'session': session}
    phase = store.register_packet(session, 0, digest(wrapper), 'deepseek')
    posts = []

    def handler(request):
        posts.append(json.loads(request.content))
        content = {'queries': [packet['view']['original_question'], 'unauthorized transfer investigation']}
        if invalid == 'schema':
            content = {'queries': []}
        elif invalid == 'original_question':
            content['queries'][0] = 'An altered original question'
        return httpx.Response(200, json=cloud_body(content, fp='new-fp'))

    provider = planning.ReferencePlannerProvider('deepseek', profile=provider_profile, budget=budget,
        campaign=wrapper, campaign_sha=digest(wrapper), phase=phase, output=tmp / 'receipts', runtime=tmp,
        transport=httpx.MockTransport(handler))
    return provider, posts


@pytest.mark.parametrize('mutation', ['count', 'allocation', 'source', 'outbound', 'suite_query'])
def test_24_query_contract_rejects_scope_and_rehashed_outbound_changes(planner_ready, mutation):
    _, _, original, _, tmp = planner_ready
    cfg = deepcopy(original)
    if mutation == 'count':
        cfg['max_requests'] = 25
    elif mutation == 'allocation':
        cfg['deepseek_before'] = 1383
    elif mutation == 'source':
        Path(cfg['source_root'], 'src/fixture.py').write_text('changed = True\n', encoding='utf-8')
    else:
        path = Path(cfg['cases'][0]['payload_path'] if mutation == 'outbound' else cfg['suite_path'])
        body = planning.read(path)
        if mutation == 'outbound':
            body['messages'][1]['content'] = canonical({'original_question': 'changed'})
            cfg['payloads'][0] = digest(body)
        else:
            body['cases'][0]['query'] = 'Changed reviewed query'
        write(path, body)
        cfg['files'][str(path)] = file_hash(path)
    with pytest.raises(ExecutionStop):
        rs.read_contract(write(tmp / 'bad-planner-contract.json', cfg))


@pytest.mark.parametrize('mutation', ['parent_open', 'parent_receipt', 'payload', 'order', 'cost', 'unknown_history'])
def test_shared_ledger_guards_parent_history_order_and_batch_cap(planner_ready, mutation):
    budget, _, cfg, _, _ = planner_ready
    before = budget.snapshot()
    if mutation == 'parent_open':
        with budget.transaction() as db:
            db.execute('UPDATE rank_successors SET closed=0 WHERE campaign_id=?', (cfg['parent_campaign'],))
    elif mutation == 'parent_receipt':
        with budget.transaction() as db:
            receipt = db.execute('SELECT receipt_path FROM execution_requests JOIN rank_successor_results USING(attempt_id) '
                                 'WHERE campaign_id=? AND position=0', (cfg['parent_campaign'],)).fetchone()[0]
        Path(receipt).write_text('changed', encoding='utf-8')
    elif mutation == 'unknown_history':
        with budget.transaction() as db:
            db.execute("UPDATE execution_requests SET accounted=accounted+1 WHERE attempt_id='prior0'")
    changes = {'packet_sha': 'f' * 64} if mutation == 'payload' else {'reserve_micro': 5_000_001} if mutation == 'cost' else {}
    with pytest.raises(ExecutionStop):
        reserve(planner_ready, 1 if mutation == 'order' else 0, **changes)
    assert budget.snapshot()['total_attempts'] == before['total_attempts'] == 1405
    assert budget.snapshot()['unresolved_attempts'] == 2 and budget.snapshot()['integrity_stops'] == 1


@pytest.mark.parametrize('invalid', ['schema', 'original_question'])
def test_invalid_plan_is_settled_once_and_never_repaired_or_retried(planner_ready, invalid):
    provider, posts = mock_provider(planner_ready, invalid=invalid)
    first = provider.generate('0')
    replay = provider.generate('0')
    assert first['code'] == 'invalid_model_action' and not first['schema_and_binding_valid']
    assert replay['replayed_without_model_request'] and replay['attempt_id'] == first['attempt_id']
    assert len(posts) == 1 and planner_ready[0].snapshot()['total_attempts'] == 1406
    assert planner_ready[0].snapshot()['unresolved_attempts'] == 2


def test_live_requires_specific_approval_before_opening_budget(planner_ready, monkeypatch):
    monkeypatch.setattr(cli, 'check', lambda path: (planner_ready[2], {'paid_calls_in_preflight': 0, 'used_in_batch': 0}))
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **k: pytest.fail('approval must precede opening a live ledger'))
    assert cli.run(planner_ready[3])['paid_calls_in_preflight'] == 0
    with pytest.raises(ExecutionStop, match='incomplete_batch'):
        cli.evaluate_completed(planner_ready[3])
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    with pytest.raises(ValueError, match='approval'):
        cli.run(planner_ready[3], live=True)


def test_all_24_results_bind_receipts_and_resume_without_new_requests(planner_ready, monkeypatch):
    budget, _, cfg, path, _ = planner_ready
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
    monkeypatch.setattr(cli, 'load_contract', lambda path: budget.contract)
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    first = cli.run(path, live=True, approval_note=AUTHORIZATION)
    assert first['first_plans'] == first['valid_plans'] == len(posts) == 24
    views = [json.loads(payload['messages'][1]['content']) for payload in posts]
    assert all(set(view) == {'original_question', 'public_source_catalog'} for view in views)
    assert [view['original_question'] for view in views] == [case['query'] for case in planning.read(cfg['suite_path'])['cases']]
    snapshot = budget.snapshot()
    assert (snapshot['deepseek_attempts'], snapshot['total_attempts']) == (1406, 1429)
    assert (snapshot['unresolved_attempts'], snapshot['integrity_stops']) == (2, 1)
    second = cli.run(path, live=True, approval_note=AUTHORIZATION)
    assert second == first and len(posts) == 24 and budget.snapshot() == snapshot
    with budget.transaction() as db:
        missing = db.execute('SELECT * FROM rank_successor_results WHERE campaign_id=? AND position=23',
                             (cfg['campaign'],)).fetchone()
        db.execute('DELETE FROM rank_successor_results WHERE campaign_id=? AND position=23', (cfg['campaign'],))
    with pytest.raises(ExecutionStop, match='closed_missing_result_no_resend'):
        cli.run(path, live=True, approval_note=AUTHORIZATION)
    assert len(posts) == 24
    with budget.transaction() as db:
        db.execute('INSERT INTO rank_successor_results VALUES(?,?,?,?,?)', missing)
    with budget.transaction() as db:
        assert db.execute('SELECT closed FROM rank_successors WHERE campaign_id=?', (cfg['campaign'],)).fetchone() == (1,)
        assert db.execute("SELECT identity FROM provider_identity WHERE provider='deepseek'").fetchone() == (cfg['old_identity'],)
        result = planning.verify_saved_result(db, cfg, cfg['campaign'], 0)
        assert result is not None
        saved = planning.read(result[0])
        attempt = saved['provider_result']['attempt_id']
        receipt = db.execute('SELECT receipt_path FROM execution_requests WHERE attempt_id=?', (attempt,)).fetchone()[0]
        changed = planning.read(receipt)
        changed['payload_sha256'] = 'f' * 64
        write(Path(receipt), changed)
        db.execute('UPDATE execution_requests SET receipt_sha=? WHERE attempt_id=?', (file_hash(Path(receipt)), attempt))
        with pytest.raises(ExecutionStop, match='receipt_binding'):
            planning.verify_saved_result(db, cfg, cfg['campaign'], 0)
