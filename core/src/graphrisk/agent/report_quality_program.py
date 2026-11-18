"""A finite, freshly authorized quality program preserving two exact unknowns.

New frozen batches may change prompts after offline review. They share one
finite cumulative allocation and cannot reopen a stopped batch or absorb new unknowns.
"""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.controlled_recovery import unknown_record
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash

VERSION = 'report-quality-program-v1'
BATCH = 'report-quality-batch-v1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def old_rows(db, program):
    rows = db.execute('SELECT a.*,r.* FROM model_attempts a JOIN execution_requests r USING(attempt_id) '
        'LEFT JOIN autonomous_phases p USING(phase) LEFT JOIN autonomous_sessions s USING(session_id) '
        'WHERE s.campaign_id IS NULL OR s.campaign_id NOT IN '
        '(SELECT campaign_id FROM report_quality_batches WHERE program_id=?) ORDER BY a.attempt_id', (program,)).fetchall()
    return {'count': len(rows), 'sha256': digest(rows)}


def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS report_quality_programs(program_id TEXT PRIMARY KEY,parent_campaign TEXT UNIQUE NOT NULL,'
               'contract_path TEXT NOT NULL,file_sha TEXT NOT NULL,approval_json TEXT NOT NULL,closed_reason TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS report_quality_extensions(program_id TEXT PRIMARY KEY,contract_path TEXT NOT NULL,file_sha TEXT NOT NULL,approval_json TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS report_quality_batches(campaign_id TEXT PRIMARY KEY,program_id TEXT NOT NULL,'
               'contract_path TEXT NOT NULL,file_sha TEXT NOT NULL,closed_reason TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS report_pilot_payloads(phase TEXT PRIMARY KEY,case_id TEXT NOT NULL,'
               'request_key TEXT UNIQUE NOT NULL,payload_sha TEXT NOT NULL,packet_sha TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS report_pilot_reviews(campaign_id TEXT NOT NULL,case_id TEXT NOT NULL,'
               'quality TEXT NOT NULL,path TEXT NOT NULL,file_sha TEXT NOT NULL,PRIMARY KEY(campaign_id,case_id))')


def sealed(value):
    return value.get('contract_sha256') == digest({k:v for k,v in value.items() if k!='contract_sha256'})


def check_program(db, path, sha, *, allow_closed=False):
    if file_hash(Path(path)) != sha:
        raise ExecutionStop('quality_program_contract_file_changed')
    cfg = read(path)
    if not sealed(cfg) or cfg.get('version') != VERSION or cfg.get('max_requests') != 30:
        raise ExecutionStop('quality_program_contract_changed')
    unknown = cfg['unknowns']
    if (len(unknown) != 2 or len({r['attempt_id'] for r in unknown}) != 2
            or sorted(r['result_code'] for r in unknown) != ['provider_connection_unknown','provider_http_rejected']):
        raise ExecutionStop('quality_program_exact_two_historical_unknowns_required')
    for row in unknown:
        if (row['state'] != 'unknown' or row['provider'] != 'deepseek' or row['receipt_sha'] is not None
                or row['receipt_path'] is not None or row['integrity_stop'] or row['accounted'] <= 0
                or row['accounted'] != row['reserved_micro_cny'] or unknown_record(db,row['attempt_id']) != row):
            raise ExecutionStop('quality_program_historical_unknown_changed')
    unresolved = db.execute("SELECT attempt_id FROM model_attempts WHERE state IN ('reserved','unknown') ORDER BY attempt_id").fetchall()
    if unresolved != [(r['attempt_id'],) for r in sorted(unknown,key=lambda r:r['attempt_id'])]:
        raise ExecutionStop('quality_program_new_unresolved_stop')
    if db.execute('SELECT 1 FROM execution_requests WHERE integrity_stop=1').fetchone():
        raise ExecutionStop('quality_program_integrity_stop')
    if old_rows(db,cfg['program_id']) != cfg['baseline']:
        raise ExecutionStop('quality_program_old_rows_changed')
    for name, expected in cfg['protected_files'].items():
        if file_hash(Path(name)) != expected:
            raise ExecutionStop('quality_program_old_evidence_changed')
    row = db.execute('SELECT closed_reason FROM report_quality_programs WHERE program_id=?',(cfg['program_id'],)).fetchone()
    if row and row[0] and not allow_closed:
        approved = None
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='report_quality_operational_reviews'").fetchone():
            approved = db.execute('SELECT r.path,r.file_sha FROM report_quality_operational_reviews r '
                'JOIN report_quality_stop_events e ON r.program_id=e.program_id AND r.stop_index=e.event_index '
                'WHERE r.program_id=? AND e.reason=? AND e.event_index=(SELECT max(event_index) FROM report_quality_stop_events WHERE program_id=?)',
                (cfg['program_id'],'report_pilot_execution_guard_failed',cfg['program_id'])).fetchone()
        if not approved and db.execute("SELECT 1 FROM sqlite_master WHERE name='report_quality_fix_reviews'").fetchone():
            approved = db.execute('SELECT r.path,r.file_sha FROM report_quality_fix_reviews r '
                'JOIN report_quality_stop_events e ON r.program_id=e.program_id AND r.stop_index=e.event_index '
                'WHERE r.program_id=? AND e.event_index=(SELECT max(event_index) FROM report_quality_stop_events WHERE program_id=?)',
                (cfg['program_id'],cfg['program_id'])).fetchone()
        if row[0]!='report_pilot_execution_guard_failed' or not approved or file_hash(Path(approved[0]))!=approved[1]:
            raise ExecutionStop('quality_program_closed')
    return cfg



