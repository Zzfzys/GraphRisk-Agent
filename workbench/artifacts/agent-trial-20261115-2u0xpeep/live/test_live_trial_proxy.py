"""Exercise the real non-Mock provider boundary with a no-network POST spy."""
from contextlib import contextmanager
from copy import deepcopy
import sqlite3
from types import SimpleNamespace

import pytest

import serve_live_trial_recovery as recovery
from graphrisk.agent import autonomous_providers
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_http import TransportFailure
from graphrisk.agent.upgrade_contract import digest


@pytest.fixture
def constructor_args(tmp_path, monkeypatch):
    profile = {'approved_loopback_proxy': recovery.PROXY,
               'deepseek': {'endpoint': recovery.ENDPOINT}}
    campaign = {'profile_sha256': digest(profile),
                'packets': [{'logical_key': '0', 'view': {'fixture': True}}],
                'session': 'offline-proxy-boundary'}
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE execution_phases(phase TEXT PRIMARY KEY,manifest_sha TEXT)')
    db.execute('INSERT INTO execution_phases VALUES(?,?)', ('fixture-phase', digest(campaign)))

    @contextmanager
    def transaction():
        yield db

    budget = SimpleNamespace(mode='live', transaction=transaction)
    monkeypatch.setattr(autonomous_providers, 'credentials',
                        lambda runtime: pytest.fail('constructor must not read credentials'))
    args = dict(profile=profile, budget=budget, campaign=campaign,
        campaign_sha=digest(campaign), phase='fixture-phase', output=tmp_path / 'receipts',
        runtime=tmp_path, live=True)
    yield args
    db.close()


@pytest.mark.parametrize('live,environment', [(True, None), (False, '1')])
def test_real_non_mock_constructor_requires_both_live_opt_ins(
        constructor_args, monkeypatch, live, environment):
    if environment is None:
        monkeypatch.delenv('GRAPHRISK_UPGRADE_LIVE', raising=False)
    else:
        monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', environment)
    args = {**constructor_args, 'live': live}
    assert 'transport' not in args  # This must exercise fake=False.
    with pytest.raises(ActionFailure, match='upgrade_live_opt_in_required'):
        recovery.ApprovedProxyProvider('deepseek', **args)
    assert not args['output'].exists()


def test_real_provider_post_routes_exact_approved_proxy_once(constructor_args, monkeypatch):
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    calls = []
    response = {'fixture_response': True}

    def post(url, payload, **kwargs):
        calls.append((url, payload, kwargs))
        return response

    monkeypatch.setattr(recovery.autonomous_http, 'post_one', post)
    provider = recovery.ApprovedProxyProvider('deepseek', **constructor_args)
    assert provider.transport is None and provider.fake is False
    payload, headers = {'fixture': 'no transaction'}, {'Authorization': 'Bearer fixture-only'}
    assert provider.post_payload(payload, headers=headers, timeout=12) is response
    assert calls == [(recovery.ENDPOINT, payload,
        {'headers': headers, 'timeout': 12, 'transport': None, 'proxy': recovery.PROXY})]


def test_failed_proxy_post_is_propagated_without_retry(constructor_args, monkeypatch):
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs['proxy'])
        raise TransportFailure('provider_connection_unknown')

    monkeypatch.setattr(recovery.autonomous_http, 'post_one', post)
    provider = recovery.ApprovedProxyProvider('deepseek', **constructor_args)
    with pytest.raises(TransportFailure, match='provider_connection_unknown'):
        provider.post_payload({'fixture': True}, headers={}, timeout=3)
    assert calls == [recovery.PROXY]


@pytest.mark.parametrize('proxy', [None, 'http://localhost:7890', 'http://127.0.0.1:7891',
                                 'https://127.0.0.1:7890', 'http://example.com:7890'])
def test_missing_or_changed_proxy_stops_before_provider_creation(
        constructor_args, monkeypatch, proxy):
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    profile = deepcopy(constructor_args['profile'])
    profile['approved_loopback_proxy'] = proxy
    args = {**constructor_args, 'profile': profile}
    monkeypatch.setattr(recovery.autonomous_http, 'post_one',
                        lambda *a, **kw: pytest.fail('invalid route must never POST'))
    with pytest.raises(ActionFailure, match='web_trial_approved_proxy_required'):
        recovery.ApprovedProxyProvider('deepseek', **args)
    assert not args['output'].exists()


@pytest.mark.parametrize('provider,endpoint', [('local', recovery.ENDPOINT),
    ('deepseek', 'https://example.com/chat/completions'),
    ('deepseek', 'http://api.deepseek.com/chat/completions')])
def test_non_official_provider_or_endpoint_cannot_use_recovery_route(
        constructor_args, monkeypatch, provider, endpoint):
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    profile = deepcopy(constructor_args['profile'])
    profile['deepseek']['endpoint'] = endpoint
    with pytest.raises(ActionFailure, match='web_trial_approved_proxy_required'):
        recovery.ApprovedProxyProvider(provider, **{**constructor_args, 'profile': profile})


def test_route_is_revalidated_at_actual_post_boundary(constructor_args, monkeypatch):
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    provider = recovery.ApprovedProxyProvider('deepseek', **constructor_args)
    provider.profile['approved_loopback_proxy'] = None
    monkeypatch.setattr(recovery.autonomous_http, 'post_one',
                        lambda *a, **kw: pytest.fail('changed route must never POST'))
    with pytest.raises(ActionFailure, match='web_trial_approved_proxy_required'):
        provider.post_payload({'fixture': True}, headers={}, timeout=3)


def test_recovery_delegate_keeps_existing_wrapper_and_approval_guards(
        constructor_args, monkeypatch, tmp_path):
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE', '1')
    steps = []
    campaign = constructor_args['campaign']
    delegate = object.__new__(recovery.RecoveryLiveDelegate)
    delegate.provider = 'deepseek'
    delegate.report_profile = constructor_args['profile']
    delegate.provider_transport = None
    delegate.store = SimpleNamespace(budget=constructor_args['budget'])
    delegate.protocol = SimpleNamespace(config_path=tmp_path / 'config.json',
        cfg={'runtime': str(tmp_path)},
        register_packet=lambda budget, phase, wrapper, profile: steps.append(
            ('register', wrapper is campaign, digest(wrapper))),
        guard_campaign=lambda wrapper, phase, profile: steps.append(
            ('guard', wrapper is campaign, digest(wrapper))))
    provider = delegate.make_provider(campaign, 'fixture-phase', 9999999999)
    assert isinstance(provider, recovery.ApprovedProxyProvider)
    assert steps == [('register', True, digest(campaign)), ('guard', True, digest(campaign))]
    assert provider.campaign is campaign and set(campaign) == {'profile_sha256', 'packets', 'session'}
    assert provider.remaining() > 0


@pytest.mark.parametrize('authorized,used,state', [(False, 1, 'stopped'),
                                                  (True, 1, 'ready'),
                                                  (True, 6, 'stopped')])
def test_recovery_status_keeps_failed_h1_and_separate_approval(authorized, used, state):
    protocol = SimpleNamespace(status=lambda: {'authorized': authorized, 'web_calls': used,
        'status': 'authorized' if authorized else 'paused_recovery_approval_pending'})
    value = recovery.recovery_public_status(protocol)
    assert value['state'] == state and value['used_calls'] == used and value['max_calls'] == 6
    assert value['approved'] is authorized and 'H1' in value['detail']
