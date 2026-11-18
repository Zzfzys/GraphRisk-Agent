"""Local per-turn reference packages; no claim about real model answer quality."""
from copy import deepcopy
from datetime import date

import pytest

from test_daily_conversation_backend import SealedStore, request
from test_reference_answers import answer_source
from graphrisk.agent.autonomous_evidence import EvidenceBundle
from graphrisk.agent.public_references import ReferenceRetrievalResult
from graphrisk.agent.upgrade_contract import canonical, digest
from graphrisk.serving import daily_reference
from graphrisk.serving.conversation_store import MemoryConversationStore
from graphrisk.serving.conversations import ConversationError, ConversationService, FollowUp
from graphrisk.serving.daily_conversation_backend import DailyConversationBackend, daily_report


def configured(references, *, source=None, store=None, as_of=date(2026, 11, 13)):
    source = source or SealedStore()
    backend = DailyConversationBackend(source, policy_as_of=as_of, case_reference_service=references)
    service = ConversationService(store or MemoryConversationStore(), backend)
    return source, backend, service


def test_local_case_package_preserves_report_unknown_facts_and_zero_model_calls(answer_source):
    references, _, _ = answer_source
    _, _, service = configured(references)
    value = service.create(request('生成风险报告'), 'case-reference-first')
    turn = value['turns'][0]
    package = daily_reference.verify_case_report_package(value, turn, references)
    bundle = EvidenceBundle.model_validate_json(canonical(value['evidence']), strict=True)
    assert turn['report'] == daily_report(bundle)
    assert package['report_sha256'] == digest(turn['report'])
    assert package['task'] == 'risk_assessment' and package['turn_id'] == turn['turn_id']
    assert package['case_binding_sha256'] == value['case_reference_binding_sha256']
    assert package['selection_status'] == package['semantic_support'] == 'not_evaluated'
    assert package['reference']['answer']['status'] == 'not_evaluated'
    assert package['reference']['answer']['sources'] == []
    context = package['reference']['packet']['request']['context']
    assert context == {'as_of': '2026-11-13', 'jurisdiction': None, 'payment_rail': None, 'facts': {}}
    assert package['reference']['packet']['view']['references']
    assert 'public_reference' not in value['evidence'] and 'report_package' not in turn['report']
    assert value['reserved_model_calls'] == turn['actual_model_calls'] == package['model_calls'] == 0


def test_follow_up_changes_catalog_question_without_rebinding_the_earlier_report(answer_source):
    references, _, _ = answer_source
    _, _, service = configured(references)
    first = service.create(request('生成风险报告'), 'case-reference-first')
    first_turn = deepcopy(first['turns'][0])
    follow = FollowUp(parent_turn_id=first_turn['turn_id'], evidence_version=first['evidence']['evidence_version'],
                      message='政策前提是什么')
    result = service.follow(first['conversation_id'], follow, 'case-reference-follow')
    old, new = result['turns']
    assert old == first_turn
    assert old['report_package']['question_id'] != new['report_package']['question_id']
    assert new['report_package']['reference']['packet']['request']['purpose'] == 'transaction_application'
    assert new['report_package']['selection_status'] == 'not_evaluated'
    assert old['report_package']['reference']['packet']['request']['query'] != new['report_package']['reference']['packet']['request']['query']
    assert service.follow(first['conversation_id'], follow, 'case-reference-follow') == result
    assert service.get(first['conversation_id']) == result
    assert result['reserved_model_calls'] == 0


@pytest.mark.parametrize('status,code,answer_status', [('no_match', 'no_match', 'not_evaluated'),
                                                      ('error', 'corpus_unavailable', 'error')])
def test_no_match_and_error_are_not_fabricated_support_or_final_refusal(answer_source, monkeypatch, status, code, answer_status):
    references, _, _ = answer_source
    monkeypatch.setattr(references.retriever, 'retrieve', lambda *a, **kw: ReferenceRetrievalResult(
        status=status, error_code=code, corpus_version=references.retriever.expected_version,
        planner='rules', queries=(a[0],), hits=()))
    _, _, service = configured(references)
    value = service.create(request('生成风险报告'), 'case-reference-retrieval-state')
    package = daily_reference.verify_case_report_package(value, value['turns'][0], references)
    assert package['selection_status'] == 'not_evaluated' and package['retrieval_status'] == status
    assert package['retrieval_error_code'] == code
    assert package['reference']['answer']['status'] == answer_status
    assert package['reference']['answer']['draft'] is None and package['reference']['answer']['sources'] == []


