"""Eight first whole-narrative judgments, separately approved and receipted."""
from contextlib import closing
from datetime import date
from pathlib import Path
import sqlite3

from graphrisk.agent import daily_report_judge as judge, rank_successor as successor
from graphrisk.agent.autonomous_providers import reservation_micro
from graphrisk.agent.daily_reference_comparison_execution import PINNED_VERIFIERS, check_authorization
from graphrisk.agent.judge_control_execution import verify_saved_result as verify_parent_result
from graphrisk.agent.reference_planner_execution import read
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash

KEYS = [f'W{i:02}' for i in range(1, 9)]


def require(ok, code):
    successor.require(ok, 'daily_judge_' + code)


def packet_payload(case, profile):
    frozen = read(case['packet_path'])
    packet = judge.packet_for(frozen['view']['reports'], profile)
    require(packet == frozen, 'packet_reconstruction')
    formatter = object.__new__(judge.Provider)
    formatter.provider, formatter.profile = 'deepseek', profile
    return packet, formatter.payload(packet)


def check_contract(cfg):
    require(cfg['version'] == successor.DAILY_JUDGE_VERSION, 'version')
    require(all(type(cfg.get(k)) is int for k in ('max_requests', 'cap_micro_cny', 'deepseek_before', 'attempts_before'))
        and (cfg['max_requests'], cfg['cap_micro_cny'], cfg['deepseek_before'], cfg['attempts_before'])
        == (8, 5_000_000, 1438, 1461), 'exact_allocation')
    require(all(type(cfg.get(k)) is int and cfg[k] == 0 for k in
        ('retries', 'automatic_retries', 'repairs', 'report_generation_calls', 'secondary_judge_calls'))
        and type(cfg.get('judge_calls')) is int and cfg['judge_calls'] == 8
        and cfg['pause_cny'] == 80 and cfg['overall_max_cny'] == 100, 'limits')
    require(cfg['pricing_required'] is False and cfg['pricing_date'] == '2026-11-14', 'pricing')
    source = Path(cfg['source_root'])
    actual = {p.relative_to(source).as_posix(): file_hash(p)
              for folder in ('src', 'api', 'scripts') for p in (source / folder).rglob('*.py')}
    require(bool(actual) and actual == cfg['source_python_sha256'] and all(
        cfg['files'].get(str(source / rel)) == sha for rel, sha in actual.items()), 'source_changed')
    require(cfg['files'].get(str(source / 'configs/agent_investigation_experiment.json')) ==
        file_hash(source / 'configs/agent_investigation_experiment.json'), 'budget_config')
    require(all(cfg['files'].get(cfg[k]) == file_hash(Path(cfg[k])) for k in
        ('profile_path', 'offline_result_path', 'offline_freeze_path', 'gold_path')), 'local_inputs_unbound')
    profile = read(cfg['profile_path'])
    require(profile['deepseek']['endpoint'] == cfg['endpoint'] and profile['deepseek']['model'] == cfg['model']
        and profile['deepseek']['pricing_verified_date'] == cfg['pricing_date']
        and profile.get('approved_loopback_proxy') == 'http://127.0.0.1:7890'
        and profile.get('report_thinking') is False and profile['max_output_tokens'] == 4096
        and profile['cloud_total_token_budget'] == 16384 and profile['max_prompt_utf8_bytes'] == 24_000,
        'profile')
    offline = read(cfg['offline_result_path'])
    require(offline['version'] == 'route8-daily-whole-controls-v1'
        and offline['pairs'] == 8 and offline['whole_versions'] == 16 and offline['new_calls'] == 0
        and file_hash(source / 'src/graphrisk/agent/daily_report_judge.py') ==
        offline['source_python_sha256']['src/graphrisk/agent/daily_report_judge.py'], 'offline_judge_unchanged')
    freeze = read(cfg['offline_freeze_path'])
    require(file_hash(Path(cfg['gold_path'])) == freeze['gold_sha256'], 'gold_changed')
    require(cfg['case_keys'] == KEYS and len(cfg['cases']) == 8, 'eight_case_scope')
    for i, case in enumerate(cfg['cases']):
        require(case['position'] == i and case['case_id'] == KEYS[i], 'case_identity')
        packet, payload = packet_payload(case, profile)
        require(digest(packet) == freeze['packets_sha256'][KEYS[i]]
            and digest(payload) == freeze['payloads_sha256'][KEYS[i]]
            and read(case['payload_path']) == payload and digest(payload) == cfg['payloads'][i]
            and all(cfg['files'].get(case[k]) == file_hash(Path(case[k])) for k in ('packet_path', 'payload_path')),
            'offline_input_changed')


