"""Offline formal-version plumbing and display binding, not writing-quality evaluation."""
from copy import deepcopy

import pytest

from test_autonomous_tool_contracts import scope, index, registry, executor_for, run_core
from test_autonomous_action_providers import profile
from test_daily_narrative import daily_scope, make_backend
from test_judgment_v3 import good
from graphrisk.agent import judgment_v3 as legacy, judgment_v12_daily_quotes as quotes
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_tools import ToolBoundaryError
from graphrisk.agent.narrative import run_narrative
from graphrisk.agent.report_generation_profile import quality_report_profile
from graphrisk.agent.upgrade_contract import canonical, digest
from graphrisk.serving.daily_narrative import (
    CONTRACT, SCOPE_FIELDS, attach_daily_verification, configure_daily_narrative,
    prepare_daily_narrative, verify_daily_report,
)


@pytest.fixture
def formal(daily_scope,index,registry,profile):
    executor,_=executor_for(daily_scope,index);run_core(executor)
    bundle=build_evidence(daily_scope,executor.observations,registry)
    packet=prepare_daily_narrative(daily_scope,executor.observations,registry,quality_report_profile(profile))
    errors,report=quotes.verify(quotes.parse(good(),packet,bundle),packet,bundle)
    assert errors==[]
    report['model_identity']={'provider':'deepseek','model':'deepseek-flash','fingerprint':'fixture-fp'}
    report.pop('report_sha256');report['report_sha256']=digest(report)
    return report,packet,bundle,daily_scope


def rehash(report):
    context=report['verification_context']
    context['context_sha256']=digest({k:v for k,v in context.items() if k!='context_sha256'})
    report['report_sha256']=digest({k:v for k,v in report.items() if k!='report_sha256'})


def test_default_quotes_packet_and_explicit_legacy_packet_are_separate(daily_scope,index,registry,profile):
    executor,_=executor_for(daily_scope,index);run_core(executor)
    selected=quality_report_profile(profile)
    packet=prepare_daily_narrative(daily_scope,executor.observations,registry,selected)
    old=prepare_daily_narrative(daily_scope,executor.observations,registry,selected,
        report_contract=legacy.CONTRACT)
    assert CONTRACT==quotes.CONTRACT
    assert packet['judgment_rules_version']==quotes.RULES
    assert old['judgment_rules_version']==legacy.RULES
    assert packet['view']['narrative_contract']==quotes.CONTRACT
    assert old['view']['narrative_contract']==legacy.CONTRACT


def test_safe_context_rebuilds_same_draft_identity_and_semantics_without_private_ids(formal):
    report,packet,bundle,scope=formal
    before=deepcopy(report)
    public=attach_daily_verification(report,packet,bundle)
    assert report==before
    assert public['draft']==report['draft']
    assert public['model_identity']==report['model_identity']
    context=public['verification_context']
    assert set(context['scope_private'])<=SCOPE_FIELDS
    assert context['scope_private']['scoring_context']==bundle.scoring_context.model_dump(mode='json')
    assert context['view']==packet['view']
    assert context['source_scope_sha256']==scope.binding_sha256
    assert context['purpose']=='display_validation_not_original_request_proof'
    assert 'transaction_id' not in canonical(context) and 'target_event_second' not in canonical(context)
    assert scope.transaction_id not in canonical(context)
    assert verify_daily_report(public,bundle)==public
    assert attach_daily_verification(public,packet,bundle)==public


@pytest.mark.parametrize('changed', ['source_revision','source_model','evidence','view','private_id','extra_context'])
def test_recomputed_outer_hash_cannot_hide_changed_verification_context(formal,changed):
    report,packet,bundle,_=formal
    public=attach_daily_verification(report,packet,bundle)
    context=public['verification_context']
    if changed=='source_revision':context['scope_private']['transaction_revision']='f'*64
    elif changed=='source_model':context['scope_private']['primary_model_version']='wrong-model'
    elif changed=='evidence':context['source_evidence_sha256']='f'*64
    elif changed=='view':context['view']['facts'][0][1]='changed factual text'
    elif changed=='private_id':context['scope_private']['transaction_id']='private-id'
    else:context['unapproved_field']='not allowed'
    rehash(public)
    with pytest.raises((ActionFailure,ValueError)):
        verify_daily_report(public,bundle)


