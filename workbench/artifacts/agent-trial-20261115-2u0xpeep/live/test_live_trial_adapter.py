"""Small mock checks: no credentials, network, real ledger or transaction labels."""
from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

import serve_live_trial as adapter
from api.main import create_app
from graphrisk.agent import judgment_v12_daily_quotes as quotes
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical, digest
from graphrisk.demo.conversation_workbench import readable_report
from graphrisk.serving.conversation_store import MemoryConversationStore
from graphrisk.serving.conversations import ConversationService
from graphrisk.serving.daily_risk_service import RoutedConversationService
from graphrisk.serving.daily_narrative import verify_daily_report
from test_autonomous_tool_contracts import scope, index, registry, executor_for, run_core
from test_autonomous_action_providers import profile
from test_daily_narrative import daily_scope
from test_autonomous_loop import make_engine
from test_judgment_v3 import good


class FixtureProtocol:
    def __init__(self, tmp_path, profile, budget, *, approved=True, wrong_payload=False):
        self.config_path = tmp_path / 'config.json'
        self.sha256, self.approved, self.wrong_payload = 'a'*64, approved, wrong_payload
        self.cfg = dict(campaign='web-trial-fixture', source_root=str(tmp_path),
                        runtime=str(tmp_path), ledger_path=str(budget.path))
        self.profile, self.budget, self.registered = profile, budget, 0

    def authorize(self):
        if not self.approved:
            raise ExecutionStop('web_trial_specific_approval_required')
        return {'outbound_authorized': True}

    def register_campaign(self, sessions):
        self.authorize()
        sessions.campaign(self.cfg['campaign'], self.sha256, cap_each=6, group_key='route5')
        self.registered += 1

    def register_packet(self, budget, phase, campaign, profile):
        self.authorize()
        assert set(campaign) == {'profile_sha256', 'packets', 'session'}
        with budget.transaction() as db:
            assert db.execute('SELECT manifest_sha FROM execution_phases WHERE phase=?',
                              (phase,)).fetchone() == (digest(campaign),)

    def guard_campaign(self, campaign, phase, profile):
        self.authorize()
        if self.wrong_payload:
            raise ExecutionStop('web_trial_payload_not_approved')
        assert len(campaign['packets']) == 1
        assert campaign['packets'][0]['logical_key'] == '0'
        assert profile == self.profile

    def status(self):
        return {'authorized': self.approved, 'used_calls': self.budget.snapshot()['deepseek_attempts']}


@pytest.fixture
def setup_trial(tmp_path, daily_scope, index, registry, profile, monkeypatch):
    # Generic tool fixtures use PRIVATE-TARGET; the real public API correctly
    # accepts numeric transaction IDs only. Rebind this authored API case first.
    daily_scope = type(daily_scope).model_validate_json(canonical({
        **daily_scope.model_dump(mode='json'), 'transaction_id': '999999000123'}), strict=True)
    engine, budget, _, _ = make_engine(tmp_path, daily_scope, index, registry, profile)
    executor, _ = executor_for(daily_scope, index)
    run_core(executor)
    bundle = adapter.build_evidence(daily_scope, executor.observations, registry)
    entry = dict(scope=daily_scope, observations=executor.observations,
                 evidence=bundle, registry=registry, guard=lambda: None)
    report_profile = adapter.quality_report_profile(profile)
    protocol = FixtureProtocol(tmp_path, report_profile, budget)
    posts, defect = [], {'invalid': False}

    def transport(request):
        posts.append(json.loads(request.content))
        value = good()
        if defect['invalid']:
            value['rationale']['refs'] = ['F999']
        return httpx.Response(200, json=dict(model='deepseek-flash', system_fingerprint='fixture-fp',
            choices=[dict(finish_reason='stop', message=dict(role='assistant', content=canonical(value)))],
            usage=dict(prompt_tokens=100, completion_tokens=300, total_tokens=400,
                       prompt_cache_hit_tokens=5, prompt_cache_miss_tokens=95)))

    monkeypatch.setattr(adapter, 'InvestigationBudget', lambda *args, **kwargs: budget)
    monkeypatch.setattr(adapter, 'load_contract', lambda path: budget.contract)
    delegate = adapter.LiveDelegate(protocol, report_profile,
                                    provider_transport=httpx.MockTransport(transport))
    backend = adapter.FrozenDailyBackend([entry], policy_as_of=daily_scope.policy_as_of,
                                        guard=lambda: None, narrative_backend=delegate)
    service = RoutedConversationService(ConversationService(MemoryConversationStore(), backend))
    app = create_app(conversation_service_factory=lambda: service)
    return SimpleNamespace(app=app, backend=backend, budget=budget, protocol=protocol,
        posts=posts, defect=defect, scope=daily_scope, bundle=bundle)


