"""Freeze a read-only web-trial allocation while preserving unfinished Route 9."""
from argparse import ArgumentParser
from collections import Counter
from pathlib import Path
import json
import sqlite3

from graphrisk.agent import full_case_execution as formal
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash


VERSION = 'daily-web-trial-with-route9-v1'
BASELINE = {'deepseek': 1707, 'local': 95}
CAPS = {'deepseek': 1771, 'local': 348, 'total': 2119}
REVISION = 'd700ea7299d78740e668776abcfe76c9f524072abc6801858511995b17231acd'


def require(ok, code):
    if not ok:
        raise ValueError('daily_web_prepare_' + code)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def ledger_baseline(db):
    """Use the durable ledger's original full-row order, without reserving."""
    rows = formal.history_rows(db)
    require(len(rows) == db.execute('SELECT count(*) FROM model_attempts').fetchone()[0]
            == db.execute('SELECT count(*) FROM execution_requests').fetchone()[0],
            'ledger_orphan_rows')
    counts = dict(Counter(row[1] for row in rows))
    require(counts == BASELINE and len(rows) == sum(BASELINE.values()), 'baseline_counts')
    unknown = [row for row in rows if row[2] in ('reserved', 'unknown')]
    integrity = [row for row in rows if row[-1]]
    require(len(unknown) == 3 and all(row[2] == 'unknown' for row in unknown)
            and len(integrity) == 1, 'historical_stops_changed')
    require(all(type(row[7]) is int and row[7] >= 0 for row in rows), 'accounted_cost')
    require(sum(row[7] for row in rows) + 5_000_000 < 80_000_000,
            'money_pause_headroom')
    identities = db.execute('SELECT provider,identity FROM provider_identity ORDER BY provider').fetchall()
    require({row[0] for row in identities} == {'deepseek', 'local'}, 'historical_provider_identity_rows')
    return {'count': len(rows), 'provider_counts': counts, 'rows_sha256': digest(rows),
            'provider_identity_rows': identities, 'provider_identity_rows_sha256': digest(identities),
            'unknown_rows_sha256': digest(unknown),
            'integrity_rows_sha256': digest(integrity)}


def pending_sessions(episodes, saved_ids, active_ids):
    """Never allocate a replacement for a saved failure or interrupted session."""
    require(len(episodes) == 130 and len({e['session_id'] for e in episodes}) == 130,
            'route9_episode_registration')
    registered = {e['session_id'] for e in episodes}
    require(saved_ids <= registered and active_ids <= registered, 'route9_unregistered_session')
    saved = [e for e in episodes if e['session_id'] in saved_ids]
    pending = [e for e in episodes if e['session_id'] not in saved_ids]
    require(len(saved) == 90 and Counter(e['provider'] for e in saved)
            == {'deepseek': 65, 'local': 25}, 'route9_saved_results')
    require(len(pending) == 40 and all(e['provider'] == 'local' for e in pending)
            and sum(e['repeat'] is True for e in pending) == 5
            and sum(e['repeat'] is False for e in pending) == 35,
            'route9_remaining_scope')
    require(not ({e['session_id'] for e in pending} & active_ids), 'route9_inflight_session')
    return [e['session_id'] for e in pending]


def check_capacity(baseline, pending_count, max_calls, absolute):
    require(absolute == CAPS and pending_count == 40 and max_calls == 5,
            'route9_remaining_allocation')
    remaining = pending_count * max_calls
    counts = baseline['provider_counts']
    require(counts == BASELINE and baseline['count'] == sum(counts.values()), 'baseline_counts')
    require(counts['deepseek'] + 6 <= absolute['deepseek']
            and counts['local'] + remaining <= absolute['local']
            and baseline['count'] + 6 + remaining <= absolute['total'], 'absolute_count_headroom')
    return remaining


def bind_file(files, path, expected=None):
    path = Path(path).resolve()
    require(path.is_file(), 'bound_file_missing')
    actual = file_hash(path)
    require(expected is None or actual == expected, 'bound_file_changed')
    require(str(path) not in files or files[str(path)] == actual, 'conflicting_file_binding')
    files[str(path)] = actual
    return path


