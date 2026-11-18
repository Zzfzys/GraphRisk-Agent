from copy import deepcopy
import json
from pathlib import Path
import pytest
from test_judgment_v3 import evidence,good
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from graphrisk.agent import judgment_v13_1 as sut,judgment_v13 as prior,fact_support
from graphrisk.agent.fact_presentation import numeric_display,fact_text
from graphrisk.agent.upgrade_contract import canonical,digest
from test_autonomous_tool_contracts import executor_for,run_core
from graphrisk.agent.autonomous_evidence import build_evidence

@pytest.fixture
def case(evidence,scope,registry,profile):
    executor,_,bundle=evidence
    packet=sut.packet_for(scope,executor.observations,registry,{**profile,'max_prompt_utf8_bytes':20000})
    value=good();value['gaps']=[];value['next_check']={'text':'authorization'}
    return value,packet,bundle

@pytest.mark.parametrize('mode',['primary','repeated','empty'])
def test_gap_redundancy_is_idempotent_and_keeps_original(case,mode):
    value,packet,_=case
    gap=next(g for g in packet['view']['gap_explanations'] if g!=value['main_limit']['source'])
    value['gaps']=([deepcopy(value['main_limit'])] if mode=='primary' else
        [{'source':gap,'impact':'这个缺口仍需核实。'}]*2 if mode=='repeated' else [])
    before=deepcopy(value)
    parsed=sut.parse(value,packet)
    assert parsed.model_dump(mode='json')==before and value==before
    assert [g['source'] for g in sut.effective_gaps(value,packet)]==([gap] if mode=='repeated' else [])

@pytest.mark.parametrize('unknown',['G9','G999'])
def test_unknown_gaps_still_rejected(case,unknown):
    value,packet,_=case;value['gaps']=[{'source':unknown,'impact':'未知缺口仍须拒绝。'}]
    with pytest.raises(ValueError,match='unknown_gap'):sut.parse(value,packet)

def test_duplicate_gap_bad_prose_is_not_erased_before_checks(case):
    value,packet,bundle=case
    value['gaps']=[{'source':'G1','impact':'已经完成核查并已核实授权。'}]
    issues,report=sut.verify(sut.parse(value,packet),packet,bundle)
    assert report is None and any(i['code']=='unperformed_check_claim' for i in issues)
    assert all(i['expected'] for i in issues)

def test_program_gaps_merge_by_id_not_by_model_object(case):
    value,packet,bundle=case
    value['gaps']=[deepcopy(value['main_limit'])]
    issues,report=sut.verify(sut.parse(value,packet),packet,bundle)
    assert not issues and report['draft']==value and report['judgment']['gaps']==[]
    assert report['gap_normalization']['original_draft_preserved']
    assert report['approval_eligible'] is False
    assert report['semantic_quality']=='requires_review'

@pytest.fixture
def numeric_case(scope,index,registry,profile):
    def mutate(name,response):
        if name=='get_transaction':response['data']['transaction_amount']=9.837
        if name=='get_risk_score':response['data']['risk_score']=0.9312345678901
        if name=='get_transaction_features':response['data'].update(
            tx_count_last_24h=413378,tx_count_last_7d=413400,amount_mean_last_24h=20.123,amount_std_last_24h=2.456,
            amount_deviation_last_24h=-10.286)
        return response
    executor,_=executor_for(scope,index,mutate=mutate);run_core(executor)
    packet=sut.packet_for(scope,executor.observations,registry,{**profile,'max_prompt_utf8_bytes':20000})
    bundle=build_evidence(scope,executor.observations,registry)
    value=good();value['gaps']=[];value['next_check']={'text':'authorization'}
    return value,packet,bundle

@pytest.mark.parametrize('metric,value,expected',[
    ('primary_model_score',0.9312345678901,'0.931'),
    ('experimental_graph_score',0.2312345678901,'0.231'),
    ('transaction_amount',9.837,'9.837'),('transaction_amount',1000,'1000'),
    ('history_amount_mean',20.123,'20.123'),('history_amount_population_std',2.456,'2.456'),
    ('amount_minus_history_mean',-10.286,'-10.29'),
    ('history_transaction_count',413378,'413,378'),
])
def test_metric_specific_numeric_display(metric,value,expected):
    assert numeric_display(metric,value)==expected

@pytest.mark.parametrize('value',[True,None,float('nan'),float('inf'),'0.931'])
def test_numeric_display_rejects_invalid_values(value):
    with pytest.raises(ValueError,match='finite_number'):numeric_display('primary_model_score',value)

