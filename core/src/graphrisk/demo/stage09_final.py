"""Original Stage09 final evidence, explicit historical replay and independent states."""
from __future__ import annotations
import argparse
from contextlib import redirect_stdout
from datetime import UTC, datetime
import io
import json
import time
from pathlib import Path
from unittest.mock import patch

from graphrisk.demo.stage09_operations import (SOURCE, read_json, write_json, file_binding,
    inside, pin_inputs, require_inputs_unchanged, verify_recovery_evidence)
from graphrisk.graph.coverage_resources import CoverageMonitor


REPLAY_NOTICE = "历史真实报告回放：本次不运行风险模型或大模型；审批仅操作新的隔离演示记录。"


def sealed_json(path):
    from graphrisk.demo.stage09_evaluation import digest
    value = read_json(path)
    if digest({k:v for k,v in value.items() if k != 'content_sha256'}) != value['content_sha256']:
        raise ValueError('historical JSON seal changed')
    return value


def historical_binding(root, item, archives):
    """Resolve exact historical bytes, never treat old runtime source as active imports."""
    candidates = [inside(root, item['path']), inside(SOURCE, item['path'])]
    candidates += [inside(base, item['path']) for base in archives]
    for candidate in candidates:
        if candidate.is_file():
            actual = file_binding(candidate)
            if actual['sha256'] == item['sha256'] and ('bytes' not in item or actual['bytes'] == item['bytes']):
                return candidate
    raise ValueError('historical input missing or changed: '+item['path'])


def require_final_states(value):
    needed = {'historical_regrade', 'current_api_ui', 'layered_regression', 'operations_recovery',
              'frozen_models_and_ledgers', 'reproduction_guide'}
    criteria = value.get('criteria', {})
    if set(criteria) != needed or any(criteria[k] is not True for k in needed):
        raise ValueError('final engineering criteria are incomplete')
    if (value.get('engineering') != 'done' or value.get('personal_learning') != 'Pending'
        or value.get('human_usefulness_review') != 'Pending' or value.get('gnn_increment') != 'failed_no_promotion'
        or value.get('overall_stage') != 'In Progress'
        or value.get('agent_quality') != 'historical_bounded_judgments_verified_current_profile_not_freshly_measured'
        or any(value.get(k) != 0 for k in ('new_real_fits','new_real_generation','paid_calls'))):
        raise ValueError('final acceptance cannot hide model limitations or sign personal learning')
    return value


