"""Freeze six public boundary tasks before any validation generation."""
import argparse
from datetime import date
from pathlib import Path
from copy import deepcopy
from freeze_report_pilot import SOURCE,source_snapshot,source_archive,write_new,read
from run_bounded_investigation import ledger
from graphrisk.agent import report_quality_program as program
from graphrisk.agent.report_quality_scenarios import SCENARIOS,SCENARIOS_V2,build_scenario
from graphrisk.agent.report_generation_profile import quality_report_profile
from graphrisk.agent.policy_corpus import load_policy_corpus
from graphrisk.agent.policy_retrieval import build_policy_index
from graphrisk.agent.autonomous_policy import PolicyRegistry
from graphrisk.agent.autonomous_loop import observation_rows
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.judgment_v3 import packet_for,JudgmentProvider
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash


def freeze(runtime,directory,output,development,*,cohort=1):
    scenarios=SCENARIOS if cohort==1 else SCENARIOS_V2
    if cohort not in (1,2):raise ExecutionStop("quality_validation_unknown_cohort")
    if output.exists():raise ExecutionStop('quality_validation_new_directory_required')
    base=read(development/'contract-private.json');profile=quality_report_profile(base['profile'])
    if profile!=base['profile']:raise ExecutionStop('quality_validation_profile_differs_from_final_candidate')
    budget=ledger(runtime);path=directory/'program-private.json'
    with budget.transaction() as db:grant=program.check_program(db,path,file_hash(path))
    output.mkdir(parents=True)
    snapshot=source_snapshot();source_archive(SOURCE,output/'source-archive.zip',snapshot)
    index=build_policy_index(load_policy_corpus(SOURCE/'data/policies/stage05'))
    registry=PolicyRegistry.from_file(index,SOURCE/'configs/agent_investigation_policy_requirements.json')
    index_path=output/'public-policy-index.json';write_new(index_path,index.model_dump(mode='json'))
    protocol={'version':'public-boundary-one-shot-v1','data_source':'synthetic_authored_public','tasks':list(scenarios),'cohort':cohort,
        'model':'real_deepseek_api','generation_per_task':1,'repair':False,'minimum_quality_passes':5,'task_count':6,
        'critical_fact_errors_allowed':0,'quality_total_at_least':6,'each_dimension_at_least':1,
        'judging':'same_provider_separate_blinded_context_plus_assistant_evidence_review',
        'failure_tasks_not_replaced_or_used_to_tune_this_validation':True,'personal_readability':'Pending',
        'profile_sha256':digest(profile),'source':snapshot,'development_contract_sha256':file_hash(development/'contract-private.json')}
    protocol_path=output/'validation-protocol.json';write_new(protocol_path,protocol)
    batches=[]
    for pair in range(3):
        batch=output/(f'validation-pair-{pair+1}' if cohort==1 else f'validation-v2-pair-{pair+1}');batch.mkdir();cases={}
        files={str(p):file_hash(p) for p in (index_path,protocol_path,output/'source-archive.zip',development/'contract-private.json')}
        for case,spec in zip(('C1','C2'),scenarios[pair*2:pair*2+2]):
            scope,observations=build_scenario(spec,index,registry);bundle=build_evidence(scope,observations,registry)
            packet=packet_for(scope,observations,registry,profile)
            raw=batch/(case+'-raw-evidence-private.json');write_new(raw,{'scope':scope.model_dump(mode='json'),'observations':observation_rows(observations),'evidence':bundle.model_dump(mode='json')})
            formatter=object.__new__(JudgmentProvider);formatter.provider,formatter.profile='deepseek',profile
            payload=formatter.payload(packet);files[str(raw)]=file_hash(raw)
            payload_path=batch/(case+'-first-request-private.json');write_new(payload_path,payload);files[str(payload_path)]=file_hash(payload_path)
            cases[case]={'raw_evidence_path':str(raw),'first_payload_sha':digest(payload),'evidence_sha':digest(bundle.model_dump(mode='json')),
                'session':batch.name+'-'+case,'session_manifest':{'validation_protocol_sha256':file_hash(protocol_path),'scenario_id':spec['id'],'evidence_sha256':digest(bundle.model_dump(mode='json'))}}
        cfg={'version':program.BATCH,'source_root':str(SOURCE),'source':snapshot,'files':files,'cases':cases,
            'registry_index':str(index_path),'campaign':batch.name,'program_id':grant['program_id'],'program_sha256':grant['contract_sha256'],
            'kind':'report','data_kind':'synthetic_validation','profile':deepcopy(profile),'pricing_date':date.today().isoformat(),
            'max_requests':4,'max_requests_per_case':2,'actual_generation_cap':2,'repair_allowed':False,'order':['C1','C2']}
        cfg['contract_sha256']=digest(cfg);write_new(batch/'contract-private.json',cfg)
        SessionStore(budget).campaign(cfg['campaign'],cfg['contract_sha256'],cap_each=4,group_key='route5_supplement_v3')
        program.register_batch(budget,batch/'contract-private.json');batches.append(str(batch))
    write_new(output/'manifest.json',{'protocol_sha256':file_hash(protocol_path),'batches':batches,'new_model_calls':0})
    return {'status':'six_public_tasks_frozen_no_model_calls','batches':batches,'new_model_calls':0}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True);p.add_argument('--program',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--development',type=Path,required=True)
    p.add_argument('--cohort',type=int,choices=(1,2),default=1)
    a=p.parse_args();runtime,directory,output=a.runtime.resolve(),a.program.resolve(),a.output.resolve()
    if not directory.is_relative_to(runtime/'reports/generated/agent-investigation-upgrade/route5/report-v3') or not output.is_relative_to(directory):p.exit(2,'quality_validation_output_outside_program\n')
    print(canonical(freeze(runtime,directory,output,a.development.resolve(),cohort=a.cohort)))
if __name__=='__main__':main()
