"""Offline integration fixtures; these do not establish semantic answer quality."""
from copy import deepcopy
from datetime import date

import pytest

from test_daily_conversation_backend import SealedStore, request
from test_reference_answers import answer_source
from graphrisk.agent.autonomous_evidence import EvidenceBundle
from graphrisk.agent.public_references import ReferenceContext
from graphrisk.agent.upgrade_contract import canonical, digest
from graphrisk.serving.conversation_store import MemoryConversationStore
from graphrisk.serving.conversations import ConversationError, ConversationService
from graphrisk.serving.daily_conversation_backend import DailyConversationBackend, daily_report


QUESTION = '信用卡未授权交易调查可以核对哪些签名和交付材料？'


class ReferenceService:
    """Deterministic trusted service double, with no provider or transport."""
    def __init__(self):
        self.version = 'a'*64
        self.packets = []

    def prepare(self, query, context, *, purpose, top_k, planner):
        request = dict(query=query, context=context.model_dump(mode='json'), purpose=purpose,
                       top_k=top_k, planner=planner)
        packet = dict(request=request, request_sha256=digest(request), corpus_version=self.version)
        self.packets.append(deepcopy(packet))
        return packet

    def finish(self, packet, draft):
        if draft is not None and draft != {'decision': 'cite_reference',
                'citation_ids': ['R-test'], 'reason': 'reference_explanation'}:
            raise ValueError('invalid selector')
        result = dict(packet=deepcopy(packet), packet_sha256=digest(packet), draft=deepcopy(draft),
            status='not_evaluated' if draft is None else 'reference_cited',
            sources=[] if draft is None else [{'citation_id': 'R-test', 'text': 'Full conditional excerpt.'}])
        result['answer_sha256'] = digest(result)
        return result

    def verify_saved(self, answer):
        expected = self.finish(answer['packet'], answer['draft'])
        if expected != answer or answer['packet']['corpus_version'] != self.version:
            raise ValueError('answer changed')
        return expected


def configured():
    source, references = SealedStore(), ReferenceService()
    backend = DailyConversationBackend(source, policy_as_of=date(2026, 11, 12),
        reference_answer_service=references, reference_query=QUESTION)
    service = ConversationService(MemoryConversationStore(), backend)
    return source, references, backend, service


def test_explicit_reference_is_pending_independent_and_does_not_generate_or_modify_report():
    _, references, backend, service = configured()
    value = service.create(request('生成风险报告'), 'reference-create')
    sidecar = value['public_reference']
    assert sidecar['answer']['status'] == 'not_evaluated'
    assert sidecar['evidence_version'] == value['evidence']['evidence_version']
    assert 'public_reference' not in value['evidence']
    bundle = EvidenceBundle.model_validate_json(canonical(value['evidence']), strict=True)
    assert value['turns'][0]['report'] == daily_report(bundle)
    assert value['reserved_model_calls'] == value['turns'][0]['actual_model_calls'] == 0
    for packet in references.packets:
        assert packet['request']['query'] == QUESTION and packet['request']['purpose'] == 'reference_explanation'
        assert packet['request']['context'] == {'as_of': '2026-11-12', 'jurisdiction': None,
                                               'payment_rail': None, 'facts': {}}
        assert 'transaction_id' not in canonical(packet) and 'score' not in canonical(packet)
    assert backend.formal_report_enabled is False


def test_checked_selector_is_persisted_replayed_and_revalidated_after_backend_restart():
    source, references, _, service = configured()
    first = service.create(request(), 'reference-create')
    old_evidence, old_turns = deepcopy(first['evidence']), deepcopy(first['turns'])
    selected = service.set_reference_answer(first['conversation_id'],
        {'decision': 'cite_reference', 'citation_ids': ['R-test'], 'reason': 'reference_explanation'})
    assert selected['public_reference']['answer']['status'] == 'reference_cited'
    assert selected['evidence'] == old_evidence and selected['turns'] == old_turns
    assert service.get(first['conversation_id']) == selected
    restarted = ConversationService(service.store, DailyConversationBackend(source,
        policy_as_of=date(2026, 11, 12), reference_answer_service=references, reference_query=QUESTION))
    assert restarted.get(first['conversation_id']) == selected


@pytest.mark.parametrize('changed', ['query', 'context', 'corpus', 'disabled'])
def test_changed_server_reference_configuration_cannot_rebind_existing_case(changed):
    _, references, backend, service = configured()
    value = service.create(request(), 'reference-create')
    if changed == 'query':
        backend.reference_query = '另一项公开问题'
    elif changed == 'context':
        backend.policy_as_of = date(2026, 11, 13)
        backend.entries.clear()
    elif changed == 'corpus':
        references.version = 'b'*64
    else:
        backend.reference_answer_service = None
    with pytest.raises(ConversationError, match='public_reference_|evidence_binding_changed'):
        service.get(value['conversation_id'])


