param(
    [ValidateSet('check','serve')][string]$Action='check',
    [string]$ExecutionRoot='E:\GraphRisk-Agent',
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [int]$UiPort=8531
)
$ErrorActionPreference='Stop'
$pictureSource=Split-Path $PSScriptRoot -Parent
. (Join-Path $ExecutionRoot 'scripts\enter_project_env.ps1') | Out-Null
$env:PYTHONPATH="$pictureSource\src;$pictureSource;$pictureSource\scripts"
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
$env:UV_PROJECT_ENVIRONMENT=Join-Path $ExecutionRoot '.venv'
$env:STREAMLIT_BROWSER_GATHER_USAGE_STATS='false'
$picturePython=@'
"""Recorded enhanced-profile report replay; no model/data component factories."""
from pathlib import Path
import argparse, hashlib, json, os, socket, subprocess, sys, time
from copy import deepcopy
from graphrisk.demo.stage10_results import read_results
from graphrisk.demo.stage09_final import ReplayRunner
from graphrisk.demo.stage09_operations_recovery import api_client
from graphrisk.serving.investigation_service import Stage06Service, InvestigationRecord, ServiceError

p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--runtime',type=Path,required=True)
p.add_argument('--output',type=Path,required=True);p.add_argument('--action',choices=['check','serve'],required=True);p.add_argument('--ui-port',type=int,required=True)
a=p.parse_args();source=a.source.resolve();root=a.runtime.resolve();output=a.output.resolve()
if not output.is_relative_to(root/'reports/generated/stage10/route5') or output.exists():raise ValueError('new route5 output directory required')
output.mkdir(parents=True)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name,value):
    with (output/name).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
frozen=root/'reports/generated/stage10/route2/20261017-real-v2'
ledgers=[root/'reports/generated/stage09/generation-ledger.json',root/'reports/generated/stage10/generation-ledger.jsonl',root/'artifacts/stage-09/route5b/20261009-preflight-v1/fit-ledger.json']
before={str(q):sha(q) for q in ledgers}
save('preflight.json',{'mode':'stage10_enhanced_saved_report_replay','action':a.action,'ledgers':before,'source':str(source),'no_new_inference':True})
summary=read_results(source,root,frozen)
save('saved-results-check.json',summary)
records=[]
for name in ['case-01','case-04']:
    path=frozen/'results'/(name+'.json')
    if sha(path)!=summary['result_bindings'][name]['sha256']:raise ValueError('saved result binding changed')
    row=json.loads(path.read_text(encoding='utf-8'));records.append(InvestigationRecord.model_validate(row['record']))
