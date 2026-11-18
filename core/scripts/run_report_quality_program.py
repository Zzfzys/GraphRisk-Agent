"""Finite autonomous improvement, under a fresh preserved-unknown program grant."""
import argparse
from datetime import date
from pathlib import Path

from freeze_report_pilot import SOURCE, inputs, source_snapshot, read, write_new
from run_bounded_investigation import ledger
from run_report_pilot import freeze, run_case, review_case
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent import report_quality_program as program
from graphrisk.agent.report_pilot import readonly_budget
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
from graphrisk.agent.autonomous_budget import ExecutionStop


def prepare(runtime, directory, pricing_path, authorization):
    if directory.exists():
        raise ExecutionStop('quality_program_new_directory_required')
    budget = ledger(runtime)
    profile,cases,_,_ = inputs(runtime,read(pricing_path),date.today().isoformat())
    before = readonly_budget(budget.path)
    parent = runtime/'reports/generated/agent-investigation-upgrade/route5/report-v3/20261024-natural-pilot-live-v1'
    if read(parent/'STOP.json')['reason']!='model_request_unknown_saved_do_not_retry':
        raise ExecutionStop('quality_program_parent_stop_changed')
    protected = {str(p):file_hash(p) for p in parent.rglob('*') if p.is_file()}
    protected[str(pricing_path.resolve())]=file_hash(pricing_path)
    with budget.transaction() as db:
        program.setup(db)
        baseline = program.old_rows(db,directory.name)
    cfg = {'version':program.VERSION,'program_id':directory.name,'parent_campaign':parent.name,
           'unknowns':before['unknown'],'baseline':baseline,'protected_files':protected,'max_requests':30,
           'budget_before':before,'real_evidence_allowed':[c['packet']['source_evidence_sha256'] for c in cases],
           'scope':'two_previously_approved_real_summaries_and_public_synthetic_boundary_tasks',
           'stop_on_new_unknown_identity_integrity_or_budget':True,'no_automatic_retry':True,
           'development_and_unseen_task_validation_separate':True,'personal_readability':'Pending'}
    cfg['contract_sha256']=digest(cfg)
    directory.mkdir(parents=True)
    path=directory/'program-private.json'
    write_new(path,cfg)
    approval=program.register_program(budget,path,authorization)
    write_new(directory/'authorization-bound.json',approval)
    return {'status':'new_program_authorized_old_stops_preserved','program_sha256':cfg['contract_sha256'],
            'max_requests':30,'old_unknowns':len(before['unknown']),'new_model_calls':0}


def batch(runtime, directory, output, pricing_path):
    path=directory/'program-private.json'
    result=freeze(runtime,output,pricing_path,program_path=path)
    cfg=read(output/'contract-private.json')
    budget=ledger(runtime)
    SessionStore(budget).campaign(cfg['campaign'],cfg['contract_sha256'],cap_each=4,group_key='route5_supplement_v3')
    program.register_batch(budget,output/'contract-private.json')
    return {**result,'status':'batch_frozen_and_bound_to_program','program_id':cfg['program_id']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','batch','run-case','review'])
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--program',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--pricing-evidence',type=Path)
    parser.add_argument('--authorization-file',type=Path)
    parser.add_argument('--case',choices=['C1','C2'])
    parser.add_argument('--review',type=Path)
    args=parser.parse_args()
    runtime,directory=args.runtime.resolve(),args.program.resolve()
    allowed=runtime/'reports/generated/agent-investigation-upgrade/route5/report-v3'
    if directory==allowed or not directory.is_relative_to(allowed) or (args.output and not args.output.resolve().is_relative_to(directory)):
        parser.exit(2,'quality_program_output_outside_root\n')
    try:
        if args.action=='prepare':
            result=prepare(runtime,directory,args.pricing_evidence,read(args.authorization_file)['authorization'])
        elif args.action=='batch': result=batch(runtime,directory,args.output.resolve(),args.pricing_evidence)
        elif args.action=='run-case': result=run_case(runtime,args.output.resolve(),args.case)
        else: result=review_case(runtime,args.output.resolve(),args.case,args.review)
        print(canonical(result))
    except (ValueError,OSError,KeyError,TypeError) as error:
        parser.exit(2,(str(error) if isinstance(error,ExecutionStop) else 'quality_program_input_failed_no_retry')+'\n')


if __name__=='__main__':main()
