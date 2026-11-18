from copy import deepcopy
import pytest
from test_narrative import evidence,draft
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from graphrisk.agent.narrative import NarrativeDraft,verify_narrative,narrative_packet
from graphrisk.agent.narrative_repair import make_plan,NarrativePatch,apply_patch_result
from graphrisk.agent.upgrade_contract import canonical


def test_schema_and_semantic_errors_collected_together_without_coercion(evidence):
    _,packet,bundle=evidence;v=draft()
    v['basis'][0]={'text':'图邻居仅返回部分，历史金额均值尚不可用。','refs':['F1','F2','F3','F4']}
    v['fact_assessments']['graph_scope']='partial';old=deepcopy(v)
    plan=make_plan(v,packet,bundle)
    assert set(plan['allowed_paths'])=={'basis.0','fact_assessments.graph_scope'}
    codes={i['code'] for i in plan['issues']}
    assert 'too_long' in codes and 'graph_truncation_assertion_not_supported' in codes
    assert 'fact_assessment_mismatch:graph_scope:expected_unknown' in codes
    assert 'claim_missing_metric_citation:history_amount_mean' in codes
    assert plan['source']==v==old
    changes=[{'path':'basis.0','op':'replace','value':{'text':'图邻居截断状态未知，历史金额均值尚不可用。','refs':['F20','F5']}},
             {'path':'fact_assessments.graph_scope','op':'replace','value':'unknown'}]
    merged=apply_patch_result(NarrativePatch.model_validate_json(canonical({'base_sha256':plan['base_sha256'],'changes':changes})),plan)
    assert verify_narrative(NarrativeDraft.model_validate_json(canonical(merged)),packet,bundle)[1]
    assert merged['risk_level']==v['risk_level']


def test_malformed_field_does_not_hide_valid_sibling_semantics(evidence):
    _,packet,bundle=evidence;v=draft();v['basis']=None;v['policy']['refs']=17
    v['fact_assessments']['graph_scope']='partial'
    plan=make_plan(v,packet,bundle)
    assert set(plan['allowed_paths'])=={'basis','policy','fact_assessments.graph_scope'}
    assert plan['source']['basis'] is None and plan['source']['policy']['refs']==17


def test_removed_refs_cannot_leave_known_fact_clause_unsupported(evidence):
    _,packet,bundle=evidence;v=draft()
    v['basis'][0]={'text':'历史金额均值与标准差不可用，图邻居截断状态未知。','refs':['F5','F6','F20']}
    assert verify_narrative(NarrativeDraft.model_validate_json(canonical(v)),packet,bundle)[1]
    v['basis'][0]['refs']=['F5']
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(v)),packet,bundle)
    assert report is None and 'claim_missing_metric_citation:history_amount_population_std' in errors
    assert 'claim_missing_metric_citation:graph_neighbors_truncated' in errors
    assert make_plan(v,packet,bundle)['allowed_paths']==['basis.0']


@pytest.mark.parametrize('text,valid',[
 ('图邻居仅返回部分，仍需核查。',False),
 ('图邻居只覆盖部分，仍需核查。',False),
 ('图范围不完整，仍需核查。',False),
 ('建议补齐图邻居截断状态。',True),
 ('建议获取图邻居截断标记。',True),
 ('不能确认图邻居仅返回部分。',True),
 ('核查图邻居是否仅返回部分。',True),
 ('建议补齐图邻居截断状态，但图邻居仅返回部分。',False),
 ('建议补齐图邻居已被截断的证据。',False)])
def test_scope_claim_uncertainty_and_requested_measurement(evidence,text,valid):
    _,packet,bundle=evidence;v=draft();v['next_check']={'text':text,'refs':['F20']}
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(v)),packet,bundle)
    assert (report is not None)==valid
    if not valid:assert 'graph_truncation_assertion_not_supported' in errors


@pytest.mark.parametrize('window,refs,valid',[('10分钟',['P1'],True),('十分钟',['P1'],True),('20分钟',['P1'],False),('二十分钟',['P1'],False),('10分钟',['F3'],False)])
def test_policy_window_requires_matching_policy_citation(evidence,window,refs,valid):
    _,packet,bundle=evidence;packet=deepcopy(packet)
    packet['view']['policies'][0]['conditions']=[['card_attempt_count','target_card',600,'attempts','gte',4,'unmeasured',None,None,None]]
    v=draft();v['next_check']={'text':f'建议核查目标交易前{window}的卡片交易尝试。','refs':refs}
    assert (verify_narrative(NarrativeDraft.model_validate_json(canonical(v)),packet,bundle)[1] is not None)==valid


@pytest.mark.parametrize('window,valid',[('24小时',True),('二十四小时',True),('12小时',False),('十二小时',False)])
def test_limitation_window_uses_source_bundle_without_enabling_other_numbers(evidence,window,valid):
    _,packet,bundle=evidence;v=draft();v['limitation']=f'此前{window}金额均值不可用，无法比较当前金额。'
    assert (verify_narrative(NarrativeDraft.model_validate_json(canonical(v)),packet,bundle)[1] is not None)==valid
    v['limitation']+='金额为99。'
    assert verify_narrative(NarrativeDraft.model_validate_json(canonical(v)),packet,bundle)[1] is None
