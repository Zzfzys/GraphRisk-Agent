import pytest
from graphrisk.agent import judge_events_v13 as judge
from graphrisk.agent.upgrade_contract import canonical


def test_calibration_requires_both_classes_and_counts_pending_as_failure():
    gold=[{'id':'a','verdict':'defect','path':'rationale.text','span_text':'金额正常','rule_id':'missing_as_normal'},
          {'id':'b','verdict':'pass'}]
    predictions={'a':{'verdict':'defect','defects':[{'path':'rationale.text','span_text':'金额正常','rule_id':'missing_as_normal'}]},
                 'b':{'verdict':'pass','defects':[]}}
    result=judge.calibration(gold,predictions)
    assert result['sensitivity']==1 and result['false_positive_rate']==0 and result['kappa']==1 and result['reference_eligible']
    predictions['b']['verdict']='pending'
    result=judge.calibration(gold,predictions)
    assert result['fp']==0 and result['pending_or_missing']==1 and result['kappa'] is None and not result['reference_eligible']
    assert judge.calibration(gold[:1],{'a':predictions['a']})['false_positive_rate'] is None
    assert not judge.calibration(gold[:1],{'a':predictions['a']})['reference_eligible']


def test_wrong_localization_does_not_count_as_detected():
    gold=[{'id':'a','verdict':'defect','path':'basis.0.text','span_text':'已核实','rule_id':'unperformed_check'},{'id':'b','verdict':'pass'}]
    p={'a':{'verdict':'defect','defects':[{'path':'rationale.text','span_text':'已核实','rule_id':'unperformed_check'}]},'b':{'verdict':'pass','defects':[]}}
    assert judge.calibration(gold,p)['fn']==1


@pytest.mark.parametrize('value',[{'path':'rationale.text','rule_id':'unsupported_citation'},
    {'path':'rationale.text','span_text':'','rule_id':'unsupported_citation'}])
def test_defect_requires_quote(value):
    with pytest.raises(ValueError):judge.Defect.model_validate_json(canonical(value))


def test_judge_cannot_invent_quoted_candidate_text():
    dim={'score':1,'reason':'部分支持，需要进一步核对。'}
    verdict={'case':'C1','verdict':'defect','defects':[{'path':'rationale.text','span_text':'已核实','rule_id':'unperformed_check'}],
        'reason':'核查尚未执行。',**{n:dim for n in ('specificity','analysis','actionability','readability')}}
    packet={'view':{'reports':[{'case':'C1','candidate':{'rationale':{'text':'建议核查授权。'}}},{'case':'C2','candidate':{}}]}}
    with pytest.raises(ValueError):judge.parse(canonical({'verdicts':[verdict,{**verdict,'case':'C2'}]}),packet)


def test_same_case_full_paths_are_normalized_without_accepting_other_cases():
    dim={'score':1,'reason':'部分支持，需要进一步核对。'}
    item={'case':'C1','verdict':'defect','defects':[{'path':'reports[0].candidate.rationale.text',
        'span_text':'已核实授权','rule_id':'unperformed_check'}],'reason':'核查尚未执行，不得声称已核实。',
        **{n:dim for n in ('specificity','analysis','actionability','readability')}}
    clean={**item,'case':'C2','verdict':'pass','defects':[]}
    packet={'view':{'reports':[{'case':'C1','candidate':{'rationale':{'text':'已核实授权。'}}},{'case':'C2','candidate':{}}]}}
    parsed=judge.parse(canonical({'verdicts':[item,clean]}),packet)
    assert parsed.verdicts[0].defects[0].path=='rationale.text'
    item['defects'][0]['path']='reports[1].candidate.rationale.text'
    with pytest.raises(ValueError):judge.parse(canonical({'verdicts':[item,clean]}),packet)