def evidence(root, output, config, require):
    from graphrisk.demo import stage09_quality as quality
    from graphrisk.demo.stage09_evaluation import digest, grade, summarize
    from graphrisk.demo.stage09_replay import validate_result_file, queue_for
    directory = inside(root, config['quality'])
    manifest = sealed_json(directory/'cases.json')
    if manifest['scoring'] != quality.SCORING:
        raise ValueError('quality scoring protocol changed')
    inputs = sealed_json(directory/'inputs.json')
    from graphrisk.agent.stage09_preflight import Stage09Contract
    Stage09Contract.model_validate(inputs['contract'])
    archives = [inside(root, name) for name in config['quality_archives']]
    historical = [historical_binding(root, item, archives) for item in inputs['artifacts']+manifest['files']]
    native = sealed_json(directory/'acceptance.json')
    historical += [historical_binding(root, item, archives) for item in native['closure_files']]
    # Bind every stored measurement/transport/snapshot before grading. No rewriting old summaries.
    parents = [directory, root/'reports/generated/stage09/route5a/20261008-quality-v1',
               inside(root, config['baseline']), inside(root, config['replay'])]
    files = historical + [p for base in parents for p in base.rglob('*.json')
                          if not any(s in {'pytest-tmp','tmp'} for s in p.relative_to(base).parts)]
    # All archives live on E. Current C source may be a byte-identical fallback: bind separately below.
    runtime_files = [p for p in files if p.is_relative_to(root)]
    bindings = pin_inputs(root, output, runtime_files)
    source_bindings = {str(p):file_binding(p) for p in files if not p.is_relative_to(root)}
    write_json(output/'historical-source-bindings.json', source_bindings)
    for case in manifest['cases']:
        if digest(read_json(directory/'snapshots'/(case['case_id']+'.json'))) != case['snapshot_sha256']:
            raise ValueError('quality snapshot changed')
    require()
    # Only replace the source-location check AFTER exact manifest/input/closure hashes were checked.
    # The actual grader, raw HTTP/schema/settings, dev selection and ledger checks remain intact.
    with patch.object(quality, 'require_registered', lambda *_: manifest), \
         patch.object(quality, 'write_json', lambda *_: None), redirect_stdout(io.StringIO()):
        current = quality.check(root, directory)
    old = read_json(directory/'summary.json')
    if {k:v for k,v in current.items() if k!='global_attempts'} != {k:v for k,v in old.items() if k!='global_attempts'}:
        raise ValueError('quality regrade differs from historical summary')
    if current['global_attempts'] != 130 or current['phase_attempts'] != 63:
        raise ValueError('quality generation denominators changed')
    summaries, ids = {}, []
    for name in ('baseline','replay'):
        require(); base = inside(root, config[name]); m = sealed_json(base/'cases.json')
        ids.append({c['transaction_id'] for c in m['cases']})
        for c,key in queue_for(m):
            result = read_json(base/'results'/(key+'.json'))
            if name == 'replay':
                validate_result_file(base,c,key,m)
            else:
                snap = read_json(base/'snapshots'/(c['case_id']+'.json'))
                if digest(snap) != c['snapshot_sha256'] or result['transaction_id'] != c['transaction_id']:
                    raise ValueError('baseline case binding changed')
                if grade(snap,result['record'],result['http_calls']) != result['grade']:
                    raise ValueError('baseline regrade changed')
        observed = summarize(base,m); saved = read_json(base/'summary.json')
        if any(saved[k] != v for k,v in observed.items()):
            raise ValueError('historical baseline/replay summary differs')
        summaries[name] = observed
    if ids[0] != ids[1] or len(ids[0]) != 30:
        raise ValueError('baseline/replay are not the same 30 unique transactions')
    ledger = read_json(root/'reports/generated/stage09/generation-ledger.json')['attempts']
    if len(ledger) != 130 or len({a['sequence'] for a in ledger}) != 130:
        raise ValueError('global generation ledger count/sequence changed')
    from collections import Counter
    phases = dict(Counter(a['phase'] for a in ledger))
    if phases != {'route1':33,'route5':33,'route5a':63,'route5a_docker_recheck':1}:
        raise ValueError('generation phase accounting changed')
    require_inputs_unchanged(root,bindings)
    if any(file_binding(Path(p)) != b for p,b in source_bindings.items()):
        raise ValueError('historical source changed during regrade')
    return {'status':'passed','historical_file_bindings_checked':len(historical),
            'baseline_and_replay':summaries,'quality':current,'global_generation_phase_counts':phases,
            'paired_unique_transactions':30,'new_generation_calls':0,'historical_quality_only':True,
            'current_5b_profile_real_quality_freshly_measured':False,'human_review':'Pending'}


class ReplayRunner:
    """Exact frozen request -> saved ReliableRun. Never instantiates an LLM/model provider."""
    def __init__(self, records):
        self.records = {r.request.transaction_id:r for r in records}
        self.calls = 0
    def run(self, request):
        from graphrisk.agent.contracts import Stage05Request
        from graphrisk.serving.investigation_service import ServiceError
        req = Stage05Request.model_validate(request)
        original = self.records.get(req.transaction_id)
        if original is None or req != original.request:
            raise ServiceError('invalid_request')
        self.calls += 1
        return original.result.model_copy(deep=True)


def load_demo_records(root, config):
    from graphrisk.serving.investigation_service import InvestigationRecord
    records = [InvestigationRecord.model_validate(read_json(inside(root,config['quality'])/'results'/name)['record'])
               for name in config['demo_cases']]
    if [r.status for r in records] != ['verified','partial_evidence'] or records[0].versions != records[1].versions:
        raise ValueError('demo must include complete/partial historical cases with the same declared versions')
    if len({r.request.transaction_id for r in records}) != 2:
        raise ValueError('demo needs two different historical transactions')
    return records


