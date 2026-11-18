"""One report-specific quarantine: two bound cases, at most four POSTs.

This is separate from the ten-cell recovery. It neither settles the old unknown
nor permits a successor, different source, case, payload or provider.
"""
from datetime import date
import json
from pathlib import Path

from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.controlled_recovery import unknown_record
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash

VERSION = 'natural-report-quarantine-v1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sealed(value):
    return value.get('contract_sha256') == digest({k: v for k, v in value.items() if k != 'contract_sha256'})


def baseline(db, campaign=None):
    """Every old attempt/metadata row remains exact; new own rows are excluded."""
    rows = db.execute('SELECT a.*,r.* FROM model_attempts a JOIN execution_requests r USING(attempt_id) '
        'LEFT JOIN autonomous_phases p USING(phase) LEFT JOIN autonomous_sessions s USING(session_id) '
        'WHERE s.campaign_id IS NULL OR s.campaign_id!=? ORDER BY a.attempt_id', (campaign or '',)).fetchall()
    return {'count': len(rows), 'sha256': digest(rows)}


def validate_files(contract):
    if (not sealed(contract) or contract.get('version') not in (VERSION, 'report-quality-batch-v1') or contract.get('max_requests') != 4
            or contract.get('order') != ['C1', 'C2'] or contract.get('max_requests_per_case') != 2):
        raise ExecutionStop('report_pilot_contract_changed')
    if contract['pricing_date'] != date.today().isoformat():
        raise ExecutionStop('report_pilot_pricing_requires_new_freeze')
    root = Path(contract['source_root']).resolve()
    expected = contract['source']
    actual = {p.relative_to(root).as_posix(): file_hash(p)
              for folder in ('src', 'api', 'scripts') for p in (root/folder).rglob('*.py')}
    if actual != expected['python_files'] or digest(actual) != expected['python_inventory_sha256']:
        raise ExecutionStop('report_pilot_source_changed')
    for name, sha in expected['configs'].items():
        if file_hash(root/'configs'/name) != sha:
            raise ExecutionStop('report_pilot_config_changed')
    for path, sha in contract['files'].items():
        if file_hash(Path(path)) != sha:
            raise ExecutionStop('report_pilot_bound_file_changed')


def check_state(db, contract, campaign):
    old = contract['unknown']
    if (old.get('provider') != 'deepseek' or old.get('state') != 'unknown'
            or old.get('result_code') != 'provider_http_rejected' or old.get('receipt_sha') is not None
            or old.get('receipt_path') is not None or old.get('integrity_stop') != 0
            or old.get('accounted', 0) <= 0 or old['accounted'] != old.get('reserved_micro_cny')
            or unknown_record(db, old['attempt_id']) != old):
        raise ExecutionStop('report_pilot_old_unknown_changed')
    unresolved = db.execute("SELECT attempt_id FROM model_attempts WHERE state IN ('reserved','unknown') ORDER BY attempt_id").fetchall()
    if unresolved != [(old['attempt_id'],)]:
        raise ExecutionStop('report_pilot_new_unresolved_stop')
    if db.execute('SELECT 1 FROM execution_requests WHERE integrity_stop=1').fetchone():
        raise ExecutionStop('report_pilot_integrity_stop')
    if baseline(db, campaign) != contract['baseline']:
        raise ExecutionStop('report_pilot_previous_ledger_rows_changed')


def load_grant(db, campaign, *, allow_closed=False):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_quality_batches'").fetchone():
        if db.execute('SELECT 1 FROM report_quality_batches WHERE campaign_id=?',(campaign,)).fetchone():
            from graphrisk.agent.report_quality_program import load_batch
            return load_batch(db,campaign,allow_closed=allow_closed)
    row = db.execute('SELECT contract_path,contract_file_sha,approval_sha,approval_json,closed_reason '
                     'FROM report_pilot_grants WHERE campaign_id=?', (campaign,)).fetchone()
    if row is None:
        raise ExecutionStop('report_pilot_not_authorized')
    path, sha, approval_sha, approval_json, closed = row
    if file_hash(Path(path)) != sha:
        raise ExecutionStop('report_pilot_contract_file_changed')
    contract, approval = read(path), json.loads(approval_json)
    validate_files(contract)
    if (digest(approval) != approval_sha or approval['contract_sha256'] != contract['contract_sha256']
            or approval['max_requests'] != 4 or approval['case_order'] != ['C1', 'C2']
            or approval['purpose'] != VERSION or not approval['authorization'].strip()
            or contract['campaign'] != campaign):
        raise ExecutionStop('report_pilot_authorization_changed')
    if db.execute('SELECT manifest_sha,cap_each,group_key FROM autonomous_campaigns WHERE campaign_id=?',
                  (campaign,)).fetchone() != (contract['contract_sha256'], 4, 'route5_supplement_v3'):
        raise ExecutionStop('report_pilot_campaign_changed')
    check_state(db, contract, campaign)
    if closed and not allow_closed:
        raise ExecutionStop('report_pilot_closed_' + closed)
    return contract


