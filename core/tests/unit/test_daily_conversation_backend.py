from copy import deepcopy
from datetime import date

import pytest
from pydantic import ValidationError

from graphrisk.agent.autonomous_actions import compact_view
from graphrisk.agent.autonomous_evidence import EvidenceBundle
from graphrisk.agent.autonomous_tools import InvestigationScope
from graphrisk.agent.fact_support import attach_contract, assess_statement, source_issues
from graphrisk.agent.narrative import check_factual_prose
from graphrisk.agent.upgrade_contract import canonical, digest
from graphrisk.serving.conversation_store import MemoryConversationStore
from graphrisk.serving.conversations import ConversationError, ConversationService, CreateConversation, FollowUp
from graphrisk.serving.daily_conversation_backend import DailyConversationBackend, daily_report


class SealedStore:
    def __init__(self):
        self.row = dict(transaction_id='123', event_second=172805, transaction_amount=17.0,
            relative_day=2, batch_end_second=259200, available_through_second=259201,
            batch_id='day-2-aabb', revision=1, sealed_content_sha256='a'*64,
            model_version='champion-xgb-day-216-plus49-fold3-v1', model_sha256='b'*64,
            feature_protocol_sha256='c'*64, category_maps_sha256='d'*64,
            policy_sha256='e'*64, calibrated_probability=False, score=0.8, risk_level='high',
            temporal_scope='relative_day_end')

    def resolve_transaction(self, identifier):
        if identifier != self.row['transaction_id']:
            raise ValueError('unknown transaction')
        return deepcopy(self.row)

    def get_transaction(self, day, identifier):
        assert day == self.row['relative_day']
        return self.resolve_transaction(identifier)

    def list_batches(self):
        return [deepcopy(self.row)]

    def get_day(self, day):
        assert day == self.row['relative_day']
        return {**deepcopy(self.row), 'results': [deepcopy(self.row)]}


@pytest.fixture
def daily():
    store = SealedStore()
    backend = DailyConversationBackend(store, policy_as_of=date(2026, 11, 6))
    return store, backend, ConversationService(MemoryConversationStore(), backend)


def request(message='调查风险', batch_id='day-2-aabb'):
    return CreateConversation(transaction_id='123', message=message,
                              risk_profile='daily_batch', batch_id=batch_id)


def packet_for(backend, bundle):
    scope = backend.entries[bundle.case_alias]['scope']
    view, refs = compact_view(bundle, task='explain_evidence', profile={'max_policy_paragraphs': 3})
    packet = dict(view=view, refs_private=refs, scope_private=scope.model_dump(mode='json'),
                  source_evidence_sha256=digest(bundle.model_dump(mode='json')))
    attach_contract(packet, bundle)
    return packet


def test_legacy_scope_and_no_context_serialization_keep_original_binding():
    original = dict(case_alias='a'*32, transaction_id='123', dataset_source='ieee_cis',
        target_event_second=10, policy_as_of='2026-11-06', transaction_revision='b'*64,
        feature_revision='c'*64, model_revision='d'*64, primary_model_version='xgb-v1',
        graph_model_version='graph-v1', policy_index_version='e'*64)
    scope = InvestigationScope.model_validate_json(canonical(original), strict=True)
    assert scope.model_dump(mode='json') == original
    assert scope.binding_sha256 == digest(original)
    bundle = EvidenceBundle(case_alias='a'*32, evidence_version='b'*64, policy_as_of='2026-11-06',
        facts=(), policies=(), tool_outcomes=(), limitations=())
    assert 'scoring_context' not in bundle.model_dump(mode='json')
    assert 'scoring_context' not in bundle.model_dump_json()


def test_registered_daily_evidence_has_real_signal_and_explicit_unmeasured_tools(daily):
    store, backend, service = daily
    value = service.create(request(), 'daily-create')
    assert value['scoring_context'] == value['evidence']['scoring_context']
    context = value['scoring_context']
    assert context['batch_end_second'] == 259200 and context['revision'] == 1
    facts = value['evidence']['facts']
    score = next(fact for fact in facts if fact['metric'] == 'primary_model_score')
    assert (score['value'], score['temporal_scope'], score['source_version']) == (0.8, 'relative_day_end', 'b'*64)
    assert all(fact['availability'] == 'not_measured' and fact['missing_reason'] == 'not_requested'
               for fact in facts if fact['source_tool'] not in ('get_transaction', 'get_risk_score'))
    assert all(outcome['status'] == 'not_requested' for outcome in value['evidence']['tool_outcomes']
               if outcome['tool'] not in ('get_transaction', 'get_risk_score'))
    assert value['reserved_model_calls'] == 0
    assert value['turns'][0]['answer']['llm_generated'] is False
    assert 'transaction_id' not in canonical(service.store.values)
    assert 'transaction_amount' in canonical(value['evidence'])


