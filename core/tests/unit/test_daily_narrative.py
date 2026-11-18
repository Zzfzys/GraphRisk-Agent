"""Local report-path checks use MockTransport; none prove real writing quality."""
from copy import deepcopy
import json

import httpx
import pytest

from test_autonomous_tool_contracts import scope,index,registry,executor_for,run_core
from test_autonomous_action_providers import profile
from test_autonomous_loop import make_engine,LIMITS
from test_judgment_v3 import good
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_tools import DailyInvestigationScope,DailyScoringContext,ToolBoundaryError
from graphrisk.agent import judgment_v12_daily_quotes as quotes
from graphrisk.agent.judgment_v3 import CONTRACT as LEGACY_CONTRACT,DAY_END_VARIANT,JudgmentProvider,packet_for
from graphrisk.agent.narrative import run_narrative
from graphrisk.agent.report_generation_profile import quality_report_profile
from graphrisk.agent.upgrade_contract import canonical,digest
from graphrisk.serving.conversation_backend import BoundedConversationBackend
from graphrisk.serving.conversations import ConversationError
from graphrisk.serving.daily_narrative import configure_daily_narrative,prepare_daily_narrative,verify_daily_report


@pytest.fixture
def daily_scope(scope):
    context=DailyScoringContext(relative_day=0,batch_end_second=86400,available_through_second=86401,
        batch_id='day-0-fixture',revision=1,sealed_content_sha256=scope.transaction_revision,
        model_version=scope.primary_model_version,model_sha256=scope.model_revision,
        feature_protocol_sha256=scope.feature_revision,category_maps_sha256='e'*64,
        policy_sha256='f'*64,score=0.8,risk_level='high')
    return DailyInvestigationScope(**scope.model_dump(),scoring_context=context)


def test_v12_plain_view_keeps_day_end_provenance_and_private_identifiers_local(daily_scope,index,registry,profile,scope):
    executor,_=executor_for(daily_scope,index);run_core(executor)
    packet=prepare_daily_narrative(daily_scope,executor.observations,registry,quality_report_profile(profile))
    assert packet['generation_variant']==quotes.GENERATION
    assert packet['view']['scoring_context']['temporal_scope']=='relative_day_end'
    assert packet['view']['scoring_context']['available_through_second']==86401
    assert '不能称为交易发生时' in packet['view']['score_time_contract']
    assert daily_scope.transaction_id not in canonical(packet['view'])
    assert 'PRIVATE-DEVICE' not in canonical(packet['view'])
    assert packet['scope_private']['transaction_id']==daily_scope.transaction_id
    with pytest.raises(ToolBoundaryError,match='daily_batch_context_required'):
        prepare_daily_narrative(scope,executor.observations,registry,profile)


def make_backend(tmp_path,daily_scope,index,registry,profile,*,defect=None,enabled=True,
                 report_contract=quotes.CONTRACT):
    engine,budget,_,_=make_engine(tmp_path,daily_scope,index,registry,profile)
    executor,_=executor_for(daily_scope,index);run_core(executor)
    bundle=build_evidence(daily_scope,executor.observations,registry)
    engine.store.campaign('daily-report-fixture','9'*64,cap_each=6,group_key='route5')
    backend=BoundedConversationBackend(entries=[dict(scope=daily_scope,registry=registry,
        observations=executor.observations,guard=lambda:None)],store=engine.store,
        campaign='daily-report-fixture',provider='deepseek',profile=profile,limits=LIMITS,
        directory=tmp_path/'daily-reports',provider_factory=None,tool_batch=None)
    posts=[]
    def transport(request):
        posts.append(json.loads(request.content))
        value=good()
        if defect=='unknown_reference':value['rationale']['refs']=['F999']
        if defect=='event_time_score':
            value['rationale']={'text':'交易发生时已获得即时评分，值得据此核查。','refs':['F8']}
        return httpx.Response(200,json={'model':'deepseek-flash','system_fingerprint':'fixture-fp',
            'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':canonical(value)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':300,'total_tokens':400,
                     'prompt_cache_hit_tokens':5,'prompt_cache_miss_tokens':95}})
    def factory(campaign,phase,deadline):
        provider = quotes.Provider if report_contract==quotes.CONTRACT else JudgmentProvider
        return provider('deepseek',profile=backend.report_profile,budget=budget,
            campaign=campaign,campaign_sha=digest(campaign),phase=phase,output=tmp_path/'receipts',
            runtime=tmp_path,transport=httpx.MockTransport(transport))
    configure_daily_narrative(backend,narrative_factory=factory,allow_generation=enabled,
                              report_contract=report_contract)
    return backend,budget,bundle,posts


