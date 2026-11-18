"""Offline-only freezing for two seen development cases, never live approval."""
from copy import deepcopy
from datetime import date
from pathlib import Path
import sqlite3

from graphrisk.agent.autonomous_tools import ToolBoundaryError
from graphrisk.agent.autonomous_providers import reservation_micro
from graphrisk.agent.judgment_v3 import CONTRACT, RULES, PROMPT, REPAIR, JudgmentDraft, JudgmentProvider
from graphrisk.agent.upgrade_contract import canonical, digest

PILOT = 'report-v3-natural-pilot-v1'
PRICE_URL = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
REVISION_URL = 'https://api-docs.deepseek.com/zh-cn/updates/'
RATES = {'cache_hit': '0.04', 'cache_miss': '2', 'output': '8'}
MODEL = 'deepseek-flash'
REVISION = 'DeepSeek-V4.1-Flash-1017'


def readonly_budget(path):
    """No BEGIN IMMEDIATE, migration, campaign or phase registration."""
    try:
        with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as db:
            db.execute('BEGIN')
            counts = dict(db.execute('SELECT provider,count(*) FROM model_attempts GROUP BY provider'))
            amount = db.execute('SELECT COALESCE(sum(accounted),0) FROM execution_requests').fetchone()[0]
            unknown = []
            cursor = db.execute("SELECT a.attempt_id,a.provider,a.state,a.reserved_micro_cny,r.request_key,r.packet_sha,r.accounted,r.receipt_sha,r.receipt_path,r.result_code,r.identity,r.usage_json,r.integrity_stop FROM model_attempts a JOIN execution_requests r USING(attempt_id) WHERE a.state IN ('reserved','unknown') ORDER BY a.attempt_id")
            names = [c[0] for c in cursor.description]
            unknown = [dict(zip(names, row)) for row in cursor]
            integrity = db.execute('SELECT count(*) FROM execution_requests WHERE integrity_stop=1').fetchone()[0]
            group = db.execute("SELECT count(*) FROM execution_requests r JOIN model_attempts a USING(attempt_id) JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) JOIN autonomous_campaigns c USING(campaign_id) WHERE c.group_key='route5_supplement_v3' AND a.provider='deepseek'").fetchone()[0]
            registration = db.execute('SELECT experiment,caps FROM budget_registration WHERE id=1').fetchone()
            missing = db.execute('SELECT count(*) FROM model_attempts a LEFT JOIN execution_requests r USING(attempt_id) WHERE r.attempt_id IS NULL').fetchone()[0]
            extra = db.execute('SELECT count(*) FROM execution_requests r LEFT JOIN model_attempts a USING(attempt_id) WHERE a.attempt_id IS NULL').fetchone()[0]
            if missing or extra or registration is None:
                raise ToolBoundaryError('pilot_existing_budget_incomplete')
            return {'counts': counts, 'total_attempts': sum(counts.values()), 'accounted_micro_cny': amount,
                    'unknown': unknown, 'integrity_stops': integrity, 'supplement_used': group,
                    'supplement_remaining': 100 - group, 'registration': list(registration)}
    except sqlite3.Error:
        raise ToolBoundaryError('pilot_readonly_budget_unavailable') from None


