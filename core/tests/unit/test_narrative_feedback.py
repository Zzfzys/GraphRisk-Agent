from copy import deepcopy
import json,time
import httpx,pytest
from test_narrative import evidence,draft
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from graphrisk.agent.narrative import NarrativeProvider,narrative_packet,numeric_prose_details,verify_narrative,NarrativeDraft
from graphrisk.agent.narrative_repair import make_plan
from graphrisk.agent.upgrade_contract import canonical,digest


def request_for(evidence,scope,registry,profile,value):
    executor,packet,bundle=evidence
    plan=make_plan(value,packet,bundle)
    request=narrative_packet(scope,executor.observations,registry,profile,repair=plan)
    return plan,request


@pytest.mark.parametrize('kind',['refs','text'])
def test_exact_subfield_actual_and_limit_survive_sentence_promotion(evidence,scope,registry,profile,kind):
    value=draft()
    if kind=='refs':value['basis'][0]['refs']=['F1','F2','F3','F4'];actual=4;limit=3
    else:value['basis'][0]['text']='待'*121;actual=121;limit=120
    plan,packet=request_for(evidence,scope,registry,profile,value)
    assert plan['allowed_paths']==['basis.0']
    repair=packet['view']['repair']
    issue=next(i for i in repair['issues'] if i['field']=='basis.0.'+kind and 'max' in i)
    assert issue['actual']==actual and issue['max']==limit
    assert repair['rules'][issue['rule']]['expected']
    assert repair['previous_fields']['basis.0']==value['basis'][0]
    assert packet['view']['output_contract']['cited_text']['refs']['max']==3
    assert packet['view']['output_contract']['cited_text']['text']['max']==120


def test_numeric_feedback_lists_literals_and_keeps_legal_window(evidence,scope,registry,profile):
    value=draft();value['basis'][0]={'text':'此前24小时返回50条、总数40466，仍需核查。','refs':['F3','F9','F10']}
    _,packet=request_for(evidence,scope,registry,profile,value)
    repair=packet['view']['repair'];issue=next(i for i in repair['issues'] if i.get('literals'))
    assert issue['literals']==['50','40466'] and issue['allowed_windows']==['24小时']
    rule=repair['rules'][issue['rule']]
    assert rule['category']=='display_contract'
    assert '编造' in rule['expected'] and '时间窗口' in rule['expected']
    assert 'fabricated_number' not in canonical(repair)


def test_reference_feedback_identifies_required_alias(evidence,scope,registry,profile):
    value=draft();value['basis'][0]={'text':'图邻居截断状态未知，需要人工核查。','refs':['F17']}
    _,packet=request_for(evidence,scope,registry,profile,value)
    issue=next(i for i in packet['view']['repair']['issues'] if i.get('required_refs'))
    assert issue['field']=='basis.0.refs' and issue['required_refs']==['F20']


def test_actual_cloud_payload_has_expected_rule_and_original_field(evidence,scope,registry,profile):
    value=draft();value['fact_assessments']['graph_scope']='partial'
    plan,packet=request_for(evidence,scope,registry,profile,value)
    provider=object.__new__(NarrativeProvider);provider.provider='deepseek';provider.profile=profile
    payload=provider.payload(packet)
    view=json.loads(payload['messages'][1]['content']);repair=view['repair']
    assert repair['rules'][repair['issues'][0]['rule']]['expected']=='unknown'
    assert repair['previous_fields']==plan['previous_fields']
    assert repair['base_sha256']==digest(value)
    assert packet['messages_sha256']==digest([payload['messages'][0]['content'],payload['messages'][1]['content']])
    assert packet['prompt_utf8_bytes']==sum(len(m['content'].encode()) for m in payload['messages'])<=7000
    assert 'source' not in repair and 'PRIVATE' not in canonical(payload)
    assert payload['response_format']=={'type':'json_object'}  # API JSON mode; schema validation is local.


def test_http_repair_request_carries_both_schema_and_fact_instructions(tmp_path,scope,index,registry,profile,evidence):
    from test_autonomous_loop import make_engine,LIMITS
    from graphrisk.serving.conversation_backend import BoundedConversationBackend
    from graphrisk.agent.narrative import run_narrative
    engine,budget,_,_=make_engine(tmp_path,scope,index,registry,profile)
    executor,_,bundle=evidence;engine.store.campaign('feedback-v6','c'*64,cap_each=10,group_key='route5')
    backend=BoundedConversationBackend(entries=[dict(scope=scope,registry=registry,observations=executor.observations,guard=lambda:None)],store=engine.store,campaign='feedback-v6',provider='deepseek',profile=engine.profile,limits=LIMITS,directory=tmp_path/'run',provider_factory=None,tool_batch=None)
    posts=[]
    def transport(request):
        payload=json.loads(request.content);posts.append(payload)
        if len(posts)==1:
            value=draft();value['basis'][0]={'text':'图邻居仅返回部分，仍需核查。','refs':['F1','F2','F3','F20']}
            value['fact_assessments']['graph_scope']='partial'
        else:
            view=json.loads(payload['messages'][1]['content']);repair=view['repair']
            assert any(i['field']=='basis.0.refs' and i.get('actual')==4 and i.get('max')==3 for i in repair['issues'])
            assert any(r['expected']=='unknown' for r in repair['rules'])
            assert view['output_contract']['cited_text']['refs']['max']==3
            value={'base_sha256':repair['base_sha256'],'changes':[
                {'path':'basis.0','op':'replace','value':{'text':'图邻居截断状态未知，仍需核查。','refs':['F20']}},
                {'path':'fact_assessments.graph_scope','op':'replace','value':'unknown'}]}
        return httpx.Response(200,json={'model':'deepseek-flash','system_fingerprint':'fixture-fp','choices':[{'finish_reason':'stop','message':{'role':'assistant','content':canonical(value)}}],'usage':{'prompt_tokens':100,'completion_tokens':300,'total_tokens':400,'prompt_cache_hit_tokens':5,'prompt_cache_miss_tokens':95}})
    backend.narrative_factory=lambda campaign,phase,deadline:NarrativeProvider('deepseek',profile=backend.profile,budget=budget,campaign=campaign,campaign_sha=digest(campaign),phase=phase,output=tmp_path/'receipts',runtime=tmp_path,transport=httpx.MockTransport(transport))
    result=run_narrative(backend,scope.case_alias,scope.binding_sha256,'feedback-test',bundle.evidence_version)
    assert result['status']=='review_required' and len(posts)==result['model_calls']==2
    assert run_narrative(backend,scope.case_alias,scope.binding_sha256,'feedback-test',bundle.evidence_version)==result
    assert len(posts)==2
