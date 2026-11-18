"""Freeze, authorize and execute ONE two-case/four-request natural report pilot."""
from datetime import date
import argparse
import json
from pathlib import Path
import time

import httpx

from freeze_report_pilot import SOURCE, inputs, source_snapshot, source_archive, read, write_new
from run_bounded_investigation import ledger
from graphrisk.agent import judgment_v3 as v3
from graphrisk.agent import report_pilot_execution as channel
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.autonomous_tools import ToolBoundaryError
from graphrisk.agent.report_pilot import readonly_budget, freeze_contract
from graphrisk.agent.report_quality import prepare_quality_packet, blank_review, assess_quality, render_quality_mapping
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash


def connection_preflight(*, transport=None):
    """Unauthenticated GET only, before opening/reserving a live model slot.

    An HTTP 401 is useful here: it proves a TLS/HTTP response was reachable,
    without reading credentials or making a model request. It does NOT check
    API credentials or guarantee a subsequent POST succeeds.
    """
    try:
        with httpx.Client(transport=transport, trust_env=False, follow_redirects=False, timeout=10) as client:
            response = client.get('https://api.deepseek.com')
        if response.status_code >= 500:
            raise ExecutionStop('report_pilot_provider_unavailable_before_reservation')
    except httpx.HTTPError:
        raise ExecutionStop('report_pilot_connection_unavailable_before_reservation') from None
    return {'connection': 'http_response_received', 'http_status': response.status_code,
            'credentials_used': False, 'model_requests': 0}


def freeze(runtime, output, pricing_path, *, program_path=None):
    if output.exists():
        raise ExecutionStop('report_pilot_new_directory_required')
    pricing = read(pricing_path)
    before = readonly_budget(runtime/'reports/generated/agent-investigation-upgrade/live-budget.sqlite3')
    profile, cases, artifacts, bindings = inputs(runtime, pricing, date.today().isoformat(),
        presentation='plain-facts-v1' if program_path else None)
    if len(before['unknown']) != (2 if program_path else 1):
        raise ExecutionStop('report_pilot_exact_old_unknown_required')
    source = source_snapshot()
    rubric = SOURCE/'docs/data/agent-investigation-report-quality.md'
    base, payloads = freeze_contract(profile=profile, pricing=pricing, verified_date=date.today().isoformat(),
        cases=cases, budget=before, source=source, parent_contract_sha256=bindings['parent_contract_file_sha256'],
        rubric_sha256=file_hash(rubric))
    budget = ledger(runtime)
    with budget.transaction() as db:
        if program_path:
            from graphrisk.agent.report_quality_program import check_program
            program = check_program(db,program_path,file_hash(program_path))
        previous_rows = channel.baseline(db)
        closed = []
        for table in ('controlled_recovery', 'controlled_recovery_successors', 'controlled_recovery_followups'):
            closed.extend(r[0] for r in db.execute('SELECT campaign_id FROM '+table))
    route = runtime/'reports/generated/agent-investigation-upgrade'
    parent = route/'route5/supplement-v3/batch1'
    registry_manifest = route/'route3/20261020-loop-v2-final/manifest-private.json'
    registry_index = Path(read(registry_manifest)['policy_index_path'])
    stopped = route/'route5/report-v2/controlled-v4-typed-support'
    if read(stopped/'STOP.json')['reason'] != 'two_consecutive_completed_acceptance_failures':
        raise ExecutionStop('report_pilot_parent_stop_not_verified')
    protected = {str(p.resolve()): file_hash(p) for p in stopped.rglob('*') if p.is_file()}
    protected.update({str(p.resolve()): file_hash(p) for p in [pricing_path, rubric, registry_manifest, registry_index,
        parent/'contract-private.json', parent/'evidence-1-private.json', parent/'evidence-2-private.json']})
    output.mkdir(parents=True)
    if program_path:
        source_archive(SOURCE,output/'source-archive.zip',source)
        protected[str(output/'source-archive.zip')]=file_hash(output/'source-archive.zip')
    write_new(output/'preparation-contract-private.json', base)
    write_new(output/'pricing-evidence.json', pricing)
    protected[str(output/'preparation-contract-private.json')] = file_hash(output/'preparation-contract-private.json')
    protected[str(output/'pricing-evidence.json')] = file_hash(output/'pricing-evidence.json')
    selected = {}
    for n, case in enumerate(cases, 1):
        name = case['id']
        if not program_path and digest(payloads[name]) != case['prior_first_payload_sha256']:
            raise ExecutionStop('report_pilot_first_payload_differs_from_audit')
        raw_path, packet_path, payload_path = [output/(name+suffix) for suffix in
            ('-raw-evidence-private.json', '-evidence-private.json', '-first-request-private.json')]
        write_new(raw_path, read(parent/f'evidence-{n}-private.json'))
        write_new(packet_path, artifacts[name])
        write_new(payload_path, payloads[name])
        protected.update({str(p): file_hash(p) for p in (raw_path, packet_path, payload_path)})
        selected[name] = {'raw_evidence_path': str(raw_path), 'first_payload_sha': digest(payloads[name]),
            'evidence_sha': case['packet']['source_evidence_sha256'], 'session': output.name+'-'+name,
            'session_manifest': {'pilot': base['contract_sha256'], 'case': name,
                                 'evidence': case['packet']['source_evidence_sha256'], 'profile': digest(profile)}}
    contract = {'version': 'report-quality-batch-v1' if program_path else channel.VERSION, 'source_root': str(SOURCE), 'source': source,
        'files': protected, 'cases': selected, 'registry_index': str(registry_index),
        'campaign': output.name, 'max_requests': 4, 'max_requests_per_case': 2, 'order': ['C1', 'C2'],
        'profile': profile, 'pricing_date': date.today().isoformat(), 'unknown': before['unknown'][0],
        'baseline': previous_rows, 'budget_before': before, 'closed_campaigns': closed,
        'preparation_contract_sha': base['contract_sha256'], 'repair_source': 'actual_first_response_only',
        'no_new_tools_or_paid_judges': True, 'authorization_required_separately': True}
    if program_path:
        contract.update(program_id=program['program_id'],program_sha256=program['contract_sha256'],
                        kind='report',data_kind='real_development')
    contract['contract_sha256'] = digest(contract)
    channel.validate_files(contract)
    if source_snapshot() != source or readonly_budget(budget.path) != before:
        raise ExecutionStop('report_pilot_inputs_changed_during_freeze')
    write_new(output/'contract-private.json', contract)
    write_new(output/'manifest.json', {'contract_sha256': contract['contract_sha256'], 'authorized': False,
        'new_model_calls': 0, 'case_order': ['C1', 'C2'], 'max_requests': 4})
    return {'status': 'refrozen_requires_bound_authorization', 'contract_sha256': contract['contract_sha256'],
            'message_bytes': [c['message_utf8_bytes'] for c in base['cases']], 'new_model_calls': 0}