@pytest.mark.parametrize('changed', ['unknown_ref','extra_report','bad_identity','program'])
def test_invalid_report_is_not_made_valid_by_attaching_context_or_rehashing(formal,changed):
    report,packet,bundle,_=formal
    public=attach_daily_verification(report,packet,bundle)
    if changed=='unknown_ref':public['draft']['rationale']['refs']=['F999']
    elif changed=='extra_report':public['automatic_approval']=True
    elif changed=='bad_identity':public['model_identity']['api_key']='never allowed'
    else:public['program']['limitations'].append('altered program text')
    rehash(public)
    with pytest.raises((ActionFailure,ValueError)):
        verify_daily_report(public,bundle)


def test_private_keys_in_generated_view_are_not_persisted_even_with_valid_hashes(formal):
    report,packet,bundle,_=formal
    # Privacy validation is a separate boundary from the quote checker.
    packet=deepcopy(packet)
    packet['view']['private_payload']={'transaction_id':'private-id'}
    packet['view_sha256']=digest(packet['view'])
    packet['messages_sha256']=digest([quotes.PROMPT,canonical(packet['view'])])
    with pytest.raises((ActionFailure,ValueError)):
        attach_daily_verification(report,packet,bundle)


def test_version_change_cannot_restore_a_checkpoint_from_the_other_report_contract(
        tmp_path,daily_scope,index,registry,profile):
    backend,budget,bundle,posts=make_backend(tmp_path,daily_scope,index,registry,profile)
    args=(backend,daily_scope.case_alias,daily_scope.binding_sha256,'version-bound-turn',bundle.evidence_version)
    first=run_narrative(*args)
    assert first['report']['contract']==quotes.CONTRACT
    before=deepcopy(budget.snapshot())
    backend.report_contract=legacy.CONTRACT
    with pytest.raises(ExecutionStop,match='session_registration_changed'):
        run_narrative(*args)
    assert len(posts)==1 and budget.snapshot()==before


def test_unknown_implementation_is_rejected_before_any_provider_work(
        tmp_path,daily_scope,index,registry,profile):
    backend,budget,bundle,posts=make_backend(tmp_path,daily_scope,index,registry,profile)
    with pytest.raises(ActionFailure,match='unsupported_judgment_implementation'):
        legacy.run_judgment(backend,daily_scope.case_alias,daily_scope.binding_sha256,
            'unknown-implementation',bundle.evidence_version,implementation=legacy)
    assert not posts


def test_unknown_daily_configuration_contract_is_not_an_implicit_fallback(profile):
    from types import SimpleNamespace
    with pytest.raises(ToolBoundaryError,match='daily_narrative_contract_unknown'):
        configure_daily_narrative(SimpleNamespace(profile=profile),narrative_factory=None,
                                  report_contract='unknown-contract')


def test_quotes_failure_codes_remain_strings_and_full_diagnostics_are_preserved(
        tmp_path,daily_scope,index,registry,profile):
    backend,_,bundle,posts=make_backend(tmp_path,daily_scope,index,registry,profile,defect='event_time_score')
    result=run_narrative(backend,daily_scope.case_alias,daily_scope.binding_sha256,
        'typed-failure-diagnostics',bundle.evidence_version)
    assert result['report'] is None and result['status']=='rejected' and len(posts)==1
    assert result['failure_codes'] and all(isinstance(code,str) for code in result['failure_codes'])
    assert 'day_end_score_not_available_at_target_event' in result['failure_codes']
    assert any(isinstance(issue,dict) and issue.get('code')=='day_end_score_not_available_at_target_event'
               for issue in result['failure_diagnostics'])