def check_parent(db, cfg):
    row = db.execute('SELECT contract_path,contract_sha,closed FROM rank_successors WHERE campaign_id=?',
                     (cfg['parent_campaign'],)).fetchone()
    require(row is not None and row[2] == 1 and row[1] == cfg['parent_contract_sha']
        and Path(row[0]).resolve() == Path(cfg['parent_contract_path']).resolve()
        and file_hash(Path(row[0])) == row[1], 'parent_binding')
    parent = read(row[0])
    require(parent['version'] == successor.JUDGE_CONTROL_VERSION and parent['max_requests'] == 12
        and parent['cap_micro_cny'] == 5_000_000 and parent['campaign'] == cfg['parent_campaign']
        and parent['expected_identity'] == cfg['expected_identity']
        and parent['deepseek_before'] + 12 == cfg['deepseek_before']
        and parent['attempts_before'] + 12 == cfg['attempts_before']
        and parent['historical_unknowns'] == cfg['historical_unknowns']
        and parent['historical_stops'] == cfg['historical_stops'], 'parent_scope')
    for name in (*PINNED_VERIFIERS, 'daily_reference_comparison_execution.py', 'reference_boundary_execution.py',
                 'judge_events_v13.py', 'judge_control_execution.py'):
        rel = 'src/graphrisk/agent/' + name
        require(file_hash(Path(cfg['source_root']) / rel) == parent['source_python_sha256'][rel], 'parent_verifier_changed')
    require(all(verify_parent_result(db, parent, parent['campaign'], i) for i in range(12)), 'parent_receipts')


def verify_saved_result(db, cfg, campaign, position):
    require(cfg['version'] == successor.DAILY_JUDGE_VERSION and cfg['campaign'] == campaign
        and type(position) is int and 0 <= position < 8, 'saved_scope')
    result = db.execute('SELECT attempt_id,result_path,result_sha FROM rank_successor_results '
                        'WHERE campaign_id=? AND position=?', (campaign, position)).fetchone()
    if result is None: return None
    case, profile = cfg['cases'][position], read(cfg['profile_path'])
    packet, payload = packet_payload(case, profile)
    require(digest(payload) == cfg['payloads'][position], 'saved_payload')
    session = campaign + '-C' + str(position + 1)
    wrapper_sha = digest({'profile_sha256': digest(profile), 'packets': [packet], 'session': session})
    row = db.execute('SELECT a.provider,a.state,r.packet_sha,r.integrity_stop,r.identity,r.receipt_path,r.receipt_sha,'
        'r.result_code,r.request_key,p.manifest_sha,s.manifest_sha,s.max_calls,c.cap_each,c.group_key '
        'FROM model_attempts a JOIN execution_requests r USING(attempt_id) '
        'JOIN execution_phases p USING(phase) JOIN autonomous_phases q USING(phase) '
        'JOIN autonomous_sessions s USING(session_id) JOIN autonomous_campaigns c USING(campaign_id) '
        'WHERE a.attempt_id=? AND s.campaign_id=? AND s.session_id=? AND q.slot=0',
        (result[0], campaign, session)).fetchone()
    require(row is not None and row[:5] == ('deepseek', 'settled', cfg['payloads'][position], 0, cfg['expected_identity'])
        and row[8:] == (digest([wrapper_sha, 'deepseek', '0']), wrapper_sha,
        digest({'campaign': campaign, 'position': position, 'payload_sha': cfg['payloads'][position]}),
        1, 8, successor.GROUP_DAILY_JUDGE), 'saved_request_binding')
    receipt_path, saved_path = Path(row[5]), Path(result[1])
    require(receipt_path.resolve().is_relative_to((Path(cfg['output']) / 'receipts').resolve())
        and file_hash(receipt_path) == row[6]
        and saved_path.resolve() == (Path(cfg['output']) / (case['case_id'] + '-result.json')).resolve()
        and file_hash(saved_path) == result[2], 'saved_files')
    receipt = read(receipt_path)
    require(receipt['attempt_id'] == result[0] and receipt['profile_sha256'] == digest(profile)
        and receipt['payload_sha256'] == cfg['payloads'][position], 'receipt_binding')
    formatter = object.__new__(judge.Provider)
    formatter.provider, formatter.profile = 'deepseek', profile
    analyzed = formatter._analyze(receipt, packet)
    require(analyzed['identity'] == cfg['expected_identity'] and analyzed['code'] == row[7]
        and read(saved_path) == saved_value(case, {**analyzed, 'attempt_id': result[0],
            'replayed_without_model_request': False}), 'saved_result_binding')
    return result[1], result[2]