def used_requests(db, program_id):
    return db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) '
        'JOIN autonomous_sessions USING(session_id) JOIN report_quality_batches USING(campaign_id) '
        'WHERE program_id=?',(program_id,)).fetchone()[0]


def extension_values(cfg, original, authorization):
    if (not sealed(cfg) or cfg.get('version')!='report-quality-additive-extension-v1'
            or cfg.get('program_id')!=original['program_id']
            or cfg.get('program_sha256')!=original['contract_sha256']
            or cfg.get('original_max_requests')!=30 or cfg.get('additional_max_requests')!=20
            or cfg.get('cumulative_max_requests')!=50
            or type(cfg.get('authorized_at_requests')) is not int
            or not 0<=cfg['authorized_at_requests']<=30
            or cfg.get('scope')!='two_previously_approved_real_summaries_and_public_synthetic_boundary_tasks'
            or cfg.get('supplement_group')!='route5_supplement_v3'
            or cfg.get('supplement_total_max_requests')!=100 or cfg.get('overall_max_cny')!=100
            or cfg.get('no_reset_no_reopen_no_unknown_retry') is not True
            or not isinstance(authorization,str) or not authorization.strip()):
        raise ExecutionStop('quality_extension_binding_invalid')
    return {'contract_sha256':cfg['contract_sha256'],'authorization':authorization,
            'program_sha256':original['contract_sha256'],'cumulative_max_requests':50}


def effective_cap(db, original):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='report_quality_extensions'").fetchone():return 30
    row=db.execute('SELECT contract_path,file_sha,approval_json FROM report_quality_extensions WHERE program_id=?',
        (original['program_id'],)).fetchone()
    if not row:return 30
    path,sha,approval=row
    if file_hash(Path(path))!=sha:raise ExecutionStop('quality_extension_file_changed')
    saved=read(path);approved=json.loads(approval)
    if extension_values(saved,original,approved.get('authorization'))!=approved:
        raise ExecutionStop('quality_extension_authorization_changed')
    if used_requests(db,original['program_id'])<saved['authorized_at_requests']:
        raise ExecutionStop('quality_extension_counter_decreased')
    from graphrisk.agent.report_ieee_validation import extended_cap
    return extended_cap(db,original,50)


def register_extension(budget, program_path, extension_path, authorization):
    """A single separately authorized additive allocation; original grant stays sealed."""
    with budget.transaction() as db:
        budget._check_registration(db);budget._check_request_links(db);setup(db)
        original=check_program(db,program_path,file_hash(program_path))
        registered=db.execute('SELECT contract_path,file_sha FROM report_quality_programs WHERE program_id=?',
            (original['program_id'],)).fetchone()
        if registered!=(str(Path(program_path).resolve()),file_hash(program_path)):
            raise ExecutionStop('quality_extension_original_not_registered')
        cfg=read(extension_path)
        approval=canonical(extension_values(cfg,original,authorization))
        wanted=(original['program_id'],str(Path(extension_path).resolve()),file_hash(extension_path),approval)
        old=db.execute('SELECT * FROM report_quality_extensions WHERE program_id=?',(original['program_id'],)).fetchone()
        if old and old!=wanted:raise ExecutionStop('quality_extension_not_resettable')
        if not old:
            if used_requests(db,original['program_id'])!=cfg['authorized_at_requests']:
                raise ExecutionStop('quality_extension_count_changed')
            db.execute('INSERT INTO report_quality_extensions VALUES(?,?,?,?)',wanted)
        effective_cap(db,original)
    return json.loads(approval)


