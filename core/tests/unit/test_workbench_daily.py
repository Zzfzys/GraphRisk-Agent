"""Day-end selection and presentation never generate a model call on rerun."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from graphrisk.demo import conversation_workbench as ui
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from test_daily_narrative import daily_scope


def context():
    return {'temporal_scope':'relative_day_end','relative_day':183,
        'batch_end_second':15897600,'available_through_second':15897600,
        'batch_id':'sealed-day-183-v1','revision':1,
        'model_version':'champion-xgb-day-216-plus49-fold3-v1',
        'risk_level':'high','score':0.8,'calibrated_probability':False}


def test_day_end_export_and_ui_keep_model_priority_separate(monkeypatch):
    ctx=context()
    value={'scoring_context':ctx,'evidence':{'scoring_context':deepcopy(ctx),'facts':[],'policies':[]},'turns':[]}
    original=deepcopy(value)
    monkeypatch.setattr(ui,'api_call',lambda *a,**k:(_ for _ in ()).throw(AssertionError('read only')))
    text=ui.readable_report(value)
    for expected in ('日终评分','15897600（不含该秒）','修订 1','模型调查优先级（试用）：高','Agent 综合研判独立',ctx['model_version']):
        assert expected in text
    app=AppTest.from_string('import streamlit as st\nfrom graphrisk.demo.conversation_workbench import show_conversation\nshow_conversation(st.session_state["case"])')
    app.session_state['case']=value
    app.run(timeout=10)
    assert not app.exception
    assert any(ctx['model_version'] in node.value for node in app.caption)
    assert any('模型调查优先级（试用）：高' in node.value for node in app.markdown)
    app.run(timeout=10)
    assert value==original and not app.exception
    value['scoring_context']={**ctx,'risk_level':'low'}
    assert '不一致' in ui.model_band_lines(value)[0]
    assert '优先级（试用）：低' not in ui.readable_report(value)


def test_sealed_batch_selection_and_explicit_x3_submit_without_automatic_posts(monkeypatch):
    ctx=context()
    batch={key:ctx[key] for key in ('batch_id','relative_day','revision','batch_end_second','available_through_second','model_version')}
    batch['transaction_count']=2
    day={**batch,'results':[
        {'transaction_id':'710001','event_second':15811201,'score':0.8,'risk_level':'high'},
        {'transaction_id':'710002','event_second':15811202,'score':0.1,'risk_level':'low'}]}
    posts=[]
    def api(method,path,*,body=None,key=None):
        if method=='GET':
            return ({'batches':[batch]} if path=='/api/v1/risk-batches' else day),None
        posts.append((path,deepcopy(body),key))
        return None,'请求尚未确认'
    monkeypatch.setattr(ui,'api_call',api)
    app=AppTest.from_string('from graphrisk.demo.conversation_workbench import render_workbench\nrender_workbench()').run(timeout=10)
    assert not app.exception and not posts
    assert app.selectbox(key='conversation_risk_profile').value=='daily_batch'
    app.selectbox(key='conversation_daily_transaction').select('710002')
    next(button for button in app.button if button.label=='新建调查').click().run(timeout=10)
    assert not app.exception
    assert posts[0][1]=={'transaction_id':'710002','message':'调查风险','risk_profile':'daily_batch','batch_id':ctx['batch_id']}
    app.run(timeout=10)
    assert len(posts)==1
    next(button for button in app.button if button.label=='使用原请求标识重新查询／提交').click().run(timeout=10)
    assert len(posts)==2 and posts[0]==posts[1]
    next(button for button in app.button if button.label.startswith('取消当前输入')).click().run(timeout=10)
    app.selectbox(key='conversation_risk_profile').select('x3').run(timeout=10)
    app.text_input(key='conversation_transaction').set_value('123')
    next(button for button in app.button if button.label=='新建调查').click().run(timeout=10)
    assert not app.exception and posts[-1][1]['risk_profile']=='x3'
    assert 'batch_id' not in posts[-1][1]


def test_changed_batch_revision_prevents_creating_conversation(monkeypatch):
    batch={**context(),'transaction_count':1}
    monkeypatch.setattr(ui,'api_call',lambda method,path,**kw:
        ({'batches':[batch]} if path=='/api/v1/risk-batches' else {**batch,'revision':2,'results':[]},None))
    app=AppTest.from_string('from graphrisk.demo.conversation_workbench import render_workbench\nrender_workbench()').run(timeout=10)
    assert not app.exception
    assert any('封批版本已变化' in node.value for node in app.error)
    assert not any(button.label=='新建调查' for button in app.button)


def test_real_daily_evidence_report_is_checked_and_never_called_llm_judgment(monkeypatch):
    from datetime import date
    from graphrisk.serving.conversation_store import MemoryConversationStore
    from graphrisk.serving.conversations import ConversationService,CreateConversation
    from graphrisk.serving.daily_conversation_backend import DailyConversationBackend
    from test_daily_conversation_backend import SealedStore
    service=ConversationService(MemoryConversationStore(),DailyConversationBackend(SealedStore(),policy_as_of=date(2026,11,6)))
    value=service.create(CreateConversation(transaction_id='123',message='生成风险报告',
        risk_profile='daily_batch',batch_id='day-2-aabb'),'ui-daily-report')
    original=deepcopy(value)
    monkeypatch.setattr(ui,'api_call',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('read only')))
    text=ui.readable_report(value)
    assert '日终证据报告（程序生成）' in text and '本轮未调用大模型' in text
    assert text.count('日终证据报告（程序生成）')==1
    assert text.count('证据缺口：')==1 and '没有取得这项数据' not in text
    assert '历史行为统计、关联交易核查、图信号、政策前提' in text
    assert '未调用或未取得不等于数值为0' in text
    assert text.count('交易金额')==text.count('日终 XGBoost 主评分')==1
    assert 'LLM可疑程度：' not in text
    app=AppTest.from_string('import streamlit as st\nfrom graphrisk.demo.conversation_workbench import show_conversation\nshow_conversation(st.session_state["case"])')
    app.session_state['case']=value
    app.run(timeout=10)
    assert not app.exception
    assert any('日终证据报告（程序生成）' in node.value for node in app.subheader)
    assert not any('Agent 研判：' in node.value for node in app.subheader)
    assert not any('没有取得这项数据' in node.value for node in app.text)
    assert len([node for node in app.info if '证据缺口：' in node.value])==1
    assert value==original
    changed=deepcopy(value)
    changed['turns'][-1]['report']['facts'][0]['text']='篡改后的程序报告'
    text=ui.readable_report(changed)
    assert '日终报告与封批证据不一致' in text and '篡改后的程序报告' not in text


@pytest.fixture
def daily_formal_view(tmp_path,daily_scope,index,registry,profile):
    """A local mocked first draft exercises rendering, never writing quality."""
    from test_daily_narrative import make_backend
    from graphrisk.agent.narrative import run_narrative
    from graphrisk.agent.judgment_v3 import CONTRACT
    backend,_,bundle,posts=make_backend(tmp_path,daily_scope,index,registry,profile,
                                       report_contract=CONTRACT)
    result=run_narrative(backend,daily_scope.case_alias,daily_scope.binding_sha256,
                         'ui-mocked-daily-first-draft',bundle.evidence_version)
    assert result['report'] and len(posts)==1
    return {'scoring_context':bundle.scoring_context.model_dump(mode='json'),
        'evidence':bundle.model_dump(mode='json'),
        'turns':[{'task':'risk_assessment','state':'completed','report':result['report'],
                  'actual_model_calls':1,'actual_tool_calls':0}]}


def test_day_end_formal_v12_report_is_reverified_in_display_and_download(daily_formal_view,monkeypatch):
    value=daily_formal_view
    original=deepcopy(value)
    monkeypatch.setattr(ui,'api_call',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('read only')))
    text=ui.readable_report(value)
    assert '日终调查阅读版（模型原稿保持不变）' in text
    assert 'XGBoost 模型调查优先级（试用）：高' in text
    assert '当前判断：暂不能判断（LLM待复核判断，不是欺诈概率）' in text
    assert '现有交易线索尚不能确认授权情况' in text
    assert '本轮未调用大模型' not in text and '尚未生成 Agent 综合研判' not in text
    app=AppTest.from_string('import streamlit as st\nfrom graphrisk.demo.conversation_workbench import show_conversation\nshow_conversation(st.session_state["case"])')
    app.session_state['case']=value
    app.run(timeout=10)
    assert not app.exception
    assert any('Agent 判断、假设与核查目的' in node.value for node in app.subheader)
    assert any('当前判断：暂不能判断' in node.value for node in app.text)
    assert not any('程序报告不包含 Agent 综合研判' in node.value for node in app.caption)
    app.run(timeout=10)
    assert not app.exception and value==original


@pytest.mark.parametrize('defect',['context','rationale'])
def test_changed_formal_day_end_report_is_not_displayed_or_exported(daily_formal_view,defect):
    from graphrisk.agent.upgrade_contract import digest
    changed=deepcopy(daily_formal_view)
    report=changed['turns'][-1]['report']
    if defect=='context':report['program']['scoring_context']['score']=0.1
    else:report['judgment']['rationale']['text']='伪造的未授权结论'
    # Even rehashing the container cannot bypass regeneration from bound facts.
    report['report_sha256']=digest({key:value for key,value in report.items() if key!='report_sha256'})
    text=ui.readable_report(changed)
    assert '报告或缺口来源与当前证据不一致，暂停展示研判。' in text
    assert '伪造的未授权结论' not in text and '当前判断：' not in text
    app=AppTest.from_string('import streamlit as st\nfrom graphrisk.demo.conversation_workbench import show_conversation\nshow_conversation(st.session_state["case"])')
    app.session_state['case']=changed
    app.run(timeout=10)
    assert not app.exception
    assert any('暂停展示研判' in node.value for node in app.text)
    assert not any('伪造的未授权结论' in node.value for node in app.text)