def saved_value(case, response):
    return {'case_id': case['case_id'], 'provider_result': response, 'first_judgment': True,
            'automatic_retries': 0, 'repairs': 0, 'secondary_judge_calls': 0}


def check(path):
    cfg = successor.read_contract(path)
    require(cfg['version'] == successor.DAILY_JUDGE_VERSION, 'wrong_scope')
    ledger = Path(cfg['runtime']) / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    require(ledger.is_file(), 'existing_shared_ledger_required')
    with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as db:
        successor.check_history(db, cfg)
        check_parent(db, cfg)
        used = db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) '
                          'JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?', (cfg['campaign'],)).fetchone()[0]
        require(0 <= used <= 8, 'used_count')
        successor.check_allocation(db, cfg, used)
        row = db.execute('SELECT contract_path,contract_sha,closed FROM rank_successors WHERE campaign_id=?',
                         (cfg['campaign'],)).fetchone()
        require((not row and used == 0) or (row and Path(row[0]).resolve() == Path(path).resolve()
            and row[1] == file_hash(Path(path)) and (not row[2] or used == 8)), 'closed_or_unregistered_no_resend')
        require(all(verify_saved_result(db, cfg, cfg['campaign'], i) for i in range(used)), 'existing_result_missing')
        reserve = reservation_micro(read(cfg['profile_path'])) if used < 8 else 0
        total = db.execute('SELECT COALESCE(sum(accounted),0) FROM execution_requests').fetchone()[0]
        cost = db.execute('SELECT COALESCE(sum(accounted),0) FROM execution_requests '
            'JOIN autonomous_phases USING(phase) JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?',
            (cfg['campaign'],)).fetchone()[0]
        require(total + reserve <= 80_000_000 and cost + reserve <= 5_000_000, 'cost_cap')
    return cfg, {'status': 'validated_not_sent' if used == 0 else 'verified_existing_judgments',
        'used_in_batch': used, 'max_new_judgments': 8 - used, 'batch_cap_cny': 5,
        'paid_calls_in_preflight': 0, 'current_deepseek_attempts': 1438 + used,
        'current_total_attempts': 1461 + used, 'pricing_current': cfg['pricing_date'] == date.today().isoformat(),
        'requires_new_specific_authorization': True, 'retries': 0, 'repairs': 0, 'secondary_judges': 0}


def evaluate_closed(path):
    cfg, preview = check(path)
    require(preview['used_in_batch'] == 8, 'evaluation_requires_eight_first_attempts')
    ledger = Path(cfg['runtime']) / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    predictions = {}
    with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as db:
        require(db.execute('SELECT closed FROM rank_successors WHERE campaign_id=?',
            (cfg['campaign'],)).fetchone() == (1,), 'evaluation_requires_closed_batch')
        for i, case in enumerate(cfg['cases']):
            verified = verify_saved_result(db, cfg, cfg['campaign'], i)
            response = read(verified[0])['provider_result']
            if response['code'] == 'ok':
                parsed = judge.parse(canonical(response['action']), read(case['packet_path']))
                predictions.update({case['case_id'] + '-' + v.case: v for v in parsed.verdicts})
    result = judge.evaluate(read(cfg['gold_path']), predictions,
        known_reviews=read(cfg['offline_result_path']).get('known_reviews', {}))
    result.update(receipt_authenticated=True, actual_llm_judge='CompletedFirstResponses',
        same_family_bias='not_independent_external_audit')
    return result
