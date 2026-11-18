"""Freeze and run blinded LLM judgments in the SAME finite quality ledger."""
import argparse
from copy import deepcopy
from datetime import date
from pathlib import Path
import time

from freeze_report_pilot import SOURCE,source_snapshot,read,write_new
from run_bounded_investigation import ledger
from run_report_pilot import connection_preflight
from graphrisk.agent import report_quality_program as program
from graphrisk.agent import report_quality_judge as judge
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash


def freeze(runtime,directory,source_batch,output,*,paired=False):
    if output.exists():raise ExecutionStop('quality_judge_new_directory_required')
    budget=ledger(runtime)
    source=read(source_batch/'contract-private.json')
    profile=deepcopy(source['profile'])
    if paired:profile['max_prompt_utf8_bytes']=32000
    program_path=directory/'program-private.json'
    with budget.transaction() as db:
        grant=program.check_program(db,program_path,file_hash(program_path))
    output.mkdir(parents=True)
    files={str(source_batch/'contract-private.json'):file_hash(source_batch/'contract-private.json')}
    cases={}
    qualities=[];source_packets=[]
    for case in ('C1','C2'):
        result_path=source_batch/(case+'-result-private.json')
        result=read(result_path)
        last=result['slots'][-1]
        slot=read(Path(last['path']))
        if file_hash(Path(last['path']))!=last['sha256'] or not slot['quality_packet_path']:
            raise ExecutionStop('quality_judge_actual_draft_unavailable')
        quality_path=Path(slot['quality_packet_path'])
        quality=read(quality_path)
        if digest(slot['draft'])!=quality['source']['draft_sha256']:
            raise ExecutionStop('quality_judge_draft_binding_changed')
        with budget.transaction() as db:
            receipt=db.execute('SELECT a.state,r.receipt_path,r.receipt_sha FROM model_attempts a JOIN execution_requests r USING(attempt_id) WHERE a.attempt_id=?',
                               (slot['result']['attempt_id'],)).fetchone()
        if not receipt or receipt[0]!='settled' or file_hash(Path(receipt[1]))!=receipt[2]:
            raise ExecutionStop('quality_judge_requires_actual_saved_response')
        qualities.append(quality);source_packets.append(slot['packet'])
        packet=judge.packet_for(quality,slot['packet'],profile)
        packet_path=output/(case+'-packet-private.json')
        write_new(packet_path,packet)
        files.update({str(p):file_hash(p) for p in (result_path,Path(last['path']),quality_path,Path(receipt[1]),packet_path)})
        cases[case]={'session':output.name+'-'+case,'session_manifest':{'quality_target':quality['target_sha256'],'packet':digest(packet)},
                     'packet_path':str(packet_path),'quality_path':str(quality_path),
                     'evidence_sha':quality['source']['evidence_sha256']}
    if paired:
        packet_path=output/'pair-packet-private.json'
        paired_packet=judge.paired_packet_for(qualities,source_packets,profile)
        write_new(packet_path,paired_packet)
        files[str(packet_path)]=file_hash(packet_path)
        cases={'C1':{**cases['C1'],'session_manifest':{'quality_targets':paired_packet['quality_targets_sha256'],'packet':digest(paired_packet)},'packet_path':str(packet_path),'quality_paths':{c:cases[c]['quality_path'] for c in ('C1','C2')}}}
    cfg={'version':program.BATCH,'source_root':str(SOURCE),'source':source_snapshot(),'files':files,'cases':cases,
         'campaign':output.name,'program_id':grant['program_id'],'program_sha256':grant['contract_sha256'],
         'profile':profile,'pricing_date':date.today().isoformat(),'kind':'judge','data_kind':'judge',
         'max_requests':4,'max_requests_per_case':2,'order':['C1','C2'],
         'actual_max_judge_calls':1 if paired else 2,'paired':paired,'same_provider_blinded_context':True,'business_approval':False}
    cfg['contract_sha256']=digest(cfg)
    write_new(output/'contract-private.json',cfg)
    SessionStore(budget).campaign(cfg['campaign'],cfg['contract_sha256'],cap_each=4,group_key='route5_supplement_v3')
    program.register_batch(budget,output/'contract-private.json')
    return {'status':'judge_batch_frozen','max_judge_calls':1 if paired else 2,'contract_sha256':cfg['contract_sha256'],
            'message_bytes':[read(Path(c['packet_path']))['prompt_utf8_bytes'] for c in cases.values()]}


