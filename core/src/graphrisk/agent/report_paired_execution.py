"""One new six-case paired scope, using the existing IEEE allocation unchanged."""
from datetime import date
import json
from pathlib import Path
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import digest,file_hash,canonical
from graphrisk.agent import report_quality_program as parent,report_ieee_validation as ieee

VERSION='report-paired-six-execution-v1'
CONTINUATION='report-paired-six-saved-result-continuation-v1'
GUIDANCE='report-paired-six-v12-guidance-v1'
REVIEW='report-single-v13.1-human-review-v1'


def require(ok,code):
    if not ok:raise ExecutionStop('paired_'+code)


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def is_batch(db,campaign):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='report_paired_batches'").fetchone()
                and db.execute('SELECT 1 FROM report_paired_batches WHERE campaign_id=?',(campaign,)).fetchone())


def validate_files(cfg):
    require(parent.sealed(cfg) and cfg.get('version') in (VERSION,CONTINUATION,GUIDANCE,REVIEW),'contract_changed')
    review=cfg['version']==REVIEW
    limit=1 if review else 12
    inherited=cfg.get('inherited_results',{})
    require(isinstance(inherited,dict) and (0<len(inherited)<12 if cfg['version']==CONTINUATION else not inherited),'inherited_scope_invalid')
    require(type(cfg.get('max_new_requests')) is int and cfg.get('max_new_requests')==limit-len(inherited) and cfg.get('new_allocation')==0
            and cfg.get('repairs')==0 and cfg.get('automatic_retries')==0 and cfg.get('judge_calls')==0
            and cfg.get('overall_max_cny')==100 and cfg.get('pause_cny')==80,'bounds_invalid')
    if review:require(cfg.get('review_only') is True and cfg.get('new_quality_target_claim') is False,'review_scope_invalid')
    require(cfg['pricing_date']==date.today().isoformat()
            and cfg['profile']['deepseek']['pricing_verified_date']==cfg['pricing_date'],'pricing_refresh_required')
    root=Path(cfg['source_root'])
    actual={p.relative_to(root).as_posix():file_hash(p) for folder in ('src','api','scripts') for p in (root/folder).rglob('*.py')}
    require(actual==cfg['source']['python_files'],'source_changed')
    for name,sha in cfg['source']['configs'].items():require(file_hash(root/'configs'/name)==sha,'config_changed')
    for name,sha in cfg['files'].items():require(file_hash(Path(name))==sha,'frozen_file_changed')
    require(len(cfg['cases'])==limit and len(cfg['order'])==limit and set(cfg['order'])==set(cfg['cases']),'case_order_invalid')
    require(set(inherited)==set(cfg['order'][:len(inherited)]),'inherited_prefix_invalid')
    excluded=set(read(cfg['exclusions_path'])['ids'])
    require(cfg['exclusions_path'] in cfg['files'],'exclusions_not_frozen')
    pairs={}
    versions={'v13_1'} if review else {'v12','v12_guidance'} if cfg['version']==GUIDANCE else {'v12','v13'}
    for key,item in cfg['cases'].items():
        require(item['version'] in versions,'version_invalid')
        require(item['raw_path'] in cfg['files'] and item['packet_path'] in cfg['files'],'case_files_not_frozen')
        raw=read(item['raw_path']);packet=read(item['packet_path'])
        require(raw['scope']['dataset_source']=='ieee_cis' and raw['scope']['transaction_id'] not in excluded,'case_outside_scope')
        require(digest(raw['evidence'])==item['evidence_sha256']==packet['source_evidence_sha256']
                and packet['scope_private']==raw['scope']
                and digest(packet)==item['packet_sha256'],'evidence_or_packet_changed')
        require(packet.get('logical_key')=='0','logical_key_invalid')
        from graphrisk.agent import judgment_v3,judgment_v13
        cls=judgment_v13.Provider if item['version']=='v13' else judgment_v3.JudgmentProvider
        if review:
            from graphrisk.agent import judgment_v13_1
            cls=judgment_v13_1.Provider
            require(packet.get('judgment_rules_version')==judgment_v13_1.RULES
                    and packet.get('quality_factors')=={'numeric_display':True,'check_options':True},'review_variant_changed')
        if item['version']=='v12_guidance':
            from graphrisk.agent.judgment_v12_guidance import GENERATION
            require(packet.get('generation_variant')==GENERATION
                    and packet['view'].get('generation_guidance',{}).get('version')==GENERATION,'guidance_variant_changed')
        formatter=object.__new__(cls);formatter.provider,formatter.profile='deepseek',cfg['profile']
        require(digest(formatter.payload(packet))==item['payload_sha256'],'payload_changed')
        pairs.setdefault(item['alias'],[]).append((item['version'],item['split'],item['evidence_sha256'],raw['scope']['transaction_id']))
    if review:
        require(len(pairs)==1 and all(len(p)==1 and p[0][1] in ('validation','test') for p in pairs.values()),'single_review_case_required')
        return
    require(len(pairs)==6 and len({p[0][3] for p in pairs.values()})==6,'six_distinct_cases_required')
    require(all(len(p)==2 and {r[0] for r in p}==versions and p[0][1:]==p[1][1:] for p in pairs.values()),'pair_mismatch')
    require(sum(p[0][1]=='validation' for p in pairs.values())==3
            and sum(p[0][1]=='test' for p in pairs.values())==3,'split_mismatch')


