from copy import deepcopy
import pytest
from test_narrative import evidence
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from test_judgment import good
from graphrisk.agent.judgment import packet_for,JudgmentDraft,verify_judgment,repair_plan
from graphrisk.agent.judgment_amounts import NOTE,amount_quote_diagnostics
from graphrisk.agent.judgment_support import RULES_VERSION
from graphrisk.agent.upgrade_contract import canonical,digest


def inputs(evidence,scope,registry,profile):
    executor,_,bundle=evidence
    facts=tuple(f.model_copy(update={'value':25.95}) if f.metric=='transaction_amount' else f for f in bundle.facts)
    bundle=bundle.model_copy(update={'facts':facts})
    packet=packet_for(scope,executor.observations,registry,profile)
    packet['source_evidence_sha256']=digest(bundle.model_dump(mode='json'))
    value=good();value['basis'][0]={'text':'目标交易金额为 25.95，需核查该笔交易的授权情况。','refs':['F1']}
    return packet,bundle,value


@pytest.mark.parametrize('clause',['目标交易金额为 25.95','该笔交易金额是25.9500','本笔交易金额为+25.95'])
def test_exact_amount_is_nonblocking_original_prose_preserved(evidence,scope,registry,profile,clause):
    packet,bundle,value=inputs(evidence,scope,registry,profile)
    value['basis'][0]['text']=clause+'，需核查授权情况。';original=deepcopy(value)
    errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,bundle)
    assert errors==[] and value==original and report['judgment']['basis']==value['basis']
    assert report['judgment_rules_version']==RULES_VERSION and report['display_notes'][0]['message']==NOTE
    assert report['semantic_quality']=='requires_review' and not report['approval_eligible']
    assert report['display_notes'][0]['ref']=='F1'


@pytest.mark.parametrize('change,code',[
    ('value','target_amount_value_mismatch'),('rounding','target_amount_value_mismatch'),
    ('refs','target_amount_citation_required'),('metric','target_amount_source_unavailable'),
    ('subject','target_amount_source_unavailable'),('unit','target_amount_source_unavailable'),
    ('time','target_amount_source_unavailable'),('window','target_amount_source_unavailable'),
    ('tool','target_amount_source_unavailable'),('unavailable','target_amount_source_unavailable'),
    ('boolean','target_amount_source_unavailable'),('duplicate','target_amount_source_unavailable'),
    ('query','target_amount_source_unavailable'),('source_version','target_amount_source_unavailable'),
    ('alias','target_amount_source_unavailable')])
def test_exact_value_and_canonical_provenance_required(evidence,scope,registry,profile,change,code):
    packet,bundle,value=inputs(evidence,scope,registry,profile)
    changes={'metric':{'metric':'neighbor_amount'},'subject':{'subject':'heuristic_identity_history'},
             'unit':{'unit':'USD'},'time':{'temporal_scope':'strictly_before_target'},'window':{'window_seconds':3600},
             'tool':{'source_tool':'get_risk_score'},'unavailable':{'value':None,'availability':'unavailable','missing_reason':'tool_failed'},
             'boolean':{'value':True},'query':{'query_ref':'q-other'},'source_version':{'source_version':'unbound'}}
    facts=list(bundle.facts);i=next(i for i,f in enumerate(facts) if f.metric=='transaction_amount')
    if change in changes:facts[i]=facts[i].model_copy(update=changes[change])
    elif change=='duplicate':facts.append(facts[i].model_copy(update={'fact_id':'F-'+'a'*24}))
    elif change=='value':value['basis'][0]['text']='目标交易金额为 30.95，需核查授权情况。'
    elif change=='rounding':value['basis'][0]['text']='目标交易金额为 25.9，需核查授权情况。'
    elif change=='refs':value['basis'][0]['refs']=['F8']
    else:packet['refs_private']['F21']=packet['refs_private']['F1']
    bundle=bundle.model_copy(update={'facts':tuple(facts)});packet['source_evidence_sha256']=digest(bundle.model_dump(mode='json'))
    # Source-invalid synthetic cases exercise the amount guard directly: invalid
    # source metadata must never turn into a display-only acceptance.
    diagnostic,issues,notes=amount_quote_diagnostics(value,packet,bundle)
    assert code in {i['code'] for i in issues} and notes==[] and '25.95' not in diagnostic['basis'][0]['text']
    if change in ('value','rounding','refs'):
        errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,bundle)
        assert code in errors and report is None
        plan=repair_plan(value,packet,bundle)
        assert plan['allowed_paths']==['basis.0'] and all('数字' not in i['expected'] for i in plan['issues'])