def test_free_daily_report_create_follow_replay_and_restart(daily):
    source, backend, service = daily
    first = service.create(request(), 'daily-create')
    follow = FollowUp(parent_turn_id=first['turns'][-1]['turn_id'],
        evidence_version=first['evidence']['evidence_version'], message='生成风险报告')
    result = service.follow(first['conversation_id'], follow, 'daily-report')
    report = result['turns'][-1]['report']
    assert report['llm_generated'] is False and report['source'] == 'program_from_sealed_batch'
    assert report == daily_report(EvidenceBundle.model_validate_json(canonical(result['evidence'])))
    assert any('日终' in fact['text'] for fact in report['facts'])
    assert result['reserved_model_calls'] == result['reserved_tool_calls'] == 0
    assert result['turns'][-1]['actual_model_calls'] == result['turns'][-1]['actual_tool_calls'] == 0
    assert service.follow(first['conversation_id'], follow, 'daily-report') == result
    restarted = ConversationService(service.store, DailyConversationBackend(source, policy_as_of=date(2026, 11, 6)))
    assert restarted.get(first['conversation_id']) == result
    direct = service.create(request('生成风险报告'), 'daily-direct-report')
    assert direct['turns'][0]['outcome'] == 'program_daily_report'


@pytest.mark.parametrize('field,value', [('score', 0.7), ('category_maps_sha256', 'f'*64),
    ('available_through_second', 259202), ('transaction_amount', 18.0), ('revision', 2)])
def test_changed_sealed_binding_prevents_old_conversation_read(daily, field, value):
    source, backend, service = daily
    first = service.create(request(), 'daily-create')
    source.row[field] = value
    with pytest.raises(ConversationError, match='source_version_changed'):
        service.get(first['conversation_id'])


def test_batch_selection_and_no_automatic_live_generation(daily):
    source, backend, service = daily
    with pytest.raises(ConversationError, match='conversation_service_unconfigured'):
        service.create(CreateConversation(transaction_id='123'), 'wrong-model-profile')
    with pytest.raises(ConversationError, match='batch_selection_mismatch'):
        service.create(request(batch_id='other-day'), 'daily-create')
    first = service.create(request(), 'daily-create')
    follow = FollowUp(parent_turn_id=first['turns'][-1]['turn_id'],
        evidence_version=first['evidence']['evidence_version'], message='继续核查')
    with pytest.raises(ConversationError, match='investigation_generation_disabled'):
        service.follow(first['conversation_id'], follow, 'daily-continue')
    assert service.get(first['conversation_id'])['reserved_model_calls'] == 0


@pytest.mark.parametrize('tamper', ['event-time', 'no-context', 'score', 'watermark', 'boundary'])
def test_daily_contract_rejects_event_time_relabelling_and_invalid_provenance(daily, tamper):
    _, backend, _ = daily
    raw = backend.resolve('123').evidence.model_dump(mode='json')
    score = next(fact for fact in raw['facts'] if fact['metric'] == 'primary_model_score')
    if tamper == 'event-time': score['temporal_scope'] = 'target_event'
    elif tamper == 'no-context': raw.pop('scoring_context')
    elif tamper == 'score': score['value'] = 0.7
    elif tamper == 'watermark': raw['scoring_context']['available_through_second'] = 259199
    else: raw['scoring_context']['batch_end_second'] = 259201
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate_json(canonical(raw), strict=True)


def test_fact_support_and_narrative_preserve_day_end_semantics(daily):
    _, backend, _ = daily
    bundle = backend.resolve('123').evidence
    packet = packet_for(backend, bundle)
    assert source_issues(packet, bundle) == []
    score = next(fact for fact in bundle.facts if fact.metric == 'primary_model_score')
    ref = next(ref for ref, identifier in packet['refs_private'].items() if identifier == score.fact_id)
    positive = assess_statement('basis.0', '日终XGBoost主评分为0.8，是未校准信号', [ref], packet, bundle)
    assert positive.issues == () and positive.witnesses[0]['temporal_scope'] == 'relative_day_end'
    missing_basis = assess_statement('basis.0', '主评分为0.8，是未校准信号', [ref], packet, bundle)
    assert 'day_end_score_must_be_explicit' in {issue['code'] for issue in missing_basis.issues}
    assert 'day_end_score_not_available_at_target_event' in check_factual_prose(
        '本笔交易时评分已经可用', [ref], packet, bundle)
    assert 'day_end_score_not_available_at_target_event' not in check_factual_prose(
        '不能把日终评分视为交易时可用的即时评分', [ref], packet, bundle)


def test_real_arrival_journal_connects_to_new_evidence_and_preserves_late_isolation(tmp_path):
    from test_daily_batch_store import candidate, event
    from graphrisk.serving.daily_batch_store import DailyBatchStore
    source = DailyBatchStore(tmp_path/'journal.sqlite', candidate(tmp_path),
                            history_cutoff_second=0, scratch=tmp_path/'work')
    source.ingest(event('123', 86401), received_second=172801)
    sealed = source.seal(1, available_through_second=172802)
    backend = DailyConversationBackend(source, policy_as_of=date(2026, 11, 6))
    service = ConversationService(MemoryConversationStore(), backend)
    value = service.create(CreateConversation(transaction_id='123', message='生成风险报告',
        risk_profile='daily_batch', batch_id=sealed['batch_id']), 'actual-journal-case')
    assert value['scoring_context']['available_through_second'] == 172802
    assert value['turns'][0]['report']['llm_generated'] is False
    source.ingest(event('124', 86402), received_second=172803)
    assert service.get(value['conversation_id']) == value
    with pytest.raises(ConversationError, match='transaction_outside_registered_cases'):
        backend.resolve('124')
