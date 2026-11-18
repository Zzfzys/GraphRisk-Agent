"""Small offline fixtures for the immutable trial preparation boundary."""
import sqlite3

import pytest

import prepare_protocol as prepare


def ledger():
    db = sqlite3.connect(':memory:')
    db.executescript("""CREATE TABLE provider_identity(provider,identity);
        INSERT INTO provider_identity VALUES('deepseek','historical-deepseek');
        INSERT INTO provider_identity VALUES('local','historical-local');
        CREATE TABLE model_attempts(attempt_id,provider,state,reserved_micro_cny);
        CREATE TABLE execution_requests(attempt_id,request_key,phase,packet_sha,accounted,
            receipt_sha,receipt_path,result_code,identity,usage_json,integrity_stop);""")
    for index in range(1802):
        provider = 'deepseek' if index < 1707 else 'local'
        state = 'unknown' if index < 3 else 'settled'
        key = str(index)
        db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)', (key, provider, state, 1))
        db.execute('INSERT INTO execution_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (key, key, key, 'packet', 1, 'receipt', 'private.json', 'ok', 'identity', '{}', int(index == 3)))
    return db


def episodes():
    rows = [{'session_id': provider + str(index), 'provider': provider, 'repeat': index >= 60}
            for provider in ('deepseek', 'local') for index in range(65)]
    saved = {row['session_id'] for row in rows[:90]}
    return rows, saved


def test_baseline_authenticates_all_rows_without_mutation():
    with ledger() as db:
        before = db.total_changes
        value = prepare.ledger_baseline(db)
        assert value['provider_counts'] == {'deepseek': 1707, 'local': 95}
        assert value['count'] == 1802 and db.total_changes == before
        assert value['provider_identity_rows'][0] == ('deepseek', 'historical-deepseek')
        db.execute("UPDATE provider_identity SET identity='changed' WHERE provider='deepseek'")
        assert prepare.ledger_baseline(db)['provider_identity_rows_sha256'] != value['provider_identity_rows_sha256']
        db.execute("UPDATE execution_requests SET packet_sha='changed' WHERE attempt_id='8'")
        assert prepare.ledger_baseline(db)['rows_sha256'] != value['rows_sha256']


@pytest.mark.parametrize('statement,code', [
    ("DELETE FROM execution_requests WHERE attempt_id='8'", 'ledger_orphan_rows'),
    ("UPDATE model_attempts SET state='reserved' WHERE attempt_id='0'", 'historical_stops_changed'),
    ("UPDATE execution_requests SET integrity_stop=1 WHERE attempt_id='8'", 'historical_stops_changed'),
    ("UPDATE execution_requests SET accounted=80000000 WHERE attempt_id='8'", 'money_pause_headroom'),
])
def test_baseline_rejects_unresolved_or_changed_history(statement, code):
    with ledger() as db:
        db.execute(statement)
        with pytest.raises(ValueError, match=code):
            prepare.ledger_baseline(db)


def test_pending_keeps_saved_failures_and_exact_remaining_registration():
    rows, saved = episodes()
    remaining = prepare.pending_sessions(rows, saved, saved)
    assert len(remaining) == 40
    assert remaining[0] == 'local25' and remaining[-1] == 'local64'
    assert not saved.intersection(remaining)


def test_pending_refuses_an_unsaved_started_session():
    rows, saved = episodes()
    with pytest.raises(ValueError, match='inflight_session'):
        prepare.pending_sessions(rows, saved, saved | {'local25'})


def test_pending_refuses_resampling_or_overwriting_a_saved_case():
    rows, saved = episodes()
    with pytest.raises(ValueError, match='saved_results'):
        prepare.pending_sessions(rows, saved - {'local24'}, saved)
    rows[-1]['repeat'] = False
    with pytest.raises(ValueError, match='remaining_scope'):
        prepare.pending_sessions(rows, saved, saved)


def test_capacity_reserves_all_two_hundred_remaining_calls():
    baseline = {'count': 1802, 'provider_counts': dict(prepare.BASELINE)}
    assert prepare.check_capacity(baseline, 40, 5, dict(prepare.CAPS)) == 200
    with pytest.raises(ValueError, match='remaining_allocation'):
        prepare.check_capacity(baseline, 39, 5, dict(prepare.CAPS))
    with pytest.raises(ValueError, match='remaining_allocation'):
        prepare.check_capacity(baseline, 40, 5, {**prepare.CAPS, 'total': 2200})


def test_binding_rejects_file_mutation(tmp_path):
    path = tmp_path / 'frozen.json'
    path.write_text('{}', encoding='utf-8')
    files = {}
    prepare.bind_file(files, path)
    path.write_text('{"changed":true}', encoding='utf-8')
    with pytest.raises(ValueError, match='bound_file_changed'):
        prepare.bind_file({}, path, files[str(path.resolve())])
