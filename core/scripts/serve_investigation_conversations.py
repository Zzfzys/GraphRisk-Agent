"""Route4 API over registered development evidence; generation is opt-in.

Default reads previously measured evidence only. No model is called on startup.
The CLI has no arbitrary prompt, source path, SQL or label-loading option.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import os
import time
import subprocess
import sys
from uuid import uuid4

from api.main import create_app
from run_bounded_investigation import ledger, read, write, registry_for, Factory
from graphrisk.agent.autonomous_tools import ArtifactGuard,InvestigationScope,ReadOnlyToolExecutor,ToolBoundaryError
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.autonomous_loop import restore_observations,observation_rows
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash
from graphrisk.serving.conversation_backend import BoundedConversationBackend
from graphrisk.serving.conversation_store import MemoryConversationStore,PostgresConversationStore
from graphrisk.serving.conversations import ConversationService

SOURCE=Path(__file__).resolve().parents[1]


def make_service(runtime: Path, *, postgres_dsn=None, live=False, worker_mode=False, narrative=False, report_contract='program-facts-judgment-v2'):
    if report_contract not in ('program-facts-judgment-v2','program-facts-judgment-v3'):
        raise ToolBoundaryError('narrative_contract_unknown')
    if not narrative and report_contract!='program-facts-judgment-v2':
        raise ToolBoundaryError('narrative_contract_requires_narrative')
    root=runtime/'reports/generated/agent-investigation-upgrade'
    budget=ledger(runtime);sessions=SessionStore(budget)
    output=root/('route5' if narrative else 'route4')/('native-v3' if report_contract=='program-facts-judgment-v3' else 'native-v1')
    output.mkdir(parents=True,exist_ok=True)
    entries=[]
    original=[]
    for run_id,session_id,index in (
        ('20261020-loop-v2-final','a15373945f7a1456b663d902',0),
        ('20261020-loop-v1','c76f65c94194ac390e777293',1)):
        directory=root/'route3'/run_id
        manifest=read(directory/'manifest-private.json')
        with budget.transaction() as db:
            row=db.execute('SELECT manifest_sha FROM autonomous_campaigns WHERE campaign_id=?',(run_id,)).fetchone()
        if row!=(digest(manifest),):raise ToolBoundaryError('original_campaign_changed')
        state=sessions.restore(session_id,directory/session_id/'checkpoints')
        scope=InvestigationScope.model_validate_json(canonical(manifest['scopes'][index]))
        if state is None or state['scope_sha256']!=scope.binding_sha256 or state['route']!='end':
            raise ToolBoundaryError('original_checkpoint_binding_failed')
        guard=ArtifactGuard({Path(p):sha for p,sha in manifest['bindings'].items()})
        guard.verify_full()
        registry=registry_for(manifest)
        entries.append(dict(scope=scope,registry=registry,observations=restore_observations(scope,state['observations']),
                            guard=guard,policy_index_path=manifest['policy_index_path']))
        original.append({'session':session_id,'checkpoint_sha256':digest(state),'manifest_sha256':digest(manifest)})
    # Frozen pilot contract: no change of prompt candidates, quotas or backend.
    manifest=read(root/'route3/20261020-loop-v2-final/manifest-private.json')
    profile=manifest['profile'];limits=manifest['limits']
    # Pricing date comes from reviewed source configuration. Never substitute
    # today's date merely to bypass the live provider's freshness gate.
    profile['deepseek']['pricing_verified_date']=read(SOURCE/('configs/agent_investigation_narrative.json' if narrative else 'configs/agent_investigation_loop.json'))['pricing_verified_date']
    if narrative and report_contract=='program-facts-judgment-v3':
        from graphrisk.agent.report_generation_profile import quality_report_profile
        report_profile=quality_report_profile(profile)
    code_paths=[p for base in ('src','api','scripts') for p in (SOURCE/base).rglob('*.py')]
    code_hashes={str(p.relative_to(SOURCE)):file_hash(p) for p in code_paths}
    authorization={'version':'conversation-pilot-v1','parents':original,'profile_sha256':digest(profile),
        'source_sha256':digest(code_hashes),'max_each_provider':10,'group':'route5' if narrative else 'route4','new_formal_cases':0}
    if report_contract=='program-facts-judgment-v3':
        authorization.update(report_contract=report_contract,report_profile_sha256=digest(report_profile))
    campaign=('route5-native-' if narrative else 'route4-native-')+digest(authorization)[:16]
    # Registration is immutable. A code change needs a reviewed new campaign;
    # every campaign still shares the same route4/global limits.
    sessions.campaign(campaign,digest(authorization),cap_each=10,group_key='route5' if narrative else 'route4')
    frozen=output/(campaign+'.json')
    if frozen.exists() and read(frozen)!=authorization:raise ToolBoundaryError('route4_source_changed')
    write(frozen,authorization)

    def batch(entry,rows,calls,deadline):
        from graphrisk.graph.coverage_service import CoverageGraphService,CoverageRiskTool,load_coverage
        from graphrisk.investigation.repository import InvestigationRepository
        from graphrisk.agent.policy_retrieval import PolicyRetriever
        if time.time()>=deadline:raise ToolBoundaryError('tool_deadline')
        transaction=runtime/'data/interim/ieee-cis/transactions.parquet'
        features=runtime/'data/interim/ieee-cis/features_all.parquet'
        coverage=load_coverage(runtime,runtime/'artifacts/stage-09/route5b/20261015-coverage-lowmem-v3/coverage-profile-calendar-v2.json',
            '5ecb2126eb282fc759bbbf261e576c6fe3b5f19d3c0185a9dee7d21a821232e4')
        repo=InvestigationRepository('ieee_cis',transaction,features)
        graph=CoverageGraphService(runtime,coverage,transaction,features)
        executor=ReadOnlyToolExecutor.from_existing(entry['scope'],repository=repo,risk_tool=CoverageRiskTool(repo,graph),
            graph_service=graph,retriever=PolicyRetriever(Path(entry['policy_index_path'])),source_guard=entry['guard'])
        executor.restore_cached(restore_observations(entry['scope'],rows))
        events=[]
        for call in calls:
            try:
                result=executor.execute(call['tool'],call['arguments'])
                events.append({'tool':call['tool'],'status':result.result().status})
            except ToolBoundaryError as error:
                if str(error)!='tool_dependency_missing':raise
                events.append({'tool':call['tool'],'status':'skipped_dependency'})
        return observation_rows(executor.observations),events

    def isolated_batch(entry, rows, calls, deadline):
        token=uuid4().hex
        request=output/'workers'/(token+'-request.json');response=output/'workers'/(token+'-response.json')
        write(request,{'case':entry['scope'].case_alias,'observations':rows,'calls':calls})
        environment=dict(os.environ);environment.pop('GRAPHRISK_UPGRADE_LIVE',None)
        try:
            done=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--runtime',str(runtime),
                '--worker-request',str(request),'--worker-response',str(response)]+(['--narrative','--report-contract',report_contract] if narrative else []),env=environment,
                capture_output=True,timeout=max(.01,min(180,deadline-time.time())))
        except subprocess.TimeoutExpired:
            raise ToolBoundaryError('tool_deadline') from None
        if done.returncode:raise ToolBoundaryError('tool_worker_failed')
        value=read(response)
        return value['observations'],value['events']

    backend=BoundedConversationBackend(entries=entries,store=sessions,campaign=campaign,provider='deepseek',profile=profile,limits=limits,
        directory=output/'turns',provider_factory=Factory('deepseek',manifest,budget,output,runtime,live,False),
        tool_batch=batch if worker_mode else isolated_batch,allow_investigation=live)
    if narrative:
        if report_contract=='program-facts-judgment-v3':
            from graphrisk.agent.judgment_v3 import JudgmentProvider as NarrativeProvider,CONTRACT
        else:
            from graphrisk.agent.judgment import JudgmentProvider as NarrativeProvider,CONTRACT
        backend.report_contract=CONTRACT
        if report_contract=='program-facts-judgment-v3':backend.report_profile=report_profile
        else:report_profile=profile
        backend.narrative_factory=lambda campaign,phase,deadline: NarrativeProvider('deepseek',profile=report_profile,budget=budget,campaign=campaign,campaign_sha=digest(campaign),phase=phase,output=output/'receipts',runtime=runtime,live=live,remaining=lambda:deadline-time.time())
    store=PostgresConversationStore(postgres_dsn) if postgres_dsn else MemoryConversationStore()
    if postgres_dsn:store.migrate()
    return ConversationService(store,backend)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8001)
    parser.add_argument('--live',action='store_true',help='Explicit metered DeepSeek investigation; also needs GRAPHRISK_UPGRADE_LIVE=1 and fresh pricing configuration')
    parser.add_argument('--narrative',action='store_true',help='Route5 Chinese narrative contract and shared route5 budget')
    parser.add_argument('--report-contract',choices=['program-facts-judgment-v2','program-facts-judgment-v3'],default='program-facts-judgment-v2',help='Versioned report generation; does not authorize or extend live budget')
    parser.add_argument('--worker-request',type=Path)
    parser.add_argument('--worker-response',type=Path)
    args=parser.parse_args()
    if args.worker_request is not None:
        root=(args.runtime/'reports/generated/agent-investigation-upgrade'/('route5' if args.narrative else 'route4')/('native-v3/workers' if args.report_contract=='program-facts-judgment-v3' else 'native-v1/workers')).resolve()
        if args.live or args.worker_response is None or any(not p.resolve().is_relative_to(root) for p in (args.worker_request,args.worker_response)):
            raise ValueError('invalid_worker_paths')
        service=make_service(args.runtime.resolve(),worker_mode=True,narrative=args.narrative,report_contract=args.report_contract)
        request=read(args.worker_request)
        if set(request)!={'case','observations','calls'} or not 1<=len(request['calls'])<=2:raise ValueError('invalid_worker_contract')
        entry=service.backend.entries[request['case']]
        rows,events=service.backend.batch(entry,request['observations'],request['calls'],time.time()+180)
        write(args.worker_response,{'observations':rows,'events':events})
        return
    import uvicorn
    service=make_service(args.runtime.resolve(),postgres_dsn=os.getenv('GRAPHRISK_CONVERSATION_DSN'),live=args.live,narrative=args.narrative,report_contract=args.report_contract)
    uvicorn.run(create_app(conversation_service_factory=lambda:service),host='127.0.0.1',port=args.port,access_log=False)


if __name__=='__main__':main()
