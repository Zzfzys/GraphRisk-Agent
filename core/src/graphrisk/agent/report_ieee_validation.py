"""A separate, additive 50-request IEEE v12 grant; no legacy allocation reset."""
import json
from pathlib import Path

from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash

GROUP='route5_ieee_v12_20261025'


def values(cfg, original, authorization):
    from graphrisk.agent.report_quality_program import sealed
    if (not sealed(cfg) or cfg.get('version')!='report-ieee-v12-additive-grant-v1'
            or cfg.get('program_id')!=original['program_id']
            or cfg.get('program_sha256')!=original['contract_sha256']
            or cfg.get('prior_cap')!=50 or cfg.get('authorized_at_requests')!=50
            or cfg.get('additional_requests')!=50 or cfg.get('cumulative_cap')!=100
            or cfg.get('group')!=GROUP or cfg.get('overall_max_cny')!=100
            or cfg.get('pause_cny')!=80 or cfg.get('no_reset_no_reopen_no_unknown_retry') is not True
            or cfg.get('scope')!='frozen_ieee_v12_first_drafts_and_bound_judges'
            or len(cfg.get('real_evidence_allowed',[]))!=6
            or len(set(cfg['real_evidence_allowed']))!=6
            or not isinstance(authorization,str) or not authorization.strip()):
        raise ExecutionStop('ieee_v12_grant_binding_invalid')
    if not cfg.get('generation_files') or not cfg.get('protected_files'):
        raise ExecutionStop('ieee_v12_frozen_files_required')
    for name,sha in {**cfg['generation_files'],**cfg['protected_files']}.items():
        if file_hash(Path(name))!=sha:raise ExecutionStop('ieee_v12_frozen_file_changed')
    return {'contract_sha256':cfg['contract_sha256'],'authorization':authorization,
            'program_sha256':original['contract_sha256'],'additional_requests':50,'cumulative_cap':100}


def registered(db, original):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='report_ieee_v12_grants'").fetchone():return None
    row=db.execute('SELECT path,file_sha,approval FROM report_ieee_v12_grants WHERE program_id=?',
                   (original['program_id'],)).fetchone()
    if not row:return None
    path,sha,approval=row
    if file_hash(Path(path))!=sha:raise ExecutionStop('ieee_v12_grant_file_changed')
    cfg=json.loads(Path(path).read_text(encoding='utf-8'));approved=json.loads(approval)
    if values(cfg,original,approved.get('authorization'))!=approved:
        raise ExecutionStop('ieee_v12_authorization_changed')
    return cfg


def group_used(db):
    return db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) '
        'JOIN autonomous_sessions USING(session_id) JOIN autonomous_campaigns USING(campaign_id) WHERE group_key=?',(GROUP,)).fetchone()[0]


def extended_cap(db, original, prior):
    from graphrisk.agent.report_quality_program import used_requests
    cfg=registered(db,original)
    if not cfg:return prior
    used=used_requests(db,original['program_id'])
    if used<50 or used-50!=group_used(db):raise ExecutionStop('ieee_v12_cumulative_counter_changed')
    return 100


def register(budget, program_path, path, authorization):
    from graphrisk.agent.report_quality_program import check_program,effective_cap,used_requests
    with budget.transaction() as db:
        budget._check_registration(db);budget._check_request_links(db)
        original=check_program(db,program_path,file_hash(program_path))
        cfg=json.loads(Path(path).read_text(encoding='utf-8'))
        approved=canonical(values(cfg,original,authorization))
        db.execute('CREATE TABLE IF NOT EXISTS report_ieee_v12_grants(program_id TEXT PRIMARY KEY,path TEXT NOT NULL,file_sha TEXT NOT NULL,approval TEXT NOT NULL)')
        wanted=(original['program_id'],str(Path(path).resolve()),file_hash(path),approved)
        old=db.execute('SELECT * FROM report_ieee_v12_grants WHERE program_id=?',(original['program_id'],)).fetchone()
        if old and old!=wanted:raise ExecutionStop('ieee_v12_grant_not_resettable')
        if not old:
            if effective_cap(db,original)!=50 or used_requests(db,original['program_id'])!=50 or group_used(db):
                raise ExecutionStop('ieee_v12_requires_preserved_fifty_request_stop')
            db.execute('INSERT INTO report_ieee_v12_grants VALUES(?,?,?,?)',wanted)
        effective_cap(db,original)
    return json.loads(approved)


def validate_batch(db, original, cfg):
    if not cfg.get('ieee_v12_grant_sha256'):return 'route5_supplement_v3'
    grant=registered(db,original)
    if (not grant or cfg['ieee_v12_grant_sha256']!=grant['contract_sha256']
            or cfg.get('data_kind') not in ('real_validation','judge')):
        raise ExecutionStop('ieee_v12_batch_grant_changed')
    if cfg['data_kind']=='real_validation':
        if cfg.get('repair_allowed') is not False or cfg.get('kind')!='report':
            raise ExecutionStop('ieee_v12_no_repair')
        for case in cfg['cases'].values():
            raw=json.loads(Path(case['raw_evidence_path']).read_text(encoding='utf-8'))
            if (case['evidence_sha'] not in grant['real_evidence_allowed']
                    or raw['scope']['dataset_source']!='ieee_cis'
                    or digest(raw['evidence'])!=case['evidence_sha']):
                raise ExecutionStop('ieee_v12_real_evidence_outside_scope')
        if digest(cfg['profile'])!=grant['report_profile_sha256']:
            raise ExecutionStop('ieee_v12_generation_profile_changed')
    else:
        for case in cfg['cases'].values():
            packet=json.loads(Path(case['packet_path']).read_text(encoding='utf-8'))
            qualities=[json.loads(Path(p).read_text(encoding='utf-8')) for p in case['quality_paths'].values()]
            if (any(p not in cfg['files'] for p in case['quality_paths'].values())
                    or [q['target_sha256'] for q in qualities]!=packet['quality_targets_sha256']
                    or any(q['source']['evidence_sha256'] not in grant['real_evidence_allowed'] for q in qualities)):
                raise ExecutionStop('ieee_v12_judge_outside_scope')
    return GROUP


def guard_allocation(db, cfg):
    row=db.execute('SELECT contract_path,file_sha FROM report_quality_programs WHERE program_id=?',(cfg['program_id'],)).fetchone()
    if not row:return
    from graphrisk.agent.report_quality_program import check_program
    original=check_program(db,*row)
    if registered(db,original):
        if validate_batch(db,original,cfg)!=GROUP:raise ExecutionStop('ieee_v12_legacy_group_remains_closed')
        if group_used(db)>=50:raise ExecutionStop('ieee_v12_fifty_request_cap')