def authorize(runtime, output, authorization_path):
    contract = read(output/'contract-private.json')
    statement = read(authorization_path)
    if set(statement) != {'contract_sha256', 'authorization'} or statement['contract_sha256'] != contract['contract_sha256']:
        raise ExecutionStop('report_pilot_permission_not_bound_to_new_contract')
    budget = ledger(runtime)
    # No POST, no attempt registration or reservation occurs here.
    SessionStore(budget).campaign(contract['campaign'], contract['contract_sha256'], cap_each=4,
                                  group_key='route5_supplement_v3')
    approval = channel.register(budget, output/'contract-private.json', statement['authorization'])
    path = output/'authorization-bound.json'
    if path.exists():
        if read(path) != approval:
            raise ExecutionStop('report_pilot_saved_authorization_changed')
    else:
        write_new(path, approval)
    return {'status': 'authorized_exact_two_cases_max_four', 'contract_sha256': contract['contract_sha256'], 'new_model_calls': 0}


def run_case(runtime, output, case, *, budget=None, transport=None):
    budget = budget or ledger(runtime)
    saved = output/(case+'-result-private.json')
    if saved.exists():
        return {'status': 'saved_case_no_new_request', 'case': case, 'result_sha256': file_hash(saved)}
    cfg = read(output/'contract-private.json')
    with budget.transaction() as db:
        cfg = channel.load_grant(db, cfg['campaign'])
    if budget.mode == 'live':
        connection_preflight()
    store = SessionStore(budget)
    identifier = cfg['cases'][case]['session']
    deadline = store.open(identifier, cfg['campaign'], cfg['cases'][case]['session_manifest'], 'deepseek', calls=2, seconds=600)
    results = []
    report = None
    # Held-out boundary validation is one-shot, never rescued by repair.
    one_shot = cfg.get('data_kind') in ('synthetic_validation','real_validation')
    for slot in range(1 if one_shot else 2):
        try:
            with budget.transaction() as db:
                channel.load_grant(db, cfg['campaign'])
                packet, bundle, plan = channel.expected_packet(db, cfg, case, slot)
            campaign = {'profile_sha256': digest(cfg['profile']), 'packets': [packet], 'session': identifier}
            phase = store.register_packet(identifier, slot, digest(campaign), 'deepseek')
            payload = channel.bind_packet(budget, cfg['campaign'], case, phase, packet)
            request_path = output/(case+f'-slot{slot}-request-private.json')
            if request_path.exists():
                if read(request_path) != payload:
                    raise ExecutionStop('report_pilot_saved_send_request_changed')
            else:
                write_new(request_path, payload)
            provider = v3.JudgmentProvider('deepseek', profile=cfg['profile'], budget=budget,
                campaign=campaign, campaign_sha=digest(campaign), phase=phase, output=output/'receipts', runtime=runtime,
                live=budget.mode == 'live', transport=transport, remaining=lambda: deadline-time.time())
            result = provider.generate(str(slot))
            draft = result.get('repair_source') if slot == 0 else None
            errors = [result['code']] if result['code'] != 'ok' else []
            if result['code'] == 'ok':
                try:
                    draft = v3.apply_patch(v3.NarrativePatch.model_validate_json(canonical(result['action']), strict=True), plan) if slot else result['action']
                    parsed = v3.JudgmentDraft.model_validate_json(canonical(draft), strict=True)
                    errors, report = v3.verify_judgment(parsed, packet, bundle)
                except ValueError:
                    errors = ['report_pilot_merged_draft_invalid']
            if report is not None:
                report['model_identity'] = {k: result[k] for k in ('provider', 'model', 'fingerprint')}
                report.pop('report_sha256')
                report['report_sha256'] = digest(report)
            record = {'slot': slot, 'result': result, 'draft': draft, 'errors': errors, 'report': report,
                      'packet': packet, 'quality_packet_path': None}
            if draft is not None:
                quality = prepare_quality_packet(draft, packet, bundle, source_id=case.lower()+f'-slot{slot}', source_kind='existing_model_output')
                mapping_path = output/(case+f'-slot{slot}-quality-private.json')
                if not mapping_path.exists():
                    write_new(mapping_path, quality)
                    write_new(output/(case+f'-slot{slot}-review-template.json'), blank_review(quality))
                    (output/(case+f'-slot{slot}-reading.md')).write_text(render_quality_mapping(quality), encoding='utf-8')
                elif read(mapping_path) != quality:
                    raise ExecutionStop('report_pilot_quality_mapping_changed')
                record['quality_packet_path'] = str(mapping_path)
            slot_path = output/(case+f'-slot{slot}-result-private.json')
            if slot_path.exists():
                old = read(slot_path)
                # Replay has the same evidence, receipt and result; only this flag differs.
                record['result']['replayed_without_model_request'] = old['result']['replayed_without_model_request']
                if record != old:
                    raise ExecutionStop('report_pilot_saved_result_changed')
            else:
                write_new(slot_path, record)
            results.append({'slot': slot, 'path': str(slot_path), 'sha256': file_hash(slot_path)})
            store.checkpoint(identifier, output/identifier/'checkpoints', {'slot': slot+1, 'results': results, 'report': report})
            if result['code'] not in ('ok', 'invalid_model_action'):
                channel.stop(budget, cfg['campaign'], 'output_or_provider_limit')
                break
            if report is not None:
                break
        except (ValueError, OSError, KeyError, TypeError) as error:
            code = str(error) if isinstance(error, (ExecutionStop, ToolBoundaryError, ActionFailure)) else 'report_pilot_execution_guard_failed'
            # Codes only; no arbitrary exception text, response or credentials.
            if not all(c.islower() or c == '_' for c in code):
                code = 'report_pilot_execution_guard_failed'
            channel.stop(budget, cfg['campaign'], code)
            write_new(output/(case+'-STOP.json'), {'reason': code, 'case': case, 'completed_slots': results})
            return {'status': 'stopped', 'reason': code, 'case': case}
    final = {'case': case, 'contract_sha256': cfg['contract_sha256'], 'slots': results, 'report': report,
             'status': 'review_required', 'new_tool_calls': 0, 'independent_judge': 'Pending', 'personal_readability': 'Pending'}
    write_new(saved, final)
    return {'status': 'review_required', 'case': case, 'attempts': store.used(identifier), 'guard_report_formed': report is not None}


