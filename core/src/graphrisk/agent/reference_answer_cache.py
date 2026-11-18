"""Read a closed, reviewed public answer without authorizing another model call."""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from pathlib import Path
import re
import sqlite3

from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.reference_answer_execution import service_for, verify_saved_result
from graphrisk.agent.reference_planner_execution import read
from graphrisk.agent.upgrade_contract import digest, file_hash


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def load_reviewed_reference_cache(path) -> dict:
    """The path is trusted server configuration, never supplied by the browser.

    Authenticate the original closed execution, not obsolete whole-source bytes
    against a newer UI checkout. The actual verification/source code remains
    pinned, as do the original request, response, receipt and reading record.
    """
    try:
        return _load(Path(path))
    except (KeyError, TypeError, OSError, sqlite3.Error, ValueError, ActionFailure, ExecutionStop) as error:
        raise ValueError('reviewed_public_reference_cache_unavailable_or_changed') from error


def _load(path: Path) -> dict:
    cache = read(path)
    _require(set(cache) == {'version', 'execution_path', 'execution_sha256', 'case_id',
                           'review_path', 'review_sha256'}
             and cache['version'] == 'reviewed-public-answer-cache-v1', 'invalid_reference_cache')
    execution = Path(cache['execution_path'])
    _require(file_hash(execution) == cache['execution_sha256'], 'reference_execution_changed')
    cfg = read(execution)
    _require(cfg['version'] == 'reference-answer-support-v1' and cfg['max_requests'] == 12
             and cfg['cap_micro_cny'] == 5_000_000
             and len(cfg['case_keys']) == len(cfg['cases']) == len(set(cfg['case_keys'])) == 12
             and cfg['endpoint'] == 'https://api.deepseek.com/chat/completions', 'wrong_reference_execution')
    review_path = Path(cache['review_path'])
    _require(file_hash(review_path) == cache['review_sha256'], 'reference_review_changed')
    review = read(review_path)
    _require(review['schema_version'] == 'route7-public-answer-reading-v1'
             and review['execution_contract_sha256'] == cache['execution_sha256']
             and review['reviewer_type'] == 'independent_assistant_reading'
             and file_hash(Path(review['review_note_path'])) == review['review_note_sha256'],
             'unbound_reference_reading')
    # UI/backend evolution must not silently change the meaning of old receipts.
    for name in ('reference_answers.py', 'reference_answer_execution.py', 'public_references.py',
                 'autonomous_providers.py', 'autonomous_http.py', 'upgrade_contract.py'):
        rel = 'src/graphrisk/agent/' + name
        _require(file_hash(Path(cfg['source_root']) / rel) == cfg['source_python_sha256'][rel],
                 'reference_verifier_changed')
    matches = [i for i, key in enumerate(cfg['case_keys']) if key == cache['case_id']]
    _require(len(matches) == 1, 'reference_case_not_registered')
    position = matches[0]
    item = cfg['cases'][position]
    for file in (cfg['profile_path'], cfg['manifest_path'],
                 item['request_path'], item['packet_path'], item['payload_path']):
        _require(cfg['files'].get(file) == file_hash(Path(file)), 'reference_inputs_changed')
    ledger = Path(cfg['runtime']) / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    _require(ledger.is_file(), 'reference_shared_ledger_missing')
    with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as db:
        _require(db.execute('SELECT contract_sha,closed FROM rank_successors WHERE campaign_id=?',
                            (cfg['campaign'],)).fetchone() == (cache['execution_sha256'], 1),
                 'reference_batch_not_closed')
        count = db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) '
                           'JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?',
                           (cfg['campaign'],)).fetchone()[0]
        _require(count == 12, 'reference_batch_incomplete')
        saved = verify_saved_result(db, cfg, cfg['campaign'], position)
        _require(saved is not None, 'reference_response_missing')
        value = read(saved[0])
        _require(value['provider_result']['code'] == 'ok', 'reference_selection_not_valid')
        receipt = db.execute('SELECT receipt_sha FROM execution_requests WHERE attempt_id=?',
                             (value['provider_result']['attempt_id'],)).fetchone()
        _require(receipt is not None and receipt[0], 'reference_receipt_missing')
    answer = value['answer']
    cases = [case for case in review['cases'] if case['case_id'] == cache['case_id']]
    _require(len(cases) == 1, 'reference_case_reading_missing')
    case = cases[0]
    _require(case['reading_status'] == 'acceptable_for_reference' and case['result_sha256'] == saved[1]
             and case['answer_sha256'] == answer['answer_sha256']
             and case['packet_sha256'] == answer['packet_sha256'], 'reference_reading_not_for_this_answer')
    service = service_for(cfg)
    _require(service.verify_saved(answer) == answer, 'reference_answer_changed')
    packet = read(item['packet_path'])
    _require(answer['packet_sha256'] == digest(packet), 'reference_packet_changed')
    return {'service': service, 'packet': packet, 'answer': deepcopy(answer),
            'provenance': {'cache_sha256': file_hash(path),
                'execution_contract_sha256': cache['execution_sha256'], 'case_id': cache['case_id'],
                'result_sha256': saved[1], 'receipt_sha256': receipt[0],
                'review_sha256': cache['review_sha256'], 'reviewer_type': review['reviewer_type'],
                'reading_status': case['reading_status'], 'user_acceptance': review['user_acceptance'],
                'model_identity': cfg['expected_identity']}}


def verify_reference_sidecar(value: dict, cache_path) -> dict:
    """Use the same trusted cache on the UI/export path; never trust reply paths."""
    try:
        reference = value['public_reference']
        evidence = value['evidence']
        loaded = load_reviewed_reference_cache(cache_path)
        packet = loaded['packet']
        request = packet['request']
        expected = {'version': 'daily-public-reference-sidecar-v1',
            'case_alias': value['case_alias'], 'evidence_version': evidence['evidence_version'],
            'case_binding_sha256': value['reference_case_binding_sha256'],
            'query_sha256': digest(request['query']), 'context_sha256': digest(request['context']),
            'request_sha256': digest(request), 'packet_sha256': digest(packet),
            'context_role': 'public_question_scope_not_case_facts',
            'reference_provenance': loaded['provenance'], 'answer': loaded['answer']}
        _require(set(reference) == set(expected) | {'sidecar_sha256'}
                 and all(reference[key] == expected_value for key, expected_value in expected.items())
                 and isinstance(reference['case_binding_sha256'], str)
                 and re.fullmatch(r'[a-f0-9]{64}', reference['case_binding_sha256']) is not None
                 and evidence['case_alias'] == value['case_alias']
                 and evidence['policy_as_of'] == request['context']['as_of']
                 and reference['sidecar_sha256'] == digest({k: v for k, v in reference.items() if k != 'sidecar_sha256'}),
                 'public_reference_sidecar_changed')
        return deepcopy(reference)
    except (KeyError, TypeError, ValueError):
        raise ValueError('public_reference_display_binding_changed') from None