@pytest.mark.parametrize('text',[
    '目标交易金额为25.95元，需核查授权情况。',
    '目标交易金额为25.95美元，需核查授权情况。',
    '历史交易金额为25.95，需核查授权情况。',
    '邻居目标交易金额为25.95，需核查授权情况。',
    '邻居 目标交易金额为25.95，需核查授权情况。',
    '不是目标交易金额为25.95，需核查授权情况。',
    '目标交易金额为2.595e1，需核查授权情况。',
    '目标交易金额为25.95%，需核查授权情况。',
    '目标交易金额为25.95，模型分数为0.9，需核查。',
    '目标交易金额为25.95，欺诈概率很高。',
    '目标交易金额为25.95，其他金额为30.95，需核查。',
])
def test_exception_cannot_hide_other_numbers_currency_or_probability(evidence,scope,registry,profile,text):
    packet,bundle,value=inputs(evidence,scope,registry,profile);value['basis'][0]['text']=text
    errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,bundle)
    assert 'numeric_or_probability_prose_not_allowed' in errors and report is None


def test_mismatched_bundle_and_nonfinite_values_never_get_notes(evidence,scope,registry,profile):
    packet,bundle,value=inputs(evidence,scope,registry,profile);packet['source_evidence_sha256']='0'*64
    assert amount_quote_diagnostics(value,packet,bundle)[1][0]['code']=='target_amount_source_unavailable'
    from graphrisk.agent.autonomous_tools import ToolBoundaryError
    with pytest.raises(ToolBoundaryError):verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,bundle)
    # JSON forbids nonfinite numbers before this contract; no quote exception.
    for number in (float('inf'),float('nan')):
        facts=tuple(f.model_copy(update={'value':number}) if f.metric=='transaction_amount' else f for f in bundle.facts)
        changed=bundle.model_copy(update={'facts':facts})
        with pytest.raises(ValueError,match='JSON compliant'):digest(changed.model_dump(mode='json'))
        with pytest.raises(ValueError):amount_quote_diagnostics(value,packet,changed)


def test_ui_download_derive_note_from_evidence_not_stored_text(evidence,scope,registry,profile):
    from graphrisk.demo.conversation_workbench import v2_lines,readable_report
    packet,bundle,value=inputs(evidence,scope,registry,profile)
    _,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,bundle)
    source=deepcopy(report);report['display_notes']=[{'message':'不要相信这个伪造提示'}]
    lines=v2_lines(report,bundle.model_dump(mode='json'))
    assert NOTE in lines and '不要相信这个伪造提示' not in lines
    case={'evidence':bundle.model_dump(mode='json'),'turns':[{'task':'risk_assessment','state':'completed','report':report}]}
    assert NOTE in readable_report(case) and source['judgment']==report['judgment']
    old=deepcopy(source);old['judgment_rules_version']='typed-citation-support-v1'
    assert NOTE not in v2_lines(old,bundle.model_dump(mode='json'))
    changed=deepcopy(source);changed['judgment']['basis'][0]['text']='目标交易金额为30.95，需核查授权。'
    assert '暂停展示' in v2_lines(changed,bundle.model_dump(mode='json'))[0]
    changed=deepcopy(source);changed['program']['facts'][0]['text']='已篡改'
    assert '不一致' in v2_lines(changed,bundle.model_dump(mode='json'))[0]


@pytest.mark.parametrize('text',[
    '币种为美元，目标交易金额为25.95，需核查授权情况。',
    '目标交易金额为25.95，按人民币记账，需核查授权情况。',
    '目标交易金额为25.95，单位为元，需核查授权情况。',
])
def test_correct_amount_does_not_establish_a_currency(evidence,scope,registry,profile,text):
    packet,bundle,value=inputs(evidence,scope,registry,profile);value['basis'][0]['text']=text
    errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,bundle)
    assert 'target_amount_currency_unverified' in errors and report is None


def test_streamlit_accepted_amount_note_is_read_only(evidence,scope,registry,profile):
    import logging
    from streamlit.testing.v1 import AppTest
    packet,bundle,value=inputs(evidence,scope,registry,profile)
    _,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,bundle)
    case={'evidence':bundle.model_dump(mode='json'),'turns':[{'task':'risk_assessment','state':'completed','report':report,'actual_model_calls':1,'actual_tool_calls':0}]}
    loggers=[logging.getLogger(n) for n in ('','uvicorn','uvicorn.error','uvicorn.access')]
    saved=[(l,list(l.handlers),l.level,l.propagate,l.disabled) for l in loggers]
    try:
        app=AppTest.from_string('from graphrisk.demo.conversation_workbench import show_conversation\nimport streamlit as st\nshow_conversation(st.session_state["case"])')
        app.session_state['case']=case;app.run()
        assert not app.exception and not app.button
        assert any(n.value==NOTE for n in app.text)
        app.run()
        assert not app.exception and app.session_state['case']['turns'][0]['actual_model_calls']==1
    finally:
        for logger,handlers,level,propagate,disabled in saved:
            logger.handlers=handlers;logger.setLevel(level);logger.propagate=propagate;logger.disabled=disabled
