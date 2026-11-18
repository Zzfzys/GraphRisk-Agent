"""Answer approvals are separate from query planning; all requests here are mocks."""
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from test_reference_planner_continuation import continuation_ready
from test_reference_planner_execution import planner_ready, AUTHORIZATION
from test_daily_quotes_execution import quotes_ready
from test_daily_candidate_execution import candidate_ready, reserve
from test_daily_report_successor import daily_ready, write
from test_rank_successor import ready
from test_autonomous_tool_contracts import scope, index, registry
from test_autonomous_action_providers import profile, cloud_body
from test_public_references import source
from graphrisk.agent import rank_successor as rs, reference_answer_execution as execution
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.reference_answers import CONTRACT, PROMPT
from graphrisk.agent.upgrade_contract import digest, file_hash
import run_reference_answers as cli
import run_reference_planner as planner_cli


ANSWER_APPROVAL = 'New fixture approval for the twelve exact public-reference answer payloads'


def install_mock(monkeypatch, module, provider_name, budget, response):
    posts = []

    def handler(request):
        payload = json.loads(request.content)
        posts.append(payload)
        return httpx.Response(200, json=cloud_body(response(payload, len(posts)), fp='new-fp'))

    provider_class = getattr(module, provider_name)
    monkeypatch.setattr(module, provider_name, lambda *a, **kw: provider_class(*a, **kw, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(module, 'check', lambda p: (rs.read_contract(p), {}))
    monkeypatch.setattr(module, 'InvestigationBudget', lambda *a, **kw: budget)
    monkeypatch.setattr(module, 'load_contract', lambda p: budget.contract)
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    return posts


@pytest.fixture
def answers_ready(continuation_ready, monkeypatch):
    budget, store, parent, parent_path, tmp = continuation_ready
    install_mock(monkeypatch, planner_cli, 'ReferencePlannerProvider', budget,
        lambda payload, n: {'queries': [json.loads(payload['messages'][1]['content'])['original_question']]})
    planner_cli.run(parent_path, live=True, approval_note=AUTHORIZATION)
    # Relocate this one fixture ledger to the standard runtime path, never clone it.
    ledger = tmp / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    ledger.parent.mkdir(parents=True)
    budget.path.rename(ledger)
    budget.path = ledger
    answer_profile = execution.read(parent['profile_path'])
    answer_profile['max_prompt_utf8_bytes'] = 24_000
    answer_profile_path = write(tmp / 'answer-profile.json', answer_profile)
    cfg = deepcopy(parent)
    cfg.update(version=rs.ANSWER_VERSION, campaign='public-answer-twelve', answer_contract=CONTRACT,
        max_requests=12, cap_micro_cny=5_000_000, deepseek_before=1406, attempts_before=1429,
        parent_campaign=parent['campaign'], parent_contract_path=str(parent_path),
        parent_contract_sha=file_hash(parent_path), prompt_sha256=digest(PROMPT), output=str(tmp / 'answers'),
        profile_path=str(answer_profile_path))
    cases = [{'case_id': f'A{n:02}', 'query': f'Unauthorized transfer review authority and benefit: authored question {n}',
              'context': {'as_of': '2026-11-12'}, 'purpose': 'reference_explanation'} for n in range(1, 13)]
    suite_path = write(tmp / 'answers/suite.json', {'schema_version': 'reference-answer-review-queries-v1',
        'corpus_version': cfg['corpus_version'], 'blind_test': False, 'cases': cases})
    cfg['suite_path'], cfg['case_keys'], cfg['cases'], cfg['payloads'] = str(suite_path), [], [], []
    cfg['files'].update({str(path): file_hash(path) for path in (suite_path, parent_path, answer_profile_path)})
    service = execution.service_for(cfg)
    formatter = object.__new__(execution.ReferenceAnswerProvider)
    formatter.provider, formatter.profile = 'deepseek', execution.read(cfg['profile_path'])
    for position, case in enumerate(cases):
        request = execution.request_for(case)
        packet = execution.prepare(service, request)
        assert packet['view']['references'], 'the authored positive fixture must retrieve its source'
        payload = formatter.payload(packet)
        paths = {key: write(tmp / f"answers/{case['case_id']}-{key}.json", value) for key, value in
                 (('request', request), ('packet', packet), ('payload', payload))}
        cfg['files'].update({str(path): file_hash(path) for path in paths.values()})
        cfg['case_keys'].append(case['case_id'])
        cfg['cases'].append({'case_id': case['case_id'], 'position': position,
                             **{key + '_path': str(path) for key, path in paths.items()}})
        cfg['payloads'].append(digest(payload))
    path = write(tmp / 'answer-contract.json', cfg)
    store.campaign(cfg['campaign'], file_hash(path), cap_each=12, group_key=rs.GROUP_REFERENCE_ANSWER)
    rs.register(budget, path, ANSWER_APPROVAL)
    return budget, store, cfg, path, tmp


def test_original_query_approval_cannot_authorize_answers_or_open_live_budget(answers_ready, monkeypatch):
    budget, _, cfg, path, _ = answers_ready
    with budget.transaction() as db:
        with pytest.raises(ExecutionStop, match='old_query_approval'):
            execution.check_authorization(db, cfg, AUTHORIZATION)
    before = budget.snapshot()
    monkeypatch.setattr(cli, 'check', lambda p: (cfg, {'paid_calls_in_preflight': 0}))
    monkeypatch.setattr(cli, 'InvestigationBudget', lambda *a, **kw: pytest.fail('no live budget before a new approval'))
    assert cli.run(path)['paid_calls_in_preflight'] == 0
    with pytest.raises(ValueError, match='new specific'):
        cli.run(path, live=True)
    assert budget.snapshot() == before


def test_contract_rebuilds_evidence_and_preserves_exact_twelve_scope(answers_ready):
    _, _, cfg, _, tmp = answers_ready
    for key, value in (('max_requests', 13), ('cap_micro_cny', 5_000_001), ('deepseek_before', 1407)):
        bad = deepcopy(cfg)
        bad[key] = value
        with pytest.raises(ExecutionStop):
            rs.read_contract(write(tmp / 'bad-scope.json', bad))
    profile_path = Path(cfg['profile_path'])
    original_profile = profile_path.read_bytes()
    bad = deepcopy(cfg)
    profile = execution.read(profile_path)
    profile['max_prompt_utf8_bytes'] = 24_001
    write(profile_path, profile)
    bad['files'][str(profile_path)] = file_hash(profile_path)
    with pytest.raises(ExecutionStop, match='profile'):
        rs.read_contract(write(tmp / 'bad-prompt-limit.json', bad))
    profile_path.write_bytes(original_profile)
    bad = deepcopy(cfg)
    packet_path = Path(bad['cases'][0]['packet_path'])
    packet = execution.read(packet_path)
    packet['view']['references'][0]['text'] = 'An invented replacement for the complete source excerpt.'
    write(packet_path, packet)
    bad['files'][str(packet_path)] = file_hash(packet_path)
    with pytest.raises(ExecutionStop, match='outbound_changed'):
        rs.read_contract(write(tmp / 'rehashed-evidence.json', bad))


def test_parent_receipts_and_historical_unknowns_cannot_be_replaced(answers_ready):
    budget, _, cfg, _, _ = answers_ready
    with budget.transaction() as db:
        db.execute('UPDATE rank_successors SET closed=0 WHERE campaign_id=?', (cfg['parent_campaign'],))
    with pytest.raises(ExecutionStop, match='parent_binding'):
        reserve(answers_ready)
    with budget.transaction() as db:
        db.execute('UPDATE rank_successors SET closed=1 WHERE campaign_id=?', (cfg['parent_campaign'],))
        db.execute('UPDATE execution_requests SET accounted=0 WHERE attempt_id=?', (cfg['prior_unknown_attempt_id'],))
    with pytest.raises(ExecutionStop, match='unknowns_changed'):
        reserve(answers_ready)


def test_twelve_first_drafts_settle_invalid_output_and_resume_without_retry(answers_ready, monkeypatch):
    budget, _, cfg, path, _ = answers_ready
    posts = install_mock(monkeypatch, cli, 'ReferenceAnswerProvider', budget, lambda payload, n: {
        'decision': 'cite_reference', 'reason': 'reference_explanation', 'citation_ids': ['R99' if n == 1 else 'R1']})
    first = cli.run(path, live=True, approval_note=ANSWER_APPROVAL)
    assert first['first_drafts'] == len(posts) == 12 and first['schema_valid'] == 11
    assert first['semantic_support_review'] == 'Pending'
    snapshot = budget.snapshot()
    assert (snapshot['deepseek_attempts'], snapshot['total_attempts']) == (1418, 1441)
    assert (snapshot['unresolved_attempts'], snapshot['integrity_stops']) == (3, 1)
    assert cli.run(path, live=True, approval_note=ANSWER_APPROVAL) == first
    assert len(posts) == 12 and budget.snapshot() == snapshot
    with budget.transaction() as db:
        assert rs.rows(db, "a.state IN ('reserved','unknown')") == cfg['historical_unknowns']
        assert rs.rows(db, 'r.integrity_stop=1') == cfg['historical_stops']
        saved_path, _ = execution.verify_saved_result(db, cfg, cfg['campaign'], 1)
        value = execution.read(saved_path)
        assert value['provider_receipt_bound'] is True and value['answer']['model_response_authenticated'] is False
        value['answer']['sources'][0]['text'] = 'changed answer text'
        write(Path(saved_path), value)
        db.execute('UPDATE rank_successor_results SET result_sha=? WHERE campaign_id=? AND position=1',
                   (file_hash(Path(saved_path)), cfg['campaign']))
        with pytest.raises(ExecutionStop, match='saved_answer_binding'):
            execution.verify_saved_result(db, cfg, cfg['campaign'], 1)
