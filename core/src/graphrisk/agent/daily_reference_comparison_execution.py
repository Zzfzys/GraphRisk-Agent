"""Two separately approved reference selections for six frozen report pairs."""
from __future__ import annotations

from contextlib import closing
from datetime import date
from pathlib import Path
import sqlite3

from graphrisk.agent import rank_successor as successor
from graphrisk.agent.autonomous_providers import reservation_micro
from graphrisk.agent.public_references import load_reference_corpus
from graphrisk.agent.reference_answer_execution import (
    MAX_PROMPT_UTF8_BYTES, ReferenceAnswerProvider, answer_for, prepare, request_for,
    service_for, verify_saved_result as verify_answer_result,
)
from graphrisk.agent.reference_answers import CONTRACT, PROMPT
from graphrisk.agent.reference_planner_execution import read
from graphrisk.agent.upgrade_contract import digest, file_hash


CASE_KEYS = ['Q1', 'Q2']
PINNED_VERIFIERS = ('reference_answers.py', 'reference_answer_execution.py',
                    'public_references.py', 'autonomous_providers.py',
                    'autonomous_http.py', 'upgrade_contract.py')


def require(condition, code):
    successor.require(condition, 'daily_reference_comparison_' + code)


def check_contract(cfg):
    """Rebuild the two unique payloads, never widen the old twelve-call scope."""
    from graphrisk.serving.daily_reference_comparison import verify_comparison

    require(cfg.get('version') == successor.CASE_REFERENCE_VERSION
            and cfg.get('answer_contract') == CONTRACT, 'version')
    require(all(type(cfg.get(key)) is int for key in
                ('max_requests', 'cap_micro_cny', 'deepseek_before', 'attempts_before'))
            and cfg.get('max_requests') == 2 and cfg.get('cap_micro_cny') == 5_000_000
            and cfg.get('deepseek_before') == 1418 and cfg.get('attempts_before') == 1441,
            'allocation')
    require(all(type(cfg.get(key)) is int and cfg[key] == 0 for key in
                ('retries', 'automatic_retries', 'repairs', 'judge_calls'))
            and cfg.get('pause_cny') == 80 and cfg.get('overall_max_cny') == 100, 'limits')
    require(cfg.get('pricing_required') is False and cfg.get('pricing_date') == '2026-11-13',
            'pricing_date')
    require(cfg.get('prompt_sha256') == digest(PROMPT), 'prompt_changed')
    root = Path(cfg['source_root'])
    actual = {path.relative_to(root).as_posix(): file_hash(path)
              for folder in ('src', 'api', 'scripts') for path in (root / folder).rglob('*.py')}
    require(bool(actual) and actual == cfg['source_python_sha256']
            and all(cfg['files'].get(str(root / name)) == sha for name, sha in actual.items()),
            'source_changed')
    for path in (root / 'configs/agent_investigation_experiment.json',
                 *(Path(cfg[key]) for key in ('profile_path', 'manifest_path',
                                              'parent_contract_path', 'paired_manifest_path'))):
        require(path.is_file() and cfg['files'].get(str(path)) == file_hash(path), 'file_not_bound')
    profile = read(cfg['profile_path'])
    require(profile['deepseek']['endpoint'] == cfg['endpoint']
            and profile['deepseek']['model'] == cfg['model']
            and profile['deepseek']['pricing_verified_date'] == cfg['pricing_date']
            and profile.get('approved_loopback_proxy') == 'http://127.0.0.1:7890'
            and profile.get('report_thinking') is False and profile['max_output_tokens'] == 4096
            and profile['cloud_total_token_budget'] == 16384
            and profile['max_prompt_utf8_bytes'] == MAX_PROMPT_UTF8_BYTES, 'profile')
    manifest, _ = load_reference_corpus(cfg['manifest_path'], cfg['corpus_version'])
    require(all(cfg['files'].get(str(Path(cfg['manifest_path']).parent / doc.snapshot_file))
                == doc.snapshot_sha256 for doc in manifest.documents), 'source_snapshots')
    paired_path = Path(cfg['paired_manifest_path'])
    require(file_hash(paired_path) == cfg.get('paired_manifest_sha256'), 'paired_manifest_hash')
    paired = verify_comparison(paired_path, expected_sha256=cfg['paired_manifest_sha256'])
    require(all(Path(paired[key]).resolve() == Path(cfg[key]).resolve()
                for key in ('profile_path', 'manifest_path'))
            and paired['corpus_version'] == cfg['corpus_version']
            and all(cfg['files'].get(path) == sha for path, sha in paired['files'].items()),
            'paired_inputs')
    require(cfg.get('case_keys') == CASE_KEYS and len(cfg.get('cases', [])) == 2
            and [query['case_id'] for query in paired['queries']] == CASE_KEYS
            and paired['denominators'] == {'model_selections': 2, 'report_displays': 6,
                                           'paired_readings': 6}, 'unique_query_scope')
    service = service_for(cfg)
    formatter = object.__new__(ReferenceAnswerProvider)
    formatter.provider, formatter.profile = 'deepseek', profile
    for position, (item, query) in enumerate(zip(cfg['cases'], paired['queries'])):
        require(set(item) == {'case_id', 'position', 'request_path', 'packet_path', 'payload_path'}
                and item['case_id'] == CASE_KEYS[position] and item['position'] == position
                and all(item[key] == query[key] for key in item), 'case_binding')
        request = read(item['request_path'])
        require(set(request) == {'query', 'context', 'purpose', 'top_k', 'planner',
                                 'supplied_plan', 'version_label'}
                and request['supplied_plan'] is None, 'request_fields')
        projected = request_for({'case_id': item['case_id'],
                                **{key: value for key, value in request.items() if key != 'supplied_plan'}})
        packet = prepare(service, projected)
        require(packet['request'] == request, 'request_fields')
        require(packet['contract'] == CONTRACT and packet['corpus_version'] == cfg['corpus_version']
                and packet['prompt_utf8_bytes'] <= profile['max_prompt_utf8_bytes'], 'prepared_packet')
        payload = formatter.payload(packet)
        for kind, expected in (('request', request), ('packet', packet), ('payload', payload)):
            path = Path(item[kind + '_path'])
            require(path.is_file() and cfg['files'].get(str(path)) == file_hash(path)
                    and read(path) == expected and query[kind + '_sha256'] == digest(expected),
                    'outbound_changed')
        require(cfg['payloads'][position] == digest(payload), 'payload_hash')


