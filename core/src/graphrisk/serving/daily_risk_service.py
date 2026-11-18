"""Application adapter: the server owns arrival time and batch closure."""
from __future__ import annotations

from collections.abc import Callable

from graphrisk.serving.conversations import ConversationError


class DailyRiskService:
    def __init__(self, store, *, clock: Callable[[], int], fallback=None):
        self.store, self.clock, self.fallback = store, clock, fallback

    def _second(self):
        second = self.clock()
        if isinstance(second, bool) or not isinstance(second, int) or second < 0:
            raise ValueError('invalid server arrival clock')
        return second

    def ingest(self, event):
        return self.store.ingest(event, received_second=self._second())

    def seal(self, day):
        return self.store.seal(day, available_through_second=self._second())

    def list_batches(self):
        return self.store.list_batches()

    def get_day(self, day):
        return self.store.get_day(day)

    def quarantined(self):
        return self.store.quarantined()

    def get_transaction(self, day, transaction_id, mode='daily'):
        if mode == 'daily':
            return self.store.get_transaction(day, transaction_id, mode='daily')
        if mode != 'x3' or self.fallback is None:
            raise ValueError('requested fallback is unavailable')
        return self.fallback.get_transaction(day, transaction_id, 'x3')


class RoutedConversationService:
    """Route new batch conversations separately from existing x3 sessions."""
    def __init__(self, daily, x3=None):
        self.daily, self.x3 = daily, x3

    def create(self, request, key):
        service = self.daily if request.risk_profile == 'daily_batch' else self.x3
        if service is None:
            raise ConversationError('conversation_service_unconfigured')
        return service.create(request, key)

    def _service(self, identifier):
        for service in (self.daily, self.x3):
            if service is None:
                continue
            try:
                record = service.store.get(identifier)
            except ConversationError as error:
                if str(error) != 'conversation_not_found':
                    raise
            else:
                is_daily = record.get('evidence', {}).get('scoring_context') is not None
                if is_daily != (service is self.daily):
                    continue
                return service
        raise ConversationError('conversation_not_found')

    def get(self, identifier):
        return self._service(identifier).get(identifier)

    def follow(self, identifier, request, key):
        return self._service(identifier).follow(identifier, request, key)
