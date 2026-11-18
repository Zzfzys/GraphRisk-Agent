"""Tiny actual graph/provider/receipt tests; no real model or paid requests."""
from copy import deepcopy
import json
import time

import httpx
import pytest

from test_autonomous_tool_contracts import index, registry, scope, executor_for
from test_autonomous_action_providers import profile
from test_autonomous_loop import LIMITS, action, normal_action
from test_daily_narrative import daily_scope
from test_judgment_v3 import good
from graphrisk.agent import judgment_v12_daily_quotes as quotes
from graphrisk.agent.autonomous_budget import ExecutionStop, InvestigationBudget
from graphrisk.agent.autonomous_evidence import EvidenceBundle, build_evidence
from graphrisk.agent.autonomous_loop import BoundedInvestigation, SUMMARY_CONTRACT, observation_rows, restore_observations
from graphrisk.agent.autonomous_providers import ActionProvider
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.report_generation_profile import quality_report_profile
from graphrisk.agent.upgrade_contract import canonical, digest, load_contract, register_budget

from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]


def make_daily(tmp_path, daily_scope, index, registry, profile, *, provider='deepseek',
               bad_first=False, invalid_action=False, timeout=False, full_path=False):
    profile=deepcopy(profile)
    profile.update(max_policy_paragraphs=2, prefer_conflicting_policies=True)
    profile['local']['keep_alive']=0
    contract=load_contract(ROOT/'configs/agent_investigation_experiment.json')
    path=tmp_path/'budget.sqlite3';register_budget(path,contract)
    budget=InvestigationBudget(path,contract,mode='fixture');store=SessionStore(budget)
    store.campaign('trial','a'*64)
    deadline=store.open('session','trial',{'scope':daily_scope.binding_sha256},provider)
    posts=[];requested=[]

    def batch(rows, proposals):
        executor,_=executor_for(daily_scope,index)
        executor.restore_cached(restore_observations(daily_scope,rows))
        events=[]
        for item in proposals:
            value=executor.execute(item['tool'],item['arguments'])
            requested.append(item['tool'])
            events.append({'tool':item['tool'],'status':value.result().status})
        return observation_rows(executor.observations),events

    def http(request):
        if request.method=='GET':
            return httpx.Response(200,json={'models':[{'name':'qwen3:4b','digest':profile['local']['digest']}]} if request.url.path.endswith('tags') else {'models':[]})
        payload=json.loads(request.content)
        system=payload['messages'][0]['content'] if provider=='deepseek' else payload['system']
        view=json.loads(payload['messages'][1]['content'] if provider=='deepseek' else payload['prompt'])
        kind='report' if system==quotes.PROMPT else 'action'
        posts.append((kind,view))
        if timeout:raise httpx.ReadTimeout('fixture',request=request)
        if kind=='report':
            value=good();value['policy']=None
            value['hypotheses']=[];value['gaps']=[]
            if bad_first and sum(k=='report' for k,_ in posts)==1:
                value['rationale']['text']='已完成核查并已核实授权。'
        elif invalid_action and len(posts)==1:
            value=action([('run_sql',{})])
        elif full_path:
            value=normal_action(view)
        elif view['tool_status']['get_transaction_features']=='not_requested':
            value=action([('get_transaction_features',{}),('get_risk_score',{})])
        else:value=action(kind='stop_insufficient')
        if provider=='deepseek':
            body={'model':'deepseek-flash','system_fingerprint':'fixture-fp',
                'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':canonical(value)}}],
                'usage':{'prompt_tokens':100,'completion_tokens':300,'total_tokens':400,
                    'prompt_cache_hit_tokens':5,'prompt_cache_miss_tokens':95}}
        else:body={'model':'qwen3:4b','done':True,'done_reason':'stop','response':canonical(value),'prompt_eval_count':100,'eval_count':300}
        return httpx.Response(200,json=body)

    class Factory:
        def __init__(self):self.provider=provider
        def __call__(self,campaign,phase,kind,deadline):
            cls=quotes.Provider if kind=='report' else ActionProvider
            selected=quality_report_profile(profile) if kind=='report' else profile
            assert campaign['profile_sha256']==digest(selected)
            return cls(provider,profile=selected,budget=budget,campaign=campaign,campaign_sha=digest(campaign),
                phase=phase,output=tmp_path/'receipts',runtime=tmp_path,
                transport=httpx.MockTransport(http),remaining=lambda:deadline-time.time())

    options=dict(scope=daily_scope,registry=registry,profile=profile,limits=LIMITS,store=store,
        session='session',directory=tmp_path/'episode',deadline=deadline,tool_batch=batch,
        provider_factory=Factory(),source_guard=lambda:None,report_contract=quotes.CONTRACT)
    return BoundedInvestigation(**options),budget,posts,requested,options


@pytest.mark.parametrize('provider',['deepseek','local'])
def test_actual_tools_then_current_daily_report_preserves_day_end_and_review(tmp_path,daily_scope,index,registry,profile,provider):
    engine,budget,posts,requested,_=make_daily(tmp_path,daily_scope,index,registry,profile,provider=provider,full_path=True)
    result=engine.run()
    assert result['status']=='verified_report' and result['model_calls']==5
    assert result['tool_requests']==6 and requested[0]=='get_transaction'
    assert posts[0][1]['tool_status']['get_risk_score']=='not_requested'
    assert [kind for kind,_ in posts]==['action','action','action','action','report']
    report=result['report']
    assert report['contract']==quotes.CONTRACT and report['semantic_quality']=='requires_review'
    assert report['quality_scores'] is None and report['approval_eligible'] is False
    assert report['program']['scoring_context']['temporal_scope']=='relative_day_end'
    assert len(result['first_drafts'])==1 and result['report_checks'][0]['native_guard_pass']
    assert result['first_drafts'][0]['draft']==report['draft']
    assert all('PRIVATE-TARGET' not in canonical(view) and 'PRIVATE-DEVICE' not in canonical(view) for _,view in posts)
    assert engine.run()==result and len(posts)==5 and budget.snapshot()['total_attempts']==5


