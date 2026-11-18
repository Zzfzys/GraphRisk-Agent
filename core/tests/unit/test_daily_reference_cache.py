"""Cache-bound daily integration; loader doubles do not certify real paid provenance."""
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from test_daily_conversation_backend import SealedStore, request
from test_reference_answers import answer_source
from graphrisk.agent import reference_answer_cache as cache_module
from graphrisk.agent.autonomous_evidence import EvidenceBundle
from graphrisk.agent.public_references import ReferenceContext
from graphrisk.agent.upgrade_contract import canonical, digest
from graphrisk.serving.conversation_store import MemoryConversationStore
from graphrisk.serving.conversations import ConversationError, ConversationService
from graphrisk.serving.daily_conversation_backend import DailyConversationBackend, daily_report


@pytest.fixture
def reviewed_cache(answer_source, tmp_path, monkeypatch):
    service, _, snapshot = answer_source
    packet = service.prepare('consumer account', ReferenceContext(as_of=date(2026, 11, 12),
        jurisdiction='US', payment_rail='eft', facts={'consumer_asset_account': True}),
        purpose='reference_explanation', top_k=5, planner='literal')
    answer = service.finish(packet, {'decision': 'cite_reference',
        'reason': 'reference_explanation', 'citation_ids': ['R1']})
    provenance = {'cache_sha256': 'a'*64, 'execution_contract_sha256': 'b'*64,
        'case_id': 'PUBLIC-FIXTURE', 'result_sha256': 'c'*64, 'receipt_sha256': 'd'*64,
        'review_sha256': 'e'*64, 'reviewer_type': 'authored_test_only', 'user_acceptance': 'not_claimed'}
    current = {'service': service, 'packet': packet, 'answer': answer, 'provenance': provenance}
    path, loads = tmp_path/'reviewed-public-answer.json', []
    def load(actual):
        assert Path(actual).resolve() == path.resolve()
        loads.append(Path(actual))
        return {'service': service, **{key: deepcopy(current[key]) for key in ('packet', 'answer', 'provenance')}}
    monkeypatch.setattr(cache_module, 'load_reviewed_reference_cache', load)
    return current, path, loads, snapshot


def configured(reviewed_cache, *, as_of=date(2026, 11, 12)):
    cache, path, _, _ = reviewed_cache
    source = SealedStore()
    backend = DailyConversationBackend(source, policy_as_of=as_of, reference_cache=path)
    return source, backend, ConversationService(MemoryConversationStore(), backend)


def test_cache_preserves_full_request_and_public_scope_without_changing_case_report(reviewed_cache):
    cache, _, loads, _ = reviewed_cache
    _, backend, service = configured(reviewed_cache)
    value = service.create(request('生成风险报告'), 'daily-cache-create')
    sidecar = value['public_reference']
    assert sidecar['answer'] == cache['answer']
    assert sidecar['packet_sha256'] == digest(cache['packet'])
    assert sidecar['request_sha256'] == digest(cache['packet']['request'])
    assert sidecar['answer']['request']['top_k'] == 5
    assert sidecar['answer']['request']['planner'] == 'literal'
    assert sidecar['context_role'] == 'public_question_scope_not_case_facts'
    assert sidecar['reference_provenance'] == cache['provenance']
    assert sidecar['answer']['context']['jurisdiction'] == 'US'
    assert 'jurisdiction' not in value['evidence'] and 'payment_rail' not in value['evidence']
    scope = backend.entries[value['case_alias']]['scope']
    assert 'jurisdiction' not in scope.model_dump(mode='json')
    bundle = EvidenceBundle.model_validate_json(canonical(value['evidence']), strict=True)
    assert value['turns'][0]['report'] == daily_report(bundle)
    assert value['reserved_model_calls'] == value['turns'][0]['actual_model_calls'] == 0
    before = len(loads)
    assert service.get(value['conversation_id']) == value
    assert len(loads) > before


def test_cache_selection_is_read_only_and_restart_revalidates(reviewed_cache):
    _, path, loads, _ = reviewed_cache
    source, _, service = configured(reviewed_cache)
    first = service.create(request('生成风险报告'), 'daily-cache-create')
    stored = deepcopy(service.store.values)
    with pytest.raises(ConversationError, match='public_reference_cache_read_only'):
        service.set_reference_answer(first['conversation_id'],
            {'decision': 'insufficient_evidence', 'reason': 'no_answer_in_sources', 'citation_ids': []})
    assert service.store.values == stored
    restarted = ConversationService(service.store, DailyConversationBackend(source,
        policy_as_of=date(2026, 11, 12), reference_cache=path))
    before = len(loads)
    assert restarted.get(first['conversation_id']) == first
    assert len(loads) > before


def test_cache_date_must_match_server_reference_date(reviewed_cache):
    _, _, service = configured(reviewed_cache, as_of=date(2026, 11, 11))
    with pytest.raises(ConversationError, match='public_reference_binding_changed'):
        service.create(request(), 'daily-cache-date')
    assert service.store.values == {}


def test_another_valid_selector_cannot_replace_the_reviewed_cache_answer(reviewed_cache):
    cache, _, _, _ = reviewed_cache
    _, _, service = configured(reviewed_cache)
    value = service.create(request(), 'daily-cache-create')
    alternative = cache['service'].finish(cache['packet'], {'decision': 'insufficient_evidence',
        'reason': 'no_answer_in_sources', 'citation_ids': []})
    assert cache['service'].verify_saved(alternative) == alternative
    stored = service.store.values[value['conversation_id']]['public_reference']
    stored['answer'] = alternative
    stored['sidecar_sha256'] = digest({k:v for k,v in stored.items() if k!='sidecar_sha256'})
    with pytest.raises(ConversationError, match='public_reference_binding_changed'):
        service.get(value['conversation_id'])


@pytest.mark.parametrize('changed', ['review', 'request', 'answer', 'source'])
def test_cache_change_cannot_rebind_an_existing_sidecar(reviewed_cache, changed):
    cache, _, _, snapshot = reviewed_cache
    _, _, service = configured(reviewed_cache)
    value = service.create(request(), 'daily-cache-create')
    if changed == 'review':
        cache['provenance']['review_sha256'] = 'f'*64
    elif changed == 'request':
        cache['packet']['request']['top_k'] = 3
    elif changed == 'answer':
        cache['answer']['status'] = 'final_refusal_passed'
    else:
        snapshot.write_bytes(snapshot.read_bytes() + b'changed')
    with pytest.raises(ConversationError, match='public_reference_binding_changed'):
        service.get(value['conversation_id'])


@pytest.mark.parametrize('legacy', [dict(reference_answer_service=object()),
    dict(reference_query='public question'),
    dict(reference_answer_service=object(), reference_query='public question')])
def test_cache_and_legacy_reference_configuration_are_mutually_exclusive(tmp_path, legacy):
    with pytest.raises(ValueError, match='mutually exclusive'):
        DailyConversationBackend(SealedStore(), policy_as_of=date(2026, 11, 12),
            reference_cache=tmp_path/'cache.json', **legacy)


def test_failed_cache_loader_is_not_treated_as_an_answer_or_empty_sources(reviewed_cache, monkeypatch):
    _, _, service = configured(reviewed_cache)
    first = service.create(request(), 'daily-cache-create')
    def failed(_):
        raise ValueError('reviewed provenance changed')
    monkeypatch.setattr(cache_module, 'load_reviewed_reference_cache', failed)
    with pytest.raises(ConversationError, match='public_reference_binding_changed'):
        service.get(first['conversation_id'])
