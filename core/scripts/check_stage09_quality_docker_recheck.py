"""Read-only native quality regrade after an explicitly registered Docker repair.

Original native evidence stays frozen. Only the two registered persistence sources
may differ; their original bytes must exist in the pre-repair source archive.
"""
import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from graphrisk.agent.stage09_preflight import _content_hash, Stage09Contract
from graphrisk.demo import stage09_quality as quality
from graphrisk.demo.stage09_evaluation import digest, read_json, write_json

CHANGED = {'src/graphrisk/serving/investigation_service.py', 'src/graphrisk/serving/postgres_service.py'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recheck-dir', default='reports/generated/stage09/route5a/docker-recheck-20261008')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = (root / args.recheck_dir).resolve()
    if not directory.is_relative_to(root / 'reports/generated/stage09/route5a'):
        parser.error('recheck-dir must be under the private route5a evidence directory')
    registration = read_json(directory / 'source-lineage-registration.json')
    assert digest({k: v for k, v in registration.items() if k != 'content_sha256'}) == registration['content_sha256'], 'repair registration changed'
    assert set(registration['runtime_changes']) == CHANGED, 'unexpected runtime repair scope'
    native = root / 'reports/generated/stage09/route5a/20261008-quality-v2'
    archive = root / 'artifacts/stage-09/route5a/20261008-quality-v2/source-before-docker-repair'
    acceptance = read_json(native / 'acceptance.json')
    assert sha(native / 'acceptance.json') == registration['native_acceptance_file_sha256']
    assert digest({k: v for k, v in acceptance.items() if k != 'content_sha256'}) == acceptance['content_sha256']
    manifest = read_json(native / 'cases.json')
    inputs = read_json(native / 'inputs.json')
    assert manifest['content_sha256'] == registration['native_manifest_sha256']
    assert _content_hash({k: v for k, v in inputs.items() if k != 'content_sha256'}) == inputs['content_sha256']
    Stage09Contract.model_validate(inputs['contract'])

    def original_path(item):
        relative = item['path']
        current = (root / relative).resolve()
        assert current.is_relative_to(root) and current.is_file(), 'frozen file missing:' + relative
        if relative in CHANGED:
            binding = registration['runtime_changes'][relative]
            assert sha(current) == binding['current_sha256'], 'repair source drift:' + relative
            assert binding['native_sha256'] == item['sha256'], 'native binding differs:' + relative
            original = archive / relative
        else:
            original = current
        assert sha(original) == item['sha256'], 'unexpected native drift:' + relative
        if 'bytes' in item:
            assert original.stat().st_size == item['bytes'], 'native size drift:' + relative
        return original

    for item in inputs['artifacts']:
        original_path(item)
    assert digest({k: v for k, v in manifest.items() if k != 'content_sha256'}) == manifest['content_sha256']
    assert manifest['scoring'] == quality.SCORING, 'quality grading contract drift'
    for item in manifest['files']:
        original_path(item)
    for case in manifest['cases']:
        assert digest(read_json(native / 'snapshots' / (case['case_id'] + '.json'))) == case['snapshot_sha256']

    # The stock regrader still verifies every result, raw HTTP, selection and ledger.
    # Supply the already-verified historical source view and capture its output
    # instead of rewriting any frozen native evidence.
    previous_summary = read_json(native / 'summary.json')
    with patch.object(quality, 'require_registered', lambda r, d: manifest), patch.object(quality, 'write_json', lambda p, v: None):
        summary = quality.check(root, native)
    # A separately registered one-call Docker smoke extends the global ledger,
    # while every historical quality metric and result remains byte-preserved.
    assert {k: v for k, v in summary.items() if k != 'global_attempts'} == {k: v for k, v in previous_summary.items() if k != 'global_attempts'}, 'native quality summary changed'
    ledger = read_json(root / 'reports/generated/stage09/generation-ledger.json')['attempts']
    docker_attempts = [a for a in ledger if a['phase'] == 'route5a_docker_recheck']
    generation_contract = read_json(directory / 'docker-generation-contract.json')
    assert digest({k: v for k, v in generation_contract.items() if k != 'content_sha256'}) == generation_contract['content_sha256']
    assert generation_contract['parent_native_quality_manifest'] == manifest['content_sha256']
    assert len(docker_attempts) == 1 and generation_contract['max_new_provider_posts'] == 1
    assert previous_summary['global_attempts'] == generation_contract['global_attempts_before'] == 129
    assert summary['global_attempts'] == previous_summary['global_attempts'] + len(docker_attempts) <= 144
    assert summary['phase_attempts'] + len(docker_attempts) <= generation_contract['aggregate_route5a_cap'] == 66
    actual_http = read_json(directory / 'container-output/ollama-http.json')
    actual = read_json(directory / 'fresh-investigation.json')
    attempt = docker_attempts[0]
    assert attempt['case'] == generation_contract['content_sha256'] + ':dev-01-container-smoke'
    assert actual_http['ledger_sequence'] == attempt['sequence'] == actual['ledger_sequence']
    assert actual_http['actual_provider_post'] is True and actual_http['http_status'] == 200
    req = actual_http['request']
    assert req['model'] == generation_contract['model'] and req['options'] == {'temperature': 0, 'num_predict': 512}
    assert req['stream'] is False and req['keep_alive'] == 0 and req['think'] is False
    assert req['format'] == quality.QualityDraft.model_json_schema()
    assert req['prompt'].startswith(quality.QUALITY_PROMPT + '\nEVIDENCE_JSON:\n')
    raw = quality.raw_judgments(read_json(native / 'snapshots/dev-01.json'), [actual_http])
    assert len(raw) == 1 and raw[0]['semantic_pass'], 'fresh raw judgment differs from frozen tool states'
    view = actual['body']
    assert actual['http_status'] == 201 and view['status'] == 'verified'
    assert view['audit']['generation_attempts'] == 1 and view['audit']['retries'] == 0
    assert view['versions']['generation_model_revision'] == generation_contract['model_digest']
    assert view['report']['analysis']['draft'] == raw[0]['draft']
    assert view['audit']['prompt_tokens'] == actual_http['response']['prompt_eval_count']
    assert view['audit']['output_tokens'] == actual_http['response']['eval_count']
    observations = read_json(directory / 'fresh-generation-resources.json')
    assert observations['samples'] and observations['resource_floor_reached'] is False
    assert min(x['available_ram_gib'] for x in observations['samples']) >= 3
    assert all(x['disk_free_gib'][d] >= floor for x in observations['samples'] for d, floor in {'C':5,'D':5,'E':10}.items())
    assert all(x['free_gpu_mib'] >= 4096 and x['gpu_utilization_percent'] <= 10 for x in observations['samples'] if 'free_gpu_mib' in x)

    closure_archived = []
    for item in acceptance['closure_files']:
        current = root / item['path']
        if current.is_file() and sha(current) == item['sha256']:
            continue
        old = archive / item['path']
        assert old.is_file() and sha(old) == item['sha256'], 'historical closure missing:' + item['path']
        assert item['path'] in CHANGED or item['path'].startswith(('docs/', 'reports/stage-')) or item['path'] == 'PROJECT_PLAN.md'
        closure_archived.append(item['path'])
    proof = {'passed': True, 'scope': 'historical native measurement plus registered current Docker persistence repair',
             'native_input_files_checked': len(inputs['artifacts']), 'native_manifest_files_checked': len(manifest['files']),
             'native_acceptance_closure_files_checked': len(acceptance['closure_files']),
             'native_closure_preserved_in_archive': closure_archived,
             'intentional_current_runtime_changes': sorted(CHANGED), 'native_results_regraded': summary['all_results'],
             'prior_development_results_retained': summary['prior_development_results'],
             'native_route5a_generation_calls': summary['phase_attempts'], 'global_generation_calls': summary['global_attempts'],
             'docker_recheck_generation_calls': len(docker_attempts), 'aggregate_route5a_attempts': summary['phase_attempts'] + len(docker_attempts), 'fresh_raw_semantic_check': True, 'native_evidence_overwritten': False, 'paid_calls': 0,
             'registration_sha256': registration['content_sha256']}
    write_json(directory / 'native-lineage-check.json', proof)
    sealed_path = directory / 'acceptance.json'
    if sealed_path.exists():
        sealed = read_json(sealed_path)
        assert digest({k: v for k, v in sealed.items() if k != 'content_sha256'}) == sealed['content_sha256'], 'Docker acceptance changed'
        assert sealed['status'] == 'passed' and all(sealed['criteria'].values()), 'Docker acceptance incomplete'
        assert sealed['parent_native_acceptance_file_sha256'] == registration['native_acceptance_file_sha256']
        for item in sealed['closure_files']:
            path = (root / item['path']).resolve()
            assert path.is_relative_to(root) and path.is_file() and sha(path) == item['sha256'], 'Docker closure drift:' + item['path']
        print(json.dumps({'docker_acceptance_passed': True, 'closure_files_checked': len(sealed['closure_files']), 'acceptance_sha256': sealed['content_sha256']}))
    print(json.dumps(proof, ensure_ascii=False))


if __name__ == '__main__':
    main()