@pytest.mark.parametrize('defect,expected',[ (None,None),
    ('unknown_reference','daily_quotes_schema_invalid'),
    ('event_time_score','day_end_score_not_available_at_target_event')])
def test_actual_budgeted_v12_dispatch_uses_one_draft_no_repair_and_replays_receipt(
        tmp_path,daily_scope,index,registry,profile,defect,expected):
    backend,budget,bundle,posts=make_backend(tmp_path,daily_scope,index,registry,profile,defect=defect)
    args=(backend,daily_scope.case_alias,daily_scope.binding_sha256,'daily-first-draft',bundle.evidence_version)
    result=run_narrative(*args)
    assert backend.report_contract==quotes.CONTRACT and backend.max_narrative_calls==1
    assert result['model_calls']==1 and result['tool_calls']==0 and len(posts)==1
    assert 'repair' not in json.loads(posts[0]['messages'][1]['content'])
    with budget.transaction() as db:
        assert db.execute('SELECT max_calls FROM autonomous_sessions WHERE session_id=?',('daily-first-draft',)).fetchone()==(1,)
    if expected:
        assert result['report'] is None and result['status']=='rejected'
        assert expected in result['failure_codes']
    else:
        report=result['report']
        assert result['status']=='review_required' and report['version']=='separated-risk-report-v12-daily-quotes-v2'
        assert report['generation_variant']==quotes.GENERATION
        assert report['program']['scoring_context']==bundle.scoring_context.model_dump(mode='json')
        assert any('日终 XGBoost 主评分' in row['text'] for row in report['program']['facts'])
        assert report['semantic_quality']=='requires_review' and report['quality_scores'] is None
        assert report['judgment']['counterevidence']==[]
        assert verify_daily_report(report,bundle)==report
    assert run_narrative(*args)==result
    assert len(posts)==1 and len(list((tmp_path/'receipts').glob('*.json')))==1


def test_offline_configuration_cannot_authorize_a_model_call(tmp_path,daily_scope,index,registry,profile):
    backend,budget,bundle,posts=make_backend(tmp_path,daily_scope,index,registry,profile,enabled=False)
    before=deepcopy(budget.snapshot())
    with pytest.raises(ConversationError,match='investigation_generation_disabled'):
        run_narrative(backend,daily_scope.case_alias,daily_scope.binding_sha256,'daily-disabled',bundle.evidence_version)
    assert not posts and budget.snapshot()==before


def test_invalid_call_limit_stops_before_new_session(tmp_path,daily_scope,index,registry,profile):
    backend,budget,bundle,posts=make_backend(tmp_path,daily_scope,index,registry,profile)
    backend.max_narrative_calls=True
    with pytest.raises(ActionFailure,match='invalid_narrative_call_limit'):
        run_narrative(backend,daily_scope.case_alias,daily_scope.binding_sha256,'bad-limit',bundle.evidence_version)
    assert not posts
    with budget.transaction() as db:
        assert not db.execute('SELECT 1 FROM autonomous_sessions WHERE session_id=?',('bad-limit',)).fetchone()


def test_explicit_legacy_v3_keeps_original_runner_and_manifest(tmp_path,daily_scope,index,registry,profile):
    backend,budget,bundle,posts=make_backend(tmp_path,daily_scope,index,registry,profile,
        report_contract=LEGACY_CONTRACT)
    result=run_narrative(backend,daily_scope.case_alias,daily_scope.binding_sha256,'legacy-day',bundle.evidence_version)
    assert result['report']['version']=='separated-risk-report-v3'
    assert result['report']['generation_variant']==DAY_END_VARIANT
    assert 'verification_context' not in result['report']
    assert len(posts)==1


def test_quotes_rejects_two_calls_before_registering_a_turn(tmp_path,daily_scope,index,registry,profile):
    backend,budget,bundle,posts=make_backend(tmp_path,daily_scope,index,registry,profile)
    backend.max_narrative_calls=2
    with pytest.raises(ActionFailure,match='daily_quotes_single_first_draft_required'):
        run_narrative(backend,daily_scope.case_alias,daily_scope.binding_sha256,'no-repair',bundle.evidence_version)
    assert not posts
    with budget.transaction() as db:
        assert not db.execute('SELECT 1 FROM autonomous_sessions WHERE session_id=?',('no-repair',)).fetchone()
