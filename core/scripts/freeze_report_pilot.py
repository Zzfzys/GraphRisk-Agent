"""Freeze/check two natural report drafts OFFLINE. No live or retry option."""
from pathlib import Path
import argparse, json, subprocess, zipfile

from graphrisk.agent.autonomous_tools import InvestigationScope, ToolBoundaryError
from graphrisk.agent.autonomous_loop import restore_observations
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.judgment_v3 import JudgmentDraft, packet_for, verify_judgment
from graphrisk.agent.report_pilot import readonly_budget, reviewed_profile, freeze_contract, check_contract
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
from run_bounded_investigation import registry_for

SOURCE = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_new(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(canonical(value) + '\n')


def source_snapshot():
    inventory = {p.relative_to(SOURCE).as_posix(): file_hash(p)
                 for folder in ('src', 'api', 'scripts') for p in (SOURCE/folder).rglob('*.py')}
    configs = {name: file_hash(SOURCE/'configs'/name) for name in (
        'agent_investigation_experiment.json', 'agent_investigation_providers.json',
        'agent_investigation_policy_requirements.json')}
    return {'python_inventory_sha256': digest(inventory), 'python_files': inventory, 'configs': configs}


def source_archive(root,path,snapshot):
    """Only approved source/config bytes; never .env, caches, data or Git."""
    names={**snapshot['python_files'],**{'configs/'+name:sha for name,sha in snapshot['configs'].items()}}
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,sha in sorted(names.items()):
            current=(root/name).resolve()
            if not current.is_relative_to(root.resolve()) or file_hash(current)!=sha:
                raise ToolBoundaryError('pilot_source_archive_changed')
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,current.read_bytes())
        archive.writestr('source-inventory.json',canonical(snapshot))


