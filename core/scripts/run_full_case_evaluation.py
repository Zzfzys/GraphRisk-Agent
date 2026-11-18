"""Run the registered complete Agent cases; default is read-only validation."""
from argparse import ArgumentParser
from pathlib import Path
import os
import sqlite3
import time

from graphrisk.agent import full_case_execution as formal,judgment_v12_daily_quotes as quotes
from graphrisk.agent.autonomous_budget import InvestigationBudget
from graphrisk.agent.autonomous_http import post_one
from graphrisk.agent.autonomous_loop import BoundedInvestigation,observation_rows,restore_observations
from graphrisk.agent.autonomous_policy import PolicyRegistry
from graphrisk.agent.autonomous_providers import ActionProvider
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.autonomous_tools import DailyInvestigationScope
from graphrisk.agent.frozen_daily_tools import executor_for
from graphrisk.agent.policy_retrieval import load_policy_index,PolicyRetriever
from graphrisk.agent.report_generation_profile import quality_report_profile
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash,load_contract


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    body=canonical(value).encode('utf-8')
    if path.exists():
        if path.read_bytes()!=body:raise ValueError('existing result differs; retain and review')
    else:
        temporary=path.with_suffix('.tmp');temporary.write_bytes(body);os.replace(temporary,path)


def official_post_payload(self,payload,*,headers,timeout):
    proxy=self.profile.get('approved_loopback_proxy') if self.provider=='deepseek' and not self.fake else None
    return post_one(self.profile[self.provider]['endpoint'],payload,headers=headers,
        timeout=timeout,transport=self.transport,proxy=proxy)


def tool_batch_for(scope,envelope,registry,retriever,source_guard):
    def batch(rows,calls):
        executor=executor_for(scope,envelope,registry,retriever,source_guard=source_guard)
        executor.restore_cached(restore_observations(scope,rows));events=[]
        for call in calls:
            before=len(executor.observations)
            value=executor.execute(call['tool'],call['arguments'])
            events.append({'tool':call['tool'],'status':value.result().status,'cached':len(executor.observations)==before})
        return observation_rows(executor.observations),events
    return batch


def check(path):
    cfg=formal.read(path);formal.check_contract(cfg,mode='live')
    ledger=Path(cfg['runtime'])/'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    with sqlite3.connect(ledger.as_uri()+'?mode=ro',uri=True) as db:
        newer=formal.check_history(db,cfg)
        table=db.execute("SELECT 1 FROM sqlite_master WHERE name='formal_case_grants'").fetchone()
        grant=db.execute('SELECT closed FROM formal_case_grants WHERE campaign_id=?',(cfg['campaign'],)).fetchone() if table else None
        saved=[formal.audit_episode(db,cfg,row) for row in cfg['episodes'] if (Path(row['directory'])/'result-private.json').exists()]
    return cfg,{'status':'validated_not_sent','formal_case_denominator_per_provider':60,
        'registered_episodes':130,'max_new_calls':650,'new_calls_already_used':len(newer),
        'new_model_calls':0,'saved_episode_results':len(saved),'execution_closed':bool(grant and grant[0]),
        'tool_execution_mode':'frozen_real_snapshot_query_replay'}


