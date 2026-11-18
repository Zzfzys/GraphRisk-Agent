"""One explicitly approved diagnostic quarantine; never resolves an unknown."""
import json
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical,digest


def unknown_record(db,attempt):
    cursor=db.execute("SELECT a.attempt_id,a.provider,a.state,a.reserved_micro_cny,r.request_key,r.packet_sha,r.accounted,r.receipt_sha,r.receipt_path,r.result_code,r.identity,r.usage_json,r.integrity_stop FROM model_attempts a JOIN execution_requests r USING(attempt_id) WHERE a.attempt_id=?",(attempt,))
    row=cursor.fetchone()
    return dict(zip([c[0] for c in cursor.description],row)) if row else None


def check_approval(db,approval):
    successor=isinstance(approval,dict) and approval.get('version') in ('controlled-recovery-v2','controlled-recovery-v3')
    expected_keys={'version','unknown','max_requests','authorization','purpose'} | ({'parent'} if successor else set())
    if not isinstance(approval,dict) or set(approval)!=expected_keys or approval['version'] not in ('controlled-recovery-v1','controlled-recovery-v2','controlled-recovery-v3') or type(approval['max_requests']) is not int or approval['max_requests']!=10 or approval['purpose']!='controlled_judgment_repairs' or not isinstance(approval['authorization'],str) or not approval['authorization'].strip():
        raise ExecutionStop('invalid_controlled_recovery_approval')
    old=approval['unknown']
    if not isinstance(old,dict) or old.get('provider')!='deepseek' or old.get('state')!='unknown' or old.get('result_code')!='provider_http_rejected' or old.get('receipt_sha') is not None or old.get('receipt_path') is not None or old.get('integrity_stop')!=0 or type(old.get('accounted')) is not int or old['accounted']<=0 or old.get('reserved_micro_cny')!=old['accounted']:
        raise ExecutionStop('recovery_unknown_not_eligible')
    if unknown_record(db,old.get('attempt_id'))!=old:
        raise ExecutionStop('recovery_unknown_record_changed')
    unresolved=db.execute("SELECT attempt_id FROM model_attempts WHERE state IN ('reserved','unknown')").fetchall()
    if unresolved!=[(old['attempt_id'],)]:raise ExecutionStop('additional_unknown_blocks_recovery')
    if db.execute('SELECT 1 FROM execution_requests WHERE integrity_stop=1 LIMIT 1').fetchone():
        raise ExecutionStop('integrity_review_required')
    if successor:
        from graphrisk.agent.recovery_successor import check_parent
        check_parent(db,approval)


def preview(budget,approval):
    with budget.transaction() as db:
        budget._check_registration(db);budget._check_request_links(db)
        check_approval(db,approval)


def register(budget,approval,campaign,manifest_sha):
    """Persist the immutable human approval bound to one already frozen batch."""
    with budget.transaction() as db:
        budget._check_registration(db);budget._check_request_links(db)
        check_approval(db,approval)
        row=db.execute('SELECT manifest_sha,cap_each,group_key FROM autonomous_campaigns WHERE campaign_id=?',(campaign,)).fetchone()
        if row!=(manifest_sha,10,'route5_supplement_v3'):
            raise ExecutionStop('recovery_requires_frozen_ten_cell_campaign')
        if approval['version'] in ('controlled-recovery-v2','controlled-recovery-v3'):
            from graphrisk.agent.recovery_successor import register_successor
            register_successor(db,approval,campaign,manifest_sha)
            return
        db.execute('CREATE TABLE IF NOT EXISTS controlled_recovery(campaign_id TEXT PRIMARY KEY, manifest_sha TEXT NOT NULL, attempt_id TEXT UNIQUE NOT NULL, approval_sha TEXT NOT NULL, approval_json TEXT NOT NULL)')
        wanted=(campaign,manifest_sha,approval['unknown']['attempt_id'],digest(approval),canonical(approval))
        old=db.execute('SELECT campaign_id,manifest_sha,attempt_id,approval_sha,approval_json FROM controlled_recovery WHERE campaign_id=? OR attempt_id=?',(campaign,approval['unknown']['attempt_id'])).fetchone()
        if old is not None:
            if old!=wanted:raise ExecutionStop('recovery_registration_changed')
            return
        used=db.execute('SELECT count(*) FROM execution_requests r JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) WHERE s.campaign_id=?',(campaign,)).fetchone()[0]
        if used:raise ExecutionStop('recovery_requires_new_campaign')
        db.execute('INSERT INTO controlled_recovery VALUES(?,?,?,?,?)',wanted)


def guard(db,*,phase,provider,request_key,packet_sha):
    """Called inside the shared reservation transaction only if unresolved exists."""
    if provider!='deepseek' or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='controlled_recovery'").fetchone():
        raise ExecutionStop('unresolved_attempt_blocks_execution')
    row=db.execute('SELECT g.manifest_sha,g.approval_sha,g.approval_json,c.manifest_sha,c.cap_each,c.group_key,c.campaign_id FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) JOIN autonomous_campaigns c USING(campaign_id) JOIN controlled_recovery g USING(campaign_id) WHERE p.phase=?',(phase,)).fetchone()
    from graphrisk.agent.recovery_successor import has_successors
    if has_successors(db):
        if row is not None and db.execute('SELECT 1 FROM controlled_recovery_successors WHERE parent_campaign_id=?',(row[6],)).fetchone():
            raise ExecutionStop('recovery_parent_closed')
        if row is None:
            row=db.execute('SELECT g.manifest_sha,g.approval_sha,g.approval_json,c.manifest_sha,c.cap_each,c.group_key,c.campaign_id FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) JOIN autonomous_campaigns c USING(campaign_id) JOIN controlled_recovery_successors g USING(campaign_id) WHERE p.phase=?',(phase,)).fetchone()
    from graphrisk.agent.recovery_successor import has_followups
    if has_followups(db):
        if row is not None and db.execute('SELECT 1 FROM controlled_recovery_followups WHERE parent_campaign_id=?',(row[6],)).fetchone():
            raise ExecutionStop('recovery_parent_closed')
        if row is None:
            row=db.execute('SELECT g.manifest_sha,g.approval_sha,g.approval_json,c.manifest_sha,c.cap_each,c.group_key,c.campaign_id FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) JOIN autonomous_campaigns c USING(campaign_id) JOIN controlled_recovery_followups g USING(campaign_id) WHERE p.phase=?',(phase,)).fetchone()
    if row is None:raise ExecutionStop('unresolved_attempt_blocks_execution')
    approval=json.loads(row[2])
    if row[0]!=row[3] or row[1]!=digest(approval) or row[4:6]!=(10,'route5_supplement_v3'):
        raise ExecutionStop('recovery_binding_changed')
    check_approval(db,approval)
    old=approval['unknown']
    if request_key==old['request_key'] or packet_sha==old['packet_sha']:
        raise ExecutionStop('recovery_old_request_must_not_be_resent')
    count=db.execute('SELECT count(*) FROM execution_requests r JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) WHERE s.campaign_id=?',(row[6],)).fetchone()[0]
    if count>=approval['max_requests']:raise ExecutionStop('recovery_ten_request_cap')
