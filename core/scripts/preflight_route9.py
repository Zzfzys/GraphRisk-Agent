"""Read-only full-case readiness; never samples, loads models or opens a provider."""
from argparse import ArgumentParser
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def require(ok, reason):
    if not ok: raise ValueError('route9_'+reason)


def cases_for(registration, *, selection_cutoff):
    rows=registration['cases']
    require(Counter(r['group'] for r in rows)=={'development':15,'natural':30,'diagnostic':30},'original_75_groups_required')
    require(all(isinstance(r['transaction_id'],str) and r['transaction_id'] and
        type(r['event_second']) is int and type(r['selection_cutoff']) is int and
        r['event_second']>r['selection_cutoff'] and r['split'] in ('validation','test') and
        re.fullmatch('[a-f0-9]{32}',r['case_alias']) for r in rows),'invalid_case_metadata')
    require(len({r['transaction_id'] for r in rows})==75 and len({r['case_alias'] for r in rows})==75,'duplicate_case')
    formal=[r for r in rows if r['group']!='development'];ids={r['transaction_id'] for r in formal}
    for key in ('repeat_ids','human_read_ids'):
        require(len(registration[key])==5 and len(set(registration[key]))==5 and set(registration[key])<=ids,key+'_invalid')
    require(registration['human_read_provider']=='deepseek' and
        registration['human_read_version']=='first_published_report_or_original_failure','reading_source_changed')
    require(type(selection_cutoff) is int and selection_cutoff>=0,'invalid_selection_cutoff')
    prepared=[]
    for r in formal:
        day=r['event_second']//86400
        prepared.append({**r,'relative_day':day,'batch_end_second':(day+1)*86400,
            'daily_model_policy_eligible':day*86400>selection_cutoff,
            'preflight_block_reason':None if day*86400>selection_cutoff else 'day_not_after_current_policy_selection',
            'repeat_selected':r['transaction_id'] in registration['repeat_ids'],
            'human_read_selected':r['transaction_id'] in registration['human_read_ids'],
            'formal_execution':'NotRun','first_report_quality':'Pending','risk_utility':'Pending'})
    return prepared


def budget_plan(counts, max_calls):
    require(set(counts)=={'deepseek','local'} and all(type(n) is int and n>=0 for n in counts.values()),'invalid_counts')
    require(max_calls==5 and type(max_calls) is int,'case_call_limit_changed')
    per_provider=(60+5)*max_calls;total=sum(counts.values())
    return {'planning_total_envelope':1500,'used_total':total,'remaining_in_planning_envelope':max(0,1500-total),
        'currently_authorized_route9_requests':0,'formal_cases_per_provider':60,'repeats_per_provider':5,
        'planned_episodes':130,'per_episode_max_model_calls_including_repairs':5,
        'proposed_max_new_requests':2*per_provider,'proposed_new_per_provider':per_provider,
        'proposed_absolute_total_cap':total+2*per_provider,
        'proposed_absolute_deepseek_cap':counts['deepseek']+per_provider,
        'proposed_absolute_local_cap':counts['local']+per_provider,
        'increase_over_current_planning_envelope':max(0,total+2*per_provider-1500),
        'hard_limit_cny':100,'pause_estimate_cny':80,'paid_judges_in_this_proposal':0,
        'fixed_packet_llm_ablation_calls_in_this_proposal':0,
        'new_request_allowance_approved':False,'specific_outbound_approval':'NotPrepared',
        'not_an_execution_authorization':True}