def load(db,campaign,*,allow_closed=False):
    row=db.execute('SELECT b.contract_path,b.file_sha,b.closed_reason,p.approval FROM report_quality_batches b '
        'JOIN report_paired_batches p USING(campaign_id) WHERE campaign_id=?',(campaign,)).fetchone()
    require(row is not None,'not_registered')
    require(file_hash(Path(row[0]))==row[1],'contract_binding_changed')
    cfg=read(row[0]);validate_files(cfg)
    approval=json.loads(row[3])
    require(approval.get('contract_sha256')==cfg['contract_sha256'] and bool(approval.get('authorization','').strip()),'authorization_changed')
    original=parent.check_program(db,cfg['program_path'],cfg['program_file_sha256'])
    allocation=ieee.registered(db,original)
    require(allocation is not None and allocation['contract_sha256']==cfg['existing_grant_sha256'],'existing_allocation_changed')
    require(db.execute('SELECT manifest_sha,cap_each,group_key FROM autonomous_campaigns WHERE campaign_id=?',(campaign,)).fetchone()
            ==(cfg['contract_sha256'],1 if cfg['version']==REVIEW else 12,ieee.GROUP),'campaign_changed')
    used=db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?',(campaign,)).fetchone()[0]
    require(ieee.group_used(db)==cfg['group_used_at_authorization']+used,'group_counter_changed')
    require(parent.used_requests(db,original['program_id'])==50+ieee.group_used(db),'program_counter_changed')
    require(type(cfg['group_used_at_authorization']) is int
            and 0<=cfg['group_used_at_authorization']<=50-cfg['max_new_requests'],'allocation_insufficient')
    inherited=cfg.get('inherited_results',{})
    if inherited:
        prior=db.execute('SELECT contract_path,file_sha,closed_reason FROM report_quality_batches WHERE campaign_id=?',(cfg['prior_campaign'],)).fetchone()
        require(prior is not None and prior[2]=='paired_console_summary_type_error_after_saved_result'
                and file_hash(Path(prior[0]))==prior[1],'inherited_prior_not_closed')
        old=read(prior[0])
        require(old['version']==VERSION and cfg['order']==old['order'] and cfg['profile']==old['profile'],'inherited_original_scope_changed')
        old_used=db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?',(cfg['prior_campaign'],)).fetchone()[0]
        require(old_used==len(inherited),'inherited_attempt_count_changed')
        for key,item in inherited.items():
            require(item['path'] in cfg['files'] and file_hash(Path(item['path']))==item['file_sha'],'inherited_result_changed')
            saved=read(item['path'])
            bound=db.execute('SELECT path,file_sha FROM report_paired_results WHERE campaign_id=? AND case_key=?',(cfg['prior_campaign'],key)).fetchone()
            receipt=db.execute('SELECT a.state,r.packet_sha,r.result_code,r.receipt_sha,r.receipt_path FROM model_attempts a JOIN execution_requests r USING(attempt_id) WHERE attempt_id=?',(saved['result']['attempt_id'],)).fetchone()
            require(bound==(item['path'],item['file_sha']) and receipt is not None
                    and receipt[0]=='settled' and receipt[1]==cfg['cases'][key]['payload_sha256']
                    and receipt[2] in ('ok','invalid_model_action') and receipt[3] and receipt[4]
                    and file_hash(Path(receipt[4]))==receipt[3]
                    and saved['case_key']==key and old['cases'][key]['payload_sha256']==receipt[1],'inherited_settled_result_required')
    require(not row[2] or allow_closed,'batch_closed')
    return cfg