@pytest.mark.parametrize('text,refs',[
    ('主评分为0.931，是未校准信号，不是欺诈概率。',['F8']),
    ('主评分为0.9312345678901，是未校准信号，不是欺诈概率。',['F8']),
    ('目标交易金额为9.837。',['F1']),
    ('此前24小时匿名匹配历史交易数为413,378。',['F3']),
    ('此前24小时匿名匹配历史交易数为413378。',['F3']),
    ('此前24小时金额与历史均值之差为-10.29。',['F7']),
    ('此前24小时金额与历史均值之差为-10.286。',['F7']),
])
def test_raw_or_unique_display_values_pass(numeric_case,text,refs):
    value,packet,bundle=numeric_case;value['rationale']={'text':text,'refs':refs}
    before=deepcopy(value);issues,report=sut.verify(sut.parse(value,packet),packet,bundle)
    assert not issues and report['draft']==before and value==before

@pytest.mark.parametrize('text,refs,expected',[
    ('主评分为0.9312，是未校准信号，不是欺诈概率。',['F8'],'0.931'),
    ('主评分为0.93，是未校准信号，不是欺诈概率。',['F8'],'0.931'),
    ('主评分为0.9，是未校准信号，不是欺诈概率。',['F8'],'0.931'),
    ('目标交易金额为9.84。',['F1'],'9.837'),
    ('目标交易金额为9837。',['F1'],'9.837'),
    ('此前24小时匿名匹配历史交易数为413,379。',['F3'],'413,378'),
    ('此前24小时金额与历史均值之差为-10.3。',['F7'],'-10.29'),
])
def test_other_rounding_and_wrong_values_stay_rejected(numeric_case,text,refs,expected):
    value,packet,bundle=numeric_case;value['rationale']={'text':text,'refs':refs}
    issues,report=sut.verify(sut.parse(value,packet),packet,bundle)
    assert report is None
    assert any(i['code']=='fact_claim_value_mismatch' and i['expected']==expected for i in issues)

@pytest.mark.parametrize('text,refs',[
    ('此前24小时匿名匹配历史交易数为41,3378。',['F3']),
    ('目标交易金额为9.837美元。',['F1']),
    ('主评分为0.931，是未校准信号，不是欺诈概率。',['F16']),
    ('此前24小时匿名匹配历史交易数为17。',['F2']),
    ('本笔欺诈概率为0.931。',['F8']),
    ('目标交易金额为9.837，另外还有99笔。',['F1']),
])
def test_numeric_counterexamples_cannot_be_released(numeric_case,text,refs):
    value,packet,bundle=numeric_case;value['rationale']={'text':text,'refs':refs}
    issues,report=sut.verify(sut.parse(value,packet),packet,bundle)
    assert issues and report is None and all(i.get('expected') for i in issues)

def test_old_numeric_contract_remains_exact(numeric_case):
    _,packet,bundle=numeric_case
    result=fact_support.assess_statement('rationale','主评分为0.931，是未校准信号，不是欺诈概率。',['F8'],packet,bundle)
    assert any(i['code']=='fact_claim_value_mismatch' for i in result.issues)

def test_old_default_fact_text_and_new_display_are_separate(numeric_case):
    _,packet,bundle=numeric_case
    fact=next(f for f in bundle.facts if f.metric=='primary_model_score').model_dump(mode='json')
    assert '0.9312345678901' in fact_text(fact)
    assert '0.931；' in fact_text(fact,numeric_formatter=numeric_display)
    assert '0.9312345678901' not in next(r[1] for r in packet['view']['facts'] if r[0]=='F8')

def test_numeric_display_binding_cannot_be_rehashed_to_another_value(case):
    value,packet,bundle=case;packet=deepcopy(packet)
    packet['view']['numeric_display_contract']['values'][0][1]='9999'
    packet['numeric_display_contract_sha256']=digest(packet['view']['numeric_display_contract'])
    with pytest.raises(ValueError,match='numeric_display_contract_changed'):sut.issues_for(value,packet,bundle)

def test_successful_feature_and_graph_queries_excluded_but_authorization_kept(case):
    _,packet,bundle=case
    choices=packet['view']['output_contract']['next_check_choices']
    assert 'history_stats' not in choices and 'graph_scope' not in choices and 'authorization' in choices
    # A successful feature tool with no historical statistic still succeeded;
    # repeating it does not create a new source.
    assert any(f.metric=='history_amount_mean' and f.availability=='unavailable' for f in bundle.facts)
    assert packet['view']['response_schema']['$defs']['CheckChoice']['properties']['text']['enum']==choices

def test_successful_available_stats_query_is_also_excluded(numeric_case):
    _,packet,_=numeric_case
    assert 'history_stats' not in packet['view']['output_contract']['next_check_choices']

