from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time

import httpx
import pytest

from test_autonomous_tool_contracts import index,registry,scope,executor_for,run_core
from test_autonomous_action_providers import profile
from graphrisk.agent.autonomous_actions import ActionFailure,active_action_schema,check_action
from graphrisk.agent.autonomous_budget import InvestigationBudget,ExecutionStop
from graphrisk.agent.autonomous_loop import BoundedInvestigation,restore_observations,observation_rows,packet_for,latest_observations
from graphrisk.agent.autonomous_providers import ActionProvider
from graphrisk.agent.autonomous_report import SummaryProvider,EvidenceDraft,verify_summary
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.autonomous_tools import ToolBoundaryError,SPEC_BY_NAME
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.upgrade_contract import canonical,digest,load_contract,register_budget
from graphrisk.investigation.contracts import ToolError

ROOT=Path(__file__).resolve().parents[2]
LIMITS={'max_model_calls':5,'max_tool_calls':8,'max_nodes':12,'max_repairs':1,'seconds':600,'no_progress_limit':2}


def action(calls=(),kind='call_tool',refs=('F1',)):
    return {'action':kind,'calls':[{'tool':name,'arguments':args} for name,args in calls],
            'purpose':'根据当前证据补查或停止。','evidence_refs':list(refs)}


def normal_action(view):
    status=view['tool_status']
    if status['get_transaction_features']=='not_requested':return action([('get_transaction_features',{}),('get_risk_score',{})])
    if status['get_related_entities']=='not_requested':return action([('get_related_entities',{'limit':3}),('find_risky_neighbors',{'limit':3})])
    if status['retrieve_policy']=='not_requested':return action([('retrieve_policy',{'focus':'trusted_device','top_k':6})])
    return action(kind='finish')


def good_summary(view):
    claims=[]
    for ref,meaning,value,available,source in view['facts']:
        metric,subject,unit,window,temporal=view['meanings'][meaning]
        if metric in ('transaction_amount','primary_model_score') and available=='available':
            claims.append(dict(ref=ref,metric=metric,subject=view['subjects'][subject],unit=view['units'][unit],window_seconds=window,
                temporal_scope=view['temporal_scopes'][temporal],value=value,availability=available))
    return {'scope':'evidence_summary_only','finding':'evidence_insufficient' if any(v!='ok' for v in view['tool_status'].values()) else 'observations_only',
            'claims':claims,'policies':[{'ref':p['ref'],'applicability':p['applicability']} for p in view['policies'][:1]],'score_meaning':'uncalibrated_signal'}