def register(budget,path,authorization):
    require(isinstance(authorization,str) and bool(authorization.strip()),'authorization_required')
    cfg=read(path);validate_files(cfg)
    with budget.transaction() as db:
        budget._check_registration(db);budget._check_request_links(db)
        parent.check_program(db,cfg['program_path'],cfg['program_file_sha256'])
        db.execute('CREATE TABLE IF NOT EXISTS report_paired_batches(campaign_id TEXT PRIMARY KEY,approval TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS report_paired_results(campaign_id TEXT NOT NULL,case_key TEXT NOT NULL,path TEXT NOT NULL,file_sha TEXT NOT NULL,PRIMARY KEY(campaign_id,case_key))')
        approval=canonical({'contract_sha256':cfg['contract_sha256'],'authorization':authorization})
        wanted=(cfg['campaign'],cfg['program_id'],str(Path(path).resolve()),file_hash(path))
        old=db.execute('SELECT campaign_id,program_id,contract_path,file_sha FROM report_quality_batches WHERE campaign_id=?',(cfg['campaign'],)).fetchone()
        if old:
            require(old==wanted and db.execute('SELECT approval FROM report_paired_batches WHERE campaign_id=?',(cfg['campaign'],)).fetchone()==(approval,),'not_resettable')
        else:
            require(ieee.group_used(db)==cfg['group_used_at_authorization'],'stale_counter')
            db.execute('INSERT INTO report_quality_batches VALUES(?,?,?,?,NULL)',wanted)
            db.execute('INSERT INTO report_paired_batches VALUES(?,?)',(cfg['campaign'],approval))
        load(db,cfg['campaign'])
    return {'contract_sha256':cfg['contract_sha256'],'authorization':authorization,'new_allocation':0,'max_requests':cfg['max_new_requests']}


def guard(db,*,phase,provider,request_key,packet_sha):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='report_paired_batches'").fetchone():return False
    row=db.execute('SELECT s.campaign_id,s.session_id,s.provider,s.max_calls,p.slot,s.manifest_sha FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) WHERE phase=?',(phase,)).fetchone()
    if not row or not is_batch(db,row[0]):return False
    cfg=load(db,row[0])
    used=db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?',(row[0],)).fetchone()[0]
    inherited=cfg.get('inherited_results',{})
    require(used<cfg['max_new_requests'] and ieee.group_used(db)<50,'request_cap')
    key=cfg['order'][used+len(inherited)];item=cfg['cases'][key]
    require(provider=='deepseek' and row[1:5]==(item['session'],'deepseek',1,0)
            and row[5]==digest(item['session_manifest']),'single_request_and_order_required')
    for prior in cfg['order'][len(inherited):len(inherited)+used]:
        result=db.execute('SELECT path,file_sha FROM report_paired_results WHERE campaign_id=? AND case_key=?',(row[0],prior)).fetchone()
        require(result is not None and file_hash(Path(result[0]))==result[1],'previous_result_required')
    packet=read(item['packet_path'])
    wrapper={'profile_sha256':digest(cfg['profile']),'packets':[packet],'session':item['session']}
    require(request_key==digest([digest(wrapper),'deepseek','0']) and packet_sha==item['payload_sha256']
            and db.execute('SELECT manifest_sha FROM execution_phases WHERE phase=?',(phase,)).fetchone()==(digest(wrapper),),'exact_payload_required')
    return True


def record_result(budget,campaign,key,path):
    result=read(path)
    with budget.transaction() as db:
        cfg=load(db,campaign)
        require(key in cfg['cases'],'unknown_case')
        row=db.execute('SELECT r.packet_sha,a.state,r.receipt_sha,r.attempt_id FROM execution_requests r JOIN model_attempts a USING(attempt_id) '
            'JOIN autonomous_phases USING(phase) JOIN autonomous_sessions USING(session_id) WHERE session_id=?',(cfg['cases'][key]['session'],)).fetchone()
        require(row is not None and row[0]==cfg['cases'][key]['payload_sha256'] and row[1]=='settled' and row[2]
                and result['result']['attempt_id']==row[3] and result['case_key']==key,'settled_result_required')
        wanted=(campaign,key,str(Path(path).resolve()),file_hash(path))
        old=db.execute('SELECT * FROM report_paired_results WHERE campaign_id=? AND case_key=?',(campaign,key)).fetchone()
        require(old is None or old==wanted,'result_not_resettable')
        if old is None:db.execute('INSERT INTO report_paired_results VALUES(?,?,?,?)',wanted)


def close(budget,campaign,reason):
    with budget.transaction() as db:
        require(is_batch(db,campaign),'not_registered')
        db.execute('UPDATE report_quality_batches SET closed_reason=COALESCE(closed_reason,?) WHERE campaign_id=?',(reason,campaign))
