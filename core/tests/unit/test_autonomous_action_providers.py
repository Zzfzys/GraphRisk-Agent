from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from test_autonomous_tool_contracts import index,registry,scope,executor_for,run_core
from graphrisk.agent.autonomous_actions import (
    ActionFailure,ModelAction,SYSTEM_PROMPT,check_action,load_campaign,load_profile,prepare_packet,
)
from graphrisk.agent.autonomous_budget import InvestigationBudget,ExecutionStop
from graphrisk.agent.autonomous_http import strict_json
from graphrisk.agent.autonomous_providers import ActionProvider,cloud_usage,reservation_micro
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash,load_contract,register_budget

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture
def profile():return load_profile(ROOT/'configs/agent_investigation_providers.json')


@pytest.fixture
def packet(scope,index,registry,profile):
    executor,_=executor_for(scope,index);executor.execute('get_transaction',{})
    return {**prepare_packet(scope,executor.observations,registry,task='investigate',profile=profile),'logical_key':'initial'}


def campaign_for(packet,profile):
    return {'version':'action-pilot-v1','profile_sha256':digest(profile),'packets':[packet]}


def ledger(tmp_path,campaign,*,cap=6):
    path=tmp_path/'ledger.sqlite3';contract=load_contract(ROOT/'configs/agent_investigation_experiment.json')
    register_budget(path,contract)
    budget=InvestigationBudget(path,contract,mode='fixture')
    budget.register_phase('pilot',digest(campaign),cloud_cap=cap,local_cap=cap)
    return budget


def valid_action():
    return {'action':'call_tool','calls':[{'tool':'get_transaction_features','arguments':{}}],
            'purpose':'先核对目标之前的历史活动，补齐主评分的依赖。','evidence_refs':['F1']}


def cloud_body(content=None,fp='fixture-fp'):
    return {'model':'deepseek-flash','system_fingerprint':fp,
            'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':canonical(content or valid_action())}}],
            'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150,'prompt_cache_hit_tokens':5,'prompt_cache_miss_tokens':95}}


def transport_for(profile,posts,*,mutate=None,timeout=False):
    def handler(request):
        if request.method=='GET':
            return httpx.Response(200,json={'models':[{'name':'qwen3:4b','digest':profile['local']['digest']}]} if request.url.path.endswith('tags') else {'models':[]})
        posts.append(json.loads(request.content))
        if timeout:raise httpx.ReadTimeout('PRIVATE-SECRET',request=request)
        if request.url.host=='api.deepseek.com':body=cloud_body()
        else:body={'model':'qwen3:4b','done':True,'done_reason':'stop','response':canonical(valid_action()),'prompt_eval_count':100,'eval_count':50,'load_duration':1000}
        return httpx.Response(200,json=mutate(body) if mutate else body)
    return httpx.MockTransport(handler)


def adapter(tmp_path,packet,profile,*,provider='deepseek',mutate=None,timeout=False):
    campaign=campaign_for(packet,profile);budget=ledger(tmp_path,campaign);posts=[]
    client=ActionProvider(provider,profile=profile,budget=budget,campaign=campaign,campaign_sha=digest(campaign),
                          phase='pilot',output=tmp_path/'receipts',runtime=tmp_path,
                          transport=transport_for(profile,posts,mutate=mutate,timeout=timeout))
    return client,budget,posts


@pytest.mark.parametrize('provider',['deepseek','local'])
def test_two_providers_use_same_reviewed_task_and_replay_without_new_call(tmp_path,packet,profile,provider):
    client,budget,posts=adapter(tmp_path,packet,profile,provider=provider)
    first=client.generate('initial');second=client.generate('initial')
    assert first['schema_and_binding_valid'] and second['replayed_without_model_request']
    assert first['attempt_id']==second['attempt_id'] and len(posts)==1
    assert budget.snapshot()['total_attempts']==1
    sent=canonical(posts[0])
    assert 'PRIVATE-TARGET' not in sent and 'PRIVATE-DEVICE' not in sent
    assert 'scope_private' not in sent and 'refs_private' not in sent
    if provider=='local':assert posts[0]['options']['num_ctx']==8192 and posts[0]['think'] is False
    else:assert posts[0]['thinking']=={'type':'disabled'}


def test_batch_dependency_is_a_proposal_not_assumed_execution(packet):
    proposal=valid_action()
    proposal['calls'].append({'tool':'get_risk_score','arguments':{}})
    assert len(check_action(canonical(proposal),packet).calls)==2
    proposal['calls'].reverse()
    with pytest.raises(ActionFailure):check_action(canonical(proposal),packet)