def register(budget, contract_path, authorization):
    contract = read(contract_path)
    validate_files(contract)
    if not isinstance(authorization, str) or not authorization.strip():
        raise ExecutionStop('report_pilot_human_authorization_missing')
    campaign = contract['campaign']
    approval = {'purpose': VERSION, 'contract_sha256': contract['contract_sha256'],
                'case_order': ['C1', 'C2'], 'max_requests': 4, 'authorization': authorization}
    with budget.transaction() as db:
        budget._check_registration(db); budget._check_request_links(db)
        check_state(db, contract, campaign)
        db.execute('CREATE TABLE IF NOT EXISTS report_pilot_grants(campaign_id TEXT PRIMARY KEY, '
                   'unknown_id TEXT UNIQUE NOT NULL,contract_path TEXT NOT NULL,contract_file_sha TEXT NOT NULL,'
                   'approval_sha TEXT NOT NULL,approval_json TEXT NOT NULL,closed_reason TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS report_pilot_payloads(phase TEXT PRIMARY KEY,case_id TEXT NOT NULL,'
                   'request_key TEXT UNIQUE NOT NULL,payload_sha TEXT NOT NULL,packet_sha TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS report_pilot_reviews(campaign_id TEXT NOT NULL,case_id TEXT NOT NULL,'
                   'quality TEXT NOT NULL,path TEXT NOT NULL,file_sha TEXT NOT NULL,PRIMARY KEY(campaign_id,case_id))')
        wanted = (campaign, contract['unknown']['attempt_id'], str(Path(contract_path).resolve()),
                  file_hash(Path(contract_path)), digest(approval), canonical(approval))
        old = db.execute('SELECT campaign_id,unknown_id,contract_path,contract_file_sha,approval_sha,approval_json '
                         'FROM report_pilot_grants WHERE campaign_id=? OR unknown_id=?', (campaign, wanted[1])).fetchone()
        if old is not None:
            if old != wanted:
                raise ExecutionStop('report_pilot_single_batch_no_reset')
        else:
            if baseline(db) != contract['baseline']:
                raise ExecutionStop('report_pilot_requires_unused_campaign')
            db.execute('INSERT INTO report_pilot_grants VALUES(?,?,?,?,?,?,NULL)', wanted)
        load_grant(db, campaign)
    return approval


def case_inputs(contract, case):
    from graphrisk.agent.autonomous_tools import InvestigationScope
    from graphrisk.agent.autonomous_loop import restore_observations
    from graphrisk.agent.autonomous_evidence import build_evidence
    from graphrisk.agent.policy_retrieval import PolicyIndex
    from graphrisk.agent.autonomous_policy import PolicyRegistry
    row = contract['cases'][case]
    raw = read(row['raw_evidence_path'])
    scope = InvestigationScope.model_validate_json(canonical(raw['scope']), strict=True)
    registry = PolicyRegistry.from_file(PolicyIndex.model_validate_json(Path(contract['registry_index']).read_text(encoding='utf-8')),
        Path(contract['source_root'])/'configs/agent_investigation_policy_requirements.json')
    observations = restore_observations(scope, raw['observations'])
    bundle = build_evidence(scope, observations, registry)
    if bundle.model_dump(mode='json') != raw['evidence'] or digest(raw['evidence']) != row['evidence_sha']:
        raise ExecutionStop('report_pilot_evidence_changed')
    return scope, observations, registry, bundle


def expected_packet(db, contract, case, slot):
    from graphrisk.agent import judgment_v3 as v3
    from graphrisk.agent.autonomous_http import strict_json
    scope, observations, registry, bundle = case_inputs(contract, case)
    first = v3.packet_for(scope, observations, registry, contract['profile'])
    plan = None
    if slot == 1:
        session = contract['cases'][case]['session']
        row = db.execute('SELECT a.state,r.receipt_path,r.receipt_sha,r.result_code FROM execution_requests r '
            'JOIN model_attempts a USING(attempt_id) JOIN autonomous_phases p USING(phase) '
            'WHERE p.session_id=? AND p.slot=0', (session,)).fetchone()
        if not row or row[0] != 'settled' or row[3] not in ('ok', 'invalid_model_action'):
            raise ExecutionStop('report_pilot_repair_requires_actual_first_response')
        path = Path(row[1])
        if file_hash(path) != row[2]:
            raise ExecutionStop('report_pilot_repair_receipt_changed')
        source = v3.safe_source(strict_json(read(path)['body']['choices'][0]['message']['content']))
        if source is None:
            raise ExecutionStop('report_pilot_repair_source_unusable')
        if not v3.issues_for(source, first, bundle):
            raise ExecutionStop('report_pilot_valid_first_needs_no_repair')
        plan = v3.repair_plan(source, first, bundle)
    elif slot != 0:
        raise ExecutionStop('report_pilot_two_slots_only')
    packet = v3.packet_for(scope, observations, registry, contract['profile'], plan)
    packet['logical_key'] = str(slot)
    return packet, bundle, plan