def inputs(runtime, pricing, verified_date, *, presentation=None):
    route = runtime/'reports/generated/agent-investigation-upgrade'
    parent = route/'route5/supplement-v3/batch1'
    original = read(parent/'contract-private.json')
    profile_path = route/'route5/report-v2/controlled-v4-typed-support/contract-private.json'
    profile = reviewed_profile(read(profile_path)['profile'], pricing, verified_date)
    if presentation is not None:
        if presentation != 'plain-facts-v1':raise ToolBoundaryError('pilot_unknown_input_presentation')
        from graphrisk.agent.report_generation_profile import quality_report_profile
        profile=quality_report_profile(profile)
    registry_manifest = route/'route3/20261020-loop-v2-final/manifest-private.json'
    registry = registry_for(read(registry_manifest))
    prior = route/'route5/report-v3/generation-contract-v1'
    audited = read(prior/'request-audit-authoritative.json')
    cases, artifacts = [], {}
    for number in (1, 2):
        identifier = 'C' + str(number)
        path = parent/f'evidence-{number}-private.json'
        if file_hash(path) != original['evidence_files'][path.name]:
            raise ToolBoundaryError('pilot_parent_evidence_changed')
        raw = read(path)
        scope = InvestigationScope.model_validate_json(canonical(raw['scope']), strict=True)
        observations = restore_observations(scope, raw['observations'])
        bundle = build_evidence(scope, observations, registry)
        if bundle.model_dump(mode='json') != raw['evidence']:
            raise ToolBoundaryError('pilot_evidence_rebuild_changed')
        packet = packet_for(scope, observations, registry, profile)
        witness_file = prior/'requests-authoritative'/f'{number}-authored-candidate-private.json'
        witness = read(witness_file)
        if witness['report']['program']['evidence_sha256'] != packet['source_evidence_sha256']:
            raise ToolBoundaryError('pilot_witness_evidence_changed')
        errors, report = verify_judgment(JudgmentDraft.model_validate_json(canonical(witness['candidate']), strict=True), packet, bundle)
        if errors or report is None:
            raise ToolBoundaryError('pilot_existing_legal_witness_rejected')
        previous = next(r for r in audited['requests'] if r['case'] == number and r['condition'] == 'initial' and r['provider'] == 'deepseek')
        cases.append({'id': identifier, 'packet': packet, 'evidence_file_sha256': file_hash(path),
                      'prior_first_payload_sha256': previous['payload_sha256']})
        artifacts[identifier] = {'packet': packet, 'evidence': bundle.model_dump(mode='json'),
                                 'input_file_sha256': file_hash(path), 'legal_witness_file_sha256': file_hash(witness_file)}
    return profile, cases, artifacts, {
        'parent_contract_file_sha256': file_hash(parent/'contract-private.json'),
        'registry_manifest_sha256': file_hash(registry_manifest),
        'profile_parent_sha256': file_hash(profile_path), 'prior_request_audit_sha256': file_hash(prior/'request-audit-authoritative.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['freeze', 'check'])
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pricing-evidence', type=Path, required=True)
    parser.add_argument('--verified-date', required=True, help='Hong Kong date of actual official documentation verification')
    args = parser.parse_args()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    allowed = (runtime/'reports/generated/agent-investigation-upgrade/route5/report-v3').resolve()
    if output == allowed or not output.is_relative_to(allowed):
        parser.exit(2, 'pilot_output_outside_report_v3_root\n')
    if args.action == 'freeze' and output.exists():
        parser.exit(2, 'pilot_new_output_directory_required\n')
    try:
        pricing = read(args.pricing_evidence)
        budget_path = runtime/'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
        before = readonly_budget(budget_path)
        profile, cases, artifacts, bindings = inputs(runtime, pricing, args.verified_date)
        source = source_snapshot()
        rubric = SOURCE/'docs/data/agent-investigation-report-quality.md'
        contract, payloads = freeze_contract(profile=profile, pricing=pricing, verified_date=args.verified_date,
            cases=cases, budget=before, source=source, parent_contract_sha256=bindings['parent_contract_file_sha256'],
            rubric_sha256=file_hash(rubric))
        for case in cases:
            if digest(payloads[case['id']]) != case['prior_first_payload_sha256']:
                raise ToolBoundaryError('pilot_first_request_differs_from_audited_v3')
        contract.pop('contract_sha256')
        contract['input_bindings'] = bindings
        contract['pricing_evidence_file_sha256'] = file_hash(args.pricing_evidence)
        contract['solvability_evidence'] = {key: value['legal_witness_file_sha256'] for key, value in artifacts.items()}
        contract['contract_sha256'] = digest(contract)
        after = readonly_budget(budget_path)
        if before != after or source_snapshot() != source:
            raise ToolBoundaryError('pilot_inputs_changed_while_freezing')
        if args.action == 'check':
            saved = read(output/'contract-private.json')
            if saved != contract:
                raise ToolBoundaryError('pilot_freeze_inputs_changed')
            for case in cases:
                identifier = case['id']
                if read(output/(identifier+'-first-request-private.json')) != payloads[identifier]:
                    raise ToolBoundaryError('pilot_saved_payload_changed')
                if read(output/(identifier+'-evidence-private.json')) != artifacts[identifier]:
                    raise ToolBoundaryError('pilot_saved_evidence_changed')
            if read(output/'manifest.json') != {'contract_sha256': digest(saved), 'cases': ['C1','C2'], 'live_enabled': False}:
                raise ToolBoundaryError('pilot_freeze_manifest_changed')
            print(canonical(check_contract(saved, budget=after, source=source, today=args.verified_date)))
            return
        output.mkdir(parents=True)
        for case in cases:
            identifier = case['id']
            write_new(output/(identifier+'-first-request-private.json'), payloads[identifier])
            write_new(output/(identifier+'-evidence-private.json'), artifacts[identifier])
            write_new(output/(identifier+'-review-state.json'), {'case': identifier, 'first_draft': 'NotRun',
                'repair': 'NotRun', 'structure': 'NotRun', 'known_guard': 'NotRun',
                'semantic_facts': 'Pending', 'scores': {'specificity': None, 'analysis': None, 'actionability': None, 'readability': None},
                'total': None, 'case_quality': 'Pending', 'independent_judge': 'Pending', 'personal_readability': 'Pending'})
        write_new(output/'contract-private.json', contract)
        write_new(output/'budget-before.json', before)
        write_new(output/'pricing-evidence.json', pricing)
        write_new(output/'manifest.json', {'contract_sha256': digest(contract), 'cases': ['C1','C2'], 'live_enabled': False})
        print(canonical({'status': 'frozen_not_executed', 'contract_sha256': contract['contract_sha256'],
            'case_count': 2, 'max_requests': 4, 'first_message_bytes': [c['message_utf8_bytes'] for c in contract['cases']],
            'new_model_calls': 0, 'execution_ready': False, 'execution_blockers': contract['execution_blockers']}))
    except (ToolBoundaryError, OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        code = str(error) if isinstance(error, ToolBoundaryError) else 'pilot_input_or_output_invalid_no_model_call'
        parser.exit(2, code + '\n')


if __name__ == '__main__':
    main()