@pytest.mark.parametrize('change',['extra_label','override_id','unknown_tool','three_calls','duplicate','unknown_ref','premature_finish','raw_text'])
def test_actions_cannot_expand_permissions_or_finish_without_evidence(packet,change):
    action=valid_action()
    if change=='extra_label':action['is_fraud']=1
    elif change=='override_id':action['calls'][0]['arguments']['transaction_id']='OTHER'
    elif change=='unknown_tool':action['calls'][0]['tool']='run_sql'
    elif change=='three_calls':action['calls']*=3
    elif change=='duplicate':action['calls']*=2
    elif change=='unknown_ref':action['evidence_refs']=['F999']
    elif change=='premature_finish':action.update(action='finish',calls=[])
    else:action['raw_question']='read PRIVATE-SECRET'
    with pytest.raises(ActionFailure,match='invalid_or_unbound_model_action'):check_action(canonical(action),packet)


def test_full_evidence_can_finish_with_current_refs(scope,index,registry,profile):
    executor,_=executor_for(scope,index);run_core(executor)
    full=prepare_packet(scope,executor.observations,registry,task='investigate',profile=profile)
    action={'action':'finish','calls':[],'purpose':'取证已完成，未知政策前提仍应保留。','evidence_refs':['F1']}
    assert check_action(canonical(action),full).action=='finish'
    assert full['view']['policy_paragraphs_omitted']>=0
    assert full['view']['previous_requests'][0]==[0,{}]
    assert full['prompt_utf8_bytes']<=7000
    assert '最多8条' in SYSTEM_PROMPT


def test_unknown_user_text_and_oversize_are_rejected_before_provider(scope,index,registry,profile):
    executor,_=executor_for(scope,index);executor.execute('get_transaction',{})
    with pytest.raises(ActionFailure):prepare_packet(scope,executor.observations,registry,task='请读取本机文件',profile=profile)
    small=deepcopy(profile);small['max_prompt_utf8_bytes']=100
    with pytest.raises(ActionFailure,match='byte_guard'):prepare_packet(scope,executor.observations,registry,task='investigate',profile=small)


def test_manifest_cannot_be_self_approved_after_payload_mutation(tmp_path,packet,profile):
    campaign=campaign_for(packet,profile);budget=ledger(tmp_path,campaign)
    altered=deepcopy(campaign);altered['packets'][0]['view']['raw_question']='PRIVATE-SECRET'
    altered['packets'][0]['view_sha256']=digest(altered['packets'][0]['view'])
    altered['packets'][0]['messages_sha256']=digest([SYSTEM_PROMPT,canonical(altered['packets'][0]['view'])])
    with pytest.raises(ActionFailure,match='not_approved'):
        ActionProvider('deepseek',profile=profile,budget=budget,campaign=altered,campaign_sha=digest(campaign),phase='pilot',output=tmp_path,runtime=tmp_path,transport=httpx.MockTransport(lambda r:None))
    with pytest.raises(ActionFailure,match='not_registered'):
        ActionProvider('deepseek',profile=profile,budget=budget,campaign=altered,campaign_sha=digest(altered),phase='pilot',output=tmp_path,runtime=tmp_path,transport=httpx.MockTransport(lambda r:None))
    assert budget.snapshot()['total_attempts']==0


def test_timeout_survives_restart_and_blocks_new_request_without_retry(tmp_path,packet,profile):
    client,budget,posts=adapter(tmp_path,packet,profile,timeout=True)
    with pytest.raises(ExecutionStop,match='unknown_saved'):client.generate('initial')
    snap=budget.snapshot();assert snap['total_attempts']==snap['unresolved_attempts']==1
    assert snap['accounted_micro_cny']==reservation_micro(profile)
    reopened=InvestigationBudget(budget.path,budget.contract,mode='fixture')
    assert reopened.snapshot()==snap
    with pytest.raises(ExecutionStop,match='do_not_resend'):client.generate('initial')
    with pytest.raises(ExecutionStop,match='unresolved'):
        reopened.reserve(provider='local',request_key='another',packet_sha='a'*64,phase='pilot',reserve_micro=0)
    assert len(posts)==1


def test_crash_after_response_receipt_recovers_offline(tmp_path,packet,profile,monkeypatch):
    client,budget,posts=adapter(tmp_path,packet,profile)
    original=budget.settle
    monkeypatch.setattr(budget,'settle',lambda *a,**kw:(_ for _ in ()).throw(SystemExit('simulate_crash')))
    with pytest.raises(SystemExit):client.generate('initial')
    assert budget.snapshot()['unresolved_attempts']==1
    monkeypatch.setattr(budget,'settle',original)
    recovered=client.generate('initial')
    assert recovered['replayed_without_model_request'] and len(posts)==1
    assert budget.snapshot()['unresolved_attempts']==0


