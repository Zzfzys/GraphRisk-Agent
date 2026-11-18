from copy import deepcopy
import json
import pytest,httpx
from test_narrative import evidence,draft
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from graphrisk.agent.judgment import JudgmentDraft,packet_for,repair_plan,verify_judgment,JudgmentProvider,CONTRACT
from graphrisk.agent.narrative_repair import NarrativePatch,apply_patch_result
from graphrisk.agent.upgrade_contract import canonical,digest


def good():
    value=draft();value.pop('fact_assessments');value.pop('limitation')
    value['basis'][0]['text']='应结合支付授权核实线索，尚不能确认欺诈。'
    value['next_check']={'text':'authorization','refs':['F1']}
    return value


def test_program_facts_and_policy_thresholds_are_not_llm_fields(evidence,scope,registry,profile):
    executor,_,bundle=evidence;packet=packet_for(scope,executor.observations,registry,profile)
    errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(good())),packet,bundle)
    assert not errors and report['program']['source']=='program'
    assert report['program']['fact_assessments']['graph_scope']=='unknown'
    assert any('图邻居查询：截断状态未知' in text for text in report['program']['limitations'])
    assert report['semantic_quality']=='requires_review' and not report['approval_eligible']
    assert report['judgment']['next_check']['choice']=='authorization'
    for key in ('fact_assessments','limitation','program'):
        value=good();value[key]={'graph_scope':'partial'}
        with pytest.raises(ValueError):JudgmentDraft.model_validate_json(canonical(value))
    policy=next(p for p in bundle.policies if p.conditions)
    bundle2=bundle.model_copy(update={'policies':(policy.model_copy(update={'conditions':(policy.conditions[0].model_copy(update={'window_seconds':600,'expected':4,'measurement':'unmeasured'}),)}),)})
    from graphrisk.agent.fact_presentation import program_presentation
    packet2={**packet,'source_evidence_sha256':digest(bundle2.model_dump(mode='json'))}
    program=program_presentation(bundle2,packet2)
    assert any('10分钟' in s and '至少4' in s and '未测量' in s for p in program['policies'] for s in p['conditions'])


@pytest.mark.parametrize('statement',['图邻居均被截断，需要核查。','图邻居悉数被截断，需要核查。','图邻居只返回部分，需要核查。'])
def test_scope_prose_is_program_owned_not_a_synonym_truth_test(evidence,scope,registry,profile,statement):
    executor,_,bundle=evidence;packet=packet_for(scope,executor.observations,registry,profile);value=good()
    value['basis'][0]={'text':statement,'refs':['F20']}
    errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,bundle)
    assert 'program_owned_scope_statement' in errors and report is None


def test_protected_field_removed_explicitly_and_whitelist_still_enforced(evidence,scope,registry,profile):
    executor,_,bundle=evidence;packet=packet_for(scope,executor.observations,registry,profile)
    value=good();value['fact_assessments']={'graph_scope':'partial'}
    plan=repair_plan(value,packet,bundle)
    patch=NarrativePatch.model_validate_json(canonical({'base_sha256':plan['base_sha256'],'changes':[{'path':'fact_assessments','op':'remove','value':None}]}))
    assert apply_patch_result(patch,plan)==good()
    # A forged remove of a legitimate model field is never authorized.
    patch=patch.model_copy(update={'changes':(patch.changes[0].model_copy(update={'path':'basis'}),)})
    with pytest.raises(ValueError):apply_patch_result(patch,plan)


def test_missing_tools_require_undetermined_and_unknown_is_not_zero(evidence,scope,registry,profile):
    executor,_,bundle=evidence;packet=packet_for(scope,executor.observations,registry,profile)
    outcomes=list(bundle.tool_outcomes);outcomes[0]=outcomes[0].model_copy(update={'status':'unavailable'})
    changed=bundle.model_copy(update={'tool_outcomes':tuple(outcomes)})
    packet['source_evidence_sha256']=digest(changed.model_dump(mode='json'))
    errors,_=verify_judgment(JudgmentDraft.model_validate_json(canonical(good())),packet,changed)
    assert 'incomplete_evidence_requires_undetermined' in errors
    value=good();value['risk_level']='undetermined'
    assert verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,changed)[1]