def reviewed_profile(profile, pricing, verified_date):
    try:
        if date.fromisoformat(verified_date).isoformat() != verified_date:
            raise ValueError
    except (TypeError, ValueError):
        raise ToolBoundaryError('pilot_pricing_date_invalid') from None
    expected = {'verified_date': verified_date, 'pricing_source': PRICE_URL, 'revision_source': REVISION_URL,
                'model': MODEL, 'documented_revision': REVISION, 'peak_cny_per_million': RATES}
    if pricing != expected:
        raise ToolBoundaryError('pilot_pricing_evidence_mismatch')
    value = deepcopy(profile)
    cloud = value['deepseek']
    kind = value.get('report_generation_profile')
    thinking = kind in ('quality-v3-thinking','quality-v3-thinking-low')
    input_limit = 16000 if kind in ('quality-v2-plain16k','quality-v3-direct') or thinking else 7000
    if kind=='quality-v3-direct' and value['max_prompt_utf8_bytes']==20000:input_limit=20000
    output_limit = (8192 if kind=='quality-v3-thinking-low' and value['max_output_tokens']==8192 else 4096) if thinking else (4096 if kind=='quality-v3-direct' else 768)
    effort = 'low' if kind == 'quality-v3-thinking-low' else 'high'
    if kind=='quality-v3-direct' and (value.get('report_thinking') is not False or 'reasoning_effort' in value):
        raise ToolBoundaryError('pilot_report_direct_profile_invalid')
    if thinking and (value.get('report_thinking') is not True or value.get('reasoning_effort') != effort):
        raise ToolBoundaryError('pilot_report_thinking_profile_invalid')
    if value.get('cloud_total_token_budget',8192) not in (8192,16384):
        raise ToolBoundaryError('pilot_cloud_token_budget_invalid')
    if (cloud['endpoint'] != 'https://api.deepseek.com/chat/completions' or cloud['model'] != MODEL
            or cloud['documented_revision'] != REVISION or cloud['peak_cny_per_million'] != RATES
            or cloud['context_limit'] != 1048576 or value['max_output_tokens'] != output_limit
            or value['max_prompt_utf8_bytes'] != input_limit or value['live_enabled_by_default'] is not False):
        raise ToolBoundaryError('pilot_provider_profile_changed')
    cloud['pricing_verified_date'] = verified_date
    return value


def first_payload(packet, profile):
    if (packet.get('schema_version') != 'reviewed-judgment-v3-packet'
            or packet.get('judgment_rules_version') != RULES
            or packet['view'].get('narrative_contract') != CONTRACT or 'repair' in packet['view']):
        raise ToolBoundaryError('pilot_requires_natural_first_draft')
    text = canonical(packet['view'])
    if (packet['view_sha256'] != digest(packet['view']) or packet['messages_sha256'] != digest([PROMPT, text])
            or packet['action_schema_sha256'] != digest(JudgmentDraft.model_json_schema())
            or packet['prompt_utf8_bytes'] != len((PROMPT + text).encode('utf-8'))
            or packet['prompt_utf8_bytes'] > profile['max_prompt_utf8_bytes']):
        raise ToolBoundaryError('pilot_first_packet_changed')
    formatter = object.__new__(JudgmentProvider)
    formatter.provider, formatter.profile = 'deepseek', profile
    payload = formatter.payload(packet)
    encoded = canonical(payload)
    if any(word in encoded for word in ('scope_private', 'refs_private', 'is_fraud', 'fraud_label', 'api_key', 'Authorization')):
        raise ToolBoundaryError('pilot_payload_contains_private_field')
    target = packet['scope_private']['transaction_id']
    if target and target in encoded:
        raise ToolBoundaryError('pilot_raw_identifier_in_payload')
    return payload