def run(path,*,provider,live=False,approval=None):
    cfg,status=check(path)
    if not live:return status
    if status.get('execution_closed'):return {**status,'status':'closed_no_resend'}
    if os.getenv('GRAPHRISK_UPGRADE_LIVE')!='1' or approval is None:
        raise ValueError('explicit live flag, environment and concrete outbound approval required')
    formal.require(provider in ('deepseek','local'),'provider')
    runtime=Path(cfg['runtime']);source=Path(cfg['source_root'])
    profile=formal.read(cfg['profile_path'])
    # Check credentials/local residency before opening formal episode timers.
    # The existing provider preflight performs no generation request.
    probe=object.__new__(ActionProvider)
    probe.provider,probe.profile,probe.runtime,probe.fake,probe.transport=provider,profile,runtime,False,None
    probe.preflight()
    budget=InvestigationBudget(runtime/'reports/generated/agent-investigation-upgrade/live-budget.sqlite3',
        load_contract(source/'configs/agent_investigation_experiment.json'),mode='live')
    store=SessionStore(budget)
    with budget.transaction() as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='formal_case_grants'").fetchone()
        registered=db.execute('SELECT contract_path,contract_sha,approval_path,approval_sha FROM formal_case_grants WHERE campaign_id=?',(cfg['campaign'],)).fetchone() if exists else None
    if registered is None:formal.register(budget,path,approval)
    else:
        formal.require(registered==(str(Path(path).resolve()),file_hash(path),str(Path(approval).resolve()),file_hash(approval)),'registration_changed')
    store.campaign(cfg['campaign'],file_hash(path),cap_each=325,group_key=formal.GROUP)
    registry=PolicyRegistry.from_file(load_policy_index(Path(cfg['policy_index_path'])),Path(cfg['policy_requirements_path']))
    retriever=PolicyRetriever(Path(cfg['policy_index_path']));results=[]
    for episode in [row for row in cfg['episodes'] if row['provider']==provider]:
        destination=Path(episode['directory'])/'result-private.json'
        if destination.exists():
            with budget.transaction() as db:formal.audit_episode(db,cfg,episode)
            results.append(formal.read(destination));continue
        if episode['preblocked']:
            result=formal.episode_result(episode)
        else:
            scope=DailyInvestigationScope.model_validate_json(canonical(formal.read(episode['scope_path'])),strict=True)
            guard=lambda:formal.check_files(cfg)
            batch=tool_batch_for(scope,formal.read(episode['envelope_path']),registry,retriever,guard)
            class Factory:
                def __init__(self):self.provider=provider
                def __call__(self,campaign,phase,kind,deadline):
                    cls=quotes.Provider if kind=='report' else ActionProvider
                    class RoutedProvider(cls):
                        post_payload=official_post_payload
                    selected=quality_report_profile(profile) if kind=='report' else profile
                    return RoutedProvider(provider,profile=selected,budget=budget,campaign=campaign,campaign_sha=digest(campaign),phase=phase,live=True,
                        output=Path(episode['directory'])/'receipts',runtime=runtime,remaining=lambda:deadline-time.time())
            deadline=store.open(episode['session_id'],cfg['campaign'],formal.session_manifest(file_hash(path),episode),provider)
            engine=BoundedInvestigation(scope=scope,registry=registry,profile=profile,limits=cfg['limits'],store=store,
                session=episode['session_id'],directory=episode['directory'],deadline=deadline,
                tool_batch=batch,provider_factory=Factory(),source_guard=guard,report_contract=quotes.CONTRACT)
            state=engine.run()
            result=formal.episode_result(episode,state)
        write(destination,result);results.append(result)
        # Unknown transport/identity/resource state stops the batch, not just the case.
        with budget.transaction() as db:
            formal.check_history(db,cfg);formal.audit_episode(db,cfg,episode)
        if result.get('state',{}).get('stop_reason') in ('provider_resource_or_revision_stop',
                'local_resource_headroom_insufficient','hard_source_or_tool_contract_failure','execution_stopped_review_required'):
            return {'status':'execution_pause_remaining_not_run','provider':provider,
                'saved_episodes':len(results),'formal_case_denominator':60,'quality_acceptance':'Pending'}
    return {'status':'provider_episodes_finished_quality_pending','provider':provider,
        'episodes':len(results),'formal_case_denominator':60,'quality_acceptance':'Pending',
        'model_calls':sum(row['model_calls'] for row in results)}


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--provider',choices=('deepseek','local'),required=True)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--outbound-approval',type=Path)
    parser.add_argument('--close-execution',action='store_true',help='Close saved outcomes only; no model call')
    args=parser.parse_args()
    if args.close_execution:
        cfg,_=check(args.config)
        budget=InvestigationBudget(Path(cfg['runtime'])/'reports/generated/agent-investigation-upgrade/live-budget.sqlite3',
            load_contract(Path(cfg['source_root'])/'configs/agent_investigation_experiment.json'),mode='live')
        value=formal.close_execution(budget,args.config)
    else:value=run(args.config,provider=args.provider,live=args.live,approval=args.outbound_approval)
    print(canonical(value))


if __name__=='__main__':main()