if [r.status for r in records]!=['verified','generation_error']:raise ValueError('registered demonstration statuses changed')
if any(r.versions.score_mode!='xgboost_only' for r in records):raise ValueError('enhanced profile required')
if records[0].versions!=records[1].versions:raise ValueError('demonstration version mismatch')
good,bad=records
probe=ReplayRunner(records)
try:probe.run(good.request.model_dump(mode='json')|{'transaction_id':'unregistered-demo-id'})
except ServiceError:pass
else:raise ValueError('replay allowed an unregistered request')
copy=probe.run(good.request.model_dump(mode='json'));copy.graph_run.node_trace.append('local_test_mutation')
if 'local_test_mutation' in probe.run(good.request.model_dump(mode='json')).graph_run.node_trace:raise ValueError('replay mutated original result')
runner=ReplayRunner(records)
service=Stage06Service(dataset_source='ieee_cis',versions=good.versions,runner=runner,readiness_probe=lambda:{'data':True,'risk_model':False,'graph':False,'policy_index':True,'ollama':False})
ui=None;ui_log=None;checks={};success=False
try:
    with api_client(service) as client:
        published=[]
        for original in records:
            reply=client.post('/api/v1/investigations',json=original.request.model_dump(mode='json'))
            published.append(dict(http_status=reply.status_code,body=reply.json()))
        if [x['http_status'] for x in published]!=[201,503]:raise ValueError('replay publication statuses changed')
        view=published[0]['body'];bad_id=published[1]['body']['run_id']
        if view['report_sha256']!=good.report_sha256:raise ValueError('historical report hash changed')
        if view['report']['model']['score_mode']!='xgboost_only':raise ValueError('primary model projection lost')
        if view['automatic_action_executed']:raise ValueError('unexpected external action')
        if client.get('/api/v1/ready').status_code!=503:raise ValueError('replay pretended model readiness')
        session={'mode':'stage10_enhanced_saved_report_replay','notice':'历史真实报告回放；本次无新模型推理，审批使用新的memory演示记录。','api_url':str(client.base_url).rstrip('/'),'ui_url':'http://127.0.0.1:'+str(a.ui_port),'successful_run_id':view['run_id'],'blocked_run_id':bad_id,'source_cases':['case-01','case-04'],'original_report_sha256':good.report_sha256,'original_statuses':[r.status for r in records],'versions':good.versions.model_dump(mode='json'),'original_quality':'6/10 Fail; unchanged','new_generation_calls':0,'storage':'memory','readiness':'not_ready by design'}
        save('session.json',session);save('initial-http.json',published)
        if a.action=='check':
            from unittest.mock import patch
            from streamlit.testing.v1 import AppTest
            with patch.dict(os.environ,{'GRAPHRISK_STAGE06_API_URL':session['api_url'],'GRAPHRISK_HISTORY_REPLAY':'1'}):
                at=AppTest.from_file(str(source/'streamlit_app.py'),default_timeout=45)
                at.query_params['run_id']=view['run_id'];at.run()
                if at.exception:raise ValueError('workbench failed to display replay')
                if not any('历史真实报告回放' in str(m.value) for m in at.info):raise ValueError('replay notice missing')
                if not any(m.label=='XGBoost 主风险分数' for m in at.metric):raise ValueError('enhanced primary score missing')
                next(w for w in at.text_input if w.key=='reviewer_id').set_value('stage10-demo-reviewer')
                next(w for w in at.text_area if w.key=='approval_note').set_value('图片演示：人工拒绝复核建议，无外部处置。')
                next(w for w in at.button if w.label=='拒绝复核建议').click().run()
                if at.exception or not any('已人工拒绝' in str(m.value) for m in at.warning):raise ValueError('rejection not shown')
                next(w for w in at.button if w.label=='刷新当前状态').click().run()
                current=client.get('/api/v1/investigations/'+view['run_id']).json()
                if current['approval']['status']!='rejected' or current['report_sha256']!=view['report_sha256']:raise ValueError('refresh changed report/decision')
                rejected=client.post('/api/v1/investigations/'+view['run_id']+'/approval',json={'run_id':view['run_id'],'session_id':view['session_id'],'transaction_id':good.request.transaction_id,'report_sha256':view['report_sha256'],'reviewer_id':'stage10-demo-reviewer','note':'图片演示：人工拒绝复核建议，无外部处置。','decision':'reject'})
                if rejected.status_code!=200:raise ValueError('identical decision was not idempotent')
                at.query_params['run_id']=bad_id;at.run()
                if at.exception or not at.error or any(w.label=='拒绝复核建议' for w in at.button):raise ValueError('blocked output gained report or approval')
            checks={'strict_request_rejected':True,'deep_copy_preserves_original':True,'source_report_sha_preserved':True,'enhanced_primary_visible':True,'replay_notice_visible':True,'human_rejection_and_refresh':True,'identical_reject_idempotent':True,'blocked_case_no_approval':True,'honest_not_ready':True,'api_calls_actual_loopback_http':True,'headless_AppTest':True,'visual_browser_review':False}
        else:
            with socket.socket() as probe_socket:
                probe_socket.bind(('127.0.0.1',a.ui_port))
            env=os.environ.copy();env.update(GRAPHRISK_STAGE06_API_URL=session['api_url'],GRAPHRISK_HISTORY_REPLAY='1',STREAMLIT_BROWSER_GATHER_USAGE_STATS='false')
            ui_log=(output/'workbench.log').open('wb')
            ui=subprocess.Popen([sys.executable,'-m','streamlit','run',str(source/'streamlit_app.py'),'--server.address','127.0.0.1','--server.port',str(a.ui_port),'--server.headless','true','--browser.gatherUsageStats','false'],cwd=output,env=env,stdout=ui_log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            print(json.dumps({'mode':session['mode'],'url':session['ui_url']+'/?run_id='+view['run_id'],'session_file':str(output/'session.json')},ensure_ascii=False),flush=True)
            end=time.monotonic()+1800
            try:
                while time.monotonic()<end and ui.poll() is None and not (output/'stop.request').exists():time.sleep(.5)
            except KeyboardInterrupt:pass
            save('final-public-views.json',{r:client.get('/api/v1/investigations/'+r).json() for r in [view['run_id'],bad_id]})
        if runner.calls!=2:raise ValueError('reads/approval unexpectedly reran recorded runner')
        success=True
finally:
    if ui is not None and ui.poll() is None:
        ui.terminate()
        try:ui.wait(timeout=10)
        except subprocess.TimeoutExpired:ui.kill();ui.wait(timeout=5)
    if ui_log:ui_log.close()
    after={str(q):sha(q) for q in ledgers}
    if before!=after:raise ValueError('existing generation/training ledger changed')
    save('completion.json',{'status':'passed' if success else 'failed','checks':checks,'runner_replay_calls':runner.calls,'new_model_calls':0,'paid_calls':0,'new_real_fits':0,'ledgers_unchanged':True,'ledgers':after,'original_quality':'Fail 6/10','services_closed':True})
print(json.dumps({'status':'passed','action':a.action,'new_model_calls':0,'checks':len(checks)},ensure_ascii=False))
'@
Push-Location -LiteralPath $ExecutionRoot
try {
    $picturePython | uv run --offline --no-sync python - --source $pictureSource --runtime $ExecutionRoot --output $OutputDirectory --action $Action --ui-port $UiPort
    if ($LASTEXITCODE -ne 0) {throw 'Picture demo failed; preserve the new private output and inspect the error.'}
} finally {Pop-Location}
