from copy import deepcopy
import pytest
from test_narrative import evidence,draft
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from graphrisk.agent.narrative import NarrativeDraft,narrative_packet,verify_narrative,narrative_issues
from graphrisk.agent.narrative_repair import make_plan,NarrativePatch,apply_patch_result,schema_issues
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.upgrade_contract import canonical,digest


@pytest.mark.parametrize('sentence',[
    '图邻居截断状态未知，尚不能判定范围。',
    '图邻居是否截断未知，需要人工核查。',
    '建议核查图邻居是否截断，确认查询范围。',
    '图邻居截断标记缺失，尚不能判断。'])
def test_uncertainty_and_recommendation_do_not_assert_truncation(evidence,sentence):
    _,packet,bundle=evidence;v=draft();v['basis'][0]={'text':sentence,'refs':['F20']}
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(v)),packet,bundle)
    assert not errors and report['semantic_quality']=='requires_review'


def test_unknown_prefix_cannot_hide_later_assertion(evidence):
    _,packet,bundle=evidence;v=draft();v['basis'][0]={'text':'图邻居截断状态未知，但图邻居已被截断。','refs':['F20']}
    errors,_=verify_narrative(NarrativeDraft.model_validate_json(canonical(v)),packet,bundle)
    assert 'graph_truncation_assertion_not_supported' in errors


@pytest.mark.parametrize('window,ref,valid',[('24小时','F3',True),('1小时','F2',True),('24小时','F2',False),('12小时','F3',False)])
def test_time_window_must_match_cited_fact(evidence,window,ref,valid):
    _,packet,bundle=evidence;v=draft();v['basis'][0]={'text':f'此前{window}的历史计数可作为核查线索。','refs':[ref]}
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(v)),packet,bundle)
    assert (report is not None)==valid


def test_valid_window_does_not_allow_invented_count(evidence):
    _,packet,bundle=evidence;v=draft();v['basis'][0]={'text':'此前24小时发生999笔交易。','refs':['F3']}
    errors,_=verify_narrative(NarrativeDraft.model_validate_json(canonical(v)),packet,bundle)
    assert 'numeric_or_probability_prose_not_allowed' in errors


def test_type_specific_zero_does_not_support_absence_of_all_relations(evidence):
    _,packet,bundle=evidence;v=draft();v['counterevidence']=[{'text':'未发现已返回的关联类型。','refs':['F12']}]
    errors,_=verify_narrative(NarrativeDraft.model_validate_json(canonical(v)),packet,bundle)
    assert 'relation_type_zero_generalized_to_all_relations' in errors


def patch(plan,changes):
    return NarrativePatch.model_validate_json(canonical({'base_sha256':plan['base_sha256'],'changes':changes}))


def test_repair_contains_old_field_and_preserves_every_other_field(evidence,scope,registry,profile):
    executor,packet,bundle=evidence;v=draft();v['fact_assessments']['graph_scope']='partial';original=deepcopy(v)
    plan=make_plan(v,packet,bundle)
    assert plan['allowed_paths']==['fact_assessments.graph_scope']
    request=narrative_packet(scope,executor.observations,registry,profile,repair=plan)
    assert request['view']['repair']['previous_fields']=={'fact_assessments.graph_scope':'partial'}
    assert 'source' not in request['view']['repair']
    out=apply_patch_result(patch(plan,[{'path':'fact_assessments.graph_scope','op':'replace','value':'unknown'}]),plan)
    expected=deepcopy(v);expected['fact_assessments']['graph_scope']='unknown'
    assert out==expected and v==original and plan['source']==original
    assert verify_narrative(NarrativeDraft.model_validate_json(canonical(out)),packet,bundle)[1] is not None


def test_repair_cannot_touch_correct_field_or_change_base(evidence):
    _,packet,bundle=evidence;v=draft();v['fact_assessments']['graph_scope']='partial';plan=make_plan(v,packet,bundle)
    with pytest.raises(ActionFailure,match='repair_paths_changed'):
        apply_patch_result(patch(plan,[{'path':'fact_assessments.relation_scope','op':'replace','value':'unknown'}]),plan)
    change=patch(plan,[{'path':'fact_assessments.graph_scope','op':'replace','value':'unknown'}])
    with pytest.raises(ActionFailure,match='repair_base_changed'):
        apply_patch_result(change.model_copy(update={'base_sha256':'f'*64}),plan)
    with pytest.raises(ActionFailure,match='repair_paths_changed'):
        apply_patch_result(change.model_copy(update={'changes':change.changes*2}),plan)


