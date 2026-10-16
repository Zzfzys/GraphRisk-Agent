"""HTTP boundary mechanisms; fixtures are not evidence of Agent/model quality."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from graphrisk.graph.coverage_service import CoverageArtifactError


class Graph:
    def __init__(self):self.calls=0;self.failure=None
    def coverage(self,key):
        return dict(status='out_of_coverage' if key=='train' else 'covered',transaction_id=key)
    def prospective(self,event):
        self.calls+=1
        if self.failure:raise self.failure('private/path/secret must not escape')
        return dict(scope='prospective_replay',risk_score=.4,label_read=False,history_mutated=False)


@pytest.fixture
def client():
    graph=Graph()
    service=SimpleNamespace(runner=SimpleNamespace(investigation=SimpleNamespace(graph_service=graph)),close=lambda:None)
    with TestClient(create_app(lambda:service)) as client:
        yield client,graph


def event():
    return dict(transaction_id='new',source_transaction_dt_seconds=100,transaction_amount=20.,
        card_id='unknown-card-new',device_id='unknown-device-new',email_domain='unknown-email-new',
        address_id='unknown-address-new',product_code='W')


def test_read_only_endpoints_distinguish_scope_and_do_not_generate_or_approve(client):
    http,graph=client
    assert http.get('/api/v1/coverage/train').json()['status']=='out_of_coverage'
    assert http.get('/api/v1/coverage/heldout').json()['status']=='covered'
    response=http.post('/api/v1/predictions/prospective',json=event())
    assert response.status_code==200 and response.json()['history_mutated'] is False
    assert graph.calls==1 and 'run_id' not in response.json()


def test_out_of_coverage_investigation_stops_before_runner_or_generation(client):
    http,graph=client
    response=http.post('/api/v1/investigations',json={
        'transaction_id':'train','dataset_source':'ieee_cis','policy_as_of':'2026-07-01','relation_limit':3})
    assert response.status_code==409 and response.json()['error_code']=='prediction_out_of_coverage'
    assert graph.calls==0


@pytest.mark.parametrize('error,status,code',[(MemoryError,503,'inference_resource_stop'),
    (BlockingIOError,429,'inference_busy'),(TimeoutError,504,'inference_timeout'),
    (CoverageArtifactError,503,'inference_unavailable'),(ValueError,422,'invalid_new_event')])
def test_predict_failure_categories_are_safe(client,error,status,code):
    http,graph=client;graph.failure=error
    response=http.post('/api/v1/predictions/prospective',json=event())
    assert response.status_code==status and response.json()['error_code']==code
    assert 'private' not in response.text


@pytest.mark.parametrize('change',[{'has_identity':'false'},{'raw_features':{'isFraud':1}},
    {'raw_features':{'hist_card_count_24h':0}}, {'source_transaction_dt_seconds':True},
    {'card_id':'unknown-card-another-event'}, {'transaction_amount':False}])
def test_bad_event_rejected_before_any_inference(client,change):
    http,graph=client
    response=http.post('/api/v1/predictions/prospective',json={**event(),**change})
    assert response.status_code==422 and graph.calls==0
