"""Conversation dispatch only; these fakes do not assess report writing."""
from datetime import date
from types import SimpleNamespace

import pytest

from test_daily_conversation_backend import SealedStore, request
from graphrisk.agent import narrative
from graphrisk.serving.conversation_store import MemoryConversationStore
from graphrisk.serving.conversations import ConversationError, ConversationService, FollowUp
from graphrisk.serving.daily_conversation_backend import DailyConversationBackend


def test_day_formal_dispatch_reserves_one_draft_and_does_not_resend(monkeypatch):
    delegate = SimpleNamespace(allow_investigation=True, entries={}, authorize=lambda: None,
        max_narrative_calls=1,report_contract='program-facts-judgment-v3')
    backend = DailyConversationBackend(SealedStore(), policy_as_of=date(2026,11,6),
                                       narrative_backend=delegate)
    service = ConversationService(MemoryConversationStore(),backend)
    calls = []
    def run(actual, alias, binding, turn_id, version):
        assert actual is delegate
        entry = actual.entries[alias]
        assert entry['evidence'].evidence_version == version
        assert entry['scope'].binding_sha256 == binding
        calls.append(turn_id)
        return dict(evidence=entry['evidence'],report={'version':'test-only-routing-output'},
                    model_calls=1,tool_calls=0,status='review_required')
    monkeypatch.setattr(narrative,'run_narrative',run)
    value = service.create(request('生成风险报告'),'formal-day')
    assert value['reserved_model_calls'] == 1
    assert value['turns'][0]['actual_model_calls'] == 1
    assert value['turns'][0]['outcome'] == 'review_required'
    assert service.create(request('生成风险报告'),'formal-day') == value
    assert len(calls) == 1
    follow = FollowUp(parent_turn_id=value['turns'][-1]['turn_id'],
        evidence_version=value['evidence']['evidence_version'],message='继续核查')
    with pytest.raises(ConversationError,match='investigation_generation_disabled'):
        service.follow(value['conversation_id'],follow,'no-new-facts')
    assert service.get(value['conversation_id'])['reserved_model_calls'] == 1


def test_disabled_day_formal_backend_stays_free_and_local(monkeypatch):
    delegate = SimpleNamespace(allow_investigation=False)
    backend = DailyConversationBackend(SealedStore(),policy_as_of=date(2026,11,6),
                                      narrative_backend=delegate)
    monkeypatch.setattr(narrative,'run_narrative',lambda *args: pytest.fail('must stay offline'))
    service = ConversationService(MemoryConversationStore(),backend)
    value = service.create(request('生成风险报告'),'offline-day')
    assert value['reserved_model_calls'] == 0
    assert value['turns'][0]['report']['llm_generated'] is False


@pytest.mark.parametrize('limit', [None, True, 2])
def test_invalid_delegate_cannot_reserve_or_call_twice(limit, monkeypatch):
    delegate = SimpleNamespace(allow_investigation=True,max_narrative_calls=limit,
        report_contract='program-facts-judgment-v3',authorize=lambda:pytest.fail('invalid configuration'))
    backend = DailyConversationBackend(SealedStore(),policy_as_of=date(2026,11,6),
                                      narrative_backend=delegate)
    service = ConversationService(MemoryConversationStore(),backend)
    with pytest.raises(ConversationError,match='daily_narrative_configuration_invalid'):
        service.create(request('生成风险报告'),'invalid-delegate')
    assert all(value['reserved_model_calls'] == 0 for value in service.store.values.values())