@pytest.mark.parametrize('status',['error','unavailable','not_requested','no_match'])
def test_failed_unknown_or_unexecuted_tool_can_remain_retry_candidate(case,status):
    _,packet,bundle=case
    changed=bundle.model_copy(update={'tool_outcomes':tuple(o.model_copy(update={'status':status})
        if o.tool=='get_transaction_features' else o for o in bundle.tool_outcomes)})
    assert 'history_stats' in sut.supported_choices(changed,packet)

def test_no_supported_direction_is_explicit_not_empty_enum(evidence,scope,registry,profile,monkeypatch):
    executor,_,_=evidence
    monkeypatch.setattr(sut,'supported_choices',lambda *args,**kwargs:[])
    with pytest.raises(ValueError,match='check_direction_unsupported'):
        sut.packet_for(scope,executor.observations,registry,{**profile,'max_prompt_utf8_bytes':20000})

def test_completed_check_is_rejected_in_parse_and_diagnostics(case):
    value,packet,bundle=case;value['next_check']={'text':'history_stats'}
    with pytest.raises(ValueError,match='check_direction_unsupported'):sut.parse(value,packet)
    issues=sut.issues_for(value,packet,bundle)
    assert issues[0]['code']=='check_direction_unsupported' and issues[0]['expected']

def test_ablation_flags_bind_independently(evidence,scope,registry,profile):
    executor,_,bundle=evidence;p={**profile,'max_prompt_utf8_bytes':20000}
    stage_a=sut.packet_for(scope,executor.observations,registry,p,numeric=False,filter_checks=False)
    stage_b=sut.packet_for(scope,executor.observations,registry,p,numeric=True,filter_checks=False)
    stage_c=sut.packet_for(scope,executor.observations,registry,p,numeric=True,filter_checks=True)
    assert stage_a['view']['facts']==prior.packet_for(scope,executor.observations,registry,p)['view']['facts']
    assert stage_a['view']['output_contract']['next_check_choices']==stage_b['view']['output_contract']['next_check_choices']
    assert 'history_stats' in stage_b['view']['output_contract']['next_check_choices']
    assert 'history_stats' not in stage_c['view']['output_contract']['next_check_choices']
    assert stage_a['source_evidence_sha256']==stage_b['source_evidence_sha256']==stage_c['source_evidence_sha256']

def test_v13_and_v12_baselines_still_reject_and_accept_their_original_shapes(case):
    from graphrisk.agent import judgment_v3 as v12
    value,packet,_=case;value['gaps']=[value['main_limit']]
    with pytest.raises(ValueError,match='duplicate_primary_gap'):prior.parse(value,packet)
    assert v12.JudgmentDraft.model_validate_json(canonical(good()))

def test_unsupported_direction_detector_with_no_available_evidence(case):
    _,packet,bundle=case;packet=deepcopy(packet);packet['refs_private']={};packet['view']['policies']=[]
    assert sut.supported_choices(bundle,packet)==[]

def test_mock_adapter_preserves_raw_redundancy_with_one_local_receipt(case,tmp_path,profile):
    from test_autonomous_action_providers import ledger,transport_for,cloud_body
    value,packet,bundle=case;value['gaps']=[deepcopy(value['main_limit'])]
    p={**profile,'max_prompt_utf8_bytes':20000};campaign={'profile_sha256':digest(p),'packets':[packet]}
    budget=ledger(tmp_path,campaign);posts=[]
    client=sut.Provider('deepseek',profile=p,budget=budget,campaign=campaign,campaign_sha=digest(campaign),
        phase='pilot',output=tmp_path/'receipts',runtime=tmp_path,
        transport=transport_for(p,posts,mutate=lambda _:cloud_body(value)))
    result=client.generate('0')
    assert result['code']=='ok' and len(posts)==1 and result['action']==value
    assert not sut.verify(sut.parse(result['action'],packet),packet,bundle)[0]
    assert client.generate('0')['replayed_without_model_request'] and len(posts)==1

FIXTURES=Path(__file__).resolve().parents[1]/'fixtures/fact_support'

@pytest.mark.parametrize('entry',json.loads((FIXTURES/'pass/quality_factors_v1.json').read_text(encoding='utf-8')),ids=lambda e:e['id'])
def test_frozen_positive_fixture_list(numeric_case,entry):
    value,packet,bundle=numeric_case;value['rationale']={k:entry[k] for k in ('text','refs')}
    issues,report=sut.verify(sut.parse(value,packet),packet,bundle)
    assert not issues and report is not None

@pytest.mark.parametrize('entry',json.loads((FIXTURES/'fail/quality_factors_v1.json').read_text(encoding='utf-8')),ids=lambda e:e['id'])
def test_frozen_negative_fixture_list(numeric_case,entry):
    value,packet,bundle=numeric_case;value['rationale']={k:entry[k] for k in ('text','refs')}
    issues,report=sut.verify(sut.parse(value,packet),packet,bundle)
    assert issues and report is None and all(i.get('expected') for i in issues)
