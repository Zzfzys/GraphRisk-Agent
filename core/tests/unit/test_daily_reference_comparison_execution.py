"""Small fixture ledger, genuine child receipts, and two mock HTTP selections only."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import httpx
import pytest

from test_daily_reference_comparison import paired_ready
from test_rank_successor import ready
from test_autonomous_action_providers import cloud_body, profile
from test_reference_answers import answer_source as local_answer_source
from graphrisk.agent import rank_successor as rs
from graphrisk.agent import daily_reference_comparison_execution as execution
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.public_references import load_reference_corpus
from graphrisk.agent.reference_answers import CONTRACT, PROMPT
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
import run_daily_reference_comparison as cli


APPROVAL = 'New fixture approval for these two exact daily case-reference selection payloads'


@pytest.fixture
def answer_source(tmp_path, monkeypatch):
    # The pure fixture forbids socket.connect; keep that guard during its setup
    # only, so Windows asyncio can create its loopback self-pipe for MockTransport.
    with monkeypatch.context() as local:
        value = local_answer_source.__wrapped__(tmp_path, local)
    return value


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value), encoding='utf-8')
    return path


class ExecutionDay(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 13)


@pytest.fixture
def comparison_ready(ready, paired_ready, monkeypatch):
    budget, store, old, old_path, tmp = ready
    paired, paired_path, _ = paired_ready
    # Only the already-tested parent-chain audit is isolated. Child contract,
    # full paired reconstruction, request guards and receipt verification stay real.
    monkeypatch.setattr(execution, 'check_parent', lambda db, cfg: None)
    for module in (execution, rs, cli):
        monkeypatch.setattr(module, 'date', ExecutionDay)
    with budget.transaction() as db:
        db.execute("INSERT INTO model_attempts VALUES('third-old-unknown','deepseek','unknown',1)")
        db.execute("INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) "
                   "VALUES('third-old-unknown','third-old-unknown','old-phase','old-payload',1)")
        for provider, count in (('deepseek', 1414), ('local', 23)):
            entries = [(f'past-{provider}-{index}', provider, 'settled', 1) for index in range(count)]
            db.executemany('INSERT INTO model_attempts VALUES(?,?,?,?)', entries)
            db.executemany('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) '
                           'VALUES(?,?,?,?,?)', [(row[0], row[0], 'old-phase', digest(row[0]), 1)
                                                for row in entries])
        unknowns = rs.rows(db, "a.state IN ('reserved','unknown')")
        stops = rs.rows(db, 'r.integrity_stop=1')
    ledger = tmp / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    ledger.parent.mkdir(parents=True)
    budget.path.rename(ledger)
    budget.path = ledger
    source = tmp / 'comparison-source'
    source_file = source / 'src/fixture.py'
    source_file.parent.mkdir(parents=True)
    source_file.write_text('fixture = True\n', encoding='utf-8')
    budget_path = write(source / 'configs/agent_investigation_experiment.json', budget.contract)
    cfg = {'version': rs.CASE_REFERENCE_VERSION, 'campaign': 'paired-case-two',
        'max_requests': 2, 'cap_micro_cny': 5_000_000, 'deepseek_before': 1418, 'attempts_before': 1441,
        'endpoint': 'https://api.deepseek.com/chat/completions', 'model': 'deepseek-flash',
        'answer_contract': CONTRACT, 'prompt_sha256': digest(PROMPT), 'pricing_date': '2026-11-13',
        'pricing_required': False, 'retries': 0, 'automatic_retries': 0, 'repairs': 0, 'judge_calls': 0,
        'pause_cny': 80, 'overall_max_cny': 100, 'runtime': str(tmp), 'source_root': str(source),
        'source_python_sha256': {'src/fixture.py': file_hash(source_file)},
        'parent_campaign': old['campaign'], 'parent_contract_path': str(old_path),
        'parent_contract_sha': file_hash(old_path), 'old_identity': old['old_identity'],
        'expected_identity': old['expected_identity'], 'historical_unknowns': unknowns,
        'historical_stops': stops, 'paired_manifest_path': str(paired_path),
        'paired_manifest_sha256': file_hash(paired_path),
        **{key: paired[key] for key in ('profile_path', 'manifest_path', 'corpus_version')},
        'case_keys': ['Q1', 'Q2'], 'cases': [{key: query[key] for key in
            ('case_id', 'position', 'request_path', 'packet_path', 'payload_path')}
            for query in paired['queries']],
        'payloads': [query['payload_sha256'] for query in paired['queries']],
        'output': str(tmp / 'actual-reference-selections-v1'), 'files': dict(paired['files'])}
    corpus, _ = load_reference_corpus(cfg['manifest_path'], cfg['corpus_version'])
    for path in (source_file, budget_path, old_path, paired_path, Path(cfg['profile_path']),
                 Path(cfg['manifest_path']), *(Path(cfg['manifest_path']).parent / document.snapshot_file
                                               for document in corpus.documents)):
        cfg['files'][str(path)] = file_hash(path)
    path = write(tmp / 'case-comparison-execution.json', cfg)
    return budget, store, cfg, path, tmp


def install_mock(monkeypatch, data, *, invalid_first=False, failure=None):
    budget = data[0]
    posts = []

    def handler(request):
        payload = json.loads(request.content)
        posts.append(payload)
        if failure == 'unknown':
            raise httpx.ReadTimeout('fixture timeout; no real request', request=request)
        view = json.loads(payload['messages'][1]['content'])
        action = {'decision': 'cite_reference', 'reason': 'reference_explanation',
                  'citation_ids': ['R99' if invalid_first and len(posts) == 1 else 'R1']}
        if view['purpose'] == 'transaction_application':
            action = {'decision': 'insufficient_evidence', 'reason': 'missing_case_facts',
                      'citation_ids': []}
        return httpx.Response(200, json=cloud_body(action,
            fp='changed-fp' if failure == 'identity' else 'new-fp'))

    provider = execution.ReferenceAnswerProvider
    monkeypatch.setattr(cli, 'ReferenceAnswerProvider', lambda *args, **kwargs:
        provider(*args, **kwargs, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *args, **kwargs: budget)
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    return posts


def test_readonly_preflight_and_old_approval_never_construct_live_budget(comparison_ready, monkeypatch):
    budget, _, cfg, path, _ = comparison_ready
    before = file_hash(budget.path)
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *args, **kwargs:
                        pytest.fail('read-only or old approval must not construct a budget'))
    preview = cli.run(path)
    assert preview['paid_calls_in_preflight'] == 0 and preview['max_new_requests'] == 2
    assert (preview['current_deepseek_attempts'], preview['current_total_attempts']) == (1418, 1441)
    assert file_hash(budget.path) == before
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    with pytest.raises(ValueError, match='new specific two-selection'):
        cli.run(path, live=True)
    with budget.transaction() as db:
        old_approval = db.execute('SELECT authorization FROM rank_successors LIMIT 1').fetchone()[0]
    with pytest.raises(ExecutionStop, match='old_registered_authorization'):
        cli.run(path, live=True, approval_note=old_approval)
    with budget.transaction() as db:
        assert not db.execute('SELECT 1 FROM rank_successors WHERE campaign_id=?', (cfg['campaign'],)).fetchone()


@pytest.mark.parametrize('key,value', [('max_requests', 3), ('max_requests', 2.0),
    ('cap_micro_cny', 5_000_001), ('deepseek_before', 1419), ('attempts_before', 1442),
    ('pricing_date', '2026-11-12')])
def test_new_scope_is_exactly_two_and_does_not_widen_old_twelve(comparison_ready, key, value):
    _, store, cfg, _, tmp = comparison_ready
    bad = deepcopy(cfg)
    bad[key] = value
    with pytest.raises(ExecutionStop):
        rs.read_contract(write(tmp / 'bad-scope.json', bad))
    assert rs.bounds({'version': rs.ANSWER_VERSION}) == (12, 5_000_000, rs.GROUP_REFERENCE_ANSWER)
    with pytest.raises(ExecutionStop, match='invalid_case_reference_cap'):
        store.campaign('wrong-cap', 'a' * 64, cap_each=3, group_key=rs.GROUP_CASE_REFERENCE)


@pytest.mark.parametrize('change', ['python', 'packet', 'context'])
def test_source_packet_and_case_context_are_rebuilt(comparison_ready, change):
    _, _, cfg, _, tmp = comparison_ready
    bad = deepcopy(cfg)
    if change == 'python':
        Path(cfg['source_root'], 'src/fixture.py').write_text('changed=True', encoding='utf-8')
    else:
        path = Path(cfg['cases'][0]['packet_path' if change == 'packet' else 'request_path'])
        value = execution.read(path)
        if change == 'packet':
            value['view']['references'][0]['text'] = 'Changed source quotation'
        else:
            value['context']['jurisdiction'] = 'US'
        write(path, value)
        bad['files'][str(path)] = file_hash(path)
    with pytest.raises((ExecutionStop, ValueError)):
        rs.read_contract(write(tmp / 'bad-binding.json', bad))


def test_two_receipts_close_and_replay_without_new_requests_or_budget(comparison_ready, monkeypatch):
    budget, _, cfg, path, _ = comparison_ready
    posts = install_mock(monkeypatch, comparison_ready, invalid_first=True)
    first = cli.run(path, live=True, approval_note=APPROVAL)
    assert first['first_selections'] == len(posts) == 2 and first['schema_valid'] == 1
    assert first['semantic_support_review'] == 'Pending' and first['report_body_generations'] == 0
    snapshot = budget.snapshot()
    assert (snapshot['deepseek_attempts'], snapshot['total_attempts']) == (1420, 1443)
    assert (snapshot['unresolved_attempts'], snapshot['integrity_stops']) == (3, 1)
    with budget.transaction() as db:
        results = execution.verified_results_by_query(db, cfg, file_hash(path))
        assert results['Q1']['answer'] is None and results['Q1']['result_code'] == 'invalid_model_action'
        assert results['Q2']['answer']['status'] == 'insufficient_evidence'
        assert results['Q2']['execution_binding']['receipt_sha256']
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *args, **kwargs:
                        pytest.fail('closed result replay is read-only'))
    assert cli.run(path, live=True, approval_note=APPROVAL) == first
    assert len(posts) == 2 and budget.snapshot() == snapshot


@pytest.mark.parametrize('change,code', [('saved_answer', 'saved_answer_binding'),
    ('receipt_action', 'saved_answer_binding'), ('session_binding', 'saved_request_binding'),
    ('missing_result', 'saved_result')])
def test_saved_answers_receipts_and_sessions_cannot_be_rebound(comparison_ready, monkeypatch, change, code):
    budget, _, cfg, path, _ = comparison_ready
    posts = install_mock(monkeypatch, comparison_ready)
    cli.run(path, live=True, approval_note=APPROVAL)
    with budget.transaction() as db:
        saved_path, _ = execution.verify_saved_result(db, cfg, cfg['campaign'], 0)
        value = execution.read(saved_path)
        if change == 'saved_answer':
            value['answer']['sources'][0]['text'] = 'Replaced quote, with saved hash recomputed'
            write(Path(saved_path), value)
            db.execute('UPDATE rank_successor_results SET result_sha=? WHERE campaign_id=? AND position=0',
                       (file_hash(Path(saved_path)), cfg['campaign']))
        elif change == 'receipt_action':
            receipt_path = Path(db.execute('SELECT receipt_path FROM execution_requests WHERE attempt_id=?',
                (value['provider_result']['attempt_id'],)).fetchone()[0])
            receipt = execution.read(receipt_path)
            receipt['body']['choices'][0]['message']['content'] = canonical({
                'decision': 'insufficient_evidence', 'reason': 'no_answer_in_sources', 'citation_ids': []})
            write(receipt_path, receipt)
            db.execute('UPDATE execution_requests SET receipt_sha=? WHERE attempt_id=?',
                       (file_hash(receipt_path), value['provider_result']['attempt_id']))
        elif change == 'session_binding':
            db.execute('UPDATE autonomous_sessions SET max_calls=2 WHERE session_id=?',
                       (cfg['campaign'] + '-C1',))
        else:
            Path(saved_path).unlink()
        with pytest.raises(ExecutionStop, match=code):
            execution.verify_saved_result(db, cfg, cfg['campaign'], 0)
    with pytest.raises(ExecutionStop, match=code):
        cli.run(path, live=True, approval_note=APPROVAL)
    assert len(posts) == 2


@pytest.mark.parametrize('failure,code', [('unknown', 'unknowns_changed'), ('identity', 'stops_changed')])
def test_new_unknown_or_identity_stop_ends_the_batch_without_retry(comparison_ready, monkeypatch, failure, code):
    budget, _, _, path, _ = comparison_ready
    posts = install_mock(monkeypatch, comparison_ready, failure=failure)
    with pytest.raises(ExecutionStop):
        cli.run(path, live=True, approval_note=APPROVAL)
    snapshot = budget.snapshot()
    assert len(posts) == 1 and snapshot['deepseek_attempts'] == 1419
    assert snapshot['unresolved_attempts'] == (4 if failure == 'unknown' else 3)
    assert snapshot['integrity_stops'] == (2 if failure == 'identity' else 1)
    with pytest.raises(ExecutionStop, match=code):
        cli.run(path, live=True, approval_note=APPROVAL)
    assert len(posts) == 1 and budget.snapshot() == snapshot


def test_closed_incomplete_batch_cannot_resend(comparison_ready, monkeypatch):
    budget, store, cfg, path, _ = comparison_ready
    store.campaign(cfg['campaign'], file_hash(path), cap_each=2, group_key=rs.GROUP_CASE_REFERENCE)
    rs.register(budget, path, APPROVAL)
    rs.close(budget, cfg['campaign'])
    monkeypatch.setattr(cli, 'ReferenceAnswerProvider', lambda *args, **kwargs:
                        pytest.fail('closed incomplete batch cannot send'))
    with pytest.raises(ExecutionStop, match='closed_incomplete_no_resend'):
        cli.run(path, live=True, approval_note=APPROVAL)
    assert budget.snapshot()['deepseek_attempts'] == 1418
