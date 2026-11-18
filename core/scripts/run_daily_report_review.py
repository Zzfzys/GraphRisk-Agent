"""Validate a frozen six-report batch; sending requires explicit live opt-in."""
from argparse import ArgumentParser
from datetime import date
import json
import os
from pathlib import Path
import sqlite3

from graphrisk.agent import rank_successor as successor
from graphrisk.agent.autonomous_budget import InvestigationBudget
from graphrisk.agent.autonomous_evidence import EvidenceBundle
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.judgment_v3 import JudgmentDraft,JudgmentProvider,verify_judgment
from graphrisk.agent.upgrade_contract import canonical,digest,load_contract


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value):path.write_text(canonical(value),encoding='utf-8')


def register_once(budget,config_path,cfg,approval_note):
    from graphrisk.agent.upgrade_contract import file_hash
    with budget.transaction() as db:
        registered=db.execute('SELECT contract_path,contract_sha,authorization,closed FROM rank_successors '
            'WHERE campaign_id=?',(cfg['campaign'],)).fetchone() if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='rank_successors'").fetchone() else None
    if registered is None:
        successor.register(budget,config_path,approval_note)
    elif (Path(registered[0]).resolve()!=config_path.resolve() or
          registered[1]!=file_hash(config_path) or registered[2]!=approval_note):
        raise ValueError('registered approval or contract binding changed')
    return registered


def check(config_path):
    """Read-only validation must not register a campaign, phase or attempt."""
    cfg=successor.read_contract(config_path)
    if cfg['version'] not in successor.DAILY_VERSIONS:
        raise ValueError('daily report contract required')
    path=Path(cfg['runtime'])/'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        successor.check_history(db,cfg)
        successor.check_parent(db,cfg)
        used=(db.execute('SELECT count(*) FROM model_attempts a JOIN execution_requests r USING(attempt_id) '
            'JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) '
            'WHERE s.campaign_id=?',(cfg['campaign'],)).fetchone()[0] if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='autonomous_sessions'").fetchone() else 0)
        successor.check_allocation(db,cfg,used)
        counts=(db.execute("SELECT count(*) FROM model_attempts WHERE provider='deepseek'").fetchone()[0],
                db.execute('SELECT count(*) FROM model_attempts').fetchone()[0])
    return cfg,counts


def report_implementation(cfg):
    if cfg['version']==successor.DAILY_QUOTES_VERSION:
        from graphrisk.agent.judgment_v12_daily_quotes import Provider,Draft,verify
        return Provider,Draft,verify
    if cfg['version']==successor.DAILY_CANDIDATE_VERSION:
        from graphrisk.agent.judgment_v12_daily_review import Provider,Draft,verify
        return Provider,Draft,verify
    if cfg['version']==successor.DAILY_VERSION:
        return JudgmentProvider,JudgmentDraft,verify_judgment
    raise ValueError('unknown daily report execution version')


def run(config_path,*,live=False,approval_note=None):
    cfg,counts=check(config_path)
    if not live:
        return {'status':'validated_not_sent','payloads':len(cfg['payloads']),
            'pricing_required':cfg['pricing_required'],'paid_calls':0,
            'current_deepseek_attempts':counts[0],'current_total_attempts':counts[1]}
    if os.getenv('GRAPHRISK_UPGRADE_LIVE')!='1' or not approval_note or len(approval_note)<10:
        raise ValueError('explicit live flag, environment and specific outbound approval note required')
    if cfg['pricing_required']:
        raise ValueError('official pricing verification required before sending')
    Provider,Draft,verify=report_implementation(cfg)
    profile=read(cfg['profile_path'])
    if profile['deepseek']['pricing_verified_date']!=date.today().isoformat():
        raise ValueError('refresh official model and pricing verification for execution date')
    runtime=Path(cfg['runtime'])
    source=Path(cfg['source_root'])
    budget=InvestigationBudget(runtime/'reports/generated/agent-investigation-upgrade/live-budget.sqlite3',
        load_contract(source/'configs/agent_investigation_experiment.json'),mode='live')
    sessions=SessionStore(budget)
    from graphrisk.agent.upgrade_contract import file_hash
    sessions.campaign(cfg['campaign'],file_hash(config_path),cap_each=6,group_key=successor.bounds(cfg)[2])
    registered=register_once(budget,config_path,cfg,approval_note)
    output=Path(cfg['output'])
    output.mkdir(parents=True,exist_ok=True)
    manifest=read(cfg['review_manifest_path'])
    results=[]
    for position,item in enumerate(manifest['selected_cases']):
        name=cfg['case_keys'][position]
        saved=output/(name+'-result-private.json')
        with budget.transaction() as db:
            prior=successor.verify_daily_saved_result(db,cfg,cfg['campaign'],position)
        if prior:
            if Path(prior[0])!=saved or file_hash(saved)!=prior[1]:
                raise ValueError('saved result binding changed')
            results.append(read(saved))
            continue
        if registered is not None and registered[3]:
            raise ValueError('closed batch is missing a saved result; do not resend')
        parent=Path(item['payload_path']).parent
        packet=read(parent/(name+'-packet-private.json'))
        raw=read(parent/(name+'-source-private.json'))
        bundle=EvidenceBundle.model_validate_json(canonical(raw['evidence']),strict=True)
        session=cfg['campaign']+'-C'+str(position+1)
        deadline=sessions.open(session,cfg['campaign'],{
            'campaign':cfg['campaign'],'position':position,'payload_sha':cfg['payloads'][position]},
            'deepseek',calls=1,seconds=600)
        wrapper={'profile_sha256':digest(profile),'packets':[packet],'session':session}
        phase=sessions.register_packet(session,0,digest(wrapper),'deepseek')
        import time
        provider=Provider('deepseek',profile=profile,budget=budget,campaign=wrapper,
            campaign_sha=digest(wrapper),phase=phase,output=output/'receipts',runtime=runtime,
            live=True,remaining=lambda:deadline-time.time())
        response=provider.generate('0')
        issues,report=[response['code']],None
        if response['code']=='ok':
            draft=Draft.model_validate_json(canonical(response['action']),strict=True)
            issues,report=verify(draft,packet,bundle)
        # Candidate reports rebuild byte-for-byte; identity stays in provider_result.
        if report and cfg['version']==successor.DAILY_VERSION:
            report['model_identity']={key:response[key] for key in ('provider','model','fingerprint')}
            report.pop('report_sha256')
            report['report_sha256']=digest(report)
        value={'case':name,'provider_result':response,'report':report,'native_guard_issues':issues,
            'first_draft':True,'repairs':0,'automatic_retries':0,'paid_judge_calls':0,
            'human_quality_review':'Pending'}
        write(saved,value)
        successor.record(budget,cfg['campaign'],position,response['attempt_id'],saved)
        results.append(value)
        if response['code'] in ('backend_identity_or_context_violation','local_running_resource_limit'):
            raise ValueError('provider integrity or resource stop; do not resend')
    if registered is None or not registered[3]:
        successor.close(budget,cfg['campaign'])
    return {'status':'first_drafts_saved','cases':len(results),'human_quality_review':'Pending'}


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--approval-note')
    args=parser.parse_args()
    print(canonical(run(args.config,live=args.live,approval_note=args.approval_note)))


if __name__=='__main__':main()
