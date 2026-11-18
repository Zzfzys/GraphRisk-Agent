"""Twelve separately approved public-reference answers on the existing ledger."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import sqlite3

from pydantic import ValidationError

from graphrisk.agent import rank_successor as successor
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_http import strict_json
from graphrisk.agent.autonomous_providers import reservation_micro
from graphrisk.agent.public_references import PublicReferenceRetriever, ReferenceContext, load_reference_corpus
from graphrisk.agent.reference_answers import (
    CONTRACT, PROMPT, ReferenceAnswerDraft, ReferenceAnswerService, validate_decision,
)
from graphrisk.agent.reference_planner_execution import ReferencePlannerProvider, read
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash

MAX_PROMPT_UTF8_BYTES = 24_000


def require(condition, code):
    successor.require(condition, 'reference_answer_' + code)


def service_for(cfg):
    return ReferenceAnswerService(PublicReferenceRetriever(cfg['manifest_path'], cfg['corpus_version']))


def request_for(case):
    require(set(case) <= {'case_id', 'query', 'context', 'purpose', 'top_k', 'planner', 'version_label'}
            and {'case_id', 'query', 'context'} <= set(case), 'public_query_fields')
    request = {key: case.get(key, default) for key, default in
               (('query', None), ('context', None), ('purpose', 'reference_explanation'),
                ('top_k', 3), ('planner', 'rules'), ('version_label', None))}
    require(type(request['top_k']) is int and 1 <= request['top_k'] <= 5
            and request['planner'] == 'rules', 'retrieval_scope')
    return request


def prepare(service, request):
    context = ReferenceContext.model_validate_json(canonical(request['context']), strict=True)
    return service.prepare(request['query'], context, purpose=request['purpose'], top_k=request['top_k'],
                           planner=request['planner'], version_label=request['version_label'])


def answer_for(service, packet, response):
    if response['code'] != 'ok':
        return None
    answer = service.finish(packet, response['action'])
    return answer.model_dump(mode='json') if hasattr(answer, 'model_dump') else answer


class ReferenceAnswerProvider(ReferencePlannerProvider):
    def system_prompt(self, packet):
        return PROMPT

    def output_schema(self, packet):
        return ReferenceAnswerDraft.model_json_schema()

    def check_output(self, content, packet):
        try:
            draft = ReferenceAnswerDraft.model_validate_json(canonical(strict_json(content)), strict=True)
            validate_decision(draft, packet)
            return draft
        except (ValidationError, ValueError, TypeError, KeyError):
            raise ActionFailure('reference_answer_draft_invalid') from None


def check_contract(cfg):
    require(cfg.get('version') == successor.ANSWER_VERSION and cfg.get('answer_contract') == CONTRACT, 'version')
    require(cfg.get('max_requests') == 12 and cfg.get('cap_micro_cny') == 5_000_000
            and cfg.get('deepseek_before') == 1406 and cfg.get('attempts_before') == 1429, 'allocation')
    require(all(type(cfg.get(key)) is int and cfg[key] == 0 for key in
                ('retries', 'automatic_retries', 'repairs', 'judge_calls'))
            and cfg.get('pause_cny') == 80 and cfg.get('overall_max_cny') == 100, 'limits')
    require(cfg.get('pricing_required') is False and isinstance(cfg.get('pricing_date'), str)
            and date.fromisoformat(cfg['pricing_date']).isoformat() == cfg['pricing_date'], 'pricing_date')
    require(cfg.get('prompt_sha256') == digest(PROMPT), 'prompt_changed')
    root = Path(cfg['source_root'])
    actual = {p.relative_to(root).as_posix(): file_hash(p)
              for folder in ('src', 'api', 'scripts') for p in (root / folder).rglob('*.py')}
    require(bool(actual) and actual == cfg['source_python_sha256']
            and all(cfg['files'].get(str(root / p)) == sha for p, sha in actual.items()), 'source_changed')
    for path in [root / 'configs/agent_investigation_experiment.json',
                 *(Path(cfg[key]) for key in ('profile_path', 'manifest_path', 'suite_path', 'parent_contract_path'))]:
        require(path.is_file() and cfg['files'].get(str(path)) == file_hash(path), 'file_not_bound')
    profile = read(cfg['profile_path'])
    require(profile['deepseek']['endpoint'] == cfg['endpoint'] and profile['deepseek']['model'] == cfg['model']
            and profile['deepseek']['pricing_verified_date'] == cfg['pricing_date']
            and profile.get('approved_loopback_proxy') == 'http://127.0.0.1:7890'
            and profile.get('report_thinking') is False and profile['max_output_tokens'] == 4096
            and profile['cloud_total_token_budget'] == 16384
            and profile['max_prompt_utf8_bytes'] == MAX_PROMPT_UTF8_BYTES, 'profile')
    manifest, _ = load_reference_corpus(cfg['manifest_path'], cfg['corpus_version'])
    require(all(cfg['files'].get(str(Path(cfg['manifest_path']).parent / doc.snapshot_file)) == doc.snapshot_sha256
                for doc in manifest.documents), 'source_snapshots')
    suite = read(cfg['suite_path'])
    require(set(suite) == {'schema_version', 'corpus_version', 'blind_test', 'cases'}
            and suite['schema_version'] == 'reference-answer-review-queries-v1'
            and suite['corpus_version'] == cfg['corpus_version'] and suite['blind_test'] is False
            and len(suite['cases']) == 12, 'suite')
    keys = [case['case_id'] for case in suite['cases']]
    require(all(isinstance(key, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}', key) for key in keys)
            and len(set(keys)) == 12 and cfg.get('case_keys') == keys and len(cfg.get('cases', [])) == 12, 'case_order')
    service = service_for(cfg)
    formatter = object.__new__(ReferenceAnswerProvider)
    formatter.provider, formatter.profile = 'deepseek', profile
    for position, (case, item) in enumerate(zip(suite['cases'], cfg['cases'])):
        require(item['case_id'] == case['case_id'] and item['position'] == position, 'case_binding')
        request = request_for(case)
        packet = prepare(service, request)
        require(packet['contract'] == CONTRACT and packet['corpus_version'] == cfg['corpus_version']
                and packet['prompt_utf8_bytes'] <= profile['max_prompt_utf8_bytes'], 'prepared_packet')
        payload = formatter.payload(packet)
        for key, expected in (('request_path', request), ('packet_path', packet), ('payload_path', payload)):
            path = Path(item[key])
            require(path.is_file() and cfg['files'].get(str(path)) == file_hash(path)
                    and read(path) == expected, 'outbound_changed')
        require(cfg['payloads'][position] == digest(payload), 'payload_hash')


def check_parent(db, cfg):
    row = db.execute('SELECT contract_path,contract_sha,closed FROM rank_successors WHERE campaign_id=?',
                     (cfg['parent_campaign'],)).fetchone()
    require(row is not None and row[2] == 1 and row[1] == cfg['parent_contract_sha']
            and Path(row[0]).resolve() == Path(cfg['parent_contract_path']).resolve()
            and file_hash(Path(row[0])) == row[1], 'parent_binding')
    parent = read(row[0])
    require(parent['version'] == successor.PLANNER_CONTINUATION_VERSION and parent['max_requests'] == 23
            and parent['campaign'] == cfg['parent_campaign'] and parent['expected_identity'] == cfg['expected_identity']
            and parent['deepseek_before'] + 23 == cfg['deepseek_before']
            and parent['attempts_before'] + 23 == cfg['attempts_before']
            and parent['historical_unknowns'] == cfg['historical_unknowns']
            and parent['historical_stops'] == cfg['historical_stops'], 'parent_scope')
    from graphrisk.agent.reference_planner_execution import check_parent as planner_parent, verify_saved_result
    planner_parent(db, parent)
    count = db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) '
                       'JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?', (parent['campaign'],)).fetchone()[0]
    require(count == 23, 'parent_count')
    historical = [parent[key] for key in ('profile_path', 'manifest_path', 'suite_path',
                                          'parent_contract_path', 'original_authorization_path')]
    historical.extend(path for case in parent['cases'] for path in (case['packet_path'], case['payload_path']))
    for path in historical:
        require(Path(path).is_file() and parent['files'].get(path) == file_hash(Path(path)), 'parent_payload_file')
    for position in range(23):
        require(verify_saved_result(db, parent, parent['campaign'], position) is not None, 'parent_result')


def check_authorization(db, cfg, authorization):
    require(isinstance(authorization, str) and len(authorization.strip()) >= 10, 'new_authorization_required')
    parent = read(cfg['parent_contract_path'])
    old = db.execute('SELECT authorization FROM rank_successors WHERE campaign_id IN (?,?)',
                     (cfg['parent_campaign'], parent['parent_campaign'])).fetchall()
    original = read(parent['original_authorization_path'])['user_text']
    require(authorization.strip() not in {text.strip() for (text,) in old} | {original.strip()},
            'old_query_approval_not_answer_approval')


def verify_saved_result(db, cfg, campaign, position):
    require(cfg['version'] == successor.ANSWER_VERSION and cfg['campaign'] == campaign
            and type(position) is int and 0 <= position < 12, 'saved_scope')
    result = db.execute('SELECT attempt_id,result_path,result_sha FROM rank_successor_results '
                        'WHERE campaign_id=? AND position=?', (campaign, position)).fetchone()
    if result is None:
        return None
    row = db.execute('SELECT a.state,r.packet_sha,r.integrity_stop,r.identity,r.receipt_sha,r.receipt_path,r.result_code '
                     'FROM model_attempts a JOIN execution_requests r USING(attempt_id) JOIN autonomous_phases p USING(phase) '
                     'JOIN autonomous_sessions s USING(session_id) WHERE a.attempt_id=? AND s.campaign_id=? AND s.session_id=?',
                     (result[0], campaign, campaign + '-C' + str(position + 1))).fetchone()
    require(row is not None and row[:4] == ('settled', cfg['payloads'][position], 0, cfg['expected_identity'])
            and row[6] in ('ok', 'invalid_model_action', 'output_truncated_or_invalid'), 'saved_attempt')
    require(row[5] and Path(row[5]).is_file() and file_hash(Path(row[5])) == row[4], 'saved_receipt')
    profile, packet = read(cfg['profile_path']), read(cfg['cases'][position]['packet_path'])
    receipt = read(row[5])
    require(receipt['attempt_id'] == result[0] and receipt['payload_sha256'] == cfg['payloads'][position]
            and receipt['profile_sha256'] == digest(profile), 'receipt_binding')
    formatter = object.__new__(ReferenceAnswerProvider)
    formatter.provider, formatter.profile = 'deepseek', profile
    analyzed = formatter._analyze(receipt, packet)
    require(analyzed['code'] == row[6] and analyzed['identity'] == cfg['expected_identity']
            and not analyzed['integrity_stop'], 'receipt_result')
    require(Path(result[1]).is_file() and file_hash(Path(result[1])) == result[2], 'saved_result')
    saved = read(result[1])
    response = saved['provider_result']
    require(saved['case_id'] == cfg['case_keys'][position] and response['attempt_id'] == result[0]
            and all(response[key] == analyzed[key] for key in
                    ('code', 'action', 'identity', 'usage', 'estimated_peak_cost_micro_cny', 'schema_and_binding_valid'))
            and saved['answer'] == answer_for(service_for(cfg), packet, analyzed), 'saved_answer_binding')
    return result[1], result[2]


def check(path):
    """Read-only validation never registers a campaign or constructs a live budget."""
    cfg = successor.read_contract(path)
    require(cfg['version'] == successor.ANSWER_VERSION, 'wrong_execution_scope')
    ledger = Path(cfg['runtime']) / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    require(ledger.is_file(), 'existing_shared_ledger_required')
    with sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True) as db:
        successor.check_history(db, cfg)
        check_parent(db, cfg)
        used = db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) '
                          'JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?', (cfg['campaign'],)).fetchone()[0]
        successor.check_allocation(db, cfg, used)
        require(0 <= used <= 12, 'used_count')
        for position in range(used):
            require(verify_saved_result(db, cfg, cfg['campaign'], position) is not None, 'existing_result')
        accounted = db.execute('SELECT COALESCE(sum(accounted),0) FROM execution_requests').fetchone()[0]
        reserve = reservation_micro(read(cfg['profile_path'])) if used < 12 else 0
        require(accounted + reserve <= 80_000_000, 'global_cost')
        batch_cost = db.execute('SELECT COALESCE(sum(accounted),0) FROM execution_requests JOIN autonomous_phases USING(phase) '
                               'JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?', (cfg['campaign'],)).fetchone()[0]
        require(batch_cost + reserve <= cfg['cap_micro_cny'], 'batch_cost')
    return cfg, {'status': 'validated_not_sent', 'paid_calls_in_preflight': 0, 'used_in_batch': used,
                 'max_new_requests': 12 - used, 'current_deepseek_attempts': 1406 + used,
                 'current_total_attempts': 1429 + used, 'batch_cap_cny': 5,
                 'pricing_current': cfg['pricing_date'] == date.today().isoformat(),
                 'requires_new_specific_answer_authorization': True, 'semantic_review': 'NotRun'}