def replay_service(dsn, records, diagnostic_dir=None):
    from graphrisk.serving.postgres_service import PostgresInvestigationService
    class DiagnosedReplayService(PostgresInvestigationService):
        def run(self, *args, **kwargs):
            try:return super().run(*args, **kwargs)
            except Exception as exc:
                if diagnostic_dir:
                    import traceback
                    chain=[];current=exc
                    while current is not None:
                        chain.append({'type':type(current).__name__,'code':getattr(current,'code',None),
                            'frames':[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(current.__traceback__)]})
                        current=current.__cause__
                    write_json(diagnostic_dir/'service-failure.json',chain)
                raise
    return DiagnosedReplayService(dsn=dsn,dataset_source='ieee_cis',versions=records[0].versions,
        runner=ReplayRunner(records),max_runs=100,readiness_probe=lambda: {
            'data':True,'risk_model':False,'graph':False,'policy_index':True,'ollama':False})


def demo(root, output, config, require, *, serve=False):
    from contextlib import ExitStack
    import socket
    import psycopg
    from graphrisk.demo.stage09_operations_recovery import Sandbox, api_client
    from graphrisk.serving.postgres_store import PostgresStore
    from graphrisk.serving.postgres_roles import grant_stage07_app
    from graphrisk.demo.stage07_probe_suite import _decision
    records = load_demo_records(root,config)
    write_json(output/'runtime-source-bindings.json',{n:file_binding(SOURCE/n) for n in ['streamlit_app.py','api/main.py','api/investigation.py','src/graphrisk/demo/stage09_final.py','src/graphrisk/serving/investigation_service.py','src/graphrisk/serving/postgres_service.py','src/graphrisk/serving/investigation_views.py']})
    pin_inputs(root,output,[inside(root,config['quality'])/'results'/name for name in config['demo_cases']])
    with Sandbox(output,config['recovery']) as box:
        import stage09_ci_pytest as guard
        guard._allowed_ports.add(box.port)
        with ExitStack() as stack:
            stack.enter_context(patch.object(socket.socket,'connect',guard._connect))
            stack.enter_context(patch.object(socket.socket,'connect_ex',guard._connect_ex))
            stack.enter_context(patch.object(socket.socket,'bind',guard._bind))
            stack.enter_context(patch.object(socket,'socketpair',guard._socketpair))
            PostgresStore(box.admin(box.source)).migrate()
            grant_stage07_app(box.admin(box.source),box.app(box.source))
            service = replay_service(box.app(box.source),records,output)
            views=[]
            with api_client(service) as client:
                for i,original in enumerate(records):
                    require()
                    request=original.request.model_dump(mode='json');key='route8-history-'+str(i)+'-'+box.nonce
                    response=client.post('/api/v1/investigations',json=request,headers={'Idempotency-Key':key})
                    write_json(output/f'create-{i}.json',{'http_status':response.status_code,'body':response.json()})
                    if response.status_code != 201:raise ValueError('historical report replay failed')
                    view=response.json();views.append(view)
                    from graphrisk.serving.investigation_views import project_investigation
                    internal=service.store.row(view['run_id'])
                    if (internal['report_sha256']!=original.report_sha256
                        or view['report']!=project_investigation(original).report.model_dump(mode='json')
                        or (i==0 and view['report_sha256']!=original.report_sha256)
                        or (i==1 and (view['approval'] is not None or view['report_sha256'] is not None))):
                        raise ValueError('historical report hash/content or partial approval boundary changed')
                    repeated=client.post('/api/v1/investigations',json=request,headers={'Idempotency-Key':key})
                    if repeated.status_code!=200 or repeated.json()['run_id']!=view['run_id'] or service.runner.calls!=i+1:
                        raise ValueError('idempotent replay reran the historical runner')
                write_json(output/'service.json',{'api_url':str(client.base_url).rstrip('/'),
                    'mode':config['demo_mode'],'notice':REPLAY_NOTICE,'views':views,'new_model_calls':0,
                    'source':str(SOURCE),'stop':'Ctrl+C; only this sandbox will be removed'})
                if serve:
                    print(json.dumps({'mode':config['demo_mode'],'api_url':str(client.base_url),'service_file':str(output/'service.json')},ensure_ascii=True),flush=True)
                    try:
                        while True:
                            require();time.sleep(1)
                    except KeyboardInterrupt:pass
                    return {'status':'stopped','real_model_calls':0}
                from streamlit.testing.v1 import AppTest
                summaries=[]
                with patch.dict('os.environ',{'GRAPHRISK_STAGE06_API_URL':str(client.base_url),'GRAPHRISK_HISTORY_REPLAY':'1'}):
                    for i,view in enumerate(views):
                        at=AppTest.from_file(str(SOURCE/'streamlit_app.py'),default_timeout=30)
                        at.query_params['run_id']=view['run_id'];at.run()
                        if at.exception or not any(REPLAY_NOTICE==m.value for m in at.info):
                            raise ValueError('historical replay UI exception or missing provenance notice')
                        if not any(m.label=='XGBoost 主风险分数' for m in at.metric):
                            raise ValueError('UI lost primary risk score')
                        if i==0:
                            next(w for w in at.text_input if w.key=='reviewer_id').set_value('route8-local-reviewer')
                            next(w for w in at.text_area if w.key=='approval_note').set_value('Historical replay mechanism; no external action')
                            next(w for w in at.button if w.label=='拒绝复核建议').click().run()
                            if at.exception or not any('已人工拒绝' in w.value for w in at.warning):
                                raise ValueError('UI rejection did not reach the API')
                            decision=_decision(view,'reject');decision['reviewer_id']='route8-local-reviewer'
                            decision['note']='Historical replay mechanism; no external action'
                            duplicate=client.post(f"/api/v1/investigations/{view['run_id']}/approval",json=decision)
                            conflict=client.post(f"/api/v1/investigations/{view['run_id']}/approval",json=decision|{'decision':'approve'})
                            if duplicate.status_code!=200 or conflict.status_code!=409:
                                raise ValueError('approval repeat/conflict contract changed')
                        next(w for w in at.button if w.label=='刷新当前状态').click().run()
                        if at.exception:raise ValueError('UI refresh failed')
                        current=client.get('/api/v1/investigations/'+view['run_id']).json()
                        if current['report_sha256']!=view['report_sha256'] or current['automatic_action_executed']:
                            raise ValueError('UI lifecycle changed the report or executed an action')
                        if i==1 and current['report']['graph']['target_graph_risk_score'] is not None:
                            raise ValueError('partial graph was promoted to a numeric score')
                        summaries.append({'source_status':view['status'],'ui_http_read_refresh':True,
                            'historical_report_hash_preserved':True,'approval_status':(current.get('approval')or{}).get('status'),
                            'public_sha_semantics':'approval_bound' if i==0 else 'no_approval_public_sha_null_internal_hash_preserved',
                            'transport_mocked':False,'headless_apptest':True,'visual_browser_review':'not_run'})
                if service.runner.calls!=2:raise ValueError('UI/reads unexpectedly replayed generation')
            resumed=replay_service(box.app(box.source),records)
            with api_client(resumed) as client:
                for old in views:
                    response=client.get('/api/v1/investigations/'+old['run_id'])
                    if response.status_code!=200 or response.json()['report_sha256']!=old['report_sha256'] or response.json()['report']!=old['report']:
                        raise ValueError('new API instance could not restore the report')
                if resumed.runner.calls!=0:raise ValueError('restoring reports invoked historical execution')
            require()
            with psycopg.connect(box.app(box.source)) as conn:
                if conn.execute('SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone()[0]:
                    raise ValueError('demo application role is privileged')
    return {'status':'passed','mode':config['demo_mode'],'cases':summaries,'historical_records':2,
        'current_models_executed':0,'new_llm_calls':0,'paid_calls':0,'external_actions':0,
        'actual_http_transport':True,'postgres_restart_instance_restored':True,'limited_role':True,
        'sandbox_removed':box.removed,'old_records_mutated':False,'personal_learning':'Pending'}


def final_check(root, output, config, evidence_dir, demo_dir, regression_root):
    from stage09_ci import source_inventory
    import hashlib
    if any(not p.resolve().is_relative_to(root/'reports/generated/stage09/route8')
           for p in (evidence_dir,demo_dir,regression_root)):
        raise ValueError('final check accepts only route8 evidence inside runtime root')
    evidence_result=read_json(evidence_dir/'result.json');demo_result=read_json(demo_dir/'result.json')
    for path,result in [(evidence_dir,evidence_result),(demo_dir,demo_result)]:
        if result['status']!='passed' or read_json(path/'resources.json')['status']!='passed':
            raise ValueError('final phase or resource check did not pass')
        if read_json(path/'contract-binding.json')!=file_binding(SOURCE/'configs/stage09_final.json'):
            raise ValueError('final phases used a different contract')
        require_inputs_unchanged(root,read_json(path/'input-bindings.json'))
    require_inputs_unchanged(SOURCE,read_json(demo_dir/'runtime-source-bindings.json'))
    if (evidence_result['paired_unique_transactions']!=30 or not evidence_result['historical_quality_only']
        or evidence_result['new_generation_calls']!=0 or demo_result['historical_records']!=2
        or not demo_result['actual_http_transport'] or not demo_result['postgres_restart_instance_restored']
        or not demo_result['sandbox_removed'] or not demo_result['limited_role']
        or any(demo_result[k]!=0 for k in ['current_models_executed','new_llm_calls','paid_calls','external_actions'])):
        raise ValueError('historical/mechanism evidence is incomplete')
    cleanup=read_json(demo_dir/'sandbox-cleanup.json')
    if not cleanup['container_removed'] or not cleanup['password_file_removed'] or cleanup['old_containers_or_volumes_touched']:
        raise ValueError('demo cleanup is not proven')
    fingerprint=hashlib.sha256(json.dumps(source_inventory(SOURCE),sort_keys=True).encode()).hexdigest()
    layers={}
    for name in ['static','cpu','database','package']:
        layer=read_json(regression_root/name/'public-summary.json');layers[name]=layer
        if layer['status']!='passed' or layer['source_sha256']!=fingerprint:
            raise ValueError('regression layer failed or used different executable inputs')
    for name,minimum in [('cpu',100),('database',26)]:
        counts=layers[name]['detail']['tests']
        if counts['passed']<minimum or counts['failed'] or counts['errors'] or (name=='database' and counts['skipped']):
            raise ValueError('required regression cases did not execute')
    if layers['database']['detail']['probes']['passed']!=15:
        raise ValueError('limited-role 15-case suite incomplete')
    mechanism=read_json(regression_root/'stage06-50.json')
    if mechanism['fake']['summary']['passed']!=50 or mechanism['fake']['summary']['count']!=50 or mechanism['real_local']['samples']:
        raise ValueError('50-case mechanism suite incomplete or unexpected real generation')
    parent=inside(root,config['route7']);parent_acceptance=read_json(parent/'acceptance.json')
    preflight=read_json(output.parent/'preflight.json')
    if file_binding(parent/'acceptance.json')['sha256']!=preflight['previous_route7_acceptance_sha256']:
        raise ValueError('route7 acceptance changed since preflight')
    require_inputs_unchanged(parent,read_json(parent/'evidence-manifest.json'))
    verify_recovery_evidence(root,parent/'recovery-v3',read_json(parent/'recovery-v3/result.json'))
    old_runtime=read_json(parent/'regression-v3/database/source-inputs.json')
    # The isolated recovery remains applicable only while its backend dependencies are unchanged.
    relevant={k:v for k,v in old_runtime.items() if k.startswith(('api/','src/graphrisk/serving/',
        'src/graphrisk/agent/','src/graphrisk/investigation/','src/graphrisk/monitoring/'))}
    if any(file_binding(SOURCE/k)['sha256']!=v for k,v in relevant.items()):
        raise ValueError('route7 backend changed; actual recovery must be revalidated')
    require_inputs_unchanged(root,preflight['legacy_ledgers'])
    from graphrisk.graph.stage09_acceptance import check_final
    old_models=check_final(root,root/'reports/generated/stage09/route5b/20261015-acceptance-v1')
    if old_models['gnn_increment']!='failed_no_promotion':
        raise ValueError('frozen GNN decision changed')
    for name in ['docs/data/stage-09-final-reproduction.md','docs/data/stage-09-personal-review.md']:
        if not (SOURCE/name).is_file():raise ValueError('personal reproduction material missing')
    result=require_final_states({'schema_version':'stage09-final-acceptance-v1','status':'passed',
        'engineering':'done','overall_stage':'In Progress','personal_learning':'Pending',
        'human_usefulness_review':'Pending','gnn_increment':'failed_no_promotion',
        'agent_quality':'historical_bounded_judgments_verified_current_profile_not_freshly_measured',
        'new_real_fits':0,'new_real_generation':0,'paid_calls':0,
        'criteria':{k:True for k in ['historical_regrade','current_api_ui','layered_regression',
            'operations_recovery','frozen_models_and_ledgers','reproduction_guide']},
        'source_input_sha256':fingerprint,'layers':layers,'historical_quality':evidence_result,
        'demo':demo_result,'recovery_reused_backend_bindings':len(relevant),
        'prior_ci':parent_acceptance['github_current_commit_run']['url'],
        'current_commit_ci':'collected_separately_after_commit','old_model_closure':old_models})
    pin_inputs(root,output,[evidence_dir/'result.json',demo_dir/'result.json',
        parent/'acceptance.json',regression_root/'stage06-50.json']+
        [regression_root/n/'public-summary.json' for n in layers])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['evidence','demo','serve','check'])
    parser.add_argument('--runtime-root',type=Path,default=Path('E:/GraphRisk-Agent'))
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--evidence-dir',type=Path)
    parser.add_argument('--demo-dir',type=Path)
    parser.add_argument('--regression-root',type=Path)
    args=parser.parse_args();root=args.runtime_root.resolve();output=args.output_dir.resolve()
    if not output.is_relative_to(root/'reports/generated/stage09/route8'):
        parser.error('output must be a new route8 directory inside runtime root')
    output.mkdir(parents=True,exist_ok=False)
    config=read_json(SOURCE/'configs/stage09_final.json')
    if any(config[k] for k in ['real_training_allowed','real_generation_allowed','paid_calls_allowed']):
        raise ValueError('final route forbids new model/paid work')
    write_json(output/'contract-binding.json',file_binding(SOURCE/'configs/stage09_final.json'))
    try:
        with CoverageMonitor(output/'resources.json',maximum_rss_mib=1280,maximum_seconds=3600 if args.action=='serve' else 600) as monitor:
            if args.action=='check':
                if any(p is None for p in (args.evidence_dir,args.demo_dir,args.regression_root)):
                    raise ValueError('check needs explicit evidence, demo and regression directories')
                result=final_check(root,output,config,args.evidence_dir,args.demo_dir,args.regression_root)
            elif args.action=='evidence':result=evidence(root,output,config,monitor.require)
            else:result=demo(root,output,config,monitor.require,serve=args.action=='serve')
        if (output/'input-bindings.json').exists():require_inputs_unchanged(root,read_json(output/'input-bindings.json'))
        write_json(output/'result.json',result)
        print(json.dumps({'action':args.action,'status':result['status'],'output':str(output)},ensure_ascii=True))
    except Exception as exc:
        import traceback
        write_json(output/'failure.json',{'status':'failed','error_type':type(exc).__name__,
            'frames':[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(exc.__traceback__)]})
        raise RuntimeError('final acceptance phase failed; inspect its private failure.json and logs') from None


if __name__=='__main__':main()