def bind_packet(budget, campaign, case, phase, packet):
    from graphrisk.agent.judgment_v3 import JudgmentProvider
    with budget.transaction() as db:
        contract = load_grant(db, campaign)
        session, slot = db.execute('SELECT session_id,slot FROM autonomous_phases WHERE phase=?', (phase,)).fetchone()
        if case not in contract['cases'] or session != contract['cases'][case]['session']:
            raise ExecutionStop('report_pilot_case_binding_changed')
        expected, _, _ = expected_packet(db, contract, case, slot)
        if packet != expected:
            raise ExecutionStop('report_pilot_exact_packet_required')
        formatter = object.__new__(JudgmentProvider)
        formatter.provider, formatter.profile = 'deepseek', contract['profile']
        payload_sha = digest(formatter.payload(packet))
        if slot == 0 and payload_sha != contract['cases'][case]['first_payload_sha']:
            raise ExecutionStop('report_pilot_first_payload_changed')
        wrapper = {'profile_sha256': digest(contract['profile']), 'packets': [packet], 'session': session}
        wrapper_sha = digest(wrapper)
        if db.execute('SELECT manifest_sha FROM execution_phases WHERE phase=?', (phase,)).fetchone() != (wrapper_sha,):
            raise ExecutionStop('report_pilot_wrapper_changed')
        wanted = (phase, case, digest([wrapper_sha, 'deepseek', str(slot)]), payload_sha, digest(packet))
        old = db.execute('SELECT * FROM report_pilot_payloads WHERE phase=?', (phase,)).fetchone()
        if old is not None and old != wanted:
            raise ExecutionStop('report_pilot_payload_binding_changed')
        if old is None:
            db.execute('INSERT INTO report_pilot_payloads VALUES(?,?,?,?,?)', wanted)
        return formatter.payload(packet)


def guard(db, *, phase, provider, request_key, packet_sha):
    """Called in the reservation transaction; never replaces other budget guards."""
    from graphrisk.agent.report_quality_program import guard as quality_guard
    if quality_guard(db,phase=phase,provider=provider,request_key=request_key,packet_sha=packet_sha):
        return True
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='report_pilot_grants' AND type='table'").fetchone():
        return False
    row = db.execute('SELECT s.campaign_id,s.session_id,p.slot,s.provider,s.max_calls,s.manifest_sha '
        'FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) WHERE p.phase=?', (phase,)).fetchone()
    campaign = row[0] if row else None
    if not db.execute('SELECT 1 FROM report_pilot_grants WHERE campaign_id=?', (campaign,)).fetchone():
        # Parent STOPs stay closed even though the old ten-cell parser is intact.
        for (path,) in db.execute('SELECT contract_path FROM report_pilot_grants'):
            if campaign in read(path)['closed_campaigns']:
                raise ExecutionStop('report_pilot_previous_campaign_closed')
        return False
    contract = load_grant(db, campaign)
    selected = [c for c, value in contract['cases'].items() if value['session'] == row[1]]
    if len(selected) != 1 or provider != 'deepseek' or row[3:5] != ('deepseek', 2):
        raise ExecutionStop('report_pilot_session_or_provider_changed')
    case = selected[0]
    if row[5] != digest(contract['cases'][case]['session_manifest']):
        raise ExecutionStop('report_pilot_session_manifest_changed')
    if case == 'C2':
        review = db.execute('SELECT quality,path,file_sha FROM report_pilot_reviews WHERE campaign_id=? AND case_id=?',
                            (campaign, 'C1')).fetchone()
        if not review or review[0] not in ('Pass', 'Fail') or file_hash(Path(review[1])) != review[2]:
            raise ExecutionStop('report_pilot_first_case_review_required')
    binding = db.execute('SELECT case_id,request_key,payload_sha,packet_sha FROM report_pilot_payloads WHERE phase=?',
                         (phase,)).fetchone()
    if not binding or binding[:3] != (case, request_key, packet_sha):
        raise ExecutionStop('report_pilot_payload_not_bound')
    expected, _, _ = expected_packet(db, contract, case, row[2])
    if digest(expected) != binding[3]:
        raise ExecutionStop('report_pilot_packet_or_repair_source_changed')
    old = contract['unknown']
    if request_key == old['request_key'] or packet_sha == old['packet_sha']:
        raise ExecutionStop('report_pilot_old_request_never_resent')
    count = db.execute('SELECT count(*) FROM execution_requests r JOIN autonomous_phases p USING(phase) '
                       'JOIN autonomous_sessions s USING(session_id) WHERE s.campaign_id=?', (campaign,)).fetchone()[0]
    if count >= 4:
        raise ExecutionStop('report_pilot_four_request_cap')
    return True


def stop(budget, campaign, reason):
    if not reason or not all(c.islower() or c == '_' for c in reason):
        raise ExecutionStop('report_pilot_stop_reason_invalid')
    from graphrisk.agent.report_quality_program import close_batch
    if close_batch(budget,campaign,reason):
        return
    with budget.transaction() as db:
        db.execute('UPDATE report_pilot_grants SET closed_reason=COALESCE(closed_reason,?) WHERE campaign_id=?', (reason, campaign))