@pytest.mark.parametrize('field', ['case_alias', 'case_binding_sha256', 'evidence_version',
    'query_sha256', 'context_sha256', 'request_sha256', 'packet_sha256'])
def test_recomputed_hash_cannot_replace_bound_sidecar_request(field):
    _, _, _, service = configured()
    value = service.create(request(), 'reference-create')
    stored = service.store.values[value['conversation_id']]['public_reference']
    stored[field] = 'f'*64
    stored['sidecar_sha256'] = digest({k:v for k,v in stored.items() if k!='sidecar_sha256'})
    with pytest.raises(ConversationError, match='public_reference_binding_changed'):
        service.get(value['conversation_id'])


def test_tampered_selected_answer_and_invalid_internal_draft_do_not_publish():
    _, _, _, service = configured()
    value = service.create(request(), 'reference-create')
    before = deepcopy(service.store.values)
    with pytest.raises(ValueError):
        service.set_reference_answer(value['conversation_id'], {'decision': 'invent_fact'})
    assert service.store.values == before
    with pytest.raises(ValueError):
        service.set_reference_answer(value['conversation_id'], None)
    assert service.store.values == before
    stored = service.store.values[value['conversation_id']]['public_reference']
    stored['answer']['status'] = 'final_refusal_passed'
    stored['sidecar_sha256'] = digest({k:v for k,v in stored.items() if k!='sidecar_sha256'})
    with pytest.raises(ConversationError, match='public_reference_binding_changed'):
        service.get(value['conversation_id'])


def test_valid_answer_for_another_public_question_cannot_replace_bound_answer():
    _, references, _, service = configured()
    value = service.create(request(), 'reference-create')
    other = references.prepare('另一个公开问题', ReferenceContext(as_of=date(2026, 11, 12)),
        purpose='reference_explanation', top_k=3, planner='rules')
    answer = references.finish(other, None)
    assert references.verify_saved(answer) == answer
    stored = service.store.values[value['conversation_id']]['public_reference']
    stored['answer'] = answer
    stored['sidecar_sha256'] = digest({k:v for k,v in stored.items() if k!='sidecar_sha256'})
    with pytest.raises(ConversationError, match='public_reference_binding_changed'):
        service.get(value['conversation_id'])


def test_unconfigured_daily_backend_is_unchanged_and_cannot_attach_selector():
    service = ConversationService(MemoryConversationStore(),
        DailyConversationBackend(SealedStore(), policy_as_of=date(2026, 11, 12)))
    value = service.create(request('生成风险报告'), 'reference-disabled')
    assert 'public_reference' not in value
    assert value['reserved_model_calls'] == 0
    with pytest.raises(ConversationError, match='public_reference_unconfigured'):
        service.set_reference_answer(value['conversation_id'], {})


@pytest.mark.parametrize('service,query', [(ReferenceService(), None), (None, QUESTION),
    (ReferenceService(), ''), (ReferenceService(), ' '*4), (ReferenceService(), 'x'*1001)])
def test_reference_configuration_requires_explicit_service_and_bounded_public_question(service, query):
    with pytest.raises(ValueError):
        DailyConversationBackend(SealedStore(), policy_as_of=date(2026, 11, 12),
            reference_answer_service=service, reference_query=query)


def test_real_reference_service_and_daily_report_share_bound_appendix_without_rewriting_report(answer_source):
    references, _, snapshot = answer_source
    backend = DailyConversationBackend(SealedStore(), policy_as_of=date(2026, 11, 12),
        reference_answer_service=references, reference_query='consumer account')
    service = ConversationService(MemoryConversationStore(), backend)
    first = service.create(request('生成风险报告'), 'real-reference-service')
    assert first['public_reference']['answer']['status'] == 'not_evaluated'
    report = deepcopy(first['turns'][0]['report'])
    selected = service.set_reference_answer(first['conversation_id'],
        {'decision': 'cite_reference', 'reason': 'reference_explanation', 'citation_ids': ['R1']})
    answer = selected['public_reference']['answer']
    assert answer['status'] == 'reference_excerpt'
    assert answer['source_integrity'] == 'verified_saved_excerpt'
    assert answer['semantic_support'] == 'requires_review'
    assert answer['sources'][0]['source_url'].startswith('https://www.consumerfinance.gov/')
    assert selected['turns'][0]['report'] == report
    assert selected['evidence'] == first['evidence']
    assert selected['public_reference']['packet_sha256'] == answer['packet_sha256']
    assert selected['reserved_model_calls'] == 0
    snapshot.write_bytes(snapshot.read_bytes() + b'changed')
    with pytest.raises(ConversationError, match='public_reference_binding_changed'):
        service.get(first['conversation_id'])