def test_new_contract_runs_via_existing_dispatch_with_receipt_replay(tmp_path,scope,index,registry,profile,evidence):
    from test_autonomous_loop import make_engine,LIMITS
    from graphrisk.serving.conversation_backend import BoundedConversationBackend
    from graphrisk.agent.narrative import run_narrative
    engine,budget,_,_=make_engine(tmp_path,scope,index,registry,profile);executor,_,bundle=evidence
    engine.store.campaign('judgment-test','d'*64,cap_each=10,group_key='route5')
    backend=BoundedConversationBackend(entries=[dict(scope=scope,registry=registry,observations=executor.observations,guard=lambda:None)],store=engine.store,campaign='judgment-test',provider='deepseek',profile=engine.profile,limits=LIMITS,directory=tmp_path/'runs',provider_factory=None,tool_batch=None)
    backend.report_contract=CONTRACT;posts=[]
    def transport(request):
        payload=json.loads(request.content);posts.append(payload)
        if len(posts)==1:
            value=good();value['basis'][0]['refs']=['F999']
        else:
            repair=json.loads(payload['messages'][1]['content'])['repair']
            value={'base_sha256':repair['base_sha256'],'changes':[{'path':'basis.0','op':'replace','value':good()['basis'][0]}]}
        return httpx.Response(200,json={'model':'deepseek-flash','system_fingerprint':'fixture-fp','choices':[{'finish_reason':'stop','message':{'role':'assistant','content':canonical(value)}}],'usage':{'prompt_tokens':100,'completion_tokens':300,'total_tokens':400,'prompt_cache_hit_tokens':5,'prompt_cache_miss_tokens':95}})
    backend.narrative_factory=lambda campaign,phase,deadline:JudgmentProvider('deepseek',profile=backend.profile,budget=budget,campaign=campaign,campaign_sha=digest(campaign),phase=phase,output=tmp_path/'receipts',runtime=tmp_path,transport=httpx.MockTransport(transport))
    result=run_narrative(backend,scope.case_alias,scope.binding_sha256,'v2-test',bundle.evidence_version)
    assert result['report']['version']=='separated-risk-report-v2' and result['model_calls']==2
    assert run_narrative(backend,scope.case_alias,scope.binding_sha256,'v2-test',bundle.evidence_version)==result
    assert len(posts)==2 and len(list((tmp_path/'receipts').glob('*.json')))==2


def test_ui_v1_v2_download_and_tampered_program_facts(evidence,scope,registry,profile):
    from graphrisk.demo.conversation_workbench import readable_report,show_conversation,v2_lines
    executor,packet,bundle=evidence;packet=packet_for(scope,executor.observations,registry,profile)
    _,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(good())),packet,bundle)
    value={'evidence':bundle.model_dump(mode='json'),'turns':[{'task':'risk_assessment','state':'completed','report':report,'actual_model_calls':1,'actual_tool_calls':0}]}
    text=readable_report(value)
    assert '程序生成的事实说明' in text and 'LLM支持线索解释' in text and '截断状态未知' in text
    changed=deepcopy(report);changed['program']['fact_assessments']['graph_scope']='partial'
    assert '不一致' in v2_lines(changed,value['evidence'])[0]
    from streamlit.testing.v1 import AppTest
    import logging
    loggers=[logging.getLogger(n) for n in ('','uvicorn','uvicorn.error','uvicorn.access')]
    saved=[(l,list(l.handlers),l.level,l.propagate,l.disabled) for l in loggers]
    try:
        app=AppTest.from_string('from graphrisk.demo.conversation_workbench import show_conversation\nimport streamlit as st\nshow_conversation(st.session_state["case"])')
        app.session_state['case']=value;app.run()
        assert not app.exception and not app.button
        assert any('程序生成的事实说明' in n.value for n in app.text)
    finally:
        for logger,handlers,level,propagate,disabled in saved:
            logger.handlers=handlers;logger.setLevel(level);logger.propagate=propagate;logger.disabled=disabled