def review_case(runtime, output, case, review_path, *, budget=None):
    budget = budget or ledger(runtime)
    cfg = read(output/'contract-private.json')
    final = read(output/(case+'-result-private.json'))
    supplied = read(review_path)
    if final['contract_sha256'] != cfg['contract_sha256'] or supplied['case'] != case or supplied['result_sha256'] != file_hash(output/(case+'-result-private.json')):
        raise ExecutionStop('report_pilot_review_target_changed')
    if len(supplied['drafts']) != len(final['slots']) or not final['slots']:
        raise ExecutionStop('report_pilot_review_each_attempt_required')
    assessed = []
    for bound, item in zip(final['slots'], supplied['drafts']):
        if bound['slot'] != item['slot'] or file_hash(Path(bound['path'])) != bound['sha256']:
            raise ExecutionStop('report_pilot_review_slot_changed')
        row = read(Path(bound['path']))
        if row['quality_packet_path']:
            quality = read(Path(row['quality_packet_path']))
            if quality['source']['draft_sha256'] != digest(row['draft']):
                raise ExecutionStop('report_pilot_review_draft_changed')
            if item['review']['reviewer']['kind'] != 'assistant_development':
                raise ExecutionStop('report_pilot_development_review_label_required')
            value = assess_quality(quality, item['review'])
            risk = item['risk_level_review']
            if risk['status'] not in ('Pass', 'Fail', 'Pending') or not risk['reason'].strip():
                raise ExecutionStop('report_pilot_risk_review_missing')
            status = 'Fail' if row['errors'] or value['status'] == 'Fail' or risk['status'] == 'Fail' else 'Pending' if value['status'] == 'Pending' or risk['status'] == 'Pending' else 'Pass'
            assessed.append({'slot': item['slot'], 'quality': status, 'assessment': value, 'risk_level_review': risk})
        else:
            if not row['errors'] or item.get('review') is not None:
                raise ExecutionStop('report_pilot_unusable_draft_review_invalid')
            assessed.append({'slot': item['slot'], 'quality': 'Fail', 'structure': 'Fail', 'semantic_facts': 'Pending',
                             'scores': None, 'reason': row['errors']})
    grade = assessed[-1]['quality']
    summary = {'case': case, 'quality': grade, 'drafts': assessed, 'input_review_sha256': file_hash(review_path),
               'case_result_sha256': supplied['result_sha256'], 'reviewer': 'assistant_development',
               'independent_judge': 'Pending', 'personal_readability': 'Pending'}
    path = output/(case+'-review.json')
    if path.exists():
        if read(path) != summary:
            raise ExecutionStop('report_pilot_review_is_immutable')
    else:
        write_new(path, summary)
    with budget.transaction() as db:
        channel.load_grant(db, cfg['campaign'], allow_closed=True)
        wanted = (cfg['campaign'], case, grade, str(path), file_hash(path))
        old = db.execute('SELECT * FROM report_pilot_reviews WHERE campaign_id=? AND case_id=?', (cfg['campaign'], case)).fetchone()
        if old and old != wanted:
            raise ExecutionStop('report_pilot_saved_review_changed')
        if not old:
            db.execute('INSERT INTO report_pilot_reviews VALUES(?,?,?,?,?)', wanted)
    if grade == 'Pending':
        channel.stop(budget, cfg['campaign'], 'review_pending')
    elif case == 'C2':
        with budget.transaction() as db:
            grades = dict(db.execute('SELECT case_id,quality FROM report_pilot_reviews WHERE campaign_id=?', (cfg['campaign'],)))
        reason = 'two_consecutive_completed_case_failures' if grades == {'C1': 'Fail', 'C2': 'Fail'} else 'two_cases_completed'
        channel.stop(budget, cfg['campaign'], reason)
        write_new(output/'STOP.json', {'reason': reason, 'grades': grades, 'no_further_requests': True})
    return {'status': 'review_recorded', 'case': case, 'quality': grade, 'new_model_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['freeze', 'authorize', 'run-case', 'review'])
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pricing-evidence', type=Path)
    parser.add_argument('--authorization', type=Path)
    parser.add_argument('--review', type=Path)
    parser.add_argument('--case', choices=['C1', 'C2'])
    args = parser.parse_args()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    allowed = runtime/'reports/generated/agent-investigation-upgrade/route5/report-v3'
    if output == allowed or not output.is_relative_to(allowed):
        parser.exit(2, 'report_pilot_output_outside_root\n')
    try:
        if args.action == 'freeze': result = freeze(runtime, output, args.pricing_evidence.resolve())
        elif args.action == 'authorize': result = authorize(runtime, output, args.authorization)
        elif args.action == 'run-case':
            if args.case is None: raise ExecutionStop('report_pilot_case_required')
            result = run_case(runtime, output, args.case)
        else:
            if args.case is None or args.review is None: raise ExecutionStop('report_pilot_review_required')
            result = review_case(runtime, output, args.case, args.review)
        print(canonical(result))
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        code = str(error) if isinstance(error, (ExecutionStop, ToolBoundaryError)) else 'report_pilot_invalid_input_no_retry'
        parser.exit(2, code+'\n')


if __name__ == '__main__':
    main()
