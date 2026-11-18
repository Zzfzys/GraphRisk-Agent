"""Freeze/run one controlled DeepSeek repair cell; no automatic retries.

This diagnostic reuses two previously authorized development summaries. The
initial drafts are authored controlled defects, not real first generations.
"""
from pathlib import Path
import argparse,time
from datetime import date
from run_bounded_investigation import read,write,ledger,registry_for
from preflight_agent_upgrade import protected_hashes
from graphrisk.agent.autonomous_tools import InvestigationScope
from graphrisk.agent.autonomous_loop import restore_observations
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_providers import reservation_micro
from graphrisk.agent.judgment import packet_for,JudgmentProvider,JudgmentDraft,verify_judgment,PROMPT,REPAIR
from graphrisk.agent.judgment_evaluation import FAMILIES,controlled_input,assert_control,assert_solvable
from graphrisk.agent.narrative_repair import NarrativePatch,apply_patch_result,schema_issues
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash

SOURCE=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pricing-verified-date',required=True)
    parser.add_argument('--run-cell')
    parser.add_argument('--recovery-approval',type=Path)
    args=parser.parse_args();runtime=args.runtime.resolve();root=args.output.resolve()
    if not root.is_relative_to(runtime/'reports/generated/agent-investigation-upgrade/route5'):raise ValueError('output_outside_experiment_root')
    if args.pricing_verified_date!=date.today().isoformat():raise ValueError('pricing_refresh_required')
    root.mkdir(parents=True,exist_ok=True);budget=ledger(runtime);before=budget.snapshot()
    approval=read(args.recovery_approval) if args.recovery_approval else None
    if approval is not None:
        from graphrisk.agent.controlled_recovery import preview
        preview(budget,approval)
    elif before['unresolved_attempts'] or before['integrity_stops']:raise ExecutionStop('prior_unknown_or_integrity_stop')
    parent=runtime/'reports/generated/agent-investigation-upgrade/route5/supplement-v3/batch1'
    original=read(parent/'contract-private.json');profile=original['profile']
    profile['deepseek']['pricing_verified_date']=args.pricing_verified_date
    registry=registry_for(read(runtime/'reports/generated/agent-investigation-upgrade/route3/20261020-loop-v2-final/manifest-private.json'))
    cells=[];packets={};bundles={};evidence_hashes={}
    for case in (1,2):
        path=parent/f'evidence-{case}-private.json'
        if file_hash(path)!=original['evidence_files'][path.name]:raise ValueError('parent_evidence_changed')
        evidence_hashes[path.name]=file_hash(path);v=read(path)
        scope=InvestigationScope.model_validate_json(canonical(v['scope']));obs=restore_observations(scope,v['observations']);bundle=build_evidence(scope,obs,registry)
        if bundle.model_dump(mode='json')!=v['evidence']:raise ValueError('evidence_rebuild_changed')
        initial=packet_for(scope,obs,registry,profile)
        for family in FAMILIES:
            identifier=family+str(case);plan=assert_control(family,initial,bundle)
            packet=packet_for(scope,obs,registry,profile,plan)
            solvability=assert_solvable(family,plan,packet,bundle)
            cells.append({'id':identifier,'case':case,'family':FAMILIES[family],'input_source':'authored_controlled_defect','initial_sha256':digest(controlled_input(family)), 'plan_sha256':digest(plan),'packet_sha256':digest(packet),'prompt_bytes':packet['prompt_utf8_bytes'],'solvability':solvability})
            packets[identifier]=(packet,plan);bundles[identifier]=bundle
    order=[family+str(case) for family in FAMILIES for case in (1,2)]
    contract={'version':'controlled-judgment-repairs-v2','profile':profile,'source_sha256':{str(p.relative_to(SOURCE)):file_hash(p) for base in ('src','api','scripts') for p in (SOURCE/base).rglob('*.py')},
        'parent_contract_sha256':digest(original),'evidence_sha256':evidence_hashes,'cells':sorted(cells,key=lambda c:order.index(c['id'])),'order':order,
        'max_requests':10,'max_requests_per_cell':1,'group_key':'route5_supplement_v3','stop_rule':'unknown usage/identity/binding immediately; two consecutive failed acceptance reviews; never edit frozen batch',
        'prompt_sha256':digest(PROMPT),'repair_prompt_sha256':digest(REPAIR),'legacy':protected_hashes(runtime),
        'recovery_approval':approval,
        'purpose':'controlled repair mechanism, not first-draft quality, autonomous tool use, formal validation or fraud accuracy',
        'reviewer':'assistant evidence review, not independent human or paid judge','peak_reserve_micro_each':reservation_micro(profile),
        'pricing_source':'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/','pricing_verified_date':args.pricing_verified_date,
        'authorization':'User approved step3 real controlled comparison on 2026-10-23; only two previously authorized minimal summaries and controlled draft fragments.'}
    path=root/'contract-private.json'
    if path.exists():
        if read(path)!=contract:raise ValueError('frozen_contract_changed')
    else:
        if args.run_cell:raise ValueError('freeze_before_run')
        write(path,contract);write(root/'before.json',before)
        for identifier,(packet,plan) in packets.items():
            write(root/(identifier+'-packet-private.json'),packet);write(root/(identifier+'-plan-private.json'),plan)
    print(canonical({'contract_sha256':digest(contract),'cells':10,'max_calls':10,'prompt_bytes':[c['prompt_bytes'] for c in contract['cells']]}),flush=True)
    if args.run_cell is None:return
    identifier=args.run_cell
    if identifier not in order or (root/'STOP.json').exists() or (root/(identifier+'-result.json')).exists():raise ValueError('invalid_or_completed_or_stopped_cell')
    reviews=[]
    for previous in order[:order.index(identifier)]:
        review=read(root/(previous+'-review.json'))
        if not review.get('completed'):raise ValueError('previous_review_required')
        reviews.append(review)
    if len(reviews)>=2 and all(r['quality']=='Fail' for r in reviews[-2:]):raise ExecutionStop('consecutive_review_failures')
    store=SessionStore(budget);campaign='controlled-v2-'+digest(contract)[:16]
    store.campaign(campaign,digest(contract),cap_each=10,group_key='route5_supplement_v3')
    if approval is not None:
        from graphrisk.agent.controlled_recovery import register
        register(budget,approval,campaign,digest(contract))
    session=campaign+'-'+identifier;deadline=store.open(session,campaign,{'contract':digest(contract),'cell':identifier},'deepseek',calls=1,seconds=240)
    packet,plan=packets[identifier];packet['logical_key']='0'
    provider_campaign={'profile_sha256':digest(profile),'packets':[packet],'session':session}
    phase=store.register_packet(session,0,digest(provider_campaign),'deepseek')
    provider=JudgmentProvider('deepseek',profile=profile,budget=budget,campaign=provider_campaign,campaign_sha=digest(provider_campaign),phase=phase,output=root/'receipts',runtime=runtime,live=True,remaining=lambda:deadline-time.time())
    try:result=provider.generate('0')
    except (ExecutionStop,ActionFailure) as error:
        write(root/'STOP.json',{'reason':str(error),'cell':identifier,'budget':budget.snapshot()});raise
    record={'cell':identifier,'provider_result':result,'initial_source':'authored_controlled_defect','model_calls':store.used(session),'schema_pass':False,'known_rules_pass':False,'errors':[],'report':None}
    if result['code']=='ok':
        try:
            merged=apply_patch_result(NarrativePatch.model_validate_json(canonical(result['action'])),plan)
            record['merged_draft']=merged
            draft=JudgmentDraft.model_validate_json(canonical(merged),strict=True);record['schema_pass']=True
            errors,report=verify_judgment(draft,packet,bundles[identifier]);record.update(errors=errors,known_rules_pass=not errors,report=report)
        except (ValueError,ActionFailure) as error:
            record['errors']=['patch_or_schema_invalid'];record['diagnostics']=schema_issues(merged,model=JudgmentDraft) if 'merged' in locals() else [str(error)]
    else:record['errors']=[result['code']]
    if protected_hashes(runtime)!=contract['legacy']:raise ValueError('legacy_changed')
    write(root/(identifier+'-result.json'),record);write(root/(identifier+'-budget.json'),budget.snapshot())
    print(canonical({'cell':identifier,'schema_pass':record['schema_pass'],'known_rules_pass':record['known_rules_pass'],'errors':record['errors'],'merged_draft':record.get('merged_draft'),'budget':budget.snapshot()}),flush=True)


if __name__=='__main__':main()