def check_parent(db, cfg):
    """Authenticate the closed old execution, not its obsolete whole-source inventory."""
    row = db.execute('SELECT contract_path,contract_sha,closed FROM rank_successors WHERE campaign_id=?',
                     (cfg['parent_campaign'],)).fetchone()
    require(row is not None and row[2] == 1 and row[1] == cfg['parent_contract_sha']
            and Path(row[0]).resolve() == Path(cfg['parent_contract_path']).resolve()
            and file_hash(Path(row[0])) == row[1], 'parent_binding')
    parent = read(row[0])
    require(parent['version'] == successor.ANSWER_VERSION and parent['max_requests'] == 12
            and parent['cap_micro_cny'] == 5_000_000 and parent['campaign'] == cfg['parent_campaign']
            and parent['expected_identity'] == cfg['expected_identity']
            and parent['deepseek_before'] + 12 == cfg['deepseek_before']
            and parent['attempts_before'] + 12 == cfg['attempts_before']
            and parent['historical_unknowns'] == cfg['historical_unknowns']
            and parent['historical_stops'] == cfg['historical_stops'], 'parent_scope')
    for name in PINNED_VERIFIERS:
        relative = 'src/graphrisk/agent/' + name
        expected = parent['source_python_sha256'][relative]
        require(file_hash(Path(parent['source_root']) / relative) == expected
                and file_hash(Path(cfg['source_root']) / relative) == expected, 'parent_verifier_changed')
    paths = [parent[key] for key in ('profile_path', 'manifest_path', 'suite_path')]
    paths.extend(path for item in parent['cases'] for path in
                 (item['request_path'], item['packet_path'], item['payload_path']))
    for path in paths:
        require(Path(path).is_file() and parent['files'].get(path) == file_hash(Path(path)),
                'parent_input_changed')
    corpus, _ = load_reference_corpus(parent['manifest_path'], parent['corpus_version'])
    require(all(parent['files'].get(str(Path(parent['manifest_path']).parent / doc.snapshot_file))
                == doc.snapshot_sha256 for doc in corpus.documents), 'parent_source_changed')
    count = db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) '
                       'JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?',
                       (parent['campaign'],)).fetchone()[0]
    require(count == 12, 'parent_count')
    for position in range(12):
        require(verify_answer_result(db, parent, parent['campaign'], position) is not None,
                'parent_result')


