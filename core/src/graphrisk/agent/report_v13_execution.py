"""A versioned scope addendum spending the existing group's remainder, not +50."""
import json
from pathlib import Path
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash
from graphrisk.agent import report_quality_program as parent,report_ieee_validation as ieee


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS report_v13_trials(trial_id TEXT PRIMARY KEY,path TEXT NOT NULL,file_sha TEXT NOT NULL,approval TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS report_v13_results(trial_id TEXT NOT NULL,campaign_id TEXT NOT NULL,session TEXT PRIMARY KEY,kind TEXT NOT NULL,result_file_sha TEXT NOT NULL,payload_sha TEXT NOT NULL,candidate_sha TEXT,path TEXT NOT NULL)')


def grant(db,trial):
    row=db.execute('SELECT path,file_sha,approval FROM report_v13_trials WHERE trial_id=?',(trial,)).fetchone()
    if not row or file_hash(Path(row[0]))!=row[1]:raise ExecutionStop('v13_trial_binding_changed')
    cfg=read(row[0]);approval=json.loads(row[2])
    if (not parent.sealed(cfg) or cfg.get('version')!='v13-existing-budget-scope-v1'
            or cfg.get('group')!=ieee.GROUP or type(cfg.get('max_new_requests')) is not int or not 1<=cfg['max_new_requests']<=14
            or cfg.get('minimum_retained_requests')!=21 or cfg.get('overall_max_cny')!=100
            or cfg.get('pause_cny')!=80 or approval.get('contract_sha256')!=cfg['contract_sha256']
            or not approval.get('authorization','').strip()
            or type(cfg.get('group_used_at_authorization')) is not int
            or cfg['group_used_at_authorization']<0
            or cfg['group_used_at_authorization']+cfg['max_new_requests']>22
            or 50-cfg['group_used_at_authorization']-cfg['max_new_requests']<21):raise ExecutionStop('v13_trial_scope_invalid')
    original=parent.check_program(db,cfg['program_path'],cfg['program_file_sha256'])
    allocation=ieee.registered(db,original)
    if not allocation or allocation['contract_sha256']!=cfg['existing_grant_sha256']:
        raise ExecutionStop('v13_original_allocation_changed')
    for name,sha in cfg['protected_files'].items():
        if file_hash(Path(name))!=sha:raise ExecutionStop('v13_frozen_input_changed')
    return cfg


def register_trial(budget,path,authorization):
    cfg=read(path)
    if not isinstance(authorization,str) or not authorization.strip():raise ExecutionStop('v13_human_authorization_required')
    with budget.transaction() as db:
        budget._check_registration(db);budget._check_request_links(db);setup(db)
        wanted=(cfg['trial_id'],str(Path(path).resolve()),file_hash(path),canonical({'contract_sha256':cfg['contract_sha256'],'authorization':authorization}))
        old=db.execute('SELECT * FROM report_v13_trials WHERE trial_id=?',(cfg['trial_id'],)).fetchone()
        if old and old!=wanted:raise ExecutionStop('v13_trial_not_resettable')
        if not old:
            if ieee.group_used(db)!=cfg['group_used_at_authorization']:raise ExecutionStop('v13_stale_counter')
            db.execute('INSERT INTO report_v13_trials VALUES(?,?,?,?)',wanted)
        grant(db,cfg['trial_id'])
    return {'contract_sha256':cfg['contract_sha256'],'authorization':authorization,'additional_allocation':0,'max_spend_requests':cfg['max_new_requests']}


def is_batch(db,campaign):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='report_v13_batch_bindings'").fetchone():return False
    return bool(db.execute('SELECT 1 FROM report_v13_batch_bindings WHERE campaign_id=?',(campaign,)).fetchone())


def validate_files(cfg):
    if not parent.sealed(cfg) or cfg.get('version')!='report-v13-batch-v1':raise ExecutionStop('v13_batch_contract_changed')
    from datetime import date
    if cfg['pricing_date']!=date.today().isoformat():raise ExecutionStop('v13_pricing_refresh_required')
    root=Path(cfg['source_root']);expected=cfg['source']
    actual={p.relative_to(root).as_posix():file_hash(p) for folder in ('src','api','scripts') for p in (root/folder).rglob('*.py')}
    if actual!=expected['python_files']:raise ExecutionStop('v13_source_changed')
    for name,sha in expected['configs'].items():
        if file_hash(root/'configs'/name)!=sha:raise ExecutionStop('v13_config_changed')
    for name,sha in cfg['files'].items():
        if file_hash(Path(name))!=sha:raise ExecutionStop('v13_batch_input_changed')


def load_batch(db,campaign,*,allow_closed=False):
    row=db.execute('SELECT b.contract_path,b.file_sha,b.closed_reason,v.trial_id FROM report_quality_batches b '
        'JOIN report_v13_batch_bindings v USING(campaign_id) WHERE campaign_id=?',(campaign,)).fetchone()
    if not row or file_hash(Path(row[0]))!=row[1]:raise ExecutionStop('v13_batch_binding_changed')
    cfg=read(row[0]);validate_files(cfg);trial=grant(db,row[3])
    if (cfg['trial_sha256']!=trial['contract_sha256'] or cfg['program_id']!=trial['program_id']
            or cfg['kind'] not in ('calibration','report','judge')):raise ExecutionStop('v13_batch_scope_changed')
    if db.execute('SELECT manifest_sha,cap_each,group_key FROM autonomous_campaigns WHERE campaign_id=?',(campaign,)).fetchone()!=(cfg['contract_sha256'],3,ieee.GROUP):
        raise ExecutionStop('v13_campaign_changed')
    allowed=trial['packet_hashes'].get(cfg['kind'],[])
    for item in cfg['cases'].values():
        packet=read(item['packet_path'])
        if digest(packet)!=item['packet_sha256']:raise ExecutionStop('v13_packet_changed')
        if cfg['kind']!='judge' and digest(packet) not in allowed:raise ExecutionStop('v13_unapproved_packet')
        if cfg['kind']=='judge':
            # A judge must bind actual, settled first drafts from this same trial.
            for target in item['actual_targets']:
                bound=db.execute("SELECT p.payload_sha FROM report_v13_results p WHERE p.trial_id=? AND p.result_file_sha=? AND kind='report'",(trial['trial_id'],target)).fetchone()
                if not bound:raise ExecutionStop('v13_judge_requires_own_saved_first_draft')
            source_hashes=[db.execute('SELECT candidate_sha FROM report_v13_results WHERE trial_id=? AND result_file_sha=?',
                (trial['trial_id'],target)).fetchone()[0] for target in item['actual_targets']]
            if source_hashes!=[digest(r['candidate']) for r in packet['view']['reports']]:raise ExecutionStop('v13_judge_raw_draft_changed')
            if digest([r['candidate'] for r in packet['view']['reports']])!=item['candidate_list_sha256']:
                raise ExecutionStop('v13_judge_candidate_changed')
    if row[2] and not allow_closed:raise ExecutionStop('v13_batch_closed')
    return cfg


def register_batch(budget,path):
    cfg=read(path)
    with budget.transaction() as db:
        setup(db)
        db.execute('CREATE TABLE IF NOT EXISTS report_v13_batch_bindings(campaign_id TEXT PRIMARY KEY,trial_id TEXT NOT NULL)')
        wanted=(cfg['campaign'],cfg['program_id'],str(Path(path).resolve()),file_hash(path))
        old=db.execute('SELECT campaign_id,program_id,contract_path,file_sha FROM report_quality_batches WHERE campaign_id=?',(cfg['campaign'],)).fetchone()
        if old and old!=wanted:raise ExecutionStop('v13_batch_not_resettable')
        if not old:
            db.execute('INSERT INTO report_quality_batches VALUES(?,?,?,?,NULL)',wanted)
            db.execute('INSERT INTO report_v13_batch_bindings VALUES(?,?)',(cfg['campaign'],cfg['trial_id']))
        load_batch(db,cfg['campaign'])


def guard(db,*,phase,provider,request_key,packet_sha):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='report_v13_batch_bindings'").fetchone():return False
    row=db.execute('SELECT s.campaign_id,s.session_id,s.provider,s.max_calls,p.slot,s.manifest_sha '
        'FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) WHERE phase=?',(phase,)).fetchone()
    if not row or not is_batch(db,row[0]):return False
    cfg=load_batch(db,row[0]);trial=grant(db,cfg['trial_id'])
    cases=[v for v in cfg['cases'].values() if v['session']==row[1]]
    if provider!='deepseek' or row[2:5]!=('deepseek',1,0) or len(cases)!=1:
        raise ExecutionStop('v13_single_request_only')
    case=cases[0];packet=read(case['packet_path'])
    if row[5]!=digest(case['session_manifest']):raise ExecutionStop('v13_session_manifest_changed')
    wrapper={'profile_sha256':digest(cfg['profile']),'packets':[packet],'session':row[1]}
    if (request_key!=digest([digest(wrapper),'deepseek','0']) or packet_sha!=case['payload_sha256']
            or db.execute('SELECT manifest_sha FROM execution_phases WHERE phase=?',(phase,)).fetchone()!=(digest(wrapper),)):
        raise ExecutionStop('v13_exact_payload_required')
    count=db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) JOIN autonomous_sessions USING(session_id) '
        'JOIN report_v13_batch_bindings USING(campaign_id) WHERE trial_id=?',(cfg['trial_id'],)).fetchone()[0]
    if count>=trial['max_new_requests'] or ieee.group_used(db)-trial['group_used_at_authorization']!=count:
        raise ExecutionStop('v13_trial_cap_or_counter_changed')
    if ieee.group_used(db)>=22:raise ExecutionStop('v13_preserve_twenty_eight_requests')
    calibration_source=trial.get('calibration_source_trial',cfg['trial_id'])
    if cfg['kind']=='report' and db.execute("SELECT count(*) FROM report_v13_results WHERE trial_id=? AND kind='calibration'",(calibration_source,)).fetchone()[0]!=5:
        raise ExecutionStop('v13_calibration_must_be_recorded_first')
    return True


