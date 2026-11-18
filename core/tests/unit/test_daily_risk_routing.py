from types import SimpleNamespace

import pytest

from graphrisk.serving.conversations import ConversationError
from graphrisk.serving.daily_risk_service import RoutedConversationService


def test_shared_store_routes_by_bound_evidence_context():
    class Store:
        def get(self, identifier):
            if identifier not in ('old', 'day'):
                raise ConversationError('conversation_not_found')
            return {'evidence': {'scoring_context': {}} if identifier == 'day' else {}}
    class Service:
        def __init__(self, name): self.name, self.store = name, Store()
        def get(self, identifier): return self.name
        def follow(self, identifier, request, key): return self.name
        def create(self, request, key): return self.name
    router = RoutedConversationService(Service('daily'), Service('x3'))
    assert router.get('old') == 'x3'
    assert router.get('day') == 'daily'
    assert router.follow('old', None, 'key') == 'x3'
    assert router.create(SimpleNamespace(risk_profile='daily_batch'), 'key') == 'daily'
    assert router.create(SimpleNamespace(risk_profile='x3'), 'key') == 'x3'
    with pytest.raises(ConversationError, match='conversation_not_found'):
        router.get('missing')


def test_x3_conversation_route_is_explicitly_unconfigured():
    with pytest.raises(ConversationError, match='conversation_service_unconfigured'):
        RoutedConversationService(object()).create(SimpleNamespace(risk_profile='x3'), 'key')
