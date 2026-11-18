from copy import deepcopy
import pytest
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from test_judgment_v3 import evidence,v3,good
from graphrisk.agent import judgment_v3 as generation
from graphrisk.agent.report_quality import prepare_quality_packet,blank_review
from graphrisk.agent.report_quality_judge import packet_for,assess,Verdict
from graphrisk.agent.fact_presentation import fact_text
from graphrisk.agent.upgrade_contract import canonical,digest


@pytest.mark.parametrize('field',['main_limit','gaps'])
def test_authorization_capability_cannot_stand_in_for_attempt_frequency(v3,field):
    packet,bundle=v3;draft=good()
    gap={'source':'G1','impact':'授权能力缺失，无法核验卡片尝试次数，速度异常判断受限。'}
    draft[field]=gap if field=='main_limit' else [gap]
    issues=generation.issues_for(draft,packet,bundle)
    assert any(i['code']=='authorization_gap_cannot_explain_attempt_frequency' for i in issues)
    plan=generation.repair_plan(draft,packet,bundle)
    assert field in plan['allowed_paths'] or 'gaps.0' in plan['allowed_paths']


def test_authorization_purpose_cannot_silently_switch_to_card_attempts(v3):
    packet,bundle=v3;draft=good()
    draft['next_check']['purpose']='统计卡片尝试次数以确认速度异常。'
    assert any(i['code']=='authorization_check_purpose_must_not_be_card_attempts' for i in generation.issues_for(draft,packet,bundle))


def test_explicitly_distinguishing_authorization_from_attempt_count_is_not_an_inability_claim(v3):
    packet,bundle=v3;draft=good()
    draft['main_limit']['impact']='授权尚未核验，不能确认持卡人同意；不能用卡片尝试次数替代。'
    draft['next_check']['purpose']='核实持卡人是否同意，不能用尝试次数代替授权核查。'
    codes={i['code'] for i in generation.issues_for(draft,packet,bundle)}
    assert 'authorization_gap_cannot_explain_attempt_frequency' not in codes
    assert 'authorization_check_purpose_must_not_be_card_attempts' not in codes


def test_overview_supports_six_verified_references_but_not_seven_or_unknown_ids(v3):
    packet,bundle=v3;draft=good()
    draft['rationale']={'text':'现有材料和局限仍需结合，暂不能确定交易性质。','refs':list(packet['refs_private'])[:6]}
    checked=generation.JudgmentDraft.model_validate_json(canonical(draft),strict=True)
    assert generation.verify_judgment(checked,packet,bundle)[0]==[]
    draft['rationale']['refs'][-1]='F999'
    errors,report=generation.verify_judgment(generation.JudgmentDraft.model_validate_json(canonical(draft),strict=True),packet,bundle)
    assert errors and report is None
    draft['rationale']['refs']=list(packet['refs_private'])[:7]
    with pytest.raises(ValueError):generation.JudgmentDraft.model_validate_json(canonical(draft),strict=True)


@pytest.mark.parametrize('phrase',['目标金额17','目标交易金额17（F1）','该笔金额是17','本笔交易金额：17'])
def test_natural_target_amount_with_exact_source_and_citation_is_nonblocking(v3,phrase):
    packet,bundle=v3;draft=good()
    draft['rationale']={'text':phrase+'，该金额本身不能确认交易性质。','refs':['F1']}
    errors,report=generation.verify_judgment(generation.JudgmentDraft.model_validate_json(canonical(draft),strict=True),packet,bundle)
    assert errors==[] and report['draft']==draft and report['display_notes']


@pytest.mark.parametrize('phrase,refs',[('目标金额18',['F1']),('目标金额17',['F8']),('历史金额17',['F1']),
                                      ('目标金额17元',['F1']),('目标金额17，欺诈概率很高',['F1']),
                                      ('目标金额17，其他金额18',['F1'])])
def test_natural_amount_cannot_hide_wrong_value_currency_probability_or_other_amounts(v3,phrase,refs):
    packet,bundle=v3;draft=good()
    draft['rationale']={'text':phrase+'，需要核查。','refs':refs}
    errors,report=generation.verify_judgment(generation.JudgmentDraft.model_validate_json(canonical(draft),strict=True),packet,bundle)
    assert errors and report is None


def test_sample_count_only_counterevidence_can_be_removed_as_whole_array(evidence,scope,registry,profile):
    executor,_,bundle=evidence
    packet=generation.packet_for(scope,executor.observations,registry,profile)
    refs=[r for r,fid in packet['refs_private'].items() if any(f.fact_id==fid and f.metric=='returned_relation_type_count' for f in bundle.facts)]
    draft=good();draft['counterevidence']=[{'text':'未观察到共享设备交易，削弱整体扩散疑点。','refs':refs[:1]}]
    assert refs
    plan=generation.repair_plan(draft,packet,bundle)
    assert 'counterevidence' in plan['allowed_paths']
    draft['counterevidence']=[]
    assert not any(i['code']=='returned_subset_count_not_global_counterevidence' for i in generation.issues_for(draft,packet,bundle))


def test_fact_label_states_returned_scope_and_entity_type():
    text=fact_text({'metric':'returned_relation_type_count','subject':'returned_shared_device_history','window_seconds':None,'availability':'available','value':0})
    assert '本次已返回样本' in text and '设备字段' in text and '0' in text


def quality(v3):
    packet,bundle=v3
    return prepare_quality_packet(good(),packet,bundle,source_id='judge-fixture',source_kind='existing_model_output')


def verdict_for(target):
    blank=blank_review(target)
    blank['facts']={'status':'Pass','reason':'Fixture evidence review only.','anchors':['risk_level']}
    blank['scores']={name:{'score':2,'reason':'Fixture score input, not real model quality.','anchors':['risk_level']} for name in blank['scores']}
    for role in blank['roles']:role.update(role='alternative',reason='Unverified possible explanation.')
    return {k:blank[k] for k in ('facts','scores','roles')} | {'risk_level_review':{'status':'Pass','reason':'Undetermined consistent with evidence limits.','anchors':['risk_level']}}


def test_judge_packet_is_blinded_bound_and_rejects_source_changes(v3,profile):
    source,bundle=v3;target=quality(v3)
    packet=packet_for(target,source,{**profile,'max_prompt_utf8_bytes':16000})
    text=canonical(packet['view'])
    assert all(word not in text for word in ('PRIVATE-TARGET','system_fingerprint','source_id','estimated_peak_cost'))
    assert packet['quality_target_sha256']==target['target_sha256']
    bad=deepcopy(source);bad['view']['injected']='changed'
    with pytest.raises(ValueError,match='binding_changed'):packet_for(target,bad,profile)


def test_judge_cannot_override_known_errors_or_forge_review_anchors(v3):
    packet,bundle=v3
    draft=good();draft['main_limit']['impact']='授权能力缺失，无法核验卡片尝试次数。'
    target=prepare_quality_packet(draft,packet,bundle,source_id='known-error',source_kind='existing_model_output')
    verdict=verdict_for(target)
    result=assess(target,verdict,identity={'model':'fixture','fingerprint':'fixture-version'})
    assert result['assessment']['combined_status']=='Fail'
    verdict['facts']['anchors']=['invented-unknown-anchor']
    with pytest.raises(ValueError,match='anchor_unknown'):assess(target,verdict,identity={'model':'fixture','fingerprint':'fixture-version'})
