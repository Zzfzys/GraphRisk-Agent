"""Freeze label-free replay cases, verify full coverage, and test real HTTP.

The report provider used for approval/display checks is an explicit deterministic
fixture. No real LLM generation, quality benchmark, paid call, or training occurs.
"""
import argparse
import gc
import json
import math
from pathlib import Path
import socket
import shutil
import sys
import threading
import time

import duckdb
import httpx
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import uvicorn

from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.components import build_components
from graphrisk.agent.quality_contracts import QualityDraft
from graphrisk.agent.quality_reporting import QualityProviderResult
from graphrisk.graph.coverage_build import check,model_for_time,verify_prepared
from graphrisk.graph.coverage_resources import CoverageMonitor
from graphrisk.graph.coverage_service import load_coverage
from graphrisk.models.enhanced_execution import binding,checked_json,sealed
from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.serving.investigation_service import Stage06Service


DEFAULT='20261015-coverage-lowmem-v3'
PARENT='20261010-confirmation-v1'


def serving_profile(root,closure):
    config=load_stage05_config(root/'configs/stage09_coverage.yaml')
    profile=load_coverage(root,config.resolve_path(config.model_policy),config.model_policy_sha256)
    if profile.get('parent_coverage_sha256')!=closure['profile_sha256']:
        raise ValueError('serving calendar profile differs from the built coverage')
    return profile