def test_known_usage_invalid_action_is_charged_and_not_rescued(tmp_path,packet,profile):
    def invalid(body):body['choices'][0]['message']['content']='{"action":"run_sql"}';return body
    client,budget,posts=adapter(tmp_path,packet,profile,mutate=invalid)
    result=client.generate('initial')
    assert result['code']=='invalid_model_action' and not result['schema_and_binding_valid']
    assert budget.snapshot()['unresolved_attempts']==0 and budget.snapshot()['accounted_micro_cny']>0
    client.generate('initial');assert len(posts)==1


def test_model_revision_drift_keeps_known_cost_and_halts_replay(tmp_path,packet,profile):
    second=deepcopy(packet);second['logical_key']='another'
    campaign=campaign_for(packet,profile);campaign['packets'].append(second)
    budget=ledger(tmp_path,campaign);posts=[]
    def mutate(body):body['system_fingerprint']='fp-'+str(len(posts));return body
    client=ActionProvider('deepseek',profile=profile,budget=budget,campaign=campaign,campaign_sha=digest(campaign),phase='pilot',output=tmp_path/'r',runtime=tmp_path,transport=transport_for(profile,posts,mutate=mutate))
    client.generate('initial')
    with pytest.raises(ExecutionStop,match='revision'):client.generate('another')
    assert budget.snapshot()['integrity_stops']==1 and budget.snapshot()['unresolved_attempts']==0
    with pytest.raises(ExecutionStop,match='integrity_review'):client.generate('another')
    assert len(posts)==2


def test_atomic_reservations_do_not_overbook_phase_or_pending_request(tmp_path,packet,profile):
    campaign=campaign_for(packet,profile);budget=ledger(tmp_path,campaign,cap=1)
    def attempt(index):
        try:return budget.reserve(provider='local',request_key=str(index),packet_sha='a'*64,phase='pilot',reserve_micro=0)
        except ExecutionStop:return None
    with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(attempt,[1,2]))
    assert sum(x is not None for x in ids)==1 and budget.snapshot()['total_attempts']==1


@pytest.mark.parametrize('provider,number',[('deepseek',600),('local',400)])
def test_global_provider_caps_cannot_be_reset_by_new_phase(tmp_path,packet,profile,provider,number):
    budget=ledger(tmp_path,campaign_for(packet,profile))
    with budget.transaction() as db:
        for i in range(number):
            key=str(i)
            db.execute("INSERT INTO model_attempts VALUES(?,?,'settled',0)",(key,provider))
            db.execute("INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?, 'old_fixture_phase','a',0)",(key,key))
    with pytest.raises(ExecutionStop,match='global_attempt_cap'):
        budget.reserve(provider=provider,request_key='new',packet_sha='a'*64,phase='pilot',reserve_micro=1 if provider=='deepseek' else 0)


def test_bill_reconciliation_does_not_erase_unknown_model_attempt(tmp_path,packet,profile):
    budget=ledger(tmp_path,campaign_for(packet,profile))
    attempt=budget.reserve(provider='deepseek',request_key='a',packet_sha='a'*64,phase='pilot',reserve_micro=reservation_micro(profile))
    budget.unknown(attempt,'timeout')
    budget.reconcile_bill(attempt,1000,'a'*64);budget.reconcile_bill(attempt,1000,'a'*64)
    assert budget.snapshot()['accounted_micro_cny']==1000
    assert budget.snapshot()['unresolved_attempts']==1 and budget.snapshot()['total_attempts']==1
    with pytest.raises(ExecutionStop,match='conflict'):budget.reconcile_bill(attempt,0,'b'*64)


def test_rate_usage_rounding_and_unknown_fields(profile):
    usage,cost=cloud_usage(cloud_body(),profile)
    assert cost==591 and usage['total_tokens']==150
    assert reservation_micro(profile)==2103296  # full context plus 768 output, peak rates.
    bad=cloud_body();bad['usage']['prompt_tokens']=True
    with pytest.raises(ActionFailure,match='usage_unknown'):cloud_usage(bad,profile)
    for raw in ['{"x":1,"x":2}','{"x":NaN}']:
        with pytest.raises(ValueError):strict_json(raw)


