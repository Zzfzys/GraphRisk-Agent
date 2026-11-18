"""Read-only supplementary checks: saved predictions, real HTTP, UI and old hashes."""
import hashlib
import json
from pathlib import Path
import socket
import sys
import threading
import time
from unittest.mock import patch

import httpx
import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest
import uvicorn

from graphrisk.agent.config import load_stage05_config
from graphrisk.demo.api_client import ApiReply,Stage06ApiClient
from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.graph.deployment import load_policy,verify_files
from graphrisk.models.enhanced_execution import binding,checked_json,sealed
from graphrisk.serving.investigation_service import Stage06Service,InvestigationRecord
from graphrisk.serving.investigation_views import project_investigation


def main():
    root=Path('E:/GraphRisk-Agent');source=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(source))
    report=root/'reports/generated/stage09/route5b/20261015-coverage-verification-v3'
    out=root/'reports/generated/stage09/route5b/20261015-coverage-delivery-v1'
    if (out/'delivery.json').exists():raise ValueError('delivery evidence already exists; never overwrite')
    accepted=checked_json(report/'acceptance.json');cases=checked_json(report/'frozen-cases.json')
    config=load_stage05_config(root/'configs/stage09_coverage.yaml')
    profile=checked_json(config.resolve_path(config.model_policy))
    scores=pd.read_parquet(root/profile['scores_path']).set_index('transaction_id')
    deltas=[]
    for number,case in enumerate(cases['cases'][:4]):
        actual=checked_json(report/('prospective-'+str(number)+'.json'))
        expected=scores.loc[case['source_id']]
        error=[abs(float(v)-float(expected['graph_seed_'+str(seed)]))
            for seed,v in zip(profile['seeds'],actual['graph_seed_scores'],strict=True)]
        assert max(error)<=1e-6
        deltas.append(dict(source_id=case['source_id'],primary_abs_delta=abs(actual['risk_score']-case['expected_primary']),
            graph_seed_abs_deltas=error,graph_mean_abs_delta=abs(actual['graph_risk_score']-case['expected_graph'])))
    legacy=load_stage05_config(root/'configs/stage09_quality_4b.yaml')
    oldprofile=load_policy(root,legacy.resolve_path(legacy.model_policy),legacy.model_policy_sha256)
    verify_files(root,oldprofile['primary_files']);verify_files(root,oldprofile['graph_files'])
    for name in ('stage05.yaml','stage09.yaml','stage09_quality_4b.yaml'):
        archived=root/'artifacts/stage-09/route5b/20261015-coverage-lowmem-v3/source-snapshot/configs'/name
        assert binding(source/'configs'/name)==binding(archived)
    old_count,old_reports,old_approvals=0,0,0
    for file in (root/'reports/generated/stage09/route5/20261007-replay-v1/results').glob('*.json'):
        raw=json.loads(file.read_text(encoding='utf-8'))
        if not raw.get('record'):continue
        record=InvestigationRecord.model_validate(raw['record']);old_count+=1
        if record.result.report_run and record.result.report_run.report:
            digest=hashlib.sha256(record.result.report_run.report.model_dump_json().encode()).hexdigest()
            assert digest==record.report_sha256
            old_reports+=1
        if record.approval and record.approval.payload:
            assert record.approval.payload.report_sha256==record.report_sha256
            old_approvals+=1
        assert project_investigation(record).model_dump(mode='json')==raw['public_view']
    assert old_reports>0 and old_approvals>0
    # Actual saved HTTP views (with a deterministic report fixture) drive native
    # workbench rendering; the HTTP adapter alone is injected, not model values.
    view=json.loads((report/'http-view-3400378.json').read_text(encoding='utf-8'))
    with patch.object(Stage06ApiClient,'get_investigation',return_value=ApiReply(200,view)), \
            patch.object(Stage06ApiClient,'get_approval',return_value=ApiReply(200,view['approval'])):
        app=AppTest.from_file(source/'streamlit_app.py',default_timeout=30)
        app.query_params['run_id']=view['run_id'];app.run()
        assert not app.exception
        assert any('无独立分数' in str(frame.value) and '仅关系' in str(frame.value) for frame in app.dataframe)
        assert any(metric.label=='XGBoost 主风险分数' for metric in app.metric)
        assert any(metric.label=='GraphSAGE 实验性分数' for metric in app.metric)
        dataframe=app.dataframe[0].value;dataframe.to_csv(out/'workbench-neighbors.csv',index=False,encoding='utf-8')
    service=Stage06Service.from_config(root/'configs/stage09_coverage.yaml')
    from api.main import create_app
    app=create_app(lambda:service)
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(16)
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    until=time.monotonic()+20
    while not server.started and thread.is_alive() and time.monotonic()<until:time.sleep(.05)
    if not server.started:raise RuntimeError('local API did not start')
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=30,trust_env=False) as http:
            ready=http.get('/api/v1/ready');assert ready.status_code==200,ready.text
            write_json(out/'http-ready.json',ready.json())
            graph=service.runner.investigation.graph_service
            target=cases['cases'][0]['source_id']
            key=graph.history.selected_history(graph.history.graph_row(target))[0]['transaction_id']
            assert http.get('/api/v1/coverage/'+key).json()['status']=='out_of_coverage'
            stopped=http.post('/api/v1/investigations',json={'transaction_id':key,'dataset_source':'ieee_cis','policy_as_of':'2026-07-01'})
            assert stopped.status_code==409 and stopped.json()['error_code']=='prediction_out_of_coverage'
            assert not service._records
            write_json(out/'http-out-of-coverage.json',stopped.json())
    finally:
        server.should_exit=True;thread.join(timeout=15);sock.close()
    assert not thread.is_alive()
    value=sealed(dict(schema_version='stage09-coverage-delivery-v1',status='passed',
        acceptance_sha256=accepted['content_sha256'],profile_sha256=profile['content_sha256'],
        per_seed_consistency=deltas,legacy_records=old_count,legacy_report_hashes=old_reports,
        legacy_approval_hashes=old_approvals,legacy_public_views_unchanged=True,
        old_profile_bindings_passed=True,old_configs_unchanged=True,readiness='ready',
        real_http_out_of_coverage_status=409,ui='AppTest_with_real_HTTP_view_fixture_generation',
        new_fits=0,real_generation_calls=0,paid_calls=0,postgres_live_regression='not_run_in_route6',
        outputs={p.name:binding(p) for p in out.iterdir() if p.suffix in ('.csv','.json')}))
    write_json(out/'delivery.json',value);print(json.dumps(value),flush=True)


if __name__=='__main__':main()