def freeze(root,report,output):
    if output.exists():raise ValueError('new verification output directory required')
    closure=check(root,report)
    profile=serving_profile(root,closure)
    scores=pd.read_parquet(root/profile['scores_path'])
    val=scores[scores.split=='validation'];test=scores[scores.split=='test']
    selected=[val.iloc[0],test.iloc[0],test.iloc[len(test)//2],test.iloc[-1]]
    ids={str(row.transaction_id) for row in selected}
    canonical=root/'data/interim/ieee-cis/transactions.parquet'
    fields=['transaction_id','source_transaction_dt_seconds','transaction_amount','card_id',
        'device_id','email_domain','address_id','product_code']
    with duckdb.connect() as db:
        db.execute('SET threads=1');db.execute("SET memory_limit='128MB'")
        rows=db.execute('SELECT '+','.join(fields)+' FROM read_parquet(?) WHERE transaction_id IN (SELECT unnest(?))',
            [str(canonical),list(ids)]).fetchdf()
    records={row['transaction_id']:row for row in rows.to_dict('records')}
    plan=checked_json(report.parent/PARENT/'plan.json')
    raw={}
    # Only whitelisted raw inputs and the explicit identity-presence flag.
    for batch in pq.ParquetFile(plan['side_path']).iter_batches(batch_size=1024,
            columns=['transaction_id','has_identity',*profile['raw_fields']],use_threads=False):
        frame=batch.to_pandas()
        for value in frame.loc[frame.transaction_id.isin(ids)].to_dict('records'):
            raw[value['transaction_id']]=value
        if len(raw)==len(ids):break
    cases=[]
    for score in selected:
        key=str(score.transaction_id);event=records[key].copy();new_id='prospective-'+key
        event['transaction_id']=new_id
        for name,prefix in [('card_id','unknown-card-'),('device_id','unknown-device-'),
                ('email_domain','unknown-email-'),('address_id','unknown-address-')]:
            if event[name].startswith(prefix):event[name]=prefix+new_id
        value=raw[key]
        event['has_identity']=bool(value['has_identity'])
        event['raw_features']={k:(None if pd.isna(value[k]) else value[k]) for k in profile['raw_fields']}
        cases.append(dict(source_id=key,event=event,expected_primary=float(score.primary_score),
            expected_graph=float(score.graph_score),expected_fold=profile['splits'][score.split]['fold']))
    new_id='prospective-unknown'
    unknown=dict(transaction_id=new_id,source_transaction_dt_seconds=int(val.iloc[0].event_second),
        transaction_amount=19.,card_id='unknown-card-'+new_id,device_id='unknown-device-'+new_id,
        email_domain='unknown-email-'+new_id,address_id='unknown-address-'+new_id,product_code='UNSEEN',raw_features={})
    cases.append(dict(source_id=None,event=unknown,expected_fold='fold-2',expected_history_count=0))
    output.mkdir(parents=True)
    value=sealed(dict(schema_version='stage09-coverage-verification-cases-v1',profile_sha256=profile['content_sha256'],
        build_closure_sha256=closure['content_sha256'],cases=cases,repeat_cases=[0,1,4],
        equality_tolerance=1e-6,selection='first_validation_first_mid_last_test_time_only',
        real_generation_calls=0,paid_calls=0,new_fits=0,new_scoring_label_batches=0))
    write_json(output/'frozen-cases.json',value)
    print(json.dumps(dict(frozen_cases=len(cases),sha256=value['content_sha256'])),flush=True)


class MechanismProvider:
    """Explicit fixture to exercise report/approval bindings, not an AI model."""
    def __init__(self):self.calls=0
    def generate(self,prompt,schema):
        self.calls+=1
        packet=json.loads(prompt.split('\nEVIDENCE_JSON:\n',1)[1]);facts=packet['facts']
        history=facts['F2']['value'];relation=facts['F4']['value'];graph=facts['F5']['value']
        draft=QualityDraft(policy_ref='P1',history_activity='unavailable' if history is None else 'observed' if history['tx_count_last_7d'] else 'none',
            relationship_evidence='unavailable' if relation is None else 'observed' if relation['returned_count'] else 'none',
            graph_evidence='available' if graph else 'unavailable',policy_applicability='not_established',
            risk_interpretation='uncalibrated_signal',evidence_refs=['F2','F3','F4','F5'],
            unknowns=['policy_conditions','verified_identity','calibration']+([] if graph else ['graph_evidence']))
        return QualityProviderResult(draft=draft,model_id='route6-mechanism-fixture',elapsed_ms=0,estimated_cost_cny=0)


def verify(root,source,report,output):
    if (output/'acceptance.json').exists():raise ValueError('accepted evidence is immutable; use --check')
    cases=checked_json(output/'frozen-cases.json')
    closure=check(root,report)
    if closure['content_sha256']!=cases['build_closure_sha256']:raise ValueError('coverage build drift')
    profile=serving_profile(root,closure)
    if profile['content_sha256']!=cases['profile_sha256']:raise ValueError('frozen serving profile changed')
    snapshot=output/('source-snapshot-'+str(time.time_ns()))
    inventory={}
    for base in ('src','api','scripts','configs'):
        for file in (source/base).rglob('*'):
            if file.is_file() and file.suffix in ('.py','.yaml','.json','.ps1') and '__pycache__' not in file.parts:
                relative=file.relative_to(source)
                target=snapshot/relative;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(file,target);inventory[relative.as_posix()]=binding(target)
                if binding(file)!=inventory[relative.as_posix()]:raise ValueError('source changed during verification snapshot')
    write_json(output/('source-inventory-'+str(time.time_ns())+'.json'),sealed(dict(snapshot=snapshot.name,files=inventory)))
    recipe=checked_json(report/'recipe.json');parent=checked_json(report.parent/PARENT/'plan.json')
    ledger=Path(parent['ledger_path']);before=binding(ledger)
    data_files={name:binding(root/name) for name in profile['files'] if name.startswith('data/')}
    rows=pd.read_parquet(root/profile['scores_path'])
    full=[]
    with duckdb.connect() as db:
        db.execute('SET threads=1');db.execute("SET memory_limit='128MB'")
        for split in ('validation','test'):
            window=recipe['windows'][split];spec=profile['splits'][split]
            eligible=model_for_time(parent['windows'],window['min_relative_seconds'])
            assert spec['fold']==eligible['name'] and spec['selection_cutoff']==eligible['calibration']['max_relative_seconds']
            wanted=db.execute('SELECT transaction_id FROM read_parquet(?) WHERE source_transaction_dt_seconds BETWEEN ? AND ? ORDER BY source_transaction_dt_seconds,transaction_id',
                [str(root/'data/interim/ieee-cis/transactions.parquet'),window['min_relative_seconds'],window['max_relative_seconds']]).fetchdf().transaction_id.tolist()
            actual=rows[rows.split==split]
            assert len(actual)==len(wanted)==88581 and actual.transaction_id.tolist()==wanted
            graphdir=root/'artifacts/stage-09/route5b'/(DEFAULT+'/validation' if split=='validation' else PARENT+'/historical-test')
            from graphrisk.graph.stage09_packed import verify_cache
            cache=verify_cache(graphdir/'cache')
            assert cache['target_ids']['test']==wanted and cache['statistics']['test']['time_violations']==0 and not cache['labels_in_cache']
            scores=actual[['primary_score','graph_score',*['graph_seed_'+str(s) for s in profile['seeds']]]].to_numpy()
            assert np.isfinite(scores).all() and ((scores>=0)&(scores<=1)).all()
            assert np.max(np.abs(actual.graph_score.to_numpy()-scores[:,2:].mean(axis=1)))<1e-7
            assert (actual.event_second>actual.selection_cutoff).all()
            full.append(dict(split=split,rows=len(wanted),fold=spec['fold'],missing_ids=0,missing_graph_index=0,
                invalid_scores=0,cache_sha256=cache['content_sha256'],strict_time_violations=0))
    config=load_stage05_config(root/'configs/stage09_coverage.yaml')
    investigation,retriever=build_components(config);graph=investigation.graph_service
    results=[]
    with CoverageMonitor(output/'resources.json',maximum_seconds=900) as monitor:
        for number,case in enumerate(cases['cases']):
            monitor.require()
            actual=graph.prospective(case['event'])
            assert actual['fold']==case['expected_fold'] and not actual['label_read'] and not actual['history_mutated']
            if case['source_id']:
                assert abs(actual['risk_score']-case['expected_primary'])<=1e-6
                assert abs(actual['graph_risk_score']-case['expected_graph'])<=1e-6
                old=graph.history.graph_row(case['source_id'])
                assert set(actual['selected_history_ids'])==set(graph.history.graph(old).selected_history_ids)
            else:assert actual['selected_history_ids']==[]
            write_json(output/('prospective-'+str(number)+'.json'),sealed(actual));results.append(actual)
            print('prospective case '+str(number)+' passed',flush=True)
        for number in cases['repeat_cases']:
            again=graph.prospective(cases['cases'][number]['event'])
            for key in ('risk_score','graph_risk_score','graph_seed_scores','selected_history_ids'):
                assert again[key]==results[number][key]
        def stop():raise MemoryError('deliberate resource fault')
        try:graph.prospective(cases['cases'][0]['event'],guard=stop)
        except MemoryError:pass
        else:raise AssertionError('resource failure was not closed')
    # Live HTTP runs against the real component graph and frozen score artifacts.
    # Generation is replaced explicitly for the two report/approval mechanism runs.
    service=Stage06Service.from_config(root/'configs/stage09_coverage.yaml')
    fixture=MechanismProvider();service.runner.provider=fixture
    service.versions=service.versions.model_copy(update={'generation_model_id':'route6-mechanism-fixture','generation_model_revision':None})
    sys.path.insert(0,str(source))
    import api.main as api_module
    if Path(api_module.__file__).resolve()!=source/'api/main.py':
        raise ValueError('verification must serve the active worktree API, not an older execution-root copy')
    create_app=api_module.create_app
    app=create_app(lambda:service)
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(32)
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    deadline=time.monotonic()+20
    while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.05)
    if not server.started:raise RuntimeError('local verification HTTP server failed to start')
    http_checks=[]
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=180.) as http:
            ready=http.get('/api/v1/ready');write_json(output/'http-ready.json',ready.json())
            assert all(ready.json()['components'][k] for k in ('data','risk_model','graph','policy_index'))
            for case in cases['cases'][:2]:
                key=case['source_id']
                response=http.get('/api/v1/coverage/'+key);assert response.status_code==200 and response.json()['status']=='covered'
                created=http.post('/api/v1/investigations',json={'transaction_id':key,'dataset_source':'ieee_cis','policy_as_of':'2026-07-01','relation_limit':20})
                assert created.status_code==201,created.text
                view=created.json();assert view['status']=='verified' and view['report']['graph']['fusion_score'] is None
                assert view['versions']['primary_model_version']==view['report']['model']['model_version']
                assert view['versions']['experimental_graph_model_version']==view['report']['graph']['graph_model_version']
                assert view['report']['model']['risk_score']==case['expected_primary']
                write_json(output/('http-view-'+key+'.json'),view)
                approval=view['approval'];body=dict(run_id=view['run_id'],session_id=approval['session_id'],
                    transaction_id=key,report_sha256=approval['report_sha256'],decision='reject',reviewer_id='route6-mechanism',note='Read-only mechanism acceptance')
                assert http.post('/api/v1/investigations/'+view['run_id']+'/approval',json=body).status_code==200
                reread=http.get('/api/v1/investigations/'+view['run_id']);assert reread.json()['approval']['status']=='rejected'
                assert reread.json()['report_sha256']==view['report_sha256']
                http_checks.append(dict(transaction_id=key,status='passed',report_sha256=view['report_sha256'],
                    relation_only=sum(n.get('evidence_role')=='relation_only' for n in view['report']['graph']['neighbors'])))
            prediction=http.post('/api/v1/predictions/prospective',json=cases['cases'][0]['event'])
            assert prediction.status_code==200,prediction.text
            assert prediction.json()['risk_score']==results[0]['risk_score']
            rejected=http.post('/api/v1/predictions/prospective',json={**cases['cases'][0]['event'],'raw_features':{'isFraud':1}})
            assert rejected.status_code==422
            old_id=graph.history.selected_history(graph.history.graph_row(cases['cases'][0]['source_id']))[0]['transaction_id']
            assert http.get('/api/v1/coverage/'+old_id).json()['status']=='out_of_coverage'
    finally:
        server.should_exit=True;thread.join(timeout=15);sock.close()
    assert not thread.is_alive()
    assert all(binding(root/n)==value for n,value in data_files.items()) and binding(ledger)==before
    outputs={p.name:binding(p) for p in output.glob('*.json') if p.name!='acceptance.json'}
    summary=sealed(dict(schema_version='stage09-coverage-acceptance-v1',status='passed',
        profile_sha256=profile['content_sha256'],build_closure_sha256=closure['content_sha256'],
        loaded_api_path=str(Path(api_module.__file__).resolve()),
        full_coverage=full,prospective_unique_cases=len(results),repeat_predictions=len(cases['repeat_cases']),
        online_offline_tolerance=1e-6,http_checks=http_checks,deterministic_fixture_calls=fixture.calls,
        real_generation_calls=0,paid_calls=0,new_fits=0,new_scoring_label_batches=0,
        fit_ledger_before=before,fit_ledger_after=binding(ledger),data_unchanged=True,
        limitations=['No new Agent quality evidence','Historical test previously observed',
            'GraphSAGE experimental; no promotion','Personal LB6 pending'],outputs=outputs))
    write_json(output/'acceptance.json',summary)
    print(json.dumps(summary),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('E:/GraphRisk-Agent'))
    parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--report',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('action',choices=['freeze','verify','check']);args=parser.parse_args()
    report=args.report or args.root/'reports/generated/stage09/route5b'/DEFAULT
    if args.action=='freeze':freeze(args.root,report,args.output)
    elif args.action=='verify':verify(args.root,args.source,report,args.output)
    else:
        value=checked_json(args.output/'acceptance.json');check(args.root,report)
        profile=serving_profile(args.root,checked_json(report/'closure.json'))
        if value['profile_sha256']!=profile['content_sha256']:raise ValueError('serving profile changed')
        for name,wanted in value['outputs'].items():
            if binding(args.output/name)!=wanted:raise ValueError('verification evidence changed')
        for inventory_path in args.output.glob('source-inventory-*.json'):
            archived=checked_json(inventory_path)
            snapshot=(args.output/archived['snapshot']).resolve()
            if not snapshot.is_relative_to(args.output.resolve()):raise ValueError('verification snapshot path escaped')
            for name,wanted in archived['files'].items():
                if binding(snapshot/name)!=wanted:raise ValueError('verification source snapshot changed')
        print(json.dumps(dict(status=value['status'],rows=sum(v['rows'] for v in value['full_coverage']),
            real_generation_calls=value['real_generation_calls'],sha256=value['content_sha256'])),flush=True)


if __name__=='__main__':main()