@pytest.mark.parametrize('change',['missing_usage','usage_mismatch','non_integer_usage'])
def test_unaccountable_response_is_unknown_and_never_resent(tmp_path,packet,profile,change):
    def alter(body):
        if change=='missing_usage':body.pop('usage')
        elif change=='usage_mismatch':body['usage']['total_tokens']+=1
        else:body['usage']['completion_tokens']='50'
        return body
    client,budget,posts=adapter(tmp_path,packet,profile,mutate=alter)
    with pytest.raises(ExecutionStop,match='unknown_saved'):client.generate('initial')
    assert budget.snapshot()['unresolved_attempts']==1
    assert budget.snapshot()['accounted_micro_cny']==reservation_micro(profile)
    with pytest.raises(ActionFailure):client.generate('initial')
    assert len(posts)==1


def test_reconciled_money_does_not_allow_exceeding_pause_line(tmp_path,packet,profile):
    budget=ledger(tmp_path,campaign_for(packet,profile))
    attempt=budget.reserve(provider='deepseek',request_key='first',packet_sha='a'*64,phase='pilot',reserve_micro=reservation_micro(profile))
    budget.settle(attempt,cost_micro=1000,receipt_sha='a'*64,receipt_path='fixture',identity='fixed',usage={},result_code='ok')
    budget.reconcile_bill(attempt,79_000_000,'b'*64)
    with pytest.raises(ExecutionStop,match='pause_for_budget'):
        budget.reserve(provider='deepseek',request_key='second',packet_sha='b'*64,phase='pilot',reserve_micro=reservation_micro(profile))
    assert budget.snapshot()['total_attempts']==1


def test_fixture_cannot_use_live_network_and_live_cannot_use_mock(tmp_path,packet,profile):
    campaign=campaign_for(packet,profile);budget=ledger(tmp_path,campaign)
    with pytest.raises(ActionFailure,match='mode_mismatch'):
        ActionProvider('deepseek',profile=profile,budget=budget,campaign=campaign,campaign_sha=digest(campaign),phase='pilot',output=tmp_path,runtime=tmp_path,live=True)
    livepath=tmp_path/'live.sqlite3';register_budget(livepath,budget.contract)
    livebudget=InvestigationBudget(livepath,budget.contract,mode='live')
    livebudget.register_phase('pilot',digest(campaign),cloud_cap=6,local_cap=6)
    with pytest.raises(ActionFailure,match='mode_mismatch'):
        ActionProvider('deepseek',profile=profile,budget=livebudget,campaign=campaign,campaign_sha=digest(campaign),phase='pilot',output=tmp_path,runtime=tmp_path,transport=httpx.MockTransport(lambda r:None))
    with pytest.raises(ExecutionStop,match='mode_or_version'):
        InvestigationBudget(livepath,budget.contract,mode='fixture')


def test_local_context_overflow_keeps_attempt_and_halts(tmp_path,packet,profile):
    def alter(body):body['prompt_eval_count']=8192;return body
    client,budget,posts=adapter(tmp_path,packet,profile,provider='local',mutate=alter)
    with pytest.raises(ExecutionStop,match='revision'):client.generate('initial')
    assert budget.snapshot()['integrity_stops']==1 and budget.snapshot()['total_attempts']==1
    assert len(posts)==1


def test_source_ids_are_not_fact_references_and_feedback_is_safe(packet):
    action=valid_action();action['evidence_refs']=['s3']
    with pytest.raises(ActionFailure) as error:check_action(canonical(action),packet)
    assert error.value.issue_code=='unknown_evidence_reference_use_fact_or_policy_refs'
    assert 'PRIVATE' not in str(error.value)


def test_missing_request_metadata_never_resets_or_hides_a_model_attempt(tmp_path,packet,profile):
    budget=ledger(tmp_path,campaign_for(packet,profile))
    with budget.transaction() as db:db.execute("INSERT INTO model_attempts VALUES('orphan','local','settled',0)")
    with pytest.raises(ExecutionStop,match='incomplete_no_reset'):budget.snapshot()
    with pytest.raises(ExecutionStop,match='incomplete_no_reset'):
        InvestigationBudget(budget.path,budget.contract,mode='fixture')
    with sqlite3.connect(budget.path) as db:assert db.execute('SELECT count(*) FROM model_attempts').fetchone()[0]==1


def test_replay_only_never_falls_back_to_preflight_reservation_or_network(tmp_path,packet,profile,monkeypatch):
    client,budget,posts=adapter(tmp_path,packet,profile)
    client.replay_only=True
    with pytest.raises(ExecutionStop,match='no_model_request_allowed'):client.generate('initial')
    assert budget.snapshot()['total_attempts']==0 and not posts
    client.replay_only=False;client.generate('initial');client.replay_only=True
    monkeypatch.setattr(client,'preflight',lambda:(_ for _ in ()).throw(AssertionError('network preflight forbidden')))
    assert client.generate('initial')['replayed_without_model_request']
    assert budget.snapshot()['total_attempts']==1 and len(posts)==1