def make_engine(tmp_path,scope,index,registry,profile,*,provider='deepseek',mutate=None,choose=normal_action,report=good_summary,seed=False,controlled=False,timeout=False,limits=None,guard=lambda:None):
    contract=load_contract(ROOT/'configs/agent_investigation_experiment.json');path=tmp_path/'budget.sqlite3'
    register_budget(path,contract);budget=InvestigationBudget(path,contract,mode='fixture');store=SessionStore(budget)
    store.campaign('trial','a'*64);deadline=store.open('session','trial',{'scope':scope.binding_sha256},provider)
    profile=deepcopy(profile);profile.update(max_policy_paragraphs=2,prefer_conflicting_policies=True);profile['local']['keep_alive']=0
    posts=[];calls=[]
    def batch(rows,proposals):
        executor,requests=executor_for(scope,index,mutate=mutate);executor.restore_cached(restore_observations(scope,rows));events=[]
        for item in proposals:
            before=len(executor.observations)
            try:
                value=executor.execute(item['tool'],item['arguments'])
                events.append({'tool':item['tool'],'status':value.result().status,'cached':len(executor.observations)==before})
            except ToolBoundaryError as error:
                if str(error)!='tool_dependency_missing':raise
                events.append({'tool':item['tool'],'status':'skipped_dependency'})
        calls.extend(requests)
        return observation_rows(executor.observations),events
    def http(request):
        if request.method=='GET':return httpx.Response(200,json={'models':[{'name':'qwen3:4b','digest':profile['local']['digest']}]} if request.url.path.endswith('tags') else {'models':[]})
        payload=json.loads(request.content);system=payload['messages'][0]['content'] if provider=='deepseek' else payload['system']
        view=json.loads(payload['messages'][1]['content'] if provider=='deepseek' else payload['prompt'])
        kind='report' if system.startswith('你是交易调查证据摘要员') else 'action'
        posts.append((kind,view))
        if timeout:raise httpx.ReadTimeout('private-secret',request=request)
        output=report(view) if kind=='report' else choose(view)
        if provider=='deepseek':
            value={'model':'deepseek-flash','system_fingerprint':'fixture-fp','choices':[{'finish_reason':'stop','message':{'role':'assistant','content':canonical(output)}}],
                'usage':{'prompt_tokens':100,'completion_tokens':300,'total_tokens':400,'prompt_cache_hit_tokens':5,'prompt_cache_miss_tokens':95}}
        else:value={'model':'qwen3:4b','done':True,'done_reason':'stop','response':canonical(output),'prompt_eval_count':100,'eval_count':300}
        return httpx.Response(200,json=value)
    class Factory:
        def __init__(self):self.provider=provider
        def __call__(self,campaign,phase,kind,deadline):
            cls=SummaryProvider if kind=='report' else ActionProvider
            return cls(provider,profile=profile,budget=budget,campaign=campaign,campaign_sha=digest(campaign),phase=phase,
                output=tmp_path/'receipts',runtime=tmp_path,transport=httpx.MockTransport(http),remaining=lambda:deadline-time.time())
    observations=()
    if seed:
        executor,_=executor_for(scope,index,mutate=mutate);run_core(executor);observations=executor.observations
    engine=BoundedInvestigation(scope=scope,registry=registry,profile=profile,limits=limits or LIMITS,store=store,session='session',
        directory=tmp_path/'episode',deadline=deadline,tool_batch=batch,provider_factory=Factory(),source_guard=guard,
        seed_observations=observations,controlled_probe=controlled or seed)
    # Seed without injecting for specific validation tests.
    if seed and not controlled:engine.controlled_probe=False
    return engine,budget,posts,calls


@pytest.mark.parametrize('provider',['deepseek','local'])
def test_real_graph_path_calls_tools_and_checks_actual_generated_summary(tmp_path,scope,index,registry,profile,provider):
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile,provider=provider)
    result=engine.run()
    assert result['status']=='verified_summary' and result['model_calls']==5
    assert {name for name,_ in calls}==set(SPEC_BY_NAME) and result['tool_requests']==6
    assert result['report']['approval_eligible'] is False
    assert any(e['event']=='verify_actual_draft' for e in result['trace'])
    assert engine.run()==result and len(posts)==5 and budget.snapshot()['total_attempts']==5
    assert all('PRIVATE-TARGET' not in canonical(view) and 'PRIVATE-DEVICE' not in canonical(view) for _,view in posts)


def unavailable(name,result):
    if name in ('find_risky_neighbors','get_transaction_features'):
        return {**result,'status':'unavailable','data':None,'evidence':[],'error':ToolError.from_code('capability_unavailable').model_dump(mode='json')}
    return result


def test_batch_rechecks_actual_dependency_after_feature_failure(tmp_path,scope,index,registry,profile):
    def choose(view):
        if view['tool_status']['get_transaction_features']=='not_requested':return normal_action(view)
        return action(kind='stop_insufficient')
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile,mutate=unavailable,choose=choose)
    result=engine.run()
    assert result['status']=='partial_summary' and result['model_calls']==3
    assert 'get_risk_score' not in [name for name,_ in calls]
    assert any(o.get('status')=='skipped_dependency' for e in result['trace'] if e['event']=='tools' for o in e['outcomes'])


def test_wrong_source_ref_is_returned_as_safe_feedback_once(tmp_path,scope,index,registry,profile):
    seen=[]
    def choose(view):
        seen.append(view['workflow']['feedback'])
        if len(seen)==1:return action([('get_transaction_features',{})],refs=('s3',))
        return action(kind='stop_insufficient')
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile,choose=choose)
    result=engine.run()
    assert result['repairs']==1 and result['model_calls']==3
    assert seen[1]==['unknown_evidence_reference_use_fact_or_policy_refs']
    assert result['trace'][1]['code']=='invalid_model_action'