def prepared_cases(manifest, files, profile):
    from graphrisk.agent import judgment_v12_daily_quotes as quotes
    require(manifest.get('version') == 'daily-web-trial-prepared-v1', 'prepared_manifest_version')
    expected = {'H1': ('3558253', 'high'), 'H2': ('3558246', 'high'),
                'M1': ('3558671', 'medium'), 'M2': ('3557515', 'medium'),
                'L1': ('3559456', 'low'), 'L2': ('3557768', 'low')}
    cases = manifest['cases']
    require(len(cases) == 6 and {case['review_case'] for case in cases} == set(expected),
            'prepared_six_cases')
    require(len({case['alias'] for case in cases}) == 6, 'prepared_unique_aliases')
    output = []
    for case in cases:
        require((case['transaction_id'], case['risk_level']) == expected[case['review_case']],
                'prepared_selection_changed')
        for kind in ('source', 'packet', 'payload'):
            bind_file(files, case[kind + '_path'], case[kind + '_sha256'])
        source, packet, payload = (read(case[kind + '_path']) for kind in ('source', 'packet', 'payload'))
        require(digest(source['scope']) == case['scope_sha256']
                and source['scope']['case_alias'] == case['alias']
                and source['scope']['transaction_id'] == case['transaction_id']
                and source['evidence']['evidence_version'] == case['evidence_version'],
                'prepared_source_binding')
        require(packet['source_scope_sha256'] == case['scope_sha256']
                and packet['source_evidence_sha256'] == digest(source['evidence'])
                and digest(packet) == case['packet_sha256']
                and digest(payload) == case['payload_sha256']
                and case['profile_sha256'] == digest(profile), 'prepared_packet_binding')
        require(set(payload) == {'model', 'messages', 'stream', 'temperature', 'max_tokens',
                                'thinking', 'response_format'}
                and payload['messages'] == [{'role': 'system', 'content': quotes.PROMPT},
                    {'role': 'user', 'content': canonical(packet['view'])}]
                and payload['model'] == profile['deepseek']['model']
                and payload['stream'] is False and payload['temperature'] == 0
                and payload['max_tokens'] == 4096
                and payload['thinking'] == {'type': 'disabled'}
                and payload['response_format'] == {'type': 'json_object'}, 'prepared_request_parameters')
        output.append({key: case[key] for key in ('alias', 'review_case', 'transaction_id',
            'payload_sha256', 'packet_sha256', 'profile_sha256', 'scope_sha256')})
    return output


