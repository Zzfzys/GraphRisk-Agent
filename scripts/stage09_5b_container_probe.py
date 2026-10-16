"""Isolated container acceptance. The report provider is explicitly a fixture."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import httpx
import psycopg
import uvicorn

from graphrisk.agent.quality_contracts import QualityDraft
from graphrisk.agent.quality_reporting import QualityProviderResult
from graphrisk.demo.api_client import Stage06ApiClient
from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.models.enhanced_execution import binding,sealed
from graphrisk.serving.postgres_service import PostgresInvestigationService


OUT=Path('/evidence')
CONFIG=Path('/app/configs/stage09_container.yaml')


class FixtureProvider:
    def generate(self,prompt,schema):
        packet=json.loads(prompt.split('\nEVIDENCE_JSON:\n',1)[1]);facts=packet['facts']
        h,r,g=(facts[k]['value'] for k in ('F2','F4','F5'))
        draft=QualityDraft(policy_ref='P1',history_activity='unavailable' if h is None else 'observed' if h['tx_count_last_7d'] else 'none',
            relationship_evidence='unavailable' if r is None else 'observed' if r['returned_count'] else 'none',
            graph_evidence='available' if g else 'unavailable',policy_applicability='not_established',
            risk_interpretation='uncalibrated_signal',evidence_refs=['F2','F3','F4','F5'],
            unknowns=['policy_conditions','verified_identity','calibration']+([] if g else ['graph_evidence']))
        counter=OUT/'fixture-calls.json'
        calls=json.loads(counter.read_text())['deterministic_fixture_calls'] if counter.exists() else 0
        write_json(counter,dict(deterministic_fixture_calls=calls+1,real_generation_calls=0,paid_calls=0))
        return QualityProviderResult(draft=draft,model_id='route7-container-fixture',elapsed_ms=0,estimated_cost_cny=0)


def serve():
    import api.main as api
    if Path(api.__file__).resolve()!=Path('/code/api/main.py'):
        raise RuntimeError('container did not load the active read-only source')
    dsn=Path('/run/secrets/runtime-dsn').read_text().strip()
    service=PostgresInvestigationService.from_config(CONFIG,dsn=dsn,max_runs=1000)
    service.runner.provider=FixtureProvider()
    service.versions=service.versions.model_copy(update={'generation_model_id':'route7-container-fixture','generation_model_revision':None})
    write_json(OUT/'runtime-identity.json',dict(python=sys.version.split()[0],api_file=str(api.__file__),
        api_binding=binding(Path(api.__file__)),service_binding=binding(Path(sys.modules[PostgresInvestigationService.__module__].__file__)),
        model_policy_sha256=service.runner.investigation.graph_service.profile['content_sha256'],real_generation_calls=0))
    uvicorn.run(api.create_app(lambda:service),host='0.0.0.0',port=8000,access_log=False,log_level='warning')


def client_checks():
    views=[]
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=90,trust_env=False) as http:
        ready=http.get('/api/v1/ready');assert ready.status_code==200,ready.text
        write_json(OUT/'ready.json',ready.json())
        for key,fold in [('3400378','fold-2'),('3488959','fold-3')]:
            covered=http.get('/api/v1/coverage/'+key);assert covered.status_code==200
            assert fold in covered.json()['primary_model_version']
            response=http.post('/api/v1/investigations',json=dict(transaction_id=key,dataset_source='ieee_cis',policy_as_of='2026-07-01',relation_limit=20))
            assert response.status_code==201,response.text
            body=response.json();assert body['status']=='verified'
            assert body['report']['graph']['fusion_score'] is None
            assert fold in body['versions']['primary_model_version'] and fold in body['versions']['experimental_graph_model_version']
            assert body['report']['model']['model_version']==body['versions']['primary_model_version']
            assert any(n.get('evidence_role')=='relation_only' and n['graph_risk_score'] is None for n in body['report']['graph']['neighbors'])
            approval=body['approval']
            action=dict(run_id=body['run_id'],session_id=approval['session_id'],transaction_id=key,
                report_sha256=approval['report_sha256'],decision='reject',reviewer_id='container-mechanism',note='Fixture report; no external action')
            wrong=http.post('/api/v1/investigations/'+body['run_id']+'/approval',json={**action,'report_sha256':'0'*64})
            assert wrong.status_code==409
            decision=http.post('/api/v1/investigations/'+body['run_id']+'/approval',json=action)
            assert decision.status_code==200 and decision.json()['status']=='rejected'
            reread=http.get('/api/v1/investigations/'+body['run_id']);assert reread.status_code==200
            assert reread.json()['report_sha256']==body['report_sha256']
            views.append(reread.json())
        assert http.get('/api/v1/coverage/not-a-registered-target').json()['status']=='out_of_coverage'
    dsn=Path('/run/secrets/runtime-dsn').read_text().strip()
    with psycopg.connect(dsn) as db:
        role=db.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone()
        assert role==(False,False,False)
        rows=db.execute('SELECT record_json FROM investigation_runs').fetchall()
        assert len(rows)==2
        for (record,) in rows:
            assert record['result']['graph_run'] is None and record['persisted_validation']
            text=json.dumps(record)
            assert 'EVIDENCE_JSON' not in text and 'isFraud' not in text and 'raw_features' not in text
    writable=[]
    for path in ('/app/data/interim/ieee-cis','/app/artifacts/stage-09/route5b'):
        probe=Path(path)/'route7-unexpected-write-probe'
        try:probe.write_text('must fail');writable.append(path);probe.unlink()
        except OSError:pass
    assert not writable
    write_json(OUT/'created-views.json',views)
    write_json(OUT/'seed-checks.json',sealed(dict(status='passed',created_records=2,folds=['fold-2','fold-3'],
        wrong_hash_http_status=409,limited_application_role=True,no_raw_graph_prompt_label_persisted=True,
        model_data_mounts_read_only=True,real_generation_calls=0,paid_calls=0)))


def restored_checks():
    expected=json.loads((OUT/'created-views.json').read_text())
    before=json.loads((OUT/'fixture-calls.json').read_text())
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=30,trust_env=False) as http:
        for view in expected:
            response=http.get('/api/v1/investigations/'+view['run_id'])
            assert response.status_code==200,response.text
            actual=response.json()
            assert actual['report']==view['report'] and actual['report_sha256']==view['report_sha256']
            assert actual['approval']['status']=='rejected'
            assert actual['versions']==view['versions']
    assert json.loads((OUT/'fixture-calls.json').read_text())==before
    write_json(OUT/'restore-checks.json',sealed(dict(status='passed',restored_records=len(expected),
        report_hash_unchanged=True,approval_terminal_state_unchanged=True,versions_unchanged=True,
        regeneration_calls=0,real_generation_calls=0,paid_calls=0)))


def ui_checks():
    from streamlit.testing.v1 import AppTest
    expected=json.loads((OUT/'created-views.json').read_text())
    # The native HTTP-only client uses the real local API; no response injection.
    with patch.dict('os.environ',{'GRAPHRISK_STAGE06_API_URL':'http://127.0.0.1:8000'}):
        for view in expected:
            app=AppTest.from_file('/code/streamlit_app.py',default_timeout=30)
            app.query_params['run_id']=view['run_id'];app.run()
            assert not app.exception
            assert any('无独立分数' in str(frame.value) and '仅关系' in str(frame.value) for frame in app.dataframe)
            assert any(metric.label=='XGBoost 主风险分数' for metric in app.metric)
            assert any('已人工拒绝' in message.value for message in app.warning)
    write_json(OUT/'ui-checks.json',sealed(dict(status='passed',real_http_views=2,transport_mocked=False,
        report_generation='deterministic_fixture',real_generation_calls=0)))


def prospective_checks():
    frozen=Path('/app/reports/generated/stage09/route5b/20261015-coverage-verification-v3/frozen-cases.json')
    case=json.loads(frozen.read_text(encoding='utf-8'))['cases'][0]
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=180,trust_env=False) as http:
        response=http.post('/api/v1/predictions/prospective',json=case['event'])
        write_json(OUT/'prospective-http.json',dict(http_status=response.status_code,body=response.json()))
        assert response.status_code==200,response.text
        value=response.json()
        tree_delta=abs(value['risk_score']-case['expected_primary'])
        graph_delta=abs(value['graph_risk_score']-case['expected_graph'])
        assert tree_delta<=1e-6 and graph_delta<=1e-6
        assert not value['label_read'] and not value['history_mutated']
    write_json(OUT/'prospective-checks.json',sealed(dict(status='passed',real_weight_events=1,
        tree_abs_delta=tree_delta,graph_mean_abs_delta=graph_delta,label_read=False,history_mutated=False,
        real_generation_calls=0,paid_calls=0)))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['serve','seed','restore','ui','prospective'])
    {'serve':serve,'seed':client_checks,'restore':restored_checks,'ui':ui_checks,'prospective':prospective_checks}[parser.parse_args().action]()