def test_unallowed_tool_is_never_executed_and_repair_is_bounded(tmp_path,scope,index,registry,profile):
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile,choose=lambda _:action([('run_sql',{})]))
    result=engine.run()
    assert result['status']=='rejected' and len(posts)==2 and result['repairs']==1
    assert [name for name,_ in calls]==['get_transaction']


def test_identical_calls_hit_cache_and_stop_without_information_gain(tmp_path,scope,index,registry,profile):
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile,choose=lambda _:action([('get_transaction',{})]))
    result=engine.run()
    assert result['stop_reason']=='no_progress' and result['tool_requests']==1
    assert len(posts)==2 and len(calls)==1


def test_different_queries_are_allowed_but_only_latest_population_is_exposed(tmp_path,scope,index,registry,profile):
    sizes=iter([1,2,3,4])
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile,choose=lambda _:action([('get_related_entities',{'limit':next(sizes)})]))
    result=engine.run()
    assert len([name for name,_ in calls if name=='get_related_entities'])>=3
    assert len(engine.bundle().facts)==20
    assert len(engine.observations())>len(latest_observations(engine.observations()))
    assert result['report'] is None and result['stop_reason'] in ('model_limit_before_model_finish','no_progress')


@pytest.mark.parametrize('bad',['value','metric','subject','window','probability','policy'])
def test_actual_draft_semantics_are_repaired_once_and_original_is_retained(tmp_path,scope,index,registry,profile,bad):
    count=0
    def report(view):
        nonlocal count
        count+=1;value=good_summary(view)
        if count==1:
            if bad=='value':value['claims'][0]['value']=999.0
            elif bad=='metric':value['claims'][0]['metric']='verified_accounts'
            elif bad=='subject':value['claims'][0]['subject']='verified_customer'
            elif bad=='window':value['claims'][0]['window_seconds']=86400
            elif bad=='probability':value['score_meaning']='fraud_probability'
            else:value['policies'][0]['applicability']='triggered'
        return value
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile,report=report,seed=True)
    result=engine.run()
    assert result['status']=='verified_summary' and len(posts)==2 and result['repairs']==1
    assert len(result['first_drafts'])==2 and result['first_drafts'][0]['draft']!=result['first_drafts'][1]['draft']
    assert posts[1][1]['workflow']['feedback']


def test_controlled_missing_graph_and_injected_fault_are_separate_from_model_errors(tmp_path,scope,index,registry,profile):
    def missing_graph(name,result):return unavailable(name,result) if name=='find_risky_neighbors' else result
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile,mutate=missing_graph,seed=True,controlled=True)
    result=engine.run()
    assert result['status']=='partial_summary' and result['repairs']==1 and len(posts)==2
    assert result['first_drafts'][0]['draft']['score_meaning']=='uncalibrated_signal'
    assert any(e['event']=='controlled_draft_fault_not_model_error' for e in result['trace'])


def test_second_bad_summary_is_rejected_without_unbounded_retry(tmp_path,scope,index,registry,profile):
    def bad(view):return {**good_summary(view),'score_meaning':'fraud_probability'}
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile,report=bad,seed=True)
    result=engine.run()
    assert result['status']=='rejected' and result['report'] is None and len(posts)==2


def test_source_change_is_hard_failure_not_a_prompt_repair(tmp_path,scope,index,registry,profile):
    bad=False
    def guard():
        if bad:raise ToolBoundaryError('source_binding_changed')
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile,guard=guard)
    old=engine.tool_batch
    def changed(*args):
        nonlocal bad
        result=old(*args);bad=True;return result
    engine.tool_batch=changed
    result=engine.run()
    assert result['status']=='rejected' and not posts and result['repairs']==0


def test_tool_and_node_limits_never_become_extra_model_budget(tmp_path,scope,index,registry,profile):
    limits={**LIMITS,'max_tool_calls':2}
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile,limits=limits)
    result=engine.run();assert result['stop_reason']=='session_tool_call_limit' and len(posts)==1


