"""One approved successor to the first stopped recovery, without resetting it."""
import json
from pathlib import Path
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash


def check_parent(db,approval):
    parent=approval.get('parent')
    if not isinstance(parent,dict) or set(parent)!={'campaign_id','manifest_sha','approval_sha','stop_path','files'}:
        raise ExecutionStop('invalid_recovery_parent')
    table='controlled_recovery_successors' if approval['version']=='controlled-recovery-v3' else 'controlled_recovery'
    row=db.execute(f'SELECT manifest_sha,approval_sha,approval_json FROM {table} WHERE campaign_id=?',(parent['campaign_id'],)).fetchone()
    if row is None or row[:2]!=(parent['manifest_sha'],parent['approval_sha']) or digest(json.loads(row[2]))!=row[1] or json.loads(row[2])['unknown']!=approval['unknown']:
        raise ExecutionStop('recovery_parent_binding_changed')
    if approval['version']=='controlled-recovery-v3':
        ancestor=json.loads(row[2])
        if ancestor.get('version')!='controlled-recovery-v2':raise ExecutionStop('recovery_parent_version_changed')
        check_parent(db,ancestor)
    stop=Path(parent['stop_path'])
    files=parent['files']
    if not stop.is_absolute() or stop.name!='STOP.json' or not isinstance(files,dict) or not 5<=len(files)<=32 or 'STOP.json' not in files:
        raise ExecutionStop('invalid_recovery_parent_files')
    try:
        for name,sha in files.items():
            p=(stop.parent/name).resolve()
            if not p.is_relative_to(stop.parent.resolve()) or not p.is_file() or p.stat().st_size>1000000 or file_hash(p)!=sha:
                raise ExecutionStop('recovery_parent_evidence_changed')
        ended=json.loads(stop.read_text(encoding='utf-8'))
        cells=ended.get('cells',[]);order=[f+str(c) for f in 'ABCDE' for c in (1,2)]
        if ended.get('reason')!='two_consecutive_completed_acceptance_failures' or not 2<=len(cells)<=10 or cells!=order[:len(cells)] or ended.get('new_requests')!=len(cells):
            raise ExecutionStop('recovery_parent_not_closed')
        qualities=[]
        for cell in cells:
            review=json.loads((stop.parent/(cell+'-review.json')).read_text(encoding='utf-8'))
            if not review.get('completed') or review.get('quality') not in ('Pass','Fail') or review.get('result_sha256')!=files.get(cell+'-result.json') or cell+'-review.json' not in files:
                raise ExecutionStop('recovery_parent_review_invalid')
            qualities.append(review['quality'])
        if qualities[-2:]!=['Fail','Fail']:raise ExecutionStop('recovery_parent_stop_invalid')
    except (OSError,ValueError,TypeError):
        raise ExecutionStop('recovery_parent_evidence_unavailable') from None
    count=db.execute('SELECT count(*) FROM execution_requests r JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) WHERE s.campaign_id=?',(parent['campaign_id'],)).fetchone()[0]
    if count!=len(cells):raise ExecutionStop('recovery_parent_count_changed')


def register_successor(db,approval,campaign,manifest_sha):
    check_parent(db,approval)
    table='controlled_recovery_followups' if approval['version']=='controlled-recovery-v3' else 'controlled_recovery_successors'
    db.execute(f'CREATE TABLE IF NOT EXISTS {table}(campaign_id TEXT PRIMARY KEY, manifest_sha TEXT NOT NULL, attempt_id TEXT UNIQUE NOT NULL, approval_sha TEXT NOT NULL, approval_json TEXT NOT NULL, parent_campaign_id TEXT UNIQUE NOT NULL)')
    parent=approval['parent']['campaign_id']
    wanted=(campaign,manifest_sha,approval['unknown']['attempt_id'],digest(approval),canonical(approval),parent)
    old=db.execute(f'SELECT * FROM {table} WHERE campaign_id=? OR attempt_id=? OR parent_campaign_id=?',(campaign,approval['unknown']['attempt_id'],parent)).fetchone()
    if old is not None:
        if old!=wanted:raise ExecutionStop('recovery_successor_registration_changed')
        return
    if campaign==parent:raise ExecutionStop('recovery_successor_must_be_new')
    used=db.execute('SELECT count(*) FROM execution_requests r JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) WHERE s.campaign_id=?',(campaign,)).fetchone()[0]
    if used:raise ExecutionStop('recovery_requires_new_campaign')
    db.execute(f'INSERT INTO {table} VALUES(?,?,?,?,?,?)',wanted)


def has_successors(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='controlled_recovery_successors'").fetchone() is not None


def has_followups(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='controlled_recovery_followups'").fetchone() is not None