def register_program(budget, path, authorization):
    cfg = read(path)
    if not isinstance(authorization,str) or not authorization.strip():
        raise ExecutionStop('quality_program_human_authorization_required')
    approval = canonical({'contract_sha256':cfg['contract_sha256'],'authorization':authorization,'max_requests':30,
                          'scope':'two_previously_approved_real_summaries_and_public_synthetic_boundary_tasks'})
    with budget.transaction() as db:
        budget._check_registration(db); budget._check_request_links(db); setup(db)
        parent = db.execute('SELECT contract_path,contract_file_sha,closed_reason FROM report_pilot_grants WHERE campaign_id=?',
                            (cfg['parent_campaign'],)).fetchone()
        if not parent or parent[2]!='model_request_unknown_saved_do_not_retry' or file_hash(Path(parent[0]))!=parent[1]:
            raise ExecutionStop('quality_program_requires_preserved_stopped_parent')
        check_program(db,path,file_hash(path))
        wanted = (cfg['program_id'],cfg['parent_campaign'],str(Path(path).resolve()),file_hash(path),approval)
        old = db.execute('SELECT program_id,parent_campaign,contract_path,file_sha,approval_json FROM report_quality_programs '
                         'WHERE program_id=? OR parent_campaign=?',(cfg['program_id'],cfg['parent_campaign'])).fetchone()
        if old and old != wanted:
            raise ExecutionStop('quality_program_allocation_not_resettable')
        if not old:
            db.execute('INSERT INTO report_quality_programs VALUES(?,?,?,?,?,NULL)',wanted)
    return json.loads(approval)


def load_batch(db,campaign,*,allow_closed=False):
    from graphrisk.agent.report_v13_execution import is_batch,load_batch as v13_load
    if is_batch(db,campaign):return v13_load(db,campaign,allow_closed=allow_closed)
    from graphrisk.agent.report_pilot_execution import validate_files
    row = db.execute('SELECT b.contract_path,b.file_sha,b.closed_reason,p.contract_path,p.file_sha,p.approval_json '
        'FROM report_quality_batches b JOIN report_quality_programs p USING(program_id) WHERE b.campaign_id=?',(campaign,)).fetchone()
    if not row:
        raise ExecutionStop('quality_batch_not_registered')
    path,sha,closed,program_path,program_sha,approval = row
    program = check_program(db,program_path,program_sha,allow_closed=allow_closed)
    if json.loads(approval)['contract_sha256'] != program['contract_sha256'] or file_hash(Path(path)) != sha:
        raise ExecutionStop('quality_batch_authorization_changed')
    cfg = read(path)
    validate_files(cfg)
    if cfg['program_sha256'] != program['contract_sha256'] or cfg['program_id'] != program['program_id']:
        raise ExecutionStop('quality_batch_program_binding_changed')
    from graphrisk.agent.report_ieee_validation import validate_batch
    group=validate_batch(db,program,cfg)
    if cfg.get('data_kind')=='real_validation':
        if group!='route5_ieee_v12_20261025':raise ExecutionStop('quality_real_validation_grant_required')
    elif cfg.get('data_kind')=='real_development':
        if any(c['evidence_sha'] not in program['real_evidence_allowed'] for c in cfg['cases'].values()):
            raise ExecutionStop('quality_batch_real_summary_outside_authorized_scope')
    elif cfg.get('data_kind') not in ('synthetic_development','synthetic_validation','judge'):
        raise ExecutionStop('quality_batch_data_kind_unregistered')
    if cfg.get('data_kind') in ('synthetic_development','synthetic_validation'):
        for case in cfg['cases'].values():
            raw=read(case['raw_evidence_path'])
            if raw['scope']['dataset_source']!='synthetic' or not raw['scope']['transaction_id'].startswith('SYN-'):
                raise ExecutionStop('quality_batch_synthetic_scope_invalid')
    if db.execute('SELECT manifest_sha,cap_each,group_key FROM autonomous_campaigns WHERE campaign_id=?',(campaign,)).fetchone() != (cfg['contract_sha256'],4,group):
        raise ExecutionStop('quality_batch_campaign_binding_changed')
    if closed and not allow_closed:
        raise ExecutionStop('quality_batch_closed')
    return cfg


