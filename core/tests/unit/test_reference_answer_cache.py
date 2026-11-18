"""Small cache binding fixtures reuse the already-tested receipt authenticator."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from test_reference_answers import answer_source, context, cite
from graphrisk.agent import reference_answer_cache as cache
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value) + '\n', encoding='utf-8')
    return path


@pytest.fixture
def reviewed(tmp_path, answer_source, monkeypatch):
    service, _, _ = answer_source
    packet = service.prepare('consumer account', context(), planner='literal')
    answer = service.finish(packet, cite())
    runtime = tmp_path / 'runtime'
    ledger = runtime / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    ledger.parent.mkdir(parents=True)
    code = tmp_path / 'code'
    source_hashes = {}
    for name in ('reference_answers.py', 'reference_answer_execution.py', 'public_references.py',
                 'autonomous_providers.py', 'autonomous_http.py', 'upgrade_contract.py'):
        rel = 'src/graphrisk/agent/' + name
        path = code / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# authored code binding fixture\n', encoding='utf-8')
        source_hashes[rel] = file_hash(path)
    request_path = write(tmp_path / 'request.json', packet['request'])
    packet_path = write(tmp_path / 'packet.json', packet)
    payload_path = write(tmp_path / 'payload.json', {'fixture': 'no transport'})
    profile_path = write(tmp_path / 'profile.json', {'fixture': 'no credentials'})
    result_path = write(tmp_path / 'result.json', {'provider_result': {'code': 'ok', 'attempt_id': 'fixture'},
                                                'answer': answer})
    files = {str(p): file_hash(p) for p in (request_path, packet_path, payload_path,
                                         profile_path, service.retriever.manifest_path)}
    cfg = {'version': 'reference-answer-support-v1', 'max_requests': 12, 'cap_micro_cny': 5_000_000,
           'endpoint': 'https://api.deepseek.com/chat/completions', 'source_root': str(code),
           'source_python_sha256': source_hashes, 'case_keys': ['A'+str(i) for i in range(12)],
           'cases': [{'request_path': str(request_path), 'packet_path': str(packet_path),
                      'payload_path': str(payload_path)}] * 12,
           'profile_path': str(profile_path), 'manifest_path': str(service.retriever.manifest_path),
           'files': files, 'runtime': str(runtime), 'campaign': 'authored-cache-fixture',
           'expected_identity': '["fixture-model","fixture-fingerprint"]'}
    execution = write(tmp_path / 'execution.json', cfg)
    note = tmp_path / 'review.md'
    note.write_text('Authored independent reading fixture; no real model quality claim.\n', encoding='utf-8')
    review = write(tmp_path / 'review.json', {'schema_version': 'route7-public-answer-reading-v1',
        'execution_contract_sha256': file_hash(execution), 'reviewer_type': 'independent_assistant_reading',
        'review_note_path': str(note), 'review_note_sha256': file_hash(note), 'user_acceptance': 'Pending',
        'cases': [{'case_id': 'A0', 'reading_status': 'acceptable_for_reference',
                   'result_sha256': file_hash(result_path), 'answer_sha256': answer['answer_sha256'],
                   'packet_sha256': answer['packet_sha256']}]})
    bundle = write(tmp_path / 'cache.json', {'version': 'reviewed-public-answer-cache-v1',
        'execution_path': str(execution), 'execution_sha256': file_hash(execution), 'case_id': 'A0',
        'review_path': str(review), 'review_sha256': file_hash(review)})
    with sqlite3.connect(ledger) as db:
        db.executescript('CREATE TABLE rank_successors(campaign_id TEXT,contract_sha TEXT,closed INT);'
            'CREATE TABLE execution_requests(attempt_id TEXT,phase TEXT,receipt_sha TEXT);'
            'CREATE TABLE autonomous_phases(phase TEXT,session_id TEXT);'
            'CREATE TABLE autonomous_sessions(session_id TEXT,campaign_id TEXT);')
        db.execute('INSERT INTO rank_successors VALUES (?,?,1)', (cfg['campaign'], file_hash(execution)))
        for index in range(12):
            key = str(index)
            db.execute('INSERT INTO autonomous_sessions VALUES (?,?)', (key, cfg['campaign']))
            db.execute('INSERT INTO autonomous_phases VALUES (?,?)', (key, key))
            db.execute('INSERT INTO execution_requests VALUES (?,?,?)',
                       ('fixture' if index == 0 else key, key, 'r'*64))
    # The actual shared function's raw receipt/auth checks have separate tests.
    # This fixture focuses on cache review/request/case binding, no POST possible.
    monkeypatch.setattr(cache, 'verify_saved_result', lambda *a: (str(result_path), file_hash(result_path)))
    monkeypatch.setattr(cache, 'service_for', lambda *a: service)
    return dict(path=bundle, packet=packet, answer=answer, ledger=ledger, cfg=cfg,
                review=review, note=note, result=result_path, execution=execution)


def test_reviewed_cache_reads_closed_answer_without_mutating_ledger_or_promoting_user_acceptance(reviewed):
    before = file_hash(reviewed['ledger'])
    first = cache.load_reviewed_reference_cache(reviewed['path'])
    second = cache.load_reviewed_reference_cache(reviewed['path'])
    assert first['answer'] == second['answer'] == reviewed['answer']
    assert first['provenance']['user_acceptance'] == 'Pending'
    assert first['answer']['model_response_authenticated'] is False
    assert first['provenance']['receipt_sha256'] == 'r'*64
    assert file_hash(reviewed['ledger']) == before


@pytest.mark.parametrize('defect', ['closed', 'count', 'review', 'note', 'code', 'result', 'request'])
def test_changed_cache_proof_cannot_be_displayed(reviewed, defect):
    if defect in ('closed', 'count'):
        with sqlite3.connect(reviewed['ledger']) as db:
            db.execute('UPDATE rank_successors SET closed=0' if defect=='closed' else
                       "DELETE FROM execution_requests WHERE attempt_id='11'")
    elif defect == 'review':
        value = json.loads(reviewed['review'].read_text('utf-8'))
        value['cases'][0]['reading_status'] = 'not_reviewed'
        write(reviewed['review'], value)
        bundle = json.loads(reviewed['path'].read_text('utf-8'))
        bundle['review_sha256'] = file_hash(reviewed['review'])
        write(reviewed['path'], bundle)
    elif defect == 'note':
        reviewed['note'].write_text('changed', encoding='utf-8')
    elif defect == 'code':
        target = Path(reviewed['cfg']['source_root']) / next(iter(reviewed['cfg']['source_python_sha256']))
        target.write_text('changed', encoding='utf-8')
    elif defect == 'request':
        write(Path(reviewed['cfg']['cases'][0]['packet_path']), {'changed': True})
    else:
        value = json.loads(reviewed['result'].read_text('utf-8'))
        value['answer']['semantic_support'] = 'user_accepted'
        write(reviewed['result'], value)
    with pytest.raises(ValueError, match='cache_unavailable_or_changed'):
        cache.load_reviewed_reference_cache(reviewed['path'])


def test_sidecar_rechecks_public_scope_case_and_answer_even_after_rehash(reviewed):
    loaded = cache.load_reviewed_reference_cache(reviewed['path'])
    packet = loaded['packet']
    request = packet['request']
    reference = {'version': 'daily-public-reference-sidecar-v1', 'case_alias': 'case',
        'case_binding_sha256': 'b'*64, 'evidence_version': 'e'*64,
        'query_sha256': digest(request['query']), 'context_sha256': digest(request['context']),
        'request_sha256': digest(request), 'packet_sha256': digest(packet),
        'context_role': 'public_question_scope_not_case_facts',
        'reference_provenance': loaded['provenance'], 'answer': loaded['answer']}
    reference['sidecar_sha256'] = digest(reference)
    value = {'case_alias': 'case', 'evidence': {'case_alias': 'case', 'evidence_version': 'e'*64,
                                            'policy_as_of': request['context']['as_of']},
             'reference_case_binding_sha256': 'b'*64,
             'public_reference': reference}
    assert cache.verify_reference_sidecar(value, reviewed['path']) == reference
    for defect in ('case', 'binding', 'date', 'context_role', 'answer'):
        changed = deepcopy(value)
        if defect == 'case': changed['case_alias'] = 'other'
        elif defect == 'binding': changed['public_reference']['case_binding_sha256'] = 'f'*64
        elif defect == 'date': changed['evidence']['policy_as_of'] = '2026-11-13'
        elif defect == 'context_role': changed['public_reference']['context_role'] = 'transaction_fact'
        else: changed['public_reference']['answer']['sources'][0]['text'] = 'opposite or shortened quote'
        changed['public_reference']['sidecar_sha256'] = digest({k:v for k,v in changed['public_reference'].items() if k!='sidecar_sha256'})
        with pytest.raises(ValueError, match='display_binding_changed'):
            cache.verify_reference_sidecar(changed, reviewed['path'])