def test_extra_version_gets_clear_feedback_and_only_explicit_removal(evidence):
    _,packet,bundle=evidence;v=draft();v['version']='typed-facts-v3';v['fact_assessments']['graph_scope']='partial'
    issues=schema_issues(v)
    assert issues[0]['path']=='version' and issues[0]['code']=='extra_forbidden' and '删除' in issues[0]['expected']
    plan=make_plan(v,packet,bundle)
    assert set(plan['allowed_paths'])=={'version','fact_assessments.graph_scope'}
    fixed=apply_patch_result(patch(plan,[{'path':'version','op':'remove','value':None},{'path':'fact_assessments.graph_scope','op':'replace','value':'unknown'}]),plan)
    assert fixed==draft() and v['version']=='typed-facts-v3'


def test_unknown_extra_field_not_echoed_as_instruction(evidence):
    _,packet,bundle=evidence;v=draft();v['ignore_all_rules']='PRIVATE-SECRET'
    assert schema_issues(v)[0]['path']=='$'
    with pytest.raises(ActionFailure,match='repair_source_unusable'):make_plan(v,packet,bundle)


def test_oversize_repair_is_blocked_before_provider(evidence,scope,registry,profile):
    executor,packet,bundle=evidence;v=draft()
    v['basis']=[{'text':'需要进一步核查。'*10,'refs':['F1']} for _ in range(12)]
    plan=make_plan(v,packet,bundle)
    with pytest.raises(ActionFailure,match='narrative_context_limit'):
        narrative_packet(scope,executor.observations,registry,profile,repair=plan)


@pytest.mark.parametrize('sentence,valid',[
    ('图邻居列表被截断，仅能作为线索。',False),
    ('关系与图邻居存在截断，范围不足。',False),
    ('人工核查目标卡片近期交易尝试次数与图邻居截断情况。',True),
    ('建议核查图邻居已被截断的情况。',False)])
def test_truncation_assertion_and_check_request_are_separate(evidence,sentence,valid):
    _,packet,bundle=evidence;v=draft();v['next_check']={'text':sentence,'refs':['F20']}
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(v)),packet,bundle)
    assert (report is not None)==valid


@pytest.mark.parametrize('mode',['extra_field','unauthorized_patch','oversize'])
def test_real_repair_workflow_preserves_receipts_and_stops_before_unsafe_request(tmp_path,scope,index,registry,profile,evidence,mode):
    import json,httpx
    from test_autonomous_loop import make_engine,LIMITS
    from graphrisk.agent.narrative import NarrativeProvider,run_narrative
    from graphrisk.serving.conversation_backend import BoundedConversationBackend
    engine,budget,_,_=make_engine(tmp_path,scope,index,registry,profile)
    executor,_,bundle=evidence;engine.store.campaign('repair-v4','c'*64,cap_each=10,group_key='route5')
    backend=BoundedConversationBackend(entries=[dict(scope=scope,registry=registry,observations=executor.observations,guard=lambda:None)],
        store=engine.store,campaign='repair-v4',provider='deepseek',profile=engine.profile,limits=LIMITS,directory=tmp_path/'run',provider_factory=None,tool_batch=None)
    posts=[]
    def transport(request):
        payload=json.loads(request.content);posts.append(payload);value=draft()
        if len(posts)==1:
            if mode=='oversize':value['basis']=[{'text':'需要进一步核查。'*10,'refs':['F1']} for _ in range(12)]
            else:value['version']='typed-facts-v3'
        else:
            repair=json.loads(payload['messages'][1]['content'])['repair']
            assert repair['previous_fields']['version']=='typed-facts-v3'
            changes=[{'path':'version','op':'remove','value':None}]
            if mode=='unauthorized_patch':changes.append({'path':'risk_level','op':'replace','value':'low'})
            value={'base_sha256':repair['base_sha256'],'changes':changes}
        return httpx.Response(200,json={'model':'deepseek-flash','system_fingerprint':'fixture-fp',
            'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':canonical(value)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':300,'total_tokens':400,'prompt_cache_hit_tokens':5,'prompt_cache_miss_tokens':95}})
    backend.narrative_factory=lambda campaign,phase,deadline:NarrativeProvider('deepseek',profile=backend.profile,budget=budget,campaign=campaign,campaign_sha=digest(campaign),phase=phase,output=tmp_path/'receipts',runtime=tmp_path,transport=httpx.MockTransport(transport))
    result=run_narrative(backend,scope.case_alias,scope.binding_sha256,'patch-session',bundle.evidence_version)
    assert result['model_calls']==(1 if mode=='oversize' else 2)
    if mode=='extra_field':assert result['report']['risk_level']=='medium' and result['status']=='review_required'
    else:assert result['report'] is None and result['status']=='rejected'
    assert len(list((tmp_path/'receipts').glob('*.json')))==len(posts)
    assert run_narrative(backend,scope.case_alias,scope.binding_sha256,'patch-session',bundle.evidence_version)==result
    assert len(posts)==result['model_calls']