def register_batch(budget,path):
    cfg = read(path)
    with budget.transaction() as db:
        wanted = (cfg['campaign'],cfg['program_id'],str(Path(path).resolve()),file_hash(path))
        old = db.execute('SELECT campaign_id,program_id,contract_path,file_sha FROM report_quality_batches WHERE campaign_id=?',(cfg['campaign'],)).fetchone()
        if old and old != wanted:
            raise ExecutionStop('quality_batch_registration_changed')
        if not old:
            db.execute('INSERT INTO report_quality_batches VALUES(?,?,?,?,NULL)',wanted)
        load_batch(db,cfg['campaign'])


def guard(db,*,phase,provider,request_key,packet_sha):
    from graphrisk.agent.report_paired_execution import guard as paired_guard
    if paired_guard(db,phase=phase,provider=provider,request_key=request_key,packet_sha=packet_sha):return True
    from graphrisk.agent.report_v13_execution import guard as v13_guard
    if v13_guard(db,phase=phase,provider=provider,request_key=request_key,packet_sha=packet_sha):return True
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_quality_batches'").fetchone():
        return False
    row = db.execute('SELECT s.campaign_id,s.session_id,s.provider,s.max_calls,s.manifest_sha,p.slot,b.program_id '
        'FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) '
        'JOIN report_quality_batches b USING(campaign_id) WHERE p.phase=?',(phase,)).fetchone()
    if not row:
        return False
    from graphrisk.agent.report_pilot_execution import expected_packet
    cfg = load_batch(db,row[0])
    cases = [c for c,v in cfg['cases'].items() if v['session']==row[1]]
    judge = cfg.get('kind')=='judge'
    if len(cases)!=1 or provider!='deepseek' or row[2:4]!=('deepseek',1 if judge else 2):
        raise ExecutionStop('quality_batch_session_binding_changed')
    case = cases[0]
    if cfg.get('data_kind') in ('synthetic_validation','real_validation') and row[5]!=0:
        raise ExecutionStop('quality_validation_one_shot_only')
    if row[4] != digest(cfg['cases'][case]['session_manifest']):
        raise ExecutionStop('quality_batch_session_manifest_changed')
    if case=='C2' and not judge:
        review = db.execute('SELECT quality,path,file_sha FROM report_pilot_reviews WHERE campaign_id=? AND case_id=?',(row[0],'C1')).fetchone()
        if not review or review[0] not in ('Pass','Fail') or file_hash(Path(review[1]))!=review[2]:
            raise ExecutionStop('quality_batch_first_review_required')
    bound = db.execute('SELECT case_id,request_key,payload_sha,packet_sha FROM report_pilot_payloads WHERE phase=?',(phase,)).fetchone()
    if not bound or bound[:3]!=(case,request_key,packet_sha):
        raise ExecutionStop('quality_batch_payload_not_bound')
    if judge:
        if row[5]!=0:raise ExecutionStop('quality_judge_one_slot_only')
        packet=read(cfg['cases'][case]['packet_path'])
    else:packet,_,_ = expected_packet(db,cfg,case,row[5])
    if digest(packet)!=bound[3]:
        raise ExecutionStop('quality_batch_packet_changed')
    from graphrisk.agent.report_ieee_validation import guard_allocation
    guard_allocation(db,cfg)
    own = db.execute('SELECT count(*) FROM execution_requests r JOIN autonomous_phases p USING(phase) '
        'JOIN autonomous_sessions s USING(session_id) JOIN report_quality_batches b USING(campaign_id) '
        'WHERE b.program_id=?',(row[6],)).fetchone()[0]
    batch = db.execute('SELECT count(*) FROM execution_requests r JOIN autonomous_phases p USING(phase) '
        'JOIN autonomous_sessions s USING(session_id) WHERE s.campaign_id=?',(row[0],)).fetchone()[0]
    registered=db.execute('SELECT contract_path,file_sha FROM report_quality_programs WHERE program_id=?',(row[6],)).fetchone()
    limit=effective_cap(db,check_program(db,*registered))
    if own>=limit or batch>=(2 if cfg.get('data_kind') in ('synthetic_validation','real_validation') else 4):
        raise ExecutionStop('quality_program_or_batch_request_cap')
    return True