def test_unknown_response_is_saved_and_restart_does_not_resend(tmp_path,scope,index,registry,profile):
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile,timeout=True)
    result=engine.run()
    assert result['model_calls']==1 and budget.snapshot()['unresolved_attempts']==1
    engine.run();assert len(posts)==1 and result['report'] is None


def test_pending_response_survives_crash_after_receipt_and_resumes_without_new_post(tmp_path,scope,index,registry,profile,monkeypatch):
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile)
    original=budget.settle
    monkeypatch.setattr(budget,'settle',lambda *a,**k:(_ for _ in ()).throw(SystemExit('crash')))
    with pytest.raises(SystemExit):engine.run()
    assert budget.snapshot()['unresolved_attempts']==1 and len(posts)==1
    monkeypatch.setattr(budget,'settle',original)
    engine.state=engine.store.restore(engine.session,engine.checkpoints)
    result=engine.run()
    assert result['status']=='verified_summary' and len(posts)==5 and result['model_calls']==5
    assert any(e.get('replayed') for e in result['trace'])


def test_checkpoint_tamper_stops_before_any_model_request(tmp_path,scope,index,registry,profile):
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile)
    with budget.transaction() as db:path=db.execute('SELECT checkpoint_path FROM autonomous_sessions').fetchone()[0]
    Path(path).write_text('{}',encoding='utf-8')
    with pytest.raises(ExecutionStop,match='modified'):engine.store.restore(engine.session,engine.checkpoints)
    assert not posts


def test_session_slots_and_deadline_cannot_be_reset(tmp_path,scope,index,registry,profile):
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile)
    first=engine.deadline
    assert engine.store.open('session','trial',{'scope':scope.binding_sha256},'deepseek')==first
    engine.store.register_packet('session',0,'a'*64,'deepseek')
    with pytest.raises(ExecutionStop,match='packet_changed'):engine.store.register_packet('session',0,'b'*64,'deepseek')
    with pytest.raises(ExecutionStop,match='unregistered_session_slot'):engine.store.register_packet('session',5,'a'*64,'deepseek')
    with budget.transaction() as db:db.execute('UPDATE autonomous_sessions SET deadline=0')
    with pytest.raises(ExecutionStop,match='deadline'):
        budget.reserve(provider='deepseek',request_key='new',packet_sha='a'*64,phase='loop-session-0',reserve_micro=1)


def test_session_and_campaign_attempt_caps_are_enforced_inside_reservation(tmp_path,scope,index,registry,profile):
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile)
    engine.run();assert budget.snapshot()['total_attempts']==5
    # Even a corrupted/extra registered phase cannot bypass the episode limit.
    budget.register_phase('extra-phase','f'*64,cloud_cap=1,local_cap=0)
    with budget.transaction() as db:db.execute("INSERT INTO autonomous_phases VALUES('extra-phase','session',99)")
    with pytest.raises(ExecutionStop,match='session_model_call_limit'):
        budget.reserve(provider='deepseek',request_key='extra',packet_sha='f'*64,phase='extra-phase',reserve_micro=1)


def test_deleted_session_metadata_is_detected_instead_of_reopening_budget(tmp_path,scope,index,registry,profile):
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile)
    engine.store.register_packet('session',0,'a'*64,'deepseek')
    with budget.transaction() as db:db.execute("DELETE FROM autonomous_sessions WHERE session_id='session'")
    with pytest.raises(ExecutionStop,match='metadata_incomplete'):
        budget.reserve(provider='deepseek',request_key='new',packet_sha='a'*64,phase='loop-session-0',reserve_micro=1)


