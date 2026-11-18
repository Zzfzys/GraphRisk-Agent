"""A separately approved six-request boundary probe; no report/model changes."""
from contextlib import closing
from datetime import date
from pathlib import Path
import sqlite3

from graphrisk.agent import rank_successor as successor
from graphrisk.agent.autonomous_providers import reservation_micro
from graphrisk.agent.daily_reference_comparison_execution import (
    PINNED_VERIFIERS, check_authorization, verified_results_by_query,
)
from graphrisk.agent.public_references import PublicReferenceRetriever, load_reference_corpus
from graphrisk.agent.reference_answer_execution import ReferenceAnswerProvider, prepare, request_for
from graphrisk.agent.reference_answers import ReferenceAnswerService
from graphrisk.agent.reference_planner_execution import ReferencePlannerProvider, packet_for, read
from graphrisk.agent.upgrade_contract import digest, file_hash


CASE_KEYS = ['P0', 'P1', 'A0', 'A1', 'A2', 'F1']
KINDS = ['planner', 'planner', 'answer', 'answer', 'answer', 'answer']


def require(ok, code):
    successor.require(ok, 'reference_boundary_' + code)


def provider_class(kind):
    require(kind in ('planner', 'answer'), 'request_kind')
    return ReferencePlannerProvider if kind == 'planner' else ReferenceAnswerProvider


def service_for_case(case):
    return ReferenceAnswerService(PublicReferenceRetriever(case['manifest_path'], case['corpus_version']))


def prepare_case(case, profile):
    request = read(case['request_path'])
    if case['kind'] == 'planner':
        require(set(request) == {'query'}, 'planner_request_fields')
        manifest, _ = load_reference_corpus(case['manifest_path'], case['corpus_version'])
        packet = packet_for(request['query'], manifest, profile)
    else:
        require(set(request) == {'query', 'context', 'purpose', 'top_k', 'planner',
                                 'supplied_plan', 'version_label'} and request['supplied_plan'] is None,
                'answer_request_fields')
        projected = request_for({'case_id': case['case_id'], **{k: v for k, v in request.items()
                                                               if k != 'supplied_plan'}})
        packet = prepare(service_for_case(case), projected)
    formatter = object.__new__(provider_class(case['kind']))
    formatter.provider, formatter.profile = 'deepseek', profile
    return packet, formatter.payload(packet)


def selection_for(case, packet, response):
    if response['code'] != 'ok':
        return None
    return response['action'] if case['kind'] == 'planner' else service_for_case(case).finish(packet, response['action'])


def check_contract(cfg):
    require(cfg['version'] == successor.BOUNDARY_VERSION, 'version')
    require(all(type(cfg.get(k)) is int for k in ('max_requests', 'cap_micro_cny', 'deepseek_before', 'attempts_before'))
            and (cfg['max_requests'], cfg['cap_micro_cny'], cfg['deepseek_before'], cfg['attempts_before'])
            == (6, 5_000_000, 1420, 1443), 'exact_allocation')
    require(all(type(cfg.get(k)) is int and cfg[k] == 0 for k in ('retries', 'automatic_retries', 'repairs', 'judge_calls'))
            and cfg['pause_cny'] == 80 and cfg['overall_max_cny'] == 100, 'limits')
    require(cfg['pricing_required'] is False and cfg['pricing_date'] == '2026-11-13', 'pricing')
    source = Path(cfg['source_root'])
    actual = {p.relative_to(source).as_posix(): file_hash(p)
              for folder in ('src', 'api', 'scripts') for p in (source / folder).rglob('*.py')}
    require(bool(actual) and actual == cfg['source_python_sha256'] and all(
        cfg['files'].get(str(source / rel)) == sha for rel, sha in actual.items()), 'source_changed')
    require(cfg['files'].get(str(source / 'configs/agent_investigation_experiment.json')) ==
            file_hash(source / 'configs/agent_investigation_experiment.json'), 'budget_config')
    profile = read(cfg['profile_path'])
    require(profile['deepseek']['endpoint'] == cfg['endpoint'] and profile['deepseek']['model'] == cfg['model']
            and profile['deepseek']['pricing_verified_date'] == cfg['pricing_date']
            and profile.get('approved_loopback_proxy') == 'http://127.0.0.1:7890'
            and profile.get('report_thinking') is False and profile['max_output_tokens'] == 4096
            and profile['cloud_total_token_budget'] == 16384 and profile['max_prompt_utf8_bytes'] == 24_000,
            'profile')
    require(cfg['case_keys'] == CASE_KEYS and len(cfg['cases']) == 6
            and [c['kind'] for c in cfg['cases']] == KINDS, 'six_case_scope')
    for i, case in enumerate(cfg['cases']):
        require(case['position'] == i and case['case_id'] == CASE_KEYS[i]
                and case['data_kind'] in ('public_reference', 'controlled_adversarial_fixture',
                                          'controlled_conflict_fixture'), 'case_identity')
        require(cfg['files'].get(case['manifest_path']) == file_hash(Path(case['manifest_path'])), 'corpus_binding')
        corpus, _ = load_reference_corpus(case['manifest_path'], case['corpus_version'])
        require(all(cfg['files'].get(str(Path(case['manifest_path']).parent / doc.snapshot_file)) == doc.snapshot_sha256
                    for doc in corpus.documents), 'source_snapshots')
        packet, payload = prepare_case(case, profile)
        require(packet['prompt_utf8_bytes'] <= 24_000, 'prompt_limit')
        for key, value in (('packet', packet), ('payload', payload)):
            path = case[key + '_path']
            require(read(path) == value and cfg['files'].get(path) == file_hash(Path(path)), 'outbound_changed')
        require(cfg['files'].get(case['request_path']) == file_hash(Path(case['request_path']))
                and digest(payload) == cfg['payloads'][i], 'request_changed')