def bind_judge_packet(budget,campaign,case,phase,packet):
    from graphrisk.agent.report_quality_judge import JudgeProvider
    with budget.transaction() as db:
        cfg=load_batch(db,campaign)
        if cfg.get('kind')!='judge' or read(cfg['cases'][case]['packet_path'])!=packet:
            raise ExecutionStop('quality_judge_packet_changed')
        session=cfg['cases'][case]['session']
        if db.execute('SELECT session_id,slot FROM autonomous_phases WHERE phase=?',(phase,)).fetchone()!=(session,0):
            raise ExecutionStop('quality_judge_session_changed')
        wrapper={'profile_sha256':digest(cfg['profile']),'packets':[packet],'session':session}
        sha=digest(wrapper)
        if db.execute('SELECT manifest_sha FROM execution_phases WHERE phase=?',(phase,)).fetchone()!=(sha,):
            raise ExecutionStop('quality_judge_wrapper_changed')
        adapter=object.__new__(JudgeProvider);adapter.provider,adapter.profile='deepseek',cfg['profile']
        payload=adapter.payload(packet)
        wanted=(phase,case,digest([sha,'deepseek','0']),digest(payload),digest(packet))
        old=db.execute('SELECT * FROM report_pilot_payloads WHERE phase=?',(phase,)).fetchone()
        if old and old!=wanted:raise ExecutionStop('quality_judge_payload_binding_changed')
        if not old:db.execute('INSERT INTO report_pilot_payloads VALUES(?,?,?,?,?)',wanted)
        return payload


def close_batch(budget,campaign,reason):
    with budget.transaction() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='report_quality_batches'").fetchone():
            return False
        row = db.execute('SELECT program_id FROM report_quality_batches WHERE campaign_id=?',(campaign,)).fetchone()
        if not row:
            return False
        db.execute('UPDATE report_quality_batches SET closed_reason=COALESCE(closed_reason,?) WHERE campaign_id=?',(reason,campaign))
        # Model quality failure may inform a new frozen development batch. A new
        # unknown, identity/integrity or operational failure closes the program.
        if reason not in ('two_cases_completed','two_consecutive_completed_case_failures','review_pending','output_or_provider_limit','judgment_v3_context_limit','development_candidate_rejected'):
            db.execute('UPDATE report_quality_programs SET closed_reason=COALESCE(closed_reason,?) WHERE program_id=?',(reason,row[0]))
            db.execute('CREATE TABLE IF NOT EXISTS report_quality_stop_events(program_id TEXT NOT NULL,event_index INTEGER NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(program_id,event_index))')
            last=db.execute('SELECT COALESCE(max(event_index),0) FROM report_quality_stop_events WHERE program_id=?',(row[0],)).fetchone()[0]
            db.execute('INSERT INTO report_quality_stop_events VALUES(?,?,?)',(row[0],last+1,reason))
        return True


def register_mapping_fix_review(budget,program_id,path):
    """Review one known settled-response mapper bug, never a missing response.

    Keep the original stop event and batch closed. Grant only the already
    authorized finite program a new frozen batch, without resetting counts.
    """
    review=read(path)
    if review.get('cause')!='next_check_list_quality_mapping_type_error' or review.get('reviewer')!='assistant_development':
        raise ExecutionStop('quality_operational_review_scope_invalid')
    test_path=Path(review['test_evidence']['path'])
    if file_hash(test_path)!=review['test_evidence']['sha256']:
        raise ExecutionStop('quality_operational_review_tests_changed')
    suites=ET.parse(test_path).getroot().iter('testsuite')
    suites=list(suites)
    if not suites or sum(int(s.attrib.get('tests',0)) for s in suites)<2 or any(int(s.attrib.get(k,0)) for s in suites for k in ('failures','errors','skipped')):
        raise ExecutionStop('quality_operational_review_tests_not_passed')
    with budget.transaction() as db:
        row=db.execute('SELECT contract_path,file_sha,closed_reason FROM report_quality_programs WHERE program_id=?',(program_id,)).fetchone()
        if not row or row[2]!='report_pilot_execution_guard_failed':
            raise ExecutionStop('quality_operational_review_wrong_stop')
        cfg=check_program(db,row[0],row[1],allow_closed=True)
        cursor=db.execute('SELECT a.state,r.receipt_path,r.receipt_sha FROM model_attempts a JOIN execution_requests r USING(attempt_id) '
            'JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) JOIN report_quality_batches b USING(campaign_id) WHERE b.program_id=?',(program_id,))
        rows=cursor.fetchall()
        if len(rows)!=1 or rows[0][0]!='settled' or file_hash(Path(rows[0][1]))!=rows[0][2]:
            raise ExecutionStop('quality_operational_review_requires_one_verified_settled_response')
        actual=read(rows[0][1])['body']['choices'][0]['message']['content']
        from graphrisk.agent.autonomous_http import strict_json
        if not isinstance(strict_json(actual).get('next_check',{}).get('text'),list):
            raise ExecutionStop('quality_operational_review_defect_not_present')
        db.execute('CREATE TABLE IF NOT EXISTS report_quality_stop_events(program_id TEXT NOT NULL,event_index INTEGER NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(program_id,event_index))')
        last=db.execute('SELECT max(event_index) FROM report_quality_stop_events WHERE program_id=?',(program_id,)).fetchone()[0]
        if last is None:
            last=1
            db.execute('INSERT INTO report_quality_stop_events VALUES(?,?,?)',(program_id,last,row[2]))
        db.execute('CREATE TABLE IF NOT EXISTS report_quality_operational_reviews(program_id TEXT PRIMARY KEY,stop_index INTEGER NOT NULL,path TEXT NOT NULL,file_sha TEXT NOT NULL)')
        wanted=(program_id,last,str(Path(path).resolve()),file_hash(Path(path)))
        old=db.execute('SELECT * FROM report_quality_operational_reviews WHERE program_id=?',(program_id,)).fetchone()
        if old and old!=wanted:
            raise ExecutionStop('quality_operational_review_not_repeatable')
        if not old:db.execute('INSERT INTO report_quality_operational_reviews VALUES(?,?,?,?)',wanted)
        check_program(db,row[0],row[1])


