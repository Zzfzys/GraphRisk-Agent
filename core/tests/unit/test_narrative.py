from copy import deepcopy
import json,time
import httpx
import pytest
from test_autonomous_tool_contracts import index,registry,scope,executor_for,run_core
from test_autonomous_action_providers import profile
from test_autonomous_loop import make_engine,LIMITS
from test_conversations import setup
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.narrative import NarrativeDraft,narrative_packet,verify_narrative,NarrativeProvider,run_narrative
from graphrisk.agent.upgrade_contract import canonical,digest
from graphrisk.serving.conversation_backend import BoundedConversationBackend


def draft():
    return {'fact_assessments':{'relation_scope':'partial','graph_scope':'unknown','amount_vs_24h_mean':'unknown'},'risk_level':'medium','basis':[{'text':'当前金额与主评分值得结合历史情况核查。','refs':['F1','F8']}],
        'counterevidence':[{'text':'尚缺交易授权信息，不能排除正常使用。','refs':['F1']}],
        'policy':{'text':'条款提供调查依据，必要业务前提仍需人工核实。','refs':['P1']},
        'limitation':'分数未校准，图信号仍属实验，现有线索不足以确认欺诈。',
        'next_check':{'text':'建议人工核实该笔交易的授权来源。','refs':['F1']}}


@pytest.fixture
def evidence(scope,index,registry,profile):
    executor,_=executor_for(scope,index);run_core(executor)
    bundle=build_evidence(scope,executor.observations,registry)
    packet=narrative_packet(scope,executor.observations,registry,profile)
    return executor,packet,bundle


def test_judgment_remains_separate_from_calibrated_probability(evidence):
    _,packet,bundle=evidence
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(draft())),packet,bundle)
    assert not errors
    assert report['semantic_quality']=='requires_review' and not report['approval_eligible']
    assert report['risk_level']=='medium' and 'probability' not in report


@pytest.mark.parametrize('change,expected',[
    ('unknown','unknown_citation'),('numeric','numeric_or_probability_prose_not_allowed'),
    ('probability','numeric_or_probability_prose_not_allowed'),('business','unsupported_business_or_identity_claim'),
    ('policy','policy_citation_required')])
def test_bad_claims_rejected(evidence,change,expected):
    _,packet,bundle=evidence;value=draft()
    if change=='unknown':value['basis'][0]['refs']=['F999']
    elif change=='numeric':value['basis'][0]['text']='此笔评分为0.9，应核查。'
    elif change=='probability':value['basis'][0]['text']='其欺诈概率很高，应核查。'
    elif change=='business':value['next_check']['text']='建议直接冻结交易账户。'
    elif change=='policy':value['policy']['refs']=['F1']
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(value)),packet,bundle)
    assert expected in errors and report is None


def test_missing_evidence_requires_undetermined(evidence):
    _,packet,bundle=evidence
    outcomes=list(bundle.tool_outcomes);outcomes[0]=outcomes[0].model_copy(update={'status':'unavailable'})
    bundle=bundle.model_copy(update={'tool_outcomes':tuple(outcomes)})
    packet['source_evidence_sha256']=digest(bundle.model_dump(mode='json'))
    errors,_=verify_narrative(NarrativeDraft.model_validate_json(canonical(draft())),packet,bundle)
    assert 'incomplete_evidence_requires_undetermined' in errors


def test_inline_citation_adjacent_to_chinese_is_not_a_numeric_claim(evidence):
    _,packet,bundle=evidence
    value=draft();value['policy']['text']='依据P1，现有条款仍需结合未测前提核查。'
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(value)),packet,bundle)
    assert not errors and report is not None
    value['policy']['text']='依据P999，现有条款仍需结合未测前提核查。'
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(value)),packet,bundle)
    assert 'unknown_inline_citation' in errors and report is None


def test_true_or_unknown_truncation_cannot_be_called_untruncated(evidence):
    _,packet,bundle=evidence
    value=draft();value['counterevidence']=[{'text':'共享关系未截断，可以继续核查。','refs':['F11']}]
    facts=list(bundle.facts)
    for i,fact in enumerate(facts):
        if fact.metric=='relations_truncated':facts[i]=fact.model_copy(update={'value':True})
    bundle=bundle.model_copy(update={'facts':tuple(facts)})
    packet['source_evidence_sha256']=digest(bundle.model_dump(mode='json'))
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(value)),packet,bundle)
    assert 'truncation_claim_conflicts_with_or_lacks_evidence' in errors and report is None