def run(runtime,output,case):
    saved=output/(case+'-judge-result-private.json')
    if saved.exists():return {'status':'saved_judgment_no_new_request','case':case}
    budget=ledger(runtime);cfg=read(output/'contract-private.json')
    with budget.transaction() as db:program.load_batch(db,cfg['campaign'])
    connection_preflight()
    row=cfg['cases'][case];packet=read(Path(row['packet_path']))
    store=SessionStore(budget)
    deadline=store.open(row['session'],cfg['campaign'],row['session_manifest'],'deepseek',calls=1,seconds=600)
    wrapper={'profile_sha256':digest(cfg['profile']),'packets':[packet],'session':row['session']}
    phase=store.register_packet(row['session'],0,digest(wrapper),'deepseek')
    payload=program.bind_judge_packet(budget,cfg['campaign'],case,phase,packet)
    request=output/(case+'-request-private.json')
    if not request.exists():write_new(request,payload)
    elif read(request)!=payload:raise ExecutionStop('quality_judge_saved_request_changed')
    provider=judge.JudgeProvider('deepseek',profile=cfg['profile'],budget=budget,campaign=wrapper,campaign_sha=digest(wrapper),
        phase=phase,output=output/'receipts',runtime=runtime,live=True,remaining=lambda:deadline-time.time())
    try:result=provider.generate('0')
    except ValueError:
        program.close_batch(budget,cfg['campaign'],'judge_generation_unknown_or_failed')
        raise
    assessment=None;review_error=None;status='Pending'
    if result['code']=='ok':
        try:
            if cfg.get('paired'):
                assessment={item['case']:judge.assess(read(Path(row['quality_paths'][item['case']])),item['verdict'],identity=result) for item in result['action']['verdicts']}
                statuses=[v['assessment']['combined_status'] for v in assessment.values()]
                status='Fail' if 'Fail' in statuses else 'Pending' if 'Pending' in statuses else 'Pass'
            else:
                assessment=judge.assess(read(Path(row['quality_path'])),result['action'],identity=result)
                status=assessment['assessment']['combined_status']
        except (ValueError,KeyError,TypeError):
            review_error='judge_assessment_invalid_no_retry'
    value={'case':case,'result':result,'review':assessment,'status':status,'review_error':review_error,
           'no_automatic_retry':True,'independent_external_review':False,'personal_readability':'Pending'}
    write_new(saved,value)
    if all((output/(c+'-judge-result-private.json')).exists() for c in cfg['cases']):
        program.close_batch(budget,cfg['campaign'],'two_cases_completed')
    scores=({c:v['assessment']['scores'] for c,v in assessment.items()} if cfg.get('paired') else assessment['assessment']['scores']) if assessment else None
    return {'status':status,'case':case,'format':result['code'],'scores':scores,'review_error':review_error}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['freeze','run'])
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--program',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source-batch',type=Path)
    parser.add_argument('--case',choices=['C1','C2'])
    parser.add_argument('--paired',action='store_true',help='One blinded request, two separately scored reports; shared program budget')
    args=parser.parse_args()
    runtime,directory,output=args.runtime.resolve(),args.program.resolve(),args.output.resolve()
    if not directory.is_relative_to(runtime/'reports/generated/agent-investigation-upgrade/route5/report-v3') or not output.is_relative_to(directory):
        parser.exit(2,'quality_judge_output_outside_program\n')
    try:
        result=freeze(runtime,directory,args.source_batch.resolve(),output,paired=args.paired) if args.action=='freeze' else run(runtime,output,args.case)
        print(canonical(result))
    except (ValueError,OSError,KeyError,TypeError) as error:
        parser.exit(2,(str(error) if isinstance(error,ExecutionStop) else 'quality_judge_input_or_review_invalid_no_retry')+'\n')


if __name__=='__main__':main()