def test_restart_and_read_rebuild_the_same_package_without_model_or_source_changes(answer_source):
    references, _, _ = answer_source
    source, _, service = configured(references)
    first = service.create(request('生成风险报告'), 'case-reference-first')
    _, _, restarted = configured(references, source=source, store=service.store)
    assert restarted.get(first['conversation_id']) == first
    assert restarted.create(request('生成风险报告'), 'case-reference-first') == first


@pytest.mark.parametrize('mutation', ['package', 'answer', 'report', 'task', 'binding', 'date'])
def test_display_rejects_rehashed_sidecar_or_changed_independent_roots(answer_source, mutation):
    references, _, _ = answer_source
    _, _, service = configured(references)
    value = service.create(request('生成风险报告'), 'case-reference-first')
    changed = deepcopy(value)
    turn = changed['turns'][0]
    package = turn['report_package']
    if mutation == 'package':
        package['question_id'] = 'another-question'
    elif mutation == 'answer':
        package['reference']['answer']['status'] = 'reference_excerpt'
        package['reference']['answer']['sources'] = [{'text': 'invented conclusion'}]
    elif mutation == 'report':
        turn['report']['message'] = 'changed primary report'
    elif mutation == 'task':
        turn['task'] = 'policy_conditions'
    elif mutation == 'binding':
        package['case_binding_sha256'] = 'f'*64
    else:
        changed['evidence']['policy_as_of'] = '2026-11-14'
    package['package_sha256'] = digest({key: item for key, item in package.items() if key != 'package_sha256'})
    with pytest.raises(ValueError, match='case_reference_report_package_changed'):
        daily_reference.verify_case_report_package(changed, turn, references)


@pytest.mark.parametrize('mutation', ['catalog', 'date', 'source', 'package', 'report'])
def test_server_rejects_changed_catalog_context_source_or_stored_package(answer_source, monkeypatch, mutation):
    references, _, snapshot = answer_source
    _, backend, service = configured(references)
    value = service.create(request('生成风险报告'), 'case-reference-first')
    if mutation == 'catalog':
        monkeypatch.setitem(daily_reference.CATALOG, 'risk_assessment', {
            **daily_reference.CATALOG['risk_assessment'], 'query': 'another server question'})
    elif mutation == 'date':
        backend.policy_as_of = date(2026, 11, 14)
    elif mutation == 'source':
        snapshot.write_bytes(snapshot.read_bytes() + b'changed')
    else:
        stored = service.store.values[value['conversation_id']]['turns'][0]
        if mutation == 'package':
            stored['report_package']['reference']['answer']['status'] = 'final_refusal_passed'
            stored['report_package']['package_sha256'] = digest({key: item for key, item in
                stored['report_package'].items() if key != 'package_sha256'})
        else:
            stored['report']['message'] = 'changed primary report'
    with pytest.raises(ConversationError):
        service.get(value['conversation_id'])


def test_unconfigured_old_mode_has_no_new_package_or_response_fields():
    backend = DailyConversationBackend(SealedStore(), policy_as_of=date(2026, 11, 13))
    service = ConversationService(MemoryConversationStore(), backend)
    value = service.create(request('生成风险报告'), 'old-mode')
    turn = value['turns'][0]
    assert 'report_package' not in turn and 'case_reference_root' not in turn
    assert 'case_reference_binding_sha256' not in value
    assert service.get(value['conversation_id']) == value


def test_case_mode_cannot_silently_switch_to_fixed_cache_or_checked_manual_selection(answer_source, tmp_path):
    references, _, _ = answer_source
    with pytest.raises(ValueError, match='mutually exclusive'):
        DailyConversationBackend(SealedStore(), policy_as_of=date(2026, 11, 13),
            case_reference_service=references, reference_cache=tmp_path/'old-cache.json')
    _, _, service = configured(references)
    value = service.create(request(), 'case-reference-first')
    with pytest.raises(ConversationError, match='public_reference_unconfigured'):
        service.set_reference_answer(value['conversation_id'], {'decision': 'cite_reference',
            'reason': 'reference_explanation', 'citation_ids': ['R1']})
