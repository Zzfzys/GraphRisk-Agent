"""Prepare offline payloads and recheck saved drafts; there is no live mode."""
from argparse import ArgumentParser
import json
from pathlib import Path

from graphrisk.agent import judgment_v3 as baseline, judgment_v12_daily_review as candidate
from graphrisk.agent.autonomous_evidence import EvidenceBundle
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def prepare(source_run, output, *, generation='citation-v1'):
    if generation == 'supported-quotes-v2':
        from graphrisk.agent import judgment_v12_daily_quotes as selected
    elif generation == 'citation-v1':
        selected = candidate
    else:
        raise ValueError('unknown offline candidate generation')
    source_run, output = source_run.resolve(), output.resolve()
    if output == source_run or output.is_relative_to(source_run) or source_run.is_relative_to(output):
        raise ValueError('output must be separate from the original run')
    if output.exists():
        raise ValueError('output already exists; use a new directory to preserve artifacts')
    cfg = read(source_run / 'execution-private.json')
    audit = read(source_run / 'LIVE-EXECUTION.json')
    keys = ['H1', 'H2', 'M1', 'M2', 'L1', 'L2']
    if cfg['case_keys'] != keys or not audit['closed'] or len(audit['cases']) != 6:
        raise ValueError('a closed six-case daily source run is required')
    files = source_run / 'final-six'
    profile = read(files / 'profile.json')
    originals = [files / 'profile.json']
    originals += [files / (key + suffix) for key in keys
                  for suffix in ('-source-private.json', '-packet-private.json', '-payload.json')]
    for path in originals:
        if cfg['files'].get(str(path)) != file_hash(path):
            raise ValueError('original source hash changed: ' + str(path))
    results = {row['case']: row for row in audit['cases']}
    prepared, rechecks = [], []
    # Reuse only the existing pure formatter. No provider initialization,
    # ledger, transport, generate() or network operation occurs in this CLI.
    formatter = object.__new__(selected.Provider)
    formatter.provider, formatter.profile = 'deepseek', profile
    for key in keys:
        source_path = files / (key + '-source-private.json')
        raw = read(source_path)
        bundle = EvidenceBundle.model_validate_json(canonical(raw['evidence']), strict=True)
        original = read(files / (key + '-packet-private.json'))
        result_path = source_run / 'actual-first-drafts-v1' / (key + '-result-private.json')
        if file_hash(result_path) != results[key]['result_sha256']:
            raise ValueError('original first-draft result changed: ' + str(result_path))
        saved = read(result_path)
        value = saved['provider_result']['action']
        old_issues = baseline.issues_for(value, original, bundle)
        old_codes = sorted({issue['code'] for issue in old_issues})
        if old_codes != saved['native_guard_issues']:
            raise ValueError('baseline replay no longer matches saved native guard: ' + key)
        packet = selected.prepare_packet(original, bundle, profile)
        payload = formatter.payload(packet)
        private_id = str(raw['scope']['transaction_id'])
        if private_id in canonical(payload):
            raise ValueError('transaction identifier in outbound payload: ' + key)
        issues = selected.issues_for(value, packet, bundle)
        prepared.append((key, packet, payload))
        rechecks.append({'case': key, 'model_priority': bundle.scoring_context.risk_level,
            'original_result_sha256': file_hash(result_path), 'baseline_codes': old_codes,
            'candidate_diagnostic_codes': sorted({row['code'] for row in issues}),
            'candidate_diagnostics': issues, 'original_native_result_preserved': True,
            'original_draft_modified': False, 'new_first_draft_generated': False})
    output.mkdir(parents=True, exist_ok=False)
    manifests = []
    for key, packet, payload in prepared:
        packet_path = output / (key + '-candidate-packet-private.json')
        packet_path.write_text(canonical(packet), encoding='utf-8')
        payload_path = output / (key + '-candidate-payload.json')
        payload_path.write_text(canonical(payload), encoding='utf-8')
        manifests.append({'case': key, 'packet_path': str(packet_path), 'packet_sha256': file_hash(packet_path),
            'payload_path': str(payload_path), 'payload_sha256': file_hash(payload_path),
            'prompt_utf8_bytes': packet['prompt_utf8_bytes'], 'contract': selected.CONTRACT,
            'generation_variant': selected.GENERATION})
    summary = {'version': 'daily-v12-candidate-offline-preparation-v1',
        'source_run': str(source_run), 'candidate_generation': selected.GENERATION,
        'source_module_sha256': file_hash(Path(selected.__file__)),
        'script_sha256': file_hash(Path(__file__)), 'cases': manifests, 'saved_draft_rechecks': rechecks,
        'paid_calls': 0, 'external_transfers': 0, 'labels_read': False,
        'new_first_drafts_generated': 0, 'candidate_writing_quality': 'NotEvaluated',
        'old_complete_quality_pass': '0/6 preserved; not recomputed by this CLI',
        'payloads_authorized_for_sending': False, 'default_serving_candidate_changed': False,
        'profile_sha256': digest(profile)}
    (output / 'offline-manifest.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return summary


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--generation', choices=('citation-v1', 'supported-quotes-v2'), default='citation-v1')
    args = parser.parse_args()
    result = prepare(args.source_run, args.output, generation=args.generation)
    print(canonical({'prepared': len(result['cases']), 'paid_calls': 0,
                     'candidate_writing_quality': result['candidate_writing_quality']}))


if __name__ == '__main__':
    main()
