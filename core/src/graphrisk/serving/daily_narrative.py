"""Day-end adapter for the existing report contract and shared attempt ledger."""
from copy import deepcopy
import re

from graphrisk.agent import judgment_v3 as legacy, judgment_v12_daily_quotes as quotes
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_tools import DailyInvestigationScope, ToolBoundaryError
from graphrisk.agent.report_generation_profile import quality_report_profile
from graphrisk.agent.upgrade_contract import canonical, digest

CONTRACT = quotes.CONTRACT
VERIFICATION = 'daily-quotes-display-verification-v1'
SCOPE_FIELDS = frozenset({'case_alias', 'transaction_revision', 'feature_revision', 'model_revision',
    'primary_model_version', 'graph_model_version', 'scoring_context', 'investigation_revision'})
CONTEXT_FIELDS = frozenset({'version', 'purpose', 'scope_private', 'source_scope_sha256',
    'source_evidence_sha256', 'refs_private', 'view', 'input_presentation_version',
    'generation_variant', 'judgment_rules_version', 'action_schema_sha256', 'view_sha256',
    'messages_sha256', 'fact_support_contract_sha256', 'context_sha256'})


def _implementation(report_contract):
    if report_contract == quotes.CONTRACT:return quotes
    if report_contract == legacy.CONTRACT:return legacy
    raise ToolBoundaryError('daily_narrative_contract_unknown')


def prepare_daily_narrative(scope, observations, registry, profile, *, report_contract=CONTRACT):
    """Build the actual report packet locally; does not reserve or send a request."""
    if not isinstance(scope, DailyInvestigationScope):
        raise ToolBoundaryError('daily_batch_context_required')
    return _implementation(report_contract).packet_for(scope,observations,registry,profile)


def configure_daily_narrative(delegate, *, narrative_factory, allow_generation=False,
                              report_contract=CONTRACT):
    """Reuse a trusted bounded backend; configuration grants no new paid quota."""
    if type(allow_generation) is not bool:
        raise ToolBoundaryError('invalid_daily_generation_setting')
    if allow_generation and not callable(narrative_factory):
        raise ToolBoundaryError('daily_narrative_factory_required')
    delegate.report_contract=_implementation(report_contract).CONTRACT
    delegate.report_profile=quality_report_profile(delegate.profile)
    delegate.max_narrative_calls=1
    delegate.narrative_factory=narrative_factory
    delegate.allow_investigation=allow_generation
    return delegate


def _safe_scope(scope):
    required = SCOPE_FIELDS - {'investigation_revision'}
    if not isinstance(scope, dict) or not required <= set(scope) or not set(scope) <= SCOPE_FIELDS:
        raise ActionFailure('daily_verification_scope_invalid')
    return deepcopy(scope)


def _private_keys(value):
    if isinstance(value, dict):
        return any(key in {'transaction_id', 'target_event_second', 'targetsecond', 'dataset_source'} or
                   _private_keys(item) for key,item in value.items())
    if isinstance(value, (list, tuple)):return any(_private_keys(item) for item in value)
    return False


def _packet(context):
    if (not isinstance(context, dict) or set(context) != CONTEXT_FIELDS or
            context.get('version') != VERIFICATION or
            context.get('purpose') != 'display_validation_not_original_request_proof' or
            context['context_sha256'] != digest({k:v for k,v in context.items() if k!='context_sha256'})):
        raise ActionFailure('daily_verification_context_changed')
    packet = {key:deepcopy(value) for key,value in context.items()
              if key not in {'version', 'purpose', 'context_sha256'}}
    packet['scope_private'] = _safe_scope(packet['scope_private'])
    if _private_keys(packet['view']):raise ActionFailure('daily_verification_private_data')
    if not re.fullmatch(r'[a-f0-9]{64}', packet['source_scope_sha256']):
        raise ActionFailure('daily_verification_scope_digest_invalid')
    # This is a new local display binding over the whitelisted source metadata,
    # never a replacement for the original private request or paid receipt.
    packet['fact_support_binding_private'] = digest({key:packet[key] for key in
        ('source_evidence_sha256', 'scope_private', 'refs_private')})
    return packet


def _identity(value):
    if value is None:return None
    if (not isinstance(value, dict) or set(value) != {'provider','model','fingerprint'} or
            value['provider'] not in {'deepseek','local'} or
            not isinstance(value['model'],str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}',value['model']) or
            not (value['fingerprint'] is None and value['provider']=='local' or
                 isinstance(value['fingerprint'],str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',value['fingerprint']))):
        raise ActionFailure('daily_report_identity_invalid')
    return deepcopy(value)


def _verify_core(report, packet, bundle):
    if bundle.scoring_context is None:raise ActionFailure('daily_batch_context_required')
    if (not isinstance(report,dict) or report.get('report_sha256') !=
            digest({key:value for key,value in report.items() if key!='report_sha256'})):
        raise ActionFailure('daily_report_changed')
    errors, rebuilt = quotes.verify(quotes.parse(report['draft'],packet,bundle),packet,bundle)
    if errors or rebuilt is None:raise ActionFailure('daily_report_guard_failed')
    rebuilt['model_identity'] = _identity(report.get('model_identity'))
    rebuilt.pop('report_sha256')
    expected = {key:value for key,value in report.items() if key not in {'report_sha256','verification_context'}}
    if rebuilt != expected:raise ActionFailure('daily_report_changed')


def attach_daily_verification(report, packet, bundle):
    """Attach a public verification context, keeping the model's draft unchanged."""
    _verify_core(report,packet,bundle)
    context = {key:deepcopy(packet[key]) for key in CONTEXT_FIELDS
               if key not in {'version','purpose','context_sha256','scope_private'}}
    context.update(version=VERIFICATION, purpose='display_validation_not_original_request_proof',
        scope_private={key:deepcopy(value) for key,value in packet['scope_private'].items() if key in SCOPE_FIELDS})
    context['context_sha256'] = digest(context)
    result = deepcopy(report)
    result['verification_context'] = context
    result.pop('report_sha256')
    result['report_sha256'] = digest(result)
    return verify_daily_report(result,bundle)


def verify_daily_report(report, bundle):
    """Rebuild quotes semantics; identity is metadata, not authenticated by this check."""
    try:
        _verify_core(report,_packet(report['verification_context']),bundle)
    except (KeyError, TypeError, AttributeError):
        raise ActionFailure('daily_report_verification_invalid') from None
    return deepcopy(report)