def body(trial):
    return dict(transaction_id=trial.scope.transaction_id, message='生成中文研判',
                risk_profile='daily_batch', batch_id=trial.scope.scoring_context.batch_id)


def test_approved_single_post_report_api_reader_and_idempotency(setup_trial):
    trial = setup_trial
    assert trial.backend.formal_report_enabled
    assert trial.protocol.registered == 0 and not trial.posts
    with TestClient(trial.app) as client:
        response = client.post('/api/v1/conversations', json=body(trial),
                               headers={'Idempotency-Key': 'approved-web-trial'})
        assert response.status_code == 201, response.text
        value = response.json()
        report = value['turns'][0]['report']
        assert report['version'] == 'separated-risk-report-v12-daily-quotes-v2'
        assert value['turns'][0]['actual_model_calls'] == 1 and len(trial.posts) == 1
        assert report['draft']['rationale']['text'] in readable_report(value)
        assert verify_daily_report(report, trial.bundle) == report
        original = deepcopy(value)
        assert client.post('/api/v1/conversations', json=body(trial),
            headers={'Idempotency-Key': 'approved-web-trial'}).json() == original
        assert client.get('/api/v1/conversations/' + value['conversation_id']).json() == original
        assert len(trial.posts) == 1


def test_pending_approval_does_not_silently_generate_program_report(setup_trial):
    trial = setup_trial
    trial.protocol.approved = False
    assert adapter.public_status(trial.protocol)['state'] == 'pending_approval'
    before = trial.budget.snapshot()
    with TestClient(trial.app) as client:
        result = client.post('/api/v1/conversations', json=body(trial),
                             headers={'Idempotency-Key': 'pending-web-trial'})
        assert result.status_code == 409
        assert result.json()['error_code'] == 'web_trial_specific_approval_required'
    assert not trial.posts and trial.budget.snapshot() == before
    assert trial.protocol.registered == 0


@pytest.mark.parametrize('defect', ['invalid', 'payload'])
def test_invalid_first_draft_or_unapproved_payload_never_published(setup_trial, defect):
    trial = setup_trial
    trial.defect['invalid'] = defect == 'invalid'
    trial.protocol.wrong_payload = defect == 'payload'
    with TestClient(trial.app) as client:
        result = client.post('/api/v1/conversations', json=body(trial),
                             headers={'Idempotency-Key': 'rejected-web-trial'})
        assert result.status_code == 201, result.text
        value = result.json()
        assert value['turns'][0]['report'] is None
        assert value['turns'][0]['outcome'] in ('rejected', 'provider_unavailable')
        assert len(trial.posts) == (1 if defect == 'invalid' else 0)
        assert client.post('/api/v1/conversations', json=body(trial),
            headers={'Idempotency-Key': 'rejected-web-trial'}).json() == value
        assert len(trial.posts) == (1 if defect == 'invalid' else 0)


def test_server_whitelist_and_batch_projection(setup_trial):
    trial = setup_trial
    with TestClient(trial.app) as client:
        request = body(trial)
        request['transaction_id'] = '9999999'
        rejected = client.post('/api/v1/conversations', json=request,
                               headers={'Idempotency-Key': 'outside-web-trial'})
        assert rejected.status_code == 409
        assert rejected.json()['error_code'] == 'transaction_outside_registered_cases'
    day = dict(relative_day=175, transaction_count=2683, batch_id='fixed-day',
               results=[dict(transaction_id='123', risk_level='high')])
    risk = adapter.FrozenRiskService(day, lambda: None)
    assert risk.list_batches()[0]['transaction_count'] == 2683
    assert len(risk.get_day(175)['results']) == 1
    with pytest.raises(ValueError, match='transaction_outside_trial'):
        risk.get_transaction(175, '999')
    assert not trial.posts


def test_payload_builder_exact_existing_provider_and_status_whitelist(setup_trial):
    trial = setup_trial
    entry = next(iter(trial.backend.entries.values()))
    packet = adapter.prepare_daily_narrative(entry['scope'], entry['observations'],
        entry['registry'], trial.protocol.profile)
    packet['logical_key'] = '0'
    payload = adapter.report_payload(packet, trial.protocol.profile)
    assert payload['messages'][0]['content'] == quotes.PROMPT
    assert json.loads(payload['messages'][1]['content']) == packet['view']
    assert payload['thinking'] == {'type': 'disabled'} and payload['max_tokens'] == 4096
    assert trial.scope.transaction_id not in canonical(payload)
    assert set(adapter.public_status(trial.protocol)) == {'approved', 'used_calls', 'max_calls', 'state'}
    assert not trial.posts