def check_authorization(db, cfg, authorization):
    require(isinstance(authorization, str) and len(authorization.strip()) >= 10,
            'new_specific_authorization_required')
    old = db.execute('SELECT authorization FROM rank_successors WHERE campaign_id!=?',
                     (cfg['campaign'],)).fetchall()
    require(authorization.strip() not in {text.strip() for (text,) in old},
            'old_registered_authorization_not_case_approval')


def verify_saved_result(db, cfg, campaign, position):
    require(cfg['version'] == successor.CASE_REFERENCE_VERSION and cfg['max_requests'] == 2
            and cfg['campaign'] == campaign and type(position) is int and 0 <= position < 2,
            'saved_scope')
    result = db.execute('SELECT attempt_id,result_path,result_sha FROM rank_successor_results '
                        'WHERE campaign_id=? AND position=?', (campaign, position)).fetchone()
    if result is None:
        return None
    session = campaign + '-C' + str(position + 1)
    profile, packet = read(cfg['profile_path']), read(cfg['cases'][position]['packet_path'])
    wrapper_sha = digest({'profile_sha256': digest(profile), 'packets': [packet], 'session': session})
    row = db.execute('SELECT a.provider,r.request_key,r.phase,p.manifest_sha,s.manifest_sha,'
                     's.max_calls,c.cap_each,c.group_key FROM model_attempts a '
                     'JOIN execution_requests r USING(attempt_id) JOIN execution_phases p USING(phase) '
                     'JOIN autonomous_phases q USING(phase) JOIN autonomous_sessions s USING(session_id) '
                     'JOIN autonomous_campaigns c USING(campaign_id) WHERE a.attempt_id=?',
                     (result[0],)).fetchone()
    require(row == ('deepseek', digest([wrapper_sha, 'deepseek', '0']), 'loop-' + session + '-0',
                    wrapper_sha, digest({'campaign': campaign, 'position': position,
                                         'payload_sha': cfg['payloads'][position]}),
                    1, 2, successor.GROUP_CASE_REFERENCE), 'saved_request_binding')
    # Reuse only the old pure receipt/action/answer verifier. This temporary version
    # adapter is never registered or used to authorize a request or widen old bounds.
    verified = verify_answer_result(db, {**cfg, 'version': successor.ANSWER_VERSION}, campaign, position)
    saved = read(verified[0])
    require(saved['first_draft'] is True and saved['provider_receipt_bound'] is True
            and all(type(saved[key]) is int and saved[key] == 0 for key in
                    ('automatic_retries', 'repairs', 'paid_judge_calls')), 'saved_first_draft')
    expected_path = Path(cfg['output']) / (cfg['case_keys'][position] + '-result.json')
    require(Path(verified[0]).resolve() == expected_path.resolve(), 'saved_path_changed')
    return verified