def build_config(*, prepared_manifest_path, route9_execution_path, route9_approval_path,
                 ledger_path, extension_files, campaign='daily-web-trial-20261115-v1'):
    """Read/authenticate only; caller writes the final concrete approval separately."""
    route9_execution_path = Path(route9_execution_path).resolve()
    require(file_hash(route9_execution_path) == REVISION, 'route9_contract_changed')
    route9 = read(route9_execution_path)
    formal.check_contract(route9, mode='live')
    require(len(route9['source_inventory']) == 311, 'source_inventory_count')
    files = dict(route9['files'])
    bind_file(files, route9_execution_path, REVISION)
    approval_path = bind_file(files, route9_approval_path)
    pause_path = bind_file(files, Path(route9['output']) / 'RESOURCE-PAUSE-20261115-114516.json')
    pause = read(pause_path)
    require(pause['local_generation_paused_at_user_request'] is True
            and pause['execution_closed'] is False and pause['labels_parsed'] is False
            and pause['saved_results'] == 90 and pause['local_remaining_episodes'] == 40
            and pause['execution_sha256'] == REVISION
            and pause['approval_sha256'] == file_hash(approval_path), 'route9_pause_binding')
    saved = {e['session_id'] for e in route9['episodes']
             if (Path(e['directory']) / 'result-private.json').is_file()}
    ledger_path = Path(ledger_path).resolve()
    require(ledger_path == Path(route9['runtime']).resolve()
            / 'reports/generated/agent-investigation-upgrade/live-budget.sqlite3', 'ledger_path')
    with sqlite3.connect(ledger_path.as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        formal.check_history(db, route9)
        baseline = ledger_baseline(db)
        grant = db.execute('SELECT contract_path,contract_sha,approval_path,approval_sha,closed '
            'FROM formal_case_grants WHERE campaign_id=?', (route9['campaign'],)).fetchone()
        require(grant == (str(route9_execution_path), REVISION, str(approval_path),
                          file_hash(approval_path), 0), 'route9_grant_changed')
        active = {row[0] for row in db.execute('SELECT session_id FROM autonomous_sessions '
                     'WHERE campaign_id=?', (route9['campaign'],))}
        pending = pending_sessions(route9['episodes'], saved, active)
        for episode in route9['episodes']:
            if episode['session_id'] in saved:
                proof = formal.audit_episode(db, route9, episode)
                for path, expected in proof.get('bound_files', {}).items():
                    bind_file(files, path, expected)
                bind_file(files, Path(episode['directory']) / 'result-private.json', proof['result_sha256'])
    remaining = check_capacity(baseline, len(pending), route9['limits']['max_model_calls'], route9['absolute_caps'])
    manifest_path = bind_file(files, prepared_manifest_path)
    manifest = read(manifest_path)
    require(Path(manifest['source_root']).resolve() == Path(route9['source_root']).resolve()
            and Path(manifest['runtime']).resolve() == Path(route9['runtime']).resolve(), 'prepared_runtime')
    for path, expected in manifest['files'].items():
        bind_file(files, path, expected)
    bind_file(files, manifest['db_path'])
    profile_path = bind_file(files, manifest['profile_path'])
    profile = read(profile_path)
    from graphrisk.agent.report_generation_profile import quality_report_profile
    require(profile == quality_report_profile(read(route9['profile_path'])), 'report_profile_derivation')
    require(digest(profile) == manifest['profile_sha256']
            and profile['deepseek']['pricing_verified_date'] == '2026-11-15'
            and profile['deepseek']['endpoint'] == 'https://api.deepseek.com/chat/completions'
            and profile['deepseek']['model'] == 'deepseek-flash'
            and profile['max_output_tokens'] == 4096 and profile['max_prompt_utf8_bytes'] == 20000
            and profile['report_thinking'] is False, 'report_profile')
    cases = prepared_cases(manifest, files, profile)
    extensions = {}
    for path in extension_files:
        bind_file(extensions, path)
    require(str(Path(__file__).resolve()) in extensions
            and all(Path(path).suffix in ('.py', '.ps1') for path in extensions)
            and sum(Path(path).name.startswith('test_') for path in extensions) == 3
            and len(extensions) >= 6, 'extension_and_three_tests_required')
    require(not set(files) & set(extensions), 'extension_source_overlap')
    return {'version': VERSION, 'campaign': campaign, 'group': 'daily_web_trial_v1',
        'runtime': route9['runtime'], 'ledger_path': str(ledger_path),
        'route9_execution_path': str(route9_execution_path), 'route9_execution_sha256': REVISION,
        'prepared_manifest_path': str(manifest_path), 'prepared_manifest_sha256': file_hash(manifest_path),
        'baseline': baseline, 'files': files, 'extension_files': extensions,
        'source_root': route9['source_root'], 'source_inventory': route9['source_inventory'],
        'profile_path': str(profile_path), 'profile_sha256': digest(profile),
        'expected_identity': route9['identities']['deepseek'], 'cases': cases,
        'route9_pending_sessions': pending,
        'caps': {'web_calls': 6, 'web_cost_micro_cny': 5_000_000,
                 'route9_remaining_calls': remaining, 'absolute': CAPS,
                 'pause_micro_cny': 80_000_000, 'total_cost_micro_cny': 100_000_000}}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-manifest', type=Path, required=True)
    parser.add_argument('--route9-execution', type=Path, required=True)
    parser.add_argument('--route9-approval', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--extension-file', type=Path, action='append', required=True)
    parser.add_argument('--campaign', default='daily-web-trial-20261115-v1')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), 'output_already_exists')
    result = build_config(prepared_manifest_path=args.prepared_manifest,
        route9_execution_path=args.route9_execution, route9_approval_path=args.route9_approval,
        ledger_path=args.ledger, extension_files=args.extension_file, campaign=args.campaign)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        handle.write(canonical(result))
    print(canonical({'status': 'prepared_not_authorized_not_registered_not_sent',
                     'config_sha256': file_hash(args.output), 'baseline_calls': result['baseline']['count'],
                     'route9_reserved_future_calls': result['caps']['route9_remaining_calls'],
                     'web_calls': 6, 'model_calls': 0}))


if __name__ == '__main__':
    main()