def record_result(budget,campaign,case,path):
    value=read(path)
    with budget.transaction() as db:
        cfg=load_batch(db,campaign);item=cfg['cases'][case]
        result=value['result'];row=db.execute('SELECT a.state,r.packet_sha,r.receipt_path,r.receipt_sha,s.session_id '
            'FROM execution_requests r JOIN model_attempts a USING(attempt_id) JOIN autonomous_phases USING(phase) '
            'JOIN autonomous_sessions s USING(session_id) WHERE a.attempt_id=?',(result['attempt_id'],)).fetchone()
        if (not row or row[0]!='settled' or row[1]!=item['payload_sha256'] or row[4]!=item['session']
                or file_hash(Path(row[2]))!=row[3]):raise ExecutionStop('v13_result_requires_settled_receipt')
        wanted=(cfg['trial_id'],campaign,item['session'],cfg['kind'],file_hash(path),row[1],
                digest(result['action']) if result.get('action') is not None else None,str(Path(path).resolve()))
        old=db.execute('SELECT * FROM report_v13_results WHERE session=?',(item['session'],)).fetchone()
        if old and old!=wanted:raise ExecutionStop('v13_result_not_resettable')
        if not old:db.execute('INSERT INTO report_v13_results VALUES(?,?,?,?,?,?,?,?)',wanted)
