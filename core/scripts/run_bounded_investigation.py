"""Frozen route3 native pilot, isolated read-only tool worker, or free replay."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from graphrisk.agent.autonomous_actions import SYSTEM_PROMPT,load_profile
from graphrisk.agent.autonomous_budget import InvestigationBudget,ExecutionStop
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_loop import BoundedInvestigation,restore_observations,observation_rows
from graphrisk.agent.autonomous_policy import PolicyRegistry
from graphrisk.agent.autonomous_providers import ActionProvider
from graphrisk.agent.autonomous_report import SummaryProvider,REPORT_PROMPT
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.autonomous_tools import InvestigationScope,ReadOnlyToolExecutor,ArtifactGuard,ToolBoundaryError
from graphrisk.agent.policy_retrieval import PolicyIndex,PolicyRetriever
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash,load_contract
from preflight_agent_upgrade import protected_hashes
from stage09_ci import source_inventory

SOURCE=Path(__file__).resolve().parents[1]

def read(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(canonical(value).encode())
def ledger(runtime):return InvestigationBudget(runtime/'reports/generated/agent-investigation-upgrade/live-budget.sqlite3',load_contract(SOURCE/'configs/agent_investigation_experiment.json'),mode='live')


def prepare(runtime,output,*,supplement=False):
    if output.exists():raise ValueError('new_trial_directory_required')
    output.mkdir(parents=True)
    previous=runtime/'reports/generated/agent-investigation-upgrade/route2/20261019-action-v2'
    campaign=read(previous/'campaign-private.json')
    assert file_hash(previous/'campaign-private.json')==read(previous/'review-summary.json')['campaign_sha256']
    scopes=[]
    for packet in campaign['packets']:
        if packet['scope_private'] not in scopes:scopes.append(packet['scope_private'])
    scopes=scopes[:2];assert len(scopes)==2
    registered=read(runtime/'reports/generated/agent-investigation-upgrade/route0/20261019-preflight-v2/closeout.json')
    cases_path=runtime/'reports/generated/agent-investigation-upgrade/route0/20261019-preflight-v2/cases-private.json'
    assert file_hash(cases_path)==registered['private_cases_sha256']
    dev={c['transaction_id'] for c in read(cases_path)['cases'] if c['group']=='development'}
    assert all(s['transaction_id'] in dev for s in scopes)
    limits=read(SOURCE/'configs/agent_investigation_loop.json')
    profile=load_profile(SOURCE/'configs/agent_investigation_providers.json')
    profile=deepcopy(profile)
    profile['deepseek']['pricing_verified_date']=limits['pricing_verified_date']
    profile['local']['keep_alive']=limits['local_keep_alive']
    profile.update(max_policy_paragraphs=limits['max_policy_paragraphs'],prefer_conflicting_policies=True)
    freeze=read(runtime/'reports/generated/agent-investigation-upgrade/route1/20261019-native-v2/source-freeze.json')
    bindings={item['path']:item['sha256'] for item in freeze['source_bindings']}
    guard=ArtifactGuard({Path(p):sha for p,sha in bindings.items()});guard.verify_full()
    stats={p:[Path(p).stat().st_size,Path(p).stat().st_mtime_ns] for p in bindings}
    index_path=runtime/'reports/generated/agent-investigation-upgrade/route1/20261019-native-v2/lexical-policy-index.json'
    manifest={'version':'bounded-native-pilot-v1','scopes':scopes,'limits':limits,'profile':profile,
        'source_inventory':source_inventory(SOURCE),'protected_legacy':protected_hashes(runtime),
        'bindings':bindings,'artifact_stats':stats,'policy_index_path':str(index_path),'policy_index_sha256':file_hash(index_path),
        'cases_sha256':file_hash(cases_path),'action_prompt_sha256':digest(SYSTEM_PROMPT),'report_prompt_sha256':digest(REPORT_PROMPT),
        'formal_cases':0,'labels_read':False,'native_cases':2,'controlled_probe':'first_case_fixed_evidence_missing_graph_with_injected_score_meaning_error',
        'max_new_attempts':10 if supplement else 30,'max_each_provider':5 if supplement else 15,
        'episode_plan':[[0,'native']] if supplement else [[0,'native'],[1,'native'],[0,'controlled']],
        'supplement_after_preserved_v1_failures':supplement}
    write(output/'manifest-private.json',manifest)
    SessionStore(ledger(runtime)).campaign(output.name,digest(manifest),cap_each=manifest['max_each_provider'])
    # Frozen, explicitly controlled evidence for the report-repair probe. This
    # is never counted as a third independent native case.
    rows=[]
    batches=[ [('get_transaction',{})], [('get_transaction_features',{}),('get_risk_score',{})],
                   [('get_related_entities',{'limit':3}),('find_risky_neighbors',{'limit':3})],
                   [('retrieve_policy',{'focus':'trusted_device','top_k':6})] ]
    for calls in ([] if supplement else batches):
        req={'scope_index':0,'scenario':'controlled','observations':rows,'calls':[{'tool':name,'arguments':args} for name,args in calls]}
        request=output/'seed-request.json';response=output/'seed-response.json'
        write(request,req);invoke_worker(runtime,output,request,response,180)
        rows=read(response)['observations']
    write(output/'controlled-seed-private.json',{'observations':rows,'not_an_autonomous_trace':True})
    ledger(runtime).register_phase('seed-'+output.name,file_hash(output/'controlled-seed-private.json'),cloud_cap=0,local_cap=0)
    write(output/'prepared.json',{'status':'Prepared','manifest_sha256':digest(manifest),'seed_sha256':file_hash(output/'controlled-seed-private.json'),
        'planned_native_episodes':2 if supplement else 4,'planned_controlled_report_probes':0 if supplement else 2,
        'max_model_attempts':manifest['max_new_attempts'],'budget':ledger(runtime).snapshot()})
    print(canonical({'status':'Prepared','max_new_model_attempts':manifest['max_new_attempts'],'native_cases':1 if supplement else 2,'controlled_probe_per_provider':0 if supplement else 1}))


def approved(runtime,output):
    manifest=read(output/'manifest-private.json')
    with ledger(runtime).transaction() as db:row=db.execute('SELECT manifest_sha FROM autonomous_campaigns WHERE campaign_id=?',(output.name,)).fetchone()
    if row!=(digest(manifest),):raise ExecutionStop('trial_manifest_not_registered')
    if protected_hashes(runtime)!=manifest['protected_legacy']:raise ToolBoundaryError('legacy_ledger_changed')
    return manifest


def source_guard(manifest):
    try:
        if source_inventory(SOURCE)!=manifest['source_inventory']:raise ToolBoundaryError('executable_source_changed')
        if {p:[Path(p).stat().st_size,Path(p).stat().st_mtime_ns] for p in manifest['artifact_stats']}!=manifest['artifact_stats']:
            raise ToolBoundaryError('source_binding_changed')
    except OSError:raise ToolBoundaryError('source_binding_unreadable') from None


def registry_for(manifest):
    path=Path(manifest['policy_index_path'])
    if file_hash(path)!=manifest['policy_index_sha256']:raise ToolBoundaryError('policy_index_changed')
    index=PolicyIndex.model_validate_json(path.read_text(encoding='utf-8'))
    return PolicyRegistry.from_file(index,SOURCE/'configs/agent_investigation_policy_requirements.json')


def invoke_worker(runtime,output,request,response,timeout):
    env=os.environ.copy();env.pop('GRAPHRISK_UPGRADE_LIVE',None)
    # Worker cannot generate. Its only entry point validates the registered
    # scope/artifacts and restores typed read-only observations.
    try:
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'worker','--runtime',str(runtime),
            '--run-id',output.name,'--request',str(request),'--response',str(response)],env=env,capture_output=True,timeout=max(.01,timeout))
    except subprocess.TimeoutExpired:raise ExecutionStop('session_deadline_reached') from None
    if result.returncode:raise ToolBoundaryError('tool_worker_failed_or_source_changed')


def worker(runtime,output,request,response):
    from graphrisk.graph.coverage_service import CoverageGraphService,CoverageRiskTool,load_coverage
    from graphrisk.investigation.repository import InvestigationRepository
    from graphrisk.investigation.contracts import FindRiskyNeighborsResult
    if not request.resolve().is_relative_to(output.resolve()) or not response.resolve().is_relative_to(output.resolve()):raise ValueError('worker_path_outside_trial')
    manifest=approved(runtime,output);source_guard(manifest);req=read(request)
    if req['scenario'] not in ('native','controlled') or req['scope_index'] not in (0,1) or not 1<=len(req['calls'])<=2:raise ValueError('invalid_worker_request')
    scope=InvestigationScope.model_validate_json(canonical(manifest['scopes'][req['scope_index']]))
    guard=ArtifactGuard({Path(p):sha for p,sha in manifest['bindings'].items()})
    model_path=runtime/'artifacts/stage-09/route5b/20261015-coverage-lowmem-v3/coverage-profile-calendar-v2.json'
    profile=load_coverage(runtime,model_path,'5ecb2126eb282fc759bbbf261e576c6fe3b5f19d3c0185a9dee7d21a821232e4')
    transaction=runtime/'data/interim/ieee-cis/transactions.parquet';features=runtime/'data/interim/ieee-cis/features_all.parquet'
    repo=InvestigationRepository('ieee_cis',transaction,features)
    graph=CoverageGraphService(runtime,profile,transaction,features)
    executor=ReadOnlyToolExecutor.from_existing(scope,repository=repo,risk_tool=CoverageRiskTool(repo,graph),
        graph_service=graph,retriever=PolicyRetriever(Path(manifest['policy_index_path'])),source_guard=guard)
    registry=registry_for(manifest);observations=restore_observations(scope,req['observations'])
    build_evidence(scope,observations,registry);executor.restore_cached(observations)
    if req['scenario']=='controlled':
        executor._backends['find_risky_neighbors']=lambda _:FindRiskyNeighborsResult.failure(code='graph_prediction_unavailable',transaction_id=scope.transaction_id,dataset_source=scope.dataset_source)
    events=[]
    for item in req['calls']:
        before=len(executor.observations)
        try:
            observation=executor.execute(item['tool'],item['arguments'])
            build_evidence(scope,executor.observations,registry)
            events.append({'tool':item['tool'],'status':observation.result().status,'cached':before==len(executor.observations),'result_sha256':observation.result_sha256})
        except ToolBoundaryError as error:
            if str(error)!='tool_dependency_missing':raise
            events.append({'tool':item['tool'],'status':'skipped_dependency','cached':False})
    guard.verify_full();source_guard(manifest)
    write(response,{'observations':observation_rows(executor.observations),'events':events,'model_calls':0,'label_access':False})


class Factory:
    def __init__(self,provider,manifest,budget,output,runtime,live,replay):
        self.provider,self.manifest,self.budget,self.output,self.runtime=provider,manifest,budget,output,runtime
        self.live,self.replay=live,replay
    def __call__(self,campaign,phase,kind,deadline):
        cls=SummaryProvider if kind=='report' else ActionProvider
        return cls(self.provider,profile=self.manifest['profile'],budget=self.budget,campaign=campaign,campaign_sha=digest(campaign),
            phase=phase,output=self.output/'receipts',runtime=self.runtime,live=self.live,replay_only=self.replay,
            remaining=lambda:deadline-time.time())


def run(runtime,output,live,selected,replay):
    manifest=approved(runtime,output)
    if not replay:source_guard(manifest)
    budget=ledger(runtime);before=budget.snapshot();store=SessionStore(budget);registry=registry_for(manifest)
    seed_path=output/'controlled-seed-private.json'
    if file_hash(seed_path)!=read(output/'prepared.json')['seed_sha256']:raise ToolBoundaryError('controlled_seed_changed')
    with budget.transaction() as db:seed_row=db.execute('SELECT manifest_sha FROM execution_phases WHERE phase=?',('seed-'+output.name,)).fetchone()
    if seed_row!=(file_hash(seed_path),):raise ToolBoundaryError('controlled_seed_registration_changed')
    results=[]
    for provider in (('deepseek','local') if selected=='both' else (selected,)):
        for number,scenario in manifest.get('episode_plan',((0,'native'),(1,'native'),(0,'controlled'))):
            scope=InvestigationScope.model_validate_json(canonical(manifest['scopes'][number]))
            identifier=digest([output.name,provider,scope.binding_sha256,scenario])[:24]
            directory=output/identifier
            session_manifest={'parent_sha256':digest(manifest),'scope':scope.model_dump(mode='json'),'provider':provider,'scenario':scenario}
            if replay:
                state=store.restore(identifier,directory/'checkpoints')
                if state is None or state['route']!='end':raise ExecutionStop('replay_requires_completed_episode')
            else:
                deadline=store.open(identifier,output.name,session_manifest,provider,calls=5,seconds=600)
                def batch(rows,calls):
                    directory.mkdir(parents=True,exist_ok=True)
                    request=directory/'worker-request.json';response=directory/'worker-response.json'
                    write(request,{'scope_index':number,'scenario':scenario,'observations':rows,'calls':calls})
                    invoke_worker(runtime,output,request,response,min(180,deadline-time.time()))
                    value=read(response);return value['observations'],value['events']
                engine=BoundedInvestigation(scope=scope,registry=registry,profile=manifest['profile'],limits=manifest['limits'],
                    store=store,session=identifier,directory=directory,deadline=deadline,tool_batch=batch,
                    provider_factory=Factory(provider,manifest,budget,output,runtime,live,replay),source_guard=lambda:source_guard(manifest),
                    seed_observations=restore_observations(scope,read(seed_path)['observations']) if scenario=='controlled' else (),controlled_probe=scenario=='controlled')
                state=engine.run()
            result={'session':identifier,'provider':provider,'case_number':number+1,'scenario':scenario,'status':state['status'],
                'stop_reason':state['stop_reason'],'model_calls':store.used(identifier),'tool_requests':state['tool_requests'],
                'repairs':state['repairs'],'report_present':state['report'] is not None,'report_sha256':state['report']['report_sha256'] if state['report'] else None,
                'trace_sha256':digest(state['trace']),'final_checkpoint_sha256':digest(state)}
            results.append(result);print(canonical(result),flush=True)
            snap=budget.snapshot()
            if snap['unresolved_attempts'] or snap['integrity_stops'] or state['stop_reason'] in ('local_resource_headroom_insufficient','provider_resource_or_revision_stop'):
                break
        if budget.snapshot()['unresolved_attempts'] or budget.snapshot()['integrity_stops']:break
    after=budget.snapshot()
    if replay:assert before==after
    if not replay:source_guard(manifest)
    assert protected_hashes(runtime)==manifest['protected_legacy']
    summary={'status':'Measured','results':results,'before':before,'after':after,'new_model_attempts':after['total_attempts']-before['total_attempts'],
        'replay_only':replay,'formal_evaluation_admission':'Pending','learner_L3':'Pending','new_training':0}
    write(output/('replay' if replay else 'execution')/f'{selected}.json',summary)
    print(canonical({'episodes':len(results),'new_model_attempts':summary['new_model_attempts'],'budget':after}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('prepare','run','replay','worker'))
    parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--run-id',required=True)
    parser.add_argument('--provider',choices=('both','deepseek','local'),default='both');parser.add_argument('--live',action='store_true')
    parser.add_argument('--request',type=Path);parser.add_argument('--response',type=Path)
    parser.add_argument('--supplement',action='store_true',help='One registered native case per provider, within the SAME route3 group budget')
    args=parser.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,63}',args.run_id):raise ValueError('invalid_run_id')
    runtime=args.runtime.resolve();output=runtime/'reports/generated/agent-investigation-upgrade/route3'/args.run_id
    if args.mode=='prepare':prepare(runtime,output,supplement=args.supplement)
    elif args.mode=='worker':worker(runtime,output,args.request,args.response)
    else:run(runtime,output,args.live,args.provider,args.mode=='replay')

if __name__=='__main__':main()
