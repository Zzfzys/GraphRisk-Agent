import httpx,pytest
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from test_narrative import evidence
from test_autonomous_loop import make_engine
from graphrisk.agent.judgment import packet_for,JudgmentProvider,REPAIR
from graphrisk.agent.judgment_evaluation import assert_control
from graphrisk.agent.upgrade_contract import canonical,digest
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_http import post_one,TransportFailure


def test_json_keyword_present_in_repair_prompt():
    assert 'json' in REPAIR.lower()


def test_bad_json_mode_request_stops_before_reservation_or_http(tmp_path,scope,index,registry,profile,evidence):
    engine,budget,_,_=make_engine(tmp_path,scope,index,registry,profile)
    executor,_,bundle=evidence
    packet=packet_for(scope,executor.observations,registry,profile);packet['logical_key']='0'
    class NoJson(JudgmentProvider):
        def system_prompt(self,packet):return '仅返回受控对象'
    packet['messages_sha256']=digest(['仅返回受控对象',canonical(packet['view'])])
    assert 'json' not in canonical(packet['view']).lower()
    campaign={'profile_sha256':digest(profile),'packets':[packet]};sha=digest(campaign)
    budget.register_phase('no-json',sha,cloud_cap=1,local_cap=0);posts=[]
    provider=NoJson('deepseek',profile=profile,budget=budget,campaign=campaign,campaign_sha=sha,phase='no-json',output=tmp_path/'receipts',runtime=tmp_path,transport=httpx.MockTransport(lambda request:posts.append(request)))
    with pytest.raises(ActionFailure,match='json_mode_requires_explicit_json_instruction'):provider.generate('0')
    assert not posts and budget.snapshot()['total_attempts']==0


def test_http_rejection_keeps_status_and_sanitizes_request_id():
    def reply(request):return httpx.Response(400,headers={'x-request-id':'request-safe-123'},json={'error':'not echoed'})
    with pytest.raises(TransportFailure) as info:
        post_one('https://api.deepseek.com/chat/completions',{},headers={},timeout=2,transport=httpx.MockTransport(reply))
    assert info.value.http_status==400 and info.value.request_id=='request-safe-123'
    assert 'not echoed' not in str(info.value)
    assert TransportFailure('provider_http_rejected',http_status=400,request_id='secret value /bad').request_id is None


def test_http_failure_journal_preserves_status_without_body_or_secrets(tmp_path,scope,index,registry,profile,evidence):
    from graphrisk.agent.autonomous_budget import ExecutionStop
    engine,budget,_,_=make_engine(tmp_path,scope,index,registry,profile)
    executor,_,_=evidence;packet=packet_for(scope,executor.observations,registry,profile);packet['logical_key']='0'
    campaign={'profile_sha256':digest(profile),'packets':[packet]};sha=digest(campaign)
    budget.register_phase('failure-journal',sha,cloud_cap=1,local_cap=0)
    def reply(request):return httpx.Response(400,headers={'x-request-id':'safe-request-42'},json={'error':'SENSITIVE-ERROR-BODY'})
    provider=JudgmentProvider('deepseek',profile=profile,budget=budget,campaign=campaign,campaign_sha=sha,phase='failure-journal',output=tmp_path/'receipts',runtime=tmp_path,transport=httpx.MockTransport(reply))
    with pytest.raises(ExecutionStop,match='model_request_unknown_saved_do_not_retry'):provider.generate('0')
    import json
    files=list((tmp_path/'receipts/failures').glob('*.json'));assert len(files)==1
    value=json.loads(files[0].read_text())
    assert value['http_status']==400 and value['request_id']=='safe-request-42'
    assert 'SENSITIVE-ERROR-BODY' not in files[0].read_text()
    assert not list((tmp_path/'receipts').glob('*.json'))
    assert budget.snapshot()['unresolved_attempts']==1
