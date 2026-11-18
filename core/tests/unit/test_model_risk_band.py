from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from graphrisk.serving.model_risk_band import MODELS, PROFILE_SHA, model_risk_band


def sample(version='x3-fold-2-historical-v1', value=.1):
    scope=SimpleNamespace(primary_model_version=version,dataset_source='ieee_cis',model_revision=PROFILE_SHA,
                          case_alias='a'*32,target_event_second=MODELS[version][1]+1)
    fact=SimpleNamespace(metric='primary_model_score',availability='available',subject='target_transaction',unit='uncalibrated_score',
                         temporal_scope='target_event',model_version=version,source_version=PROFILE_SHA,value=value)
    evidence=SimpleNamespace(case_alias=scope.case_alias,facts=[fact])
    fold='fold-2' if 'fold-2' in version else 'fold-3'
    bindings=[(Path('C:/data/coverage-profile-calendar-v2.json'),PROFILE_SHA),
              (Path('C:/data')/fold/'x3/model.json',MODELS[version][0])]
    return scope,evidence,bindings


@pytest.mark.parametrize('version',list(MODELS))
def test_exact_boundaries_and_model_binding(version):
    s,e,b=sample(version); _,_,lower,higher=MODELS[version]
    for score,expected in [(0.,'low'),(lower,'medium'),(higher,'high'),(1.,'high')]:
        e.facts[0].value=score
        assert model_risk_band(s,e,b)['risk_level']==expected


@pytest.mark.parametrize('change',['early','equal_time','missing_time','model','profile','source','wrong_fold','unbound_model','missing','duplicate','nan','bool','out_of_range','alias'])
def test_fails_closed_without_supported_scope_and_facts(change):
    s,e,b=sample()
    if change=='early':s.target_event_second=1
    elif change=='equal_time':s.target_event_second=MODELS[s.primary_model_version][1]
    elif change=='missing_time':s.target_event_second=None
    elif change=='model':s.primary_model_version='some-other-model'
    elif change=='profile':s.model_revision='b'*64
    elif change=='source':e.facts[0].source_version='b'*64
    elif change=='wrong_fold':b[1]=(Path('C:/data/fold-3/x3/model.json'),MODELS[s.primary_model_version][0])
    elif change=='unbound_model':b[1]=(b[1][0],'0'*64)
    elif change=='missing':e.facts=[]
    elif change=='duplicate':e.facts.append(deepcopy(e.facts[0]))
    elif change=='nan':e.facts[0].value=float('nan')
    elif change=='bool':e.facts[0].value=True
    elif change=='out_of_range':e.facts[0].value=1.01
    elif change=='alias':e.case_alias='b'*32
    assert model_risk_band(s,e,b)['status']=='unavailable'


def test_readable_export_and_streamlit_render_without_network(monkeypatch):
    from streamlit.testing.v1 import AppTest
    import graphrisk.demo.conversation_workbench as ui
    from test_readability import case
    value=case();value['model_band']={'status':'available','risk_level':'high','version':'v1'}
    before=deepcopy(value)
    monkeypatch.setattr(ui,'api_call',lambda *a,**k:pytest.fail('must not make network requests'))
    text=ui.readable_report(value)
    assert 'XGBoost 模型风险档位（试用）：高' in text and 'LLM可疑程度' in text
    app=AppTest.from_string('import streamlit as st\nfrom graphrisk.demo.conversation_workbench import show_conversation\nshow_conversation(st.session_state["case"])')
    app.session_state['case']=value
    app.run(timeout=10)
    assert not app.exception and any('XGBoost 模型风险档位（试用）：高' in e.value for e in app.markdown)
    app.run(timeout=10)
    assert not app.exception and value==before


def test_unavailable_is_displayed_without_inventing_level():
    from graphrisk.demo.conversation_workbench import model_band_lines
    lines=model_band_lines({'model_band':{'status':'unavailable'}})
    assert '暂不可用' in lines[0] and '低' not in lines[0]
    assert model_band_lines({})==[]


def test_http_projection_is_ephemeral_and_preserves_stored_report(setup):
    from test_conversations import client_for, start
    service,backend,store,_=setup
    backend.model_band=lambda alias,binding,evidence: {'status':'available','risk_level':'medium','version':'v1'}
    with client_for(service) as client:
        value=start(client)
        assert value['model_band']['risk_level']=='medium'
        stored=deepcopy(store.get(value['conversation_id']))
        assert 'model_band' not in stored
        response=client.get('/api/v1/conversations/'+value['conversation_id'])
        assert response.json()['model_band']['risk_level']=='medium'
        assert store.get(value['conversation_id'])==stored


from test_conversations import setup, scope, index, registry  # noqa: E402,F401 -- shares synthetic fixtures