def fingerprint(paths):
    return {str(p):{'sha256':sha(p),'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns}
        if p.exists() else None for p in paths}


def prepare(runtime, source, output, *, available_ram_bytes):
    require(not output.exists(),'new_output_directory_required')
    generated=runtime/'reports/generated/agent-investigation-upgrade'
    require(output.resolve().is_relative_to((generated/'route9').resolve()),'output_must_stay_in_route9')
    origin=generated/'route0/20261019-preflight-v2'
    policy_path=generated/'route6/20261105-daily-bands-v1/policy.json'
    last=generated/'route8/20261114-daily-whole-judge-v1'
    ledger=generated/'live-budget.sqlite3'
    paths=[origin/'closeout.json',origin/'cases-private.json',origin/'evaluation-labels.json',policy_path,
        source/'configs/agent_investigation_loop.json',source/'configs/agent_investigation_providers.json',
        last/'execution.json',last/'audit-completed.json',last/'COMPLETED-VALIDATION-SUMMARY.json',ledger,
        *[Path(str(ledger)+suffix) for suffix in ('-wal','-shm','-journal')]]
    before=fingerprint(paths)
    closed=read(origin/'closeout.json')
    require(sha(origin/'cases-private.json')==closed['private_cases_sha256'] and
        sha(origin/'evaluation-labels.json')==closed['labels_file_sha256'],'original_registration_changed')
    registration=read(origin/'cases-private.json')  # Label contents are not parsed.
    policy=read(policy_path)
    require(policy['model_sha256']=='39df475311dc34ecd83de66372cc1bc7aae4505517f6d878a4e1c4b11d47f2ba' and
        sha(policy_path)=='b0373c18c1fbe443f6ee5c9fd545d7353fc13e918724ecdf60bc1ce257f60f92','deployed_policy_changed')
    cases=cases_for(registration,selection_cutoff=policy['selection_cutoff'])
    last_cfg=read(last/'execution.json');summary=read(last/'COMPLETED-VALIDATION-SUMMARY.json')
    require(summary['whole_judge_reference_eligible'] is False,'reviewer_status_changed_reassess_protocol')
    with closing(sqlite3.connect(ledger.as_uri()+'?mode=ro',uri=True)) as db:
        counts=dict(db.execute('SELECT provider,count(*) FROM model_attempts GROUP BY provider'))
        require(counts=={'deepseek':1446,'local':23},'ledger_allocation_changed')
        require(db.execute('SELECT contract_sha,closed FROM rank_successors WHERE campaign_id=?',
            (last_cfg['campaign'],)).fetchone()==(sha(last/'execution.json'),1),'last_batch_not_closed')
        unknown=db.execute("SELECT count(*) FROM model_attempts WHERE state IN ('reserved','unknown')").fetchone()[0]
        stops=db.execute('SELECT count(*) FROM execution_requests WHERE integrity_stop=1').fetchone()[0]
        require(unknown==3 and stops==1,'history_changed')
        accounted=db.execute('SELECT COALESCE(sum(accounted),0) FROM execution_requests').fetchone()[0]
    loop=read(source/'configs/agent_investigation_loop.json');profile=read(source/'configs/agent_investigation_providers.json')
    budget=budget_plan(counts,loop['max_model_calls']);eligible=[c for c in cases if c['daily_model_policy_eligible']]
    inventory={p.relative_to(source).as_posix():sha(p) for name in ('src','api','scripts') for p in (source/name).rglob('*.py')}
    result={'version':'route9-full-case-readiness-v1','status':'Started_ReadinessNotLiveAuthorized',
        'source_python_count':len(inventory),'source_python_sha256':inventory,
        'formal_cases':60,'groups':dict(Counter(c['group'] for c in cases)),
        'existing_case_set_preserved':True,'daily_policy_eligible':len(eligible),'daily_policy_preblocked':60-len(eligible),
        'blocked_relative_days':sorted({c['relative_day'] for c in cases if not c['daily_model_policy_eligible']}),
        'repeat_selected':5,'human_read_selected':5,
        'human_read_with_daily_policy_eligibility':sum(c['human_read_selected'] for c in eligible),
        'formal_runs_completed':0,'label_contents_parsed':False,'case_replacement':False,
        'current_report_contract':'program-facts-judgment-v12-daily-quotes-v2',
        'current_model':'champion-xgb-day-216-plus49-fold3-v1','model_selection_cutoff':policy['selection_cutoff'],
        'whole_llm_judge':'NotQualified_NotUsedForAcceptance','semantic_quality_review':'AssistantReadingAndUserFeedback_Pending',
        'budget_plan':budget,'existing_accounted_peak_estimate_cny':accounted/1_000_000,
        'unknown_attempts':unknown,'identity_stops':stops,'no_old_unknowns_or_stops_reset':True,
        'resource_snapshot':{'available_ram_bytes':available_ram_bytes,'daily_ram_guard_bytes':2*2**30,
            'local_idle_guard_gib':profile['local']['idle_ram_min_gib'],
            'local_running_guard_gib':profile['local']['running_ram_min_gib'],
            'daily_headroom_pass':available_ram_bytes>=2*2**30,
            'local_headroom_pass':available_ram_bytes>=profile['local']['idle_ram_min_gib']*2**30},
        'full_agent_daily_report_integration':'NeedsIsolatedRunner_ExistingPilotIsSummaryContract',
        'development_isolation_reaudit':'Pending_NoClaimOfIndependentBlindSet',
        'new_model_requests':0,'new_paid_requests':0,'new_fits':0,'outbound_authorized':False,
        'route9':'InProgress','acceptance':'Pending','personal_L9':'Pending'}
    require(fingerprint(paths)==before,'read_only_inputs_changed')
    output.mkdir(parents=True)
    def write(name,value):
        with (output/name).open('x',encoding='utf-8') as stream:
            stream.write(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n')
    write('cases-private.json',{'source_case_sha256':closed['private_cases_sha256'],'cases':cases,
        'repeat_ids':registration['repeat_ids'],'human_read_ids':registration['human_read_ids'],
        'human_read_provider':registration['human_read_provider'],'human_read_version':registration['human_read_version']})
    write('readiness.json',result);write('protected-private.json',before)
    write('budget-proposal.json',budget)
    # Pre-register every denominator before outputs. This is not 130 completed runs.
    schedule=[{'episode':f'{provider}-{index+1:02}', 'provider':provider, 'case_alias':c['case_alias'],
        'kind':kind,'case_group':c['group'],'source_case_alias':c['case_alias'],
        'status':'NotRun','daily_policy_preblocked':not c['daily_model_policy_eligible'],
        'max_model_calls':5,'first_draft':None,'repair_report':None,'quality':'Pending'}
        for provider in ('deepseek','local') for index,(kind,c) in enumerate(
            [('first',c) for c in cases]+[('repeat',c) for c in cases if c['repeat_selected']])]
    require(len(schedule)==130,'schedule_length')
    write('episode-schedule-private.json',schedule)
    write('result-matrix.json',{'source':'pre_registered_not_actual_results','rows':schedule,
        'first_case_denominator_per_provider':60,'repeats_add_to_independent_denominator':False,
        'natural_and_diagnostic_combined_for_fraud_probability':False})
    return {k:result[k] for k in ('status','formal_cases','groups','daily_policy_eligible','daily_policy_preblocked',
        'formal_runs_completed','new_model_requests','new_paid_requests','route9','acceptance')}


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    import psutil
    print(json.dumps(prepare(args.runtime,args.source,args.output,
        available_ram_bytes=psutil.virtual_memory().available),ensure_ascii=False))