def verified_results_by_query(db, cfg, contract_sha256):
    """Only a complete closed batch can supply answers to the local pair renderer."""
    row = db.execute('SELECT contract_sha,closed FROM rank_successors WHERE campaign_id=?',
                     (cfg['campaign'],)).fetchone()
    require(row == (contract_sha256, 1), 'selection_batch_not_closed')
    count = db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) '
                       'JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?',
                       (cfg['campaign'],)).fetchone()[0]
    require(count == 2, 'selection_batch_incomplete')
    results = {}
    for position, query_id in enumerate(CASE_KEYS):
        prior = verify_saved_result(db, cfg, cfg['campaign'], position)
        require(prior is not None, 'selection_result_missing')
        saved = read(prior[0])
        response = saved['provider_result']
        receipt = db.execute('SELECT receipt_path,receipt_sha FROM execution_requests WHERE attempt_id=?',
                             (response['attempt_id'],)).fetchone()
        results[query_id] = {'query_id': query_id,
            'packet_sha256': digest(read(cfg['cases'][position]['packet_path'])),
            'payload_sha256': cfg['payloads'][position], 'answer': saved['answer'],
            'result_code': response['code'], 'execution_binding': {
                'campaign': cfg['campaign'], 'position': position, 'attempt_id': response['attempt_id'],
                'result_path': prior[0], 'result_sha256': prior[1], 'receipt_path': receipt[0],
                'receipt_sha256': receipt[1], 'contract_sha256': contract_sha256}}
    return results


def check(path):
    """Read-only preflight: no credentials, budget construction or ledger writes."""
    cfg = successor.read_contract(path)
    require(cfg['version'] == successor.CASE_REFERENCE_VERSION, 'wrong_execution_scope')
    ledger = Path(cfg['runtime']) / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    require(ledger.is_file(), 'existing_shared_ledger_required')
    with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as db:
        successor.check_history(db, cfg)
        check_parent(db, cfg)
        used = db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) '
                          'JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?',
                          (cfg['campaign'],)).fetchone()[0]
        require(0 <= used <= 2, 'used_count')
        successor.check_allocation(db, cfg, used)
        registered = db.execute('SELECT contract_path,contract_sha,closed FROM rank_successors '
                                'WHERE campaign_id=?', (cfg['campaign'],)).fetchone()
        if registered:
            require(Path(registered[0]).resolve() == Path(path).resolve()
                    and registered[1] == file_hash(Path(path)), 'registered_contract_changed')
            require(not registered[2] or used == 2, 'closed_incomplete_no_resend')
        else:
            require(used == 0, 'unregistered_attempts_no_resend')
        for position in range(used):
            require(verify_saved_result(db, cfg, cfg['campaign'], position) is not None, 'existing_result')
        reserve = reservation_micro(read(cfg['profile_path'])) if used < 2 else 0
        accounted = db.execute('SELECT COALESCE(sum(accounted),0) FROM execution_requests').fetchone()[0]
        require(accounted + reserve <= 80_000_000, 'global_cost')
        batch_cost = db.execute('SELECT COALESCE(sum(accounted),0) FROM execution_requests '
                               'JOIN autonomous_phases USING(phase) JOIN autonomous_sessions USING(session_id) '
                               'WHERE campaign_id=?', (cfg['campaign'],)).fetchone()[0]
        require(batch_cost + reserve <= cfg['cap_micro_cny'], 'batch_cost')
    return cfg, {'status': 'validated_not_sent', 'paid_calls_in_preflight': 0, 'used_in_batch': used,
        'max_new_requests': 2 - used, 'current_deepseek_attempts': 1418 + used,
        'current_total_attempts': 1441 + used, 'batch_cap_cny': 5,
        'pricing_current': cfg['pricing_date'] == date.today().isoformat(),
        'requires_new_specific_case_authorization': True, 'model_selections': 2,
        'paired_readings': 6, 'semantic_review': 'NotRun'}