def freeze_contract(*, profile, pricing, verified_date, cases, budget, source, parent_contract_sha256, rubric_sha256):
    """Prepare a reviewable batch. This function never authorizes execution."""
    frozen_profile = reviewed_profile(profile, pricing, verified_date)
    if len(cases) != 2 or [c['id'] for c in cases] != ['C1', 'C2']:
        raise ToolBoundaryError('pilot_exact_two_ordered_cases_required')
    if len({c['packet']['source_scope_sha256'] for c in cases}) != 2:
        raise ToolBoundaryError('pilot_cases_not_distinct')
    if budget['integrity_stops'] or budget['supplement_remaining'] < 4:
        raise ToolBoundaryError('pilot_budget_not_ready')
    caps = {'total_attempts': 1000, 'deepseek_attempts': 600, 'local_attempts': 400,
            'hard_limit_cny': 100, 'pause_estimate_cny': 80}
    if budget['registration'] != ['agent-investigation-20261019', canonical(caps)]:
        raise ToolBoundaryError('pilot_immutable_budget_registration_changed')
    if budget['total_attempts'] + 4 > 1000 or budget['counts'].get('deepseek', 0) + 4 > 600:
        raise ToolBoundaryError('pilot_global_attempt_cap')
    reserve = reservation_micro(frozen_profile)
    if budget['accounted_micro_cny'] + 4 * reserve > 80_000_000:
        raise ToolBoundaryError('pilot_pause_budget_limit')
    payloads = {case['id']: first_payload(case['packet'], frozen_profile) for case in cases}
    blockers = ['new_batch_authorization_not_bound', 'no_live_runner_registered_by_freeze']
    if budget['unknown']:
        blockers.append('old_unknown_requires_new_report_specific_quarantine')
    value = {'version': PILOT, 'report_contract': CONTRACT, 'judgment_rules_version': RULES,
             'profile': frozen_profile, 'pricing_evidence': pricing,
             'source': source, 'parent_contract_sha256': parent_contract_sha256, 'rubric_sha256': rubric_sha256,
             'prompt_sha256': digest(PROMPT), 'repair_prompt_sha256': digest(REPAIR),
             'draft_schema_sha256': digest(JudgmentDraft.model_json_schema()),
             'cases': [{'id': c['id'], 'evidence_file_sha256': c['evidence_file_sha256'],
                        'evidence_sha256': c['packet']['source_evidence_sha256'],
                        'scope_sha256': c['packet']['source_scope_sha256'],
                        'packet_sha256': digest(c['packet']), 'first_payload_sha256': digest(payloads[c['id']]),
                        'message_utf8_bytes': c['packet']['prompt_utf8_bytes']} for c in cases],
             'order': ['C1', 'C2'], 'provider': 'deepseek', 'group_key': 'route5_supplement_v3',
             'max_requests': 4, 'max_requests_per_case': 2, 'max_repairs_per_case': 1,
             'model_calls_for_judging': 0, 'automatic_retries': 0, 'new_tool_calls': 0,
             'evaluation_scope': 'seen_development_natural_first_draft_and_optional_repair_not_independent_validation',
             'repair_policy': {'source': 'actual_first_model_response_only', 'request_built_after_response': True,
                               'same_bound_evidence': True, 'max_prompt_utf8_bytes': frozen_profile['max_prompt_utf8_bytes'],
                               'patch_base_hash_and_whitelist_required': True,
                               'overflow': 'stop_before_post_do_not_trim_or_expand',
                               'no_authored_answer_in_messages': True},
             'review': {'standard': 'report-quality-v1', 'known_guard_cannot_be_overridden': True,
                        'semantic_facts': 'Pass|Fail|Pending',
                        'dimensions': ['specificity', 'analysis', 'actionability', 'readability'],
                        'dimension_range': [0, 2], 'minimum_total': 6, 'minimum_each': 1,
                        'risk_level_reasonableness_reviewed': True,
                        'first_and_repaired_outputs_reviewed_separately': True,
                        'reviewer': 'assistant_development_evidence_review_not_independent_acceptance',
                        'independent_judge': 'Pending', 'personal_readability': 'Pending',
                        'review_before_next_case': True, 'pending_is_not_pass_or_fail': True},
             'stop': ['new_unknown_or_usage_missing', 'identity_or_integrity_change', 'source_or_packet_change',
                      'input_or_output_limit', 'budget_or_quarantine_failure',
                      'two_consecutive_completed_case_failures', 'four_total_requests', 'review_pending'],
             'budget_before': deepcopy(budget), 'peak_reserve_micro_each': reserve,
             'maximum_new_reserves_micro_cny': 4 * reserve,
             'conservative_accounted_plus_all_reserves_micro_cny': budget['accounted_micro_cny'] + 4 * reserve,
             'live_enabled': False, 'outbound_authorized': False, 'execution_ready': False,
             'execution_blockers': blockers, 'new_model_calls': 0,
             'preserve_old_failures_stops_unknowns_and_reserved_costs': True}
    value['contract_sha256'] = digest(value)
    return value, payloads


def check_contract(value, *, budget, source, today):
    if value.get('contract_sha256') != digest({k: v for k, v in value.items() if k != 'contract_sha256'}):
        raise ToolBoundaryError('pilot_frozen_contract_changed')
    if value.get('version') != PILOT or value.get('live_enabled') is not False or value.get('execution_ready') is not False:
        raise ToolBoundaryError('pilot_freeze_is_not_live_authorization')
    if value['pricing_evidence']['verified_date'] != today:
        raise ToolBoundaryError('pilot_pricing_reverification_required_new_freeze')
    if budget != value['budget_before']:
        raise ToolBoundaryError('pilot_budget_snapshot_changed')
    if source != value['source']:
        raise ToolBoundaryError('pilot_source_snapshot_changed')
    return {'status': 'frozen_offline_checks_passed_execution_blocked', 'contract_sha256': value['contract_sha256'],
            'max_requests': 4, 'new_model_calls': 0, 'execution_ready': False,
            'execution_blockers': value['execution_blockers']}