def register_context_fix_review(budget,program_id,path):
    """One audited pre-POST overflow fix; not a waiver for unknown responses."""
    review=read(path)
    if (review.get('cause')!='repair_context_overflow' or review.get('reviewer')!='assistant_development'
            or review['inspection']['before_bytes']<=7000 or review['inspection'].get('new_input_limit')!=16000
            or review['inspection']['after_bytes']>16000):
        raise ExecutionStop('quality_context_fix_review_invalid')
    test=Path(review['test_evidence']['path'])
    if file_hash(test)!=review['test_evidence']['sha256']:
        raise ExecutionStop('quality_context_fix_tests_changed')
    suites=list(ET.parse(test).getroot().iter('testsuite'))
    if not suites or any(int(s.attrib.get(k,0)) for s in suites for k in ('failures','errors','skipped')):
        raise ExecutionStop('quality_context_fix_tests_not_passed')
    with budget.transaction() as db:
        row=db.execute('SELECT contract_path,file_sha,closed_reason FROM report_quality_programs WHERE program_id=?',(program_id,)).fetchone()
        if not row or row[2]!='report_pilot_execution_guard_failed':raise ExecutionStop('quality_context_fix_wrong_stop')
        check_program(db,row[0],row[1],allow_closed=True)
        rows=db.execute('SELECT a.state,r.receipt_path,r.receipt_sha FROM model_attempts a JOIN execution_requests r USING(attempt_id) '
            'JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) JOIN report_quality_batches b USING(campaign_id) WHERE b.program_id=?',(program_id,)).fetchall()
        if len(rows)!=2 or any(r[0]!='settled' or not r[1] or file_hash(Path(r[1]))!=r[2] for r in rows):
            raise ExecutionStop('quality_context_fix_requires_two_verified_settled_responses')
        event=db.execute('SELECT max(event_index) FROM report_quality_stop_events WHERE program_id=?',(program_id,)).fetchone()[0]
        if event is None:raise ExecutionStop('quality_context_fix_stop_event_missing')
        db.execute('CREATE TABLE IF NOT EXISTS report_quality_fix_reviews(program_id TEXT NOT NULL,stop_index INTEGER NOT NULL,path TEXT NOT NULL,file_sha TEXT NOT NULL,PRIMARY KEY(program_id,stop_index))')
        wanted=(program_id,event,str(Path(path).resolve()),file_hash(Path(path)))
        old=db.execute('SELECT * FROM report_quality_fix_reviews WHERE program_id=? AND stop_index=?',(program_id,event)).fetchone()
        if old and old!=wanted:raise ExecutionStop('quality_context_fix_review_changed')
        if not old:db.execute('INSERT INTO report_quality_fix_reviews VALUES(?,?,?,?)',wanted)
        check_program(db,row[0],row[1])