def test_daily_report_native_rejection_is_preserved_then_repaired_once(tmp_path,daily_scope,index,registry,profile):
    engine,budget,posts,_,_=make_daily(tmp_path,daily_scope,index,registry,profile,bad_first=True)
    result=engine.run()
    assert result['status']=='partial_report' and result['model_calls']==4 and result['repairs']==1
    assert len(result['first_drafts'])==2
    assert result['first_drafts'][0]['draft']['rationale']['text']=='已完成核查并已核实授权。'
    assert result['report_checks'][0]['native_guard_pass'] is False
    assert result['report_checks'][1]['native_guard_pass'] is True
    assert 'unperformed_check_claim' in posts[-1][1]['workflow']['feedback']
    assert budget.snapshot()['total_attempts']==4


def test_action_repair_and_report_repair_share_one_allowance(tmp_path,daily_scope,index,registry,profile):
    engine,_,posts,_,_=make_daily(tmp_path,daily_scope,index,registry,profile,bad_first=True,invalid_action=True)
    result=engine.run()
    assert result['status']=='rejected' and result['stop_reason']=='daily_report_guard_rejected'
    assert result['repairs']==1 and len(posts)==4 and result['report'] is None
    assert len(result['first_drafts'])==1 and result['report_checks'][0]['issues']


def test_last_report_cannot_create_a_sixth_call(tmp_path,daily_scope,index,registry,profile):
    engine,budget,posts,_,_=make_daily(tmp_path,daily_scope,index,registry,profile,bad_first=True,full_path=True)
    result=engine.run()
    assert len(posts)==5 and result['model_calls']==5 and budget.snapshot()['total_attempts']==5
    assert result['report'] is None and result['stop_reason']=='daily_report_guard_rejected'
    assert len(result['first_drafts'])==1


def test_restart_cannot_change_the_report_contract(tmp_path,daily_scope,index,registry,profile):
    _,budget,posts,_,options=make_daily(tmp_path,daily_scope,index,registry,profile)
    with pytest.raises(ExecutionStop,match='checkpoint_report_contract_changed'):
        BoundedInvestigation(**{**options,'report_contract':SUMMARY_CONTRACT})
    assert not posts and budget.snapshot()['total_attempts']==0


def test_saved_receipt_resumes_pending_report_without_duplicate_post(tmp_path,daily_scope,index,registry,profile,monkeypatch):
    engine,budget,posts,_,_=make_daily(tmp_path,daily_scope,index,registry,profile)
    settle=budget.settle
    def crash(attempt,**kwargs):
        if len(posts)==3:raise SystemExit('fixture crash after report receipt')
        return settle(attempt,**kwargs)
    monkeypatch.setattr(budget,'settle',crash)
    with pytest.raises(SystemExit):engine.run()
    assert budget.snapshot()['unresolved_attempts']==1 and len(posts)==3
    monkeypatch.setattr(budget,'settle',settle)
    engine.state=engine.store.restore(engine.session,engine.checkpoints)
    result=engine.run()
    assert result['status']=='partial_report' and len(posts)==3
    assert result['model_calls']==3 and any(item.get('replayed') for item in result['trace'])


def test_unknown_response_never_reposts_on_resume(tmp_path,daily_scope,index,registry,profile):
    engine,budget,posts,_,_=make_daily(tmp_path,daily_scope,index,registry,profile,timeout=True)
    result=engine.run()
    assert result['report'] is None and budget.snapshot()['unresolved_attempts']==1
    assert engine.run()==result and len(posts)==1


@pytest.mark.parametrize('changed',['scope','seed'])
def test_daily_mode_rejects_non_daily_or_seeded_shortcuts(tmp_path,daily_scope,scope,index,registry,profile,changed):
    _,budget,posts,_,options=make_daily(tmp_path,daily_scope,index,registry,profile)
    options.update(scope=scope) if changed=='scope' else options.update(controlled_probe=True)
    with pytest.raises(ExecutionStop,match='daily_loop_report_contract_required|daily_loop_requires_actual_investigation'):
        BoundedInvestigation(**options)
    assert not posts and budget.snapshot()['total_attempts']==0


def test_unrequested_day_end_score_stays_unmeasured(daily_scope,registry):
    bundle=build_evidence(daily_scope,(),registry)
    score=next(fact for fact in bundle.facts if fact.metric=='primary_model_score')
    assert score.temporal_scope=='relative_day_end'
    assert score.value is None and score.availability=='not_measured'
    assert score.missing_reason=='not_requested'


@pytest.mark.parametrize('tamper',['hidden_value','available_without_call','ok_without_value','wrong_model','wrong_time'])
def test_partial_daily_bundle_cannot_invent_a_measured_score(daily_scope,registry,tamper):
    value=build_evidence(daily_scope,(),registry).model_dump(mode='json')
    score=next(fact for fact in value['facts'] if fact['metric']=='primary_model_score')
    outcome=next(item for item in value['tool_outcomes'] if item['tool']=='get_risk_score')
    if tamper=='hidden_value':score['value']=daily_scope.scoring_context.score
    elif tamper=='available_without_call':score.update(value=daily_scope.scoring_context.score,availability='available',missing_reason=None)
    elif tamper=='ok_without_value':outcome['status']='ok'
    elif tamper=='wrong_model':score['model_version']='another-model'
    else:score['temporal_scope']='target_event'
    with pytest.raises(ValueError):EvidenceBundle.model_validate_json(canonical(value))
