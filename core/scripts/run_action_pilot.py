"""Prepare native reviewed inputs, or resume the separately registered 12-call pilot."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re

from graphrisk.agent.autonomous_actions import load_campaign, load_profile, prepare_packet
from graphrisk.agent.autonomous_budget import InvestigationBudget
from graphrisk.agent.autonomous_providers import ActionProvider
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash, load_contract

SOURCE=Path(__file__).resolve().parents[1]


def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
def read(path):return json.loads(path.read_text(encoding='utf-8'))


def prepare(runtime,output):
    from graphrisk.agent.autonomous_tools import ArtifactGuard, InvestigationScope, ReadOnlyToolExecutor
    from graphrisk.agent.autonomous_policy import PolicyRegistry
    from graphrisk.agent.policy_corpus import load_policy_corpus
    from graphrisk.agent.policy_retrieval import PolicyRetriever,build_policy_index,write_policy_index
    from graphrisk.graph.coverage_service import CoverageGraphService,CoverageRiskTool,load_coverage
    from graphrisk.investigation.repository import InvestigationRepository
    from preflight_agent_upgrade import protected_hashes
    from stage09_ci import source_inventory
    if output.exists():raise ValueError('new_pilot_directory_required')
    output.mkdir(parents=True)
    profile=load_profile(SOURCE/'configs/agent_investigation_providers.json')
    protected=protected_hashes(runtime)
    root0=runtime/'reports/generated/agent-investigation-upgrade/route0/20261019-preflight-v2'
    registered=read(root0/'closeout.json')
    if file_hash(root0/'cases-private.json')!=registered['private_cases_sha256']:raise ValueError('case_registration_changed')
    cases=[c for c in read(root0/'cases-private.json')['cases'] if c['group']=='development'][:3]
    assert len({c['transaction_id'] for c in cases})==3
    model_path=runtime/'artifacts/stage-09/route5b/20261015-coverage-lowmem-v3/coverage-profile-calendar-v2.json'
    frozen=load_coverage(runtime,model_path,'5ecb2126eb282fc759bbbf261e576c6fe3b5f19d3c0185a9dee7d21a821232e4')
    transaction=runtime/'data/interim/ieee-cis/transactions.parquet';features=runtime/'data/interim/ieee-cis/features_all.parquet'
    repo=InvestigationRepository('ieee_cis',transaction,features)
    graph=CoverageGraphService(runtime,frozen,transaction,features)
    index=build_policy_index(load_policy_corpus(SOURCE/'data/policies/stage05'))
    index_path=write_policy_index(index,output/'policy-index.json')
    annotations=SOURCE/'configs/agent_investigation_policy_requirements.json'
    registry=PolicyRegistry.from_file(index,annotations)
    bindings={runtime/name:meta['sha256'] for name,meta in frozen['files'].items()}
    bindings.update({model_path:file_hash(model_path),index_path:file_hash(index_path),annotations:file_hash(annotations)})
    guard=ArtifactGuard(bindings)
    packets=[];raw=[]
    for number,case in enumerate(cases,1):
        covered=graph.coverage(case['transaction_id'])
        if covered['status']!='covered':raise ValueError('pilot_case_not_covered')
        scope=InvestigationScope(case_alias=case['case_alias'],transaction_id=case['transaction_id'],dataset_source='ieee_cis',
            target_event_second=case['event_second'],policy_as_of=date(2026,10,19),transaction_revision=file_hash(transaction),
            feature_revision=file_hash(features),model_revision=file_hash(model_path),primary_model_version=covered['primary_model_version'],
            graph_model_version=covered['graph_model_version'],policy_index_version=index.index_version)
        executor=ReadOnlyToolExecutor.from_existing(scope,repository=repo,risk_tool=CoverageRiskTool(repo,graph),
            graph_service=graph,retriever=PolicyRetriever(index_path),source_guard=guard)
        executor.execute('get_transaction',{})
        packet=prepare_packet(scope,executor.observations,registry,task='investigate',profile=profile)
        packets.append({**packet,'logical_key':f'dev{number:02d}-initial'})
        for name,args in [('get_transaction_features',{}),('get_risk_score',{}),('get_related_entities',{'limit':3}),
                          ('find_risky_neighbors',{'limit':3}),*[("retrieve_policy",{'focus':f,'top_k':6}) for f in ['device_sharing','trusted_device','graph_limits']]]:
            executor.execute(name,args)
        packet=prepare_packet(scope,executor.observations,registry,task='investigate',profile=profile)
        packets.append({**packet,'logical_key':f'dev{number:02d}-full'})
        raw.append({'scope':scope.model_dump(mode='json'),'observations':[{'tool':o.call.name,'result':json.loads(o.result_json)} for o in executor.observations]})
    guard.verify_full()
    assert protected_hashes(runtime)==protected
    campaign={'version':'action-pilot-v1','profile_sha256':digest(profile),'packets':packets,
              'pilot_cases':3,'states_per_case':2,'formal_validation_cases':0,
              'source_inventory':source_inventory(SOURCE),'protected_legacy':protected,
              'route0_cases_sha256':registered['private_cases_sha256'],'evaluation_labels_read':False}
    path=output/'campaign-private.json';path.write_text(canonical(campaign),encoding='utf-8')
    sha=file_hash(path);assert sha==digest(campaign)
    budget=InvestigationBudget(runtime/'reports/generated/agent-investigation-upgrade/live-budget.sqlite3',
                               load_contract(SOURCE/'configs/agent_investigation_experiment.json'),mode='live')
    phase='route2-'+output.name
    budget.register_phase(phase,sha,cloud_cap=6,local_cap=6)
    write(output/'raw-native-private.json',raw)
    write(output/'review-summary.json',{'status':'Prepared_not_generated','phase':phase,'campaign_sha256':sha,
          'unique_development_cases':3,'packet_count':6,'planned_model_requests':12,
          'prompt_utf8_bytes':[p['prompt_utf8_bytes'] for p in packets],
          'source_evidence_versions':[p['view']['evidence_version'] for p in packets],
          'label_or_entity_values_in_model_view':False,'privacy_review':'server_rebuild_whitelist_and_hash_registered',
          'budget':budget.snapshot()})
    print(canonical({'status':'Prepared','campaign_sha256':sha,'prompt_utf8_bytes':[p['prompt_utf8_bytes'] for p in packets]}))


def run(runtime,output,live,selected_provider,*,replay_only=False):
    from preflight_agent_upgrade import protected_hashes
    from stage09_ci import source_inventory
    profile=load_profile(SOURCE/'configs/agent_investigation_providers.json')
    budget=InvestigationBudget(runtime/'reports/generated/agent-investigation-upgrade/live-budget.sqlite3',
                               load_contract(SOURCE/'configs/agent_investigation_experiment.json'),mode='live')
    phase='route2-'+output.name
    with budget.transaction() as db:
        row=db.execute('SELECT manifest_sha FROM execution_phases WHERE phase=?',(phase,)).fetchone()
    if row is None:raise ValueError('prepare_and_review_before_live')
    campaign=load_campaign(output/'campaign-private.json',row[0],profile=profile)
    if not replay_only and source_inventory(SOURCE)!=campaign['source_inventory']:raise ValueError('pilot_executable_source_changed_before_run')
    if protected_hashes(runtime)!=campaign['protected_legacy']:raise ValueError('legacy_ledger_drift')
    results=[];stopped=None;before=budget.snapshot()
    try:
        for provider in (['deepseek','local'] if selected_provider=='both' else [selected_provider]):
            adapter=ActionProvider(provider,profile=profile,budget=budget,campaign=campaign,campaign_sha=row[0],
                phase=phase,output=output/'receipts',runtime=runtime,live=live,replay_only=replay_only)
            for packet in campaign['packets']:
                result=adapter.generate(packet['logical_key'])
                path=output/(provider+'-'+packet['logical_key']+'.json')
                if path.exists():
                    old=read(path)
                    if old['attempt_id']!=result['attempt_id']:raise ValueError('saved_result_attempt_changed')
                else:write(path,result)
                results.append(result)
                print(canonical({'provider':provider,'key':packet['logical_key'],'code':result['code'],
                                 'replayed':result['replayed_without_model_request']}),flush=True)
                if result['code'] in {'local_running_resource_limit','backend_identity_or_context_violation'}:
                    raise ValueError('resource_or_identity_stop_keep_results')
    except Exception as error:
        stopped=str(error) if re.fullmatch(r'[a-z_]+',str(error)) else 'pilot_execution_stopped'
    assert protected_hashes(runtime)==campaign['protected_legacy']
    if replay_only:assert budget.snapshot()==before
    complete=len(results)==(12 if selected_provider=='both' else 6) and not stopped
    valid=sum(r['schema_and_binding_valid'] for r in results)
    summary={'status':'Pass' if complete and valid==len(results) else 'Fail' if complete else 'Pending',
             'execution_complete':bool(complete),'action_contract_status':'Pass' if complete and valid==len(results) else 'Fail' if complete else 'Pending',
             'selected_provider':selected_provider,'completed_or_replayed':len(results),'valid_actions':sum(r['schema_and_binding_valid'] for r in results),
             'stop_code':stopped,'budget':budget.snapshot(),'new_real_model_fits':0,'old_ledgers_unchanged':True,
             'replay_only':replay_only,'new_model_attempts':budget.snapshot()['total_attempts']-before['total_attempts'],
             'assessment_scope':'provider_action_pilot_not_end_to_end_agent_quality','learner_L2':'Pending'}
    write(output/('execution-'+('replay-' if replay_only else '')+selected_provider+'.json'),summary);print(canonical(summary),flush=True)
    return 0 if summary['status']=='Pass' else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','run','replay'])
    parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--run-id',required=True)
    parser.add_argument('--provider',choices=['both','deepseek','local'],default='both');parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,63}',args.run_id):raise SystemExit('invalid_run_id')
    root=args.runtime.resolve();output=root/'reports/generated/agent-investigation-upgrade/route2'/args.run_id
    if args.mode=='prepare':prepare(root,output);return 0
    return run(root,output,args.live,args.provider,replay_only=args.mode=='replay')


if __name__=='__main__':raise SystemExit(main())