@pytest.mark.parametrize('boundary',['development','campaign'])
def test_new_episode_does_not_reset_shared_development_or_pilot_allocation(tmp_path,scope,index,registry,profile,boundary):
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile)
    if boundary=='development':phases=['route2-old']*60
    else:
        phases=[]
        for n in range(3):
            identifier='old'+str(n)
            engine.store.open(identifier,'trial',{'n':n},'deepseek')
            for slot in range(5):phases.append(engine.store.register_packet(identifier,slot,'a'*64,'deepseek'))
    with budget.transaction() as db:
        for i,phase in enumerate(phases):
            key='historical-fixture-'+str(i)
            db.execute("INSERT INTO model_attempts VALUES(?,'deepseek','settled',0)",(key,))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,0)',(key,key,phase,'a'*64))
    phase=engine.store.register_packet('session',0,'b'*64,'deepseek')
    with pytest.raises(ExecutionStop,match='development_allocation_limit' if boundary=='development' else 'campaign_model_call_limit'):
        budget.reserve(provider='deepseek',request_key='new',packet_sha='b'*64,phase=phase,reserve_micro=1)
    assert not posts


def test_last_decision_has_only_terminal_permissions_and_schema_refs_are_existing(scope,index,registry,profile):
    executor,_=executor_for(scope,index);run_core(executor)
    packet=packet_for(scope,executor.observations,registry,profile,kind='action',remaining=2,remaining_tools=1,feedback=[])
    assert packet['view']['workflow']['allowed_actions']==['finish','stop_insufficient'] and packet['view']['tools']==[]
    schema=active_action_schema(packet)
    assert schema['properties']['evidence_refs']['items']['enum']==sorted(packet['refs_private'])
    assert schema['properties']['calls']['maxItems']==1
    with pytest.raises(ActionFailure) as error:check_action(canonical(action([('retrieve_policy',{'focus':'manual_review'})])),packet)
    assert error.value.issue_code=='action_not_allowed_in_current_budget_window'
    assert check_action(canonical(action(kind='finish')),packet).action=='finish'


def test_last_decision_cannot_finish_when_required_tools_were_not_attempted(scope,index,registry,profile):
    executor,_=executor_for(scope,index);executor.execute('get_transaction',{})
    packet=packet_for(scope,executor.observations,registry,profile,kind='action',remaining=2,remaining_tools=7,feedback=[])
    assert packet['view']['workflow']['allowed_actions']==['stop_insufficient']


def test_new_campaign_cannot_reset_route3_group_budget(tmp_path,scope,index,registry,profile):
    engine,budget,posts,calls=make_engine(tmp_path,scope,index,registry,profile)
    for n in range(3):
        identifier='old'+str(n);engine.store.open(identifier,'trial',{'n':n},'deepseek')
        for slot in range(5):
            phase=engine.store.register_packet(identifier,slot,'a'*64,'deepseek');key=identifier+str(slot)
            with budget.transaction() as db:
                db.execute("INSERT INTO model_attempts VALUES(?,'deepseek','settled',0)",(key,))
                db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,0)',(key,key,phase,'a'*64))
    engine.store.campaign('supplement','c'*64,cap_each=5)
    engine.store.open('new-session','supplement',{'supplement':True},'deepseek')
    phase=engine.store.register_packet('new-session',0,'b'*64,'deepseek')
    with pytest.raises(ExecutionStop,match='route3_group_model_call_limit'):
        budget.reserve(provider='deepseek',request_key='new',packet_sha='b'*64,phase=phase,reserve_micro=1)


def test_verifier_reports_all_known_semantic_errors_in_one_feedback_round(scope,index,registry,profile):
    executor,_=executor_for(scope,index);run_core(executor)
    packet=packet_for(scope,executor.observations,registry,profile,kind='report',remaining=2,remaining_tools=2,feedback=[])
    raw=good_summary(packet['view']);raw['score_meaning']='fraud_probability'
    raw['claims'][0]['window_seconds']=0;raw['claims'][0]['subject']='verified_customer'
    raw['claims']=raw['claims'][:1]
    errors,report=verify_summary(EvidenceDraft.model_validate_json(canonical(raw)),packet,build_evidence(scope,latest_observations(executor.observations),registry))
    assert report is None
    assert {'score_meaning_must_remain_uncalibrated','F1.window_seconds_expected_null','F1.subject_mismatch','F8.primary_model_score_claim_required'}<=set(errors)
    assert all('PRIVATE' not in code for code in errors)
