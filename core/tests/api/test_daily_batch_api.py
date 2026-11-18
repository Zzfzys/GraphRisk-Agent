from fastapi.testclient import TestClient
from api.main import create_app
from graphrisk.serving.daily_risk_service import DailyRiskService


def test_daily_batch_requires_configured_closed_server_batch():
    class Batch:
        def get_day(self, day):
            if day != 3:
                raise ValueError('not closed')
            return {'relative_day': day, 'temporal_scope': 'relative_day_end',
                    'results': [{'transaction_id': '42', 'risk_level': 'high'}]}
        def get_transaction(self, day, transaction_id, mode):
            return {'relative_day': day, 'transaction_id': transaction_id, 'model_version': mode}
    with TestClient(create_app()) as client:
        assert client.get('/api/v1/risk-batches/3').status_code == 503
    with TestClient(create_app(daily_risk_service_factory=Batch)) as client:
        result = client.get('/api/v1/risk-batches/3')
        assert result.status_code == 200
        assert result.json()['temporal_scope'] == 'relative_day_end'
        assert result.json()['results'][0]['risk_level'] == 'high'
        assert client.get('/api/v1/risk-batches/4').status_code == 409
        assert client.get('/api/v1/risk-batches/3/transactions/42?model=x3').json()['model_version'] == 'x3'
        assert client.get('/api/v1/risk-batches/3/transactions/42?model=daily').json()['model_version'] == 'daily'
        assert client.get('/api/v1/risk-batches/3/transactions/abc').status_code == 422


def test_arrival_and_seal_use_server_clock_not_request_watermarks():
    seen = []
    class Store:
        def ingest(self, event, *, received_second):
            seen.append(('arrival', received_second))
            return {'status': 'accepted', 'transaction_id': event.transaction_id}
        def seal(self, day, *, available_through_second):
            seen.append(('seal', available_through_second))
            raise ValueError('daily batch is not closed')
        def quarantined(self): return []
        def list_batches(self): return []
    service = DailyRiskService(Store(), clock=lambda: 100)
    event = {'transaction_id': '9001', 'source_transaction_dt_seconds': 90,
             'transaction_amount': 5.0, 'card_id': 'card-a', 'device_id': 'device-b',
             'email_domain': 'example.invalid', 'address_id': 'address-c', 'product_code': 'W'}
    with TestClient(create_app(daily_risk_service_factory=lambda: service)) as client:
        assert client.post('/api/v1/risk-batches/transactions', json=event).status_code == 201
        for invalid in ('order-1', '１２', '9' * 19):
            assert client.post('/api/v1/risk-batches/transactions', json={**event, 'transaction_id': invalid}).status_code == 422
        assert client.post('/api/v1/risk-batches/transactions', json={**event, 'received_second': 999999}).status_code == 422
        result = client.post('/api/v1/risk-batches/1/seal', json={'available_through_second': 999999})
        assert result.status_code == 409
        assert result.json()['detail']['code'] == 'batch_not_closed'
        assert client.get('/api/v1/risk-batches/late-arrivals').json() == {'late_arrivals': []}
    assert seen == [('arrival', 100), ('seal', 100)]


def test_history_cutoff_returns_actionable_error():
    class Service:
        def ingest(self, event):
            raise ValueError('new event day must follow the bound historical cutoff day')
    event = {'transaction_id': '9001', 'source_transaction_dt_seconds': 90,
             'transaction_amount': 5.0, 'card_id': 'card-a', 'device_id': 'device-b',
             'email_domain': 'example.invalid', 'address_id': 'address-c', 'product_code': 'W'}
    with TestClient(create_app(daily_risk_service_factory=Service)) as client:
        result = client.post('/api/v1/risk-batches/transactions', json=event)
        assert result.status_code == 409
        assert result.json()['detail']['code'] == 'event_in_bound_history'