def check_parent(db, cfg):
    row = db.execute('SELECT contract_path,contract_sha,closed FROM rank_successors WHERE campaign_id=?',
                     (cfg['parent_campaign'],)).fetchone()
    require(row is not None and row[2] == 1 and row[1] == cfg['parent_contract_sha']
            and Path(row[0]).resolve() == Path(cfg['parent_contract_path']).resolve()
            and file_hash(Path(row[0])) == row[1], 'parent_binding')
    parent = read(row[0])
    require(parent['version'] == successor.CASE_REFERENCE_VERSION and parent['max_requests'] == 2
            and parent['cap_micro_cny'] == 5_000_000 and parent['expected_identity'] == cfg['expected_identity']
            and parent['deepseek_before'] + 2 == cfg['deepseek_before']
            and parent['attempts_before'] + 2 == cfg['attempts_before']
            and parent['historical_unknowns'] == cfg['historical_unknowns']
            and parent['historical_stops'] == cfg['historical_stops'], 'parent_scope')
    for name in (*PINNED_VERIFIERS, 'daily_reference_comparison_execution.py'):
        relative = 'src/graphrisk/agent/' + name
        require(file_hash(Path(cfg['source_root']) / relative) == parent['source_python_sha256'][relative],
                'parent_verifier_changed')
    verified_results_by_query(db, parent, row[1])


def verify_saved_result(db, cfg, campaign, position):
    require(cfg['version'] == successor.BOUNDARY_VERSION and cfg['campaign'] == campaign
            and cfg['max_requests'] == 6 and type(position) is int and 0 <= position < 6, 'saved_scope')
    result = db.execute('SELECT attempt_id,result_path,result_sha FROM rank_successor_results '
                        'WHERE campaign_id=? AND position=?', (campaign, position)).fetchone()
    if result is None:
        return None
    case, profile = cfg['cases'][position], read(cfg['profile_path'])
    session = campaign + '-C' + str(position + 1)
    packet, payload = prepare_case(case, profile)
    require(read(case['packet_path']) == packet and digest(payload) == cfg['payloads'][position], 'saved_packet')
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
            1, 6, successor.GROUP_BOUNDARY), 'saved_request_binding')
    receipt_path, saved_path = Path(row[5]), Path(result[1])
    require(receipt_path.is_relative_to(Path(cfg['output']) / 'receipts')
            and file_hash(receipt_path) == row[6]
            and saved_path.resolve() == (Path(cfg['output']) / (case['case_id'] + '-result.json')).resolve()
            and file_hash(saved_path) == result[2], 'saved_files')
    receipt = read(receipt_path)
    require(receipt['attempt_id'] == result[0] and receipt['profile_sha256'] == digest(profile)
            and receipt['payload_sha256'] == cfg['payloads'][position], 'receipt_binding')
    formatter = object.__new__(provider_class(case['kind']))
    formatter.provider, formatter.profile = 'deepseek', profile
    analyzed = formatter._analyze(receipt, packet)
    saved = read(saved_path)
    require(analyzed['identity'] == cfg['expected_identity'] and analyzed['code'] == row[7]
        and saved == {'case_id': case['case_id'], 'kind': case['kind'],
            'provider_result': {**analyzed, 'attempt_id': result[0], 'replayed_without_model_request': False},
            'selection': selection_for(case, packet, analyzed), 'first_draft': True,
            'automatic_retries': 0, 'repairs': 0, 'paid_judge_calls': 0}, 'saved_result_binding')
    return result[1], result[2]


def check(path):
    cfg = successor.read_contract(path)
    require(cfg['version'] == successor.BOUNDARY_VERSION, 'wrong_scope')
    ledger = Path(cfg['runtime']) / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    require(ledger.is_file(), 'existing_shared_ledger_required')
    with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as db:
        successor.check_history(db, cfg)
        check_parent(db, cfg)
        used = db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) '
                          'JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?', (cfg['campaign'],)).fetchone()[0]
        require(0 <= used <= 6, 'used_count')
        successor.check_allocation(db, cfg, used)
        registered = db.execute('SELECT contract_path,contract_sha,closed FROM rank_successors WHERE campaign_id=?',
                                (cfg['campaign'],)).fetchone()
        require((not registered and used == 0) or (registered and
            Path(registered[0]).resolve() == Path(path).resolve() and registered[1] == file_hash(Path(path))
            and (not registered[2] or used == 6)), 'closed_or_unregistered_no_resend')
        for i in range(used):
            require(verify_saved_result(db, cfg, cfg['campaign'], i) is not None, 'existing_result_missing')
        reserve = reservation_micro(read(cfg['profile_path'])) if used < 6 else 0
        accounted = db.execute('SELECT COALESCE(sum(accounted),0) FROM execution_requests').fetchone()[0]
        batch_cost = db.execute('SELECT COALESCE(sum(accounted),0) FROM execution_requests '
            'JOIN autonomous_phases USING(phase) JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?',
            (cfg['campaign'],)).fetchone()[0]
        require(accounted + reserve <= 80_000_000 and batch_cost + reserve <= 5_000_000, 'cost_cap')
    return cfg, {'status': 'validated_not_sent' if used == 0 else 'verified_existing_results',
        'used_in_batch': used, 'max_new_requests': 6 - used, 'batch_cap_cny': 5,
        'paid_calls_in_preflight': 0, 'current_deepseek_attempts': 1420 + used,
        'current_total_attempts': 1443 + used, 'pricing_current': cfg['pricing_date'] == date.today().isoformat(),
        'requires_new_specific_authorization': True, 'retries': 0, 'repairs': 0, 'paid_judges': 0}