def test_metered_generation_repair_and_receipt_replay(tmp_path,scope,index,registry,profile,evidence):
    engine,budget,_,_=make_engine(tmp_path,scope,index,registry,profile)
    executor,_,bundle=evidence
    engine.store.campaign('narrative-trial','c'*64,cap_each=10,group_key='route5')
    backend=BoundedConversationBackend(entries=[dict(scope=scope,registry=registry,observations=executor.observations,guard=lambda:None)],
        store=engine.store,campaign='narrative-trial',provider='deepseek',profile=engine.profile,limits=LIMITS,
        directory=tmp_path/'narrative',provider_factory=None,tool_batch=None)
    posts=[]
    def http(request):
        payload=json.loads(request.content);posts.append(payload)
        value=draft()
        if len(posts)==1:value['basis'][0]['refs']=['F999']
        else:
            repair=json.loads(payload['messages'][1]['content'])['repair']
            assert repair['previous_fields']['basis.0']['refs']==['F999']
            value={'base_sha256':repair['base_sha256'],'changes':[{'path':'basis.0','op':'replace','value':draft()['basis'][0]}]}
        return httpx.Response(200,json={'model':'deepseek-flash','system_fingerprint':'fixture-fp','choices':[{'finish_reason':'stop','message':{'role':'assistant','content':canonical(value)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':300,'total_tokens':400,'prompt_cache_hit_tokens':5,'prompt_cache_miss_tokens':95}})
    backend.narrative_factory=lambda campaign,phase,deadline:NarrativeProvider('deepseek',profile=backend.profile,budget=budget,campaign=campaign,campaign_sha=digest(campaign),phase=phase,output=tmp_path/'receipts',runtime=tmp_path,transport=httpx.MockTransport(http))
    result=run_narrative(backend,scope.case_alias,scope.binding_sha256,'narrative-session',bundle.evidence_version)
    assert result['status']=='review_required' and result['model_calls']==2
    assert 'unknown_citation' in posts[1]['messages'][1]['content']
    assert 'PRIVATE' not in canonical(posts)
    assert run_narrative(backend,scope.case_alias,scope.binding_sha256,'narrative-session',bundle.evidence_version)==result
    assert len(posts)==2 and budget.snapshot()['total_attempts']==2


def test_ui_readable_cards_and_no_automatic_generation(evidence):
    import logging
    loggers=[logging.getLogger(name) for name in ('','uvicorn','uvicorn.error','uvicorn.access')]
    saved=[(logger,list(logger.handlers),logger.level,logger.propagate,logger.disabled) for logger in loggers]
    from streamlit.testing.v1 import AppTest
    _,packet,bundle=evidence
    _,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(draft())),packet,bundle)
    value={'evidence':bundle.model_dump(mode='json'),'turns':[{'task':'risk_assessment','state':'completed','report':report,'actual_model_calls':1,'actual_tool_calls':0}]}
    code='from graphrisk.demo.conversation_workbench import show_conversation\nimport streamlit as st\nshow_conversation(st.session_state["case"])'
    try:
        app=AppTest.from_string(code);app.session_state['case']=value;app.run()
        assert not app.exception
        assert any('Agent 研判' in node.value for node in app.subheader)
        assert any('XGBoost' in node.value for node in app.text)
        assert not app.button
    finally:
        # AppTest initializes Streamlit/Uvicorn logging. Do not leak that
        # process-global configuration into later API trace tests.
        for logger,handlers,level,propagate,disabled in saved:
            logger.handlers=handlers;logger.setLevel(level);logger.propagate=propagate;logger.disabled=disabled


def test_workbench_submits_to_real_api_and_rerun_never_posts(setup,monkeypatch):
    import logging
    for name in ('','uvicorn','uvicorn.error','uvicorn.access'):
        logger=logging.getLogger(name)
        for field in ('handlers','level','propagate','disabled'):
            value=getattr(logger,field)
            monkeypatch.setattr(logger,field,list(value) if field=='handlers' else value)
    from streamlit.testing.v1 import AppTest
    from fastapi.testclient import TestClient
    from api.main import create_app
    import graphrisk.demo.conversation_workbench as ui
    service,backend,_,_=setup;posts=[]
    with TestClient(create_app(conversation_service_factory=lambda:service)) as client:
        def api(method,path,body=None,key=None):
            if method=='POST':posts.append((path,key))
            response=client.request(method,path,json=body,headers={'Idempotency-Key':key} if key else {})
            return (response.json(),None) if not response.is_error else (None,'请求未通过')
        monkeypatch.setattr(ui,'api_call',api)
        app=AppTest.from_string('from graphrisk.demo.conversation_workbench import render_workbench\nrender_workbench()').run()
        app.text_input[0].set_value('123')
        next(b for b in app.button if b.label=='新建调查').click().run()
        assert not app.exception and len(posts)==1
        app.run();assert len(posts)==1
        app.chat_input[0].set_value('政策前提').run()
        assert not app.exception and len(posts)==2 and backend.calls==0
        app.run();assert len(posts)==2
