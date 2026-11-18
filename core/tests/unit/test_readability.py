from copy import deepcopy
import pytest
from graphrisk.demo.conversation_workbench import relation_summary,fact_text,reference_lines,readable_report


def case():
    facts=[dict(metric=m,value=v,availability='available',window_seconds=None,fact_id='f'+str(i)) for i,(m,v) in enumerate([
        ('total_related_count',97599),('returned_related_count',50),('relations_truncated',True)])]
    policy=dict(policy_ref='policy-a',text='图分数仅供调查，不能证明欺诈。',section='解释限制',applicability='guidance_only',source_type='simulated',doc_id='DOC1',version=1,line_start=22,line_end=22,conditions=[])
    item=dict(text='依据P1，应继续核查。',refs=['P1'])
    report=dict(version='chinese-risk-judgment-v1',risk_level='undetermined',references={'P1':'policy-a'},basis=[item],counterevidence=[item],policy=item,next_check=item,limitation='证据不足，不能确认欺诈。')
    return dict(evidence=dict(facts=facts,policies=[policy]),turns=[dict(task='risk_assessment',state='completed',report=report,actual_model_calls=1,actual_tool_calls=0)])


def test_summary_distinguishes_total_returned_and_does_not_change_records():
    value=case();original=deepcopy(value)
    text=readable_report(value)
    assert '97,599' in text and '50' in text and '只返回了一部分' in text
    assert '图分数仅供调查，不能证明欺诈。' in text and '第22～22行' in text
    assert '项目模拟政策' in text and '建议核查（未执行）' in text
    assert value==original


@pytest.mark.parametrize('flag,expected',[(True,'只返回了一部分'),(False,'未因返回上限而省略'),(None,'是否返回完整尚不清楚')])
def test_completeness_has_three_states(flag,expected):
    value=case();value['evidence']['facts'][2]['value']=flag
    assert expected in relation_summary(value['evidence'])


def test_unavailable_count_not_zero_and_missing_citation_not_replaced():
    value=case();value['evidence']['facts'][0]['availability']='unavailable'
    assert '共找到 未知' in relation_summary(value['evidence'])
    assert '不能当成0' in fact_text(value['evidence']['facts'][0])
    report=value['turns'][0]['report'];report['references']['P1']='old-policy-version'
    lines=reference_lines(report['policy'],report,value['evidence'])
    assert len(lines)==1 and '不能用其他版本替代' in lines[0]


def test_rejected_original_is_labeled_in_export():
    value=case();value['turns'][0]['post_review_failures']=['truncation_claim_conflicts_with_or_lacks_evidence']
    text=readable_report(value)
    assert '有错误的模型原稿' in text and 'LLM可疑程度：' not in text


def test_interface_shows_policy_original_and_scope_without_requests(monkeypatch):
    import logging
    for name in ('','uvicorn','uvicorn.error','uvicorn.access'):
        logger=logging.getLogger(name)
        for field in ('handlers','level','propagate','disabled'):
            value=getattr(logger,field);monkeypatch.setattr(logger,field,list(value) if field=='handlers' else value)
    from streamlit.testing.v1 import AppTest
    import graphrisk.demo.conversation_workbench as ui
    def forbidden(*args,**kwargs):raise AssertionError('Reading must not call API')
    monkeypatch.setattr(ui,'api_call',forbidden)
    app=AppTest.from_string('import streamlit as st\nfrom graphrisk.demo.conversation_workbench import show_conversation\nshow_conversation(st.session_state["case"])')
    app.session_state['case']=case();app.run()
    assert not app.exception
    assert any('97,599' in x.value and '50' in x.value for x in app.info)
    assert any('图分数仅供调查，不能证明欺诈。'==x.value for x in app.text)
    assert any('出处：DOC1' in x.value for x in app.text)
    app.run();assert not app.exception
