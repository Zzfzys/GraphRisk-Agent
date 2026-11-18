import pytest
from test_narrative import evidence,draft
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from test_autonomous_loop import make_engine
from graphrisk.agent.narrative import NarrativeDraft,verify_narrative,expected_fact_assessments
from graphrisk.agent.upgrade_contract import canonical,digest
from graphrisk.agent.autonomous_budget import ExecutionStop

@pytest.mark.parametrize('field,wrong',[('relation_scope','not_truncated'),('graph_scope','partial'),('amount_vs_24h_mean','above')])
def test_typed_claim_must_match_same_metric_scope(evidence,field,wrong):
    _,packet,bundle=evidence;value=draft();value['fact_assessments'][field]=wrong
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(value)),packet,bundle)
    assert report is None and any(x.startswith('fact_assessment_mismatch:'+field) for x in errors)

def test_wrong_window_or_source_never_supplies_comparison(evidence):
    _,_,bundle=evidence;facts=list(bundle.facts)
    for i,f in enumerate(facts):
        if f.metric=='history_amount_mean':facts[i]=f.model_copy(update={'window_seconds':3600,'availability':'available','value':1,'missing_reason':None})
    bundle=bundle.model_copy(update={'facts':tuple(facts)})
    assert expected_fact_assessments(bundle)['amount_vs_24h_mean']=='unknown'

@pytest.mark.parametrize('phrase,code',[('图邻居亦被截断，仅能作为线索。','graph_truncation_assertion_not_supported'),('目标交易金额与历史特征存在差异。','amount_comparison_unavailable'),('关联实体返回数量较多，仅能作为线索。','relation_count_judgment_without_validated_threshold')])
def test_saved_live_failure_sentences_rejected_even_with_correct_enums(evidence,phrase,code):
    _,packet,bundle=evidence;value=draft();value['basis'][0]['text']=phrase
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(value)),packet,bundle)
    assert report is None and code in errors

def test_supplement_cap_is_shared_across_campaigns_and_never_grants_local(tmp_path,scope,index,registry,profile):
    engine,budget,_,_=make_engine(tmp_path,scope,index,registry,profile);store=engine.store
    store.campaign('extra','f'*64,cap_each=100,group_key='route5_supplement_v3')
    for i in range(101):
        if i==100:store.campaign('other','e'*64,cap_each=100,group_key='route5_supplement_v3')
        session='extra-'+str(i);store.open(session,'extra' if i<100 else 'other',{'n':i},'deepseek',calls=1)
        phase=store.register_packet(session,0,digest(i),'deepseek')
        if i==100:
            with pytest.raises(ExecutionStop,match='supplement_100_request_cap'):
                budget.reserve(provider='deepseek',request_key=str(i),packet_sha=digest(i),phase=phase,reserve_micro=1)
            break
        attempt=budget.reserve(provider='deepseek',request_key=str(i),packet_sha=digest(i),phase=phase,reserve_micro=1)
        budget.settle(attempt,cost_micro=1,receipt_sha='a'*64,receipt_path='fixture',identity='same-fixture',usage={},result_code='ok')
    store.open('local-extra','extra',{'n':'local'},'local',calls=1)
    phase=store.register_packet('local-extra',0,'b'*64,'local')
    with pytest.raises(ExecutionStop,match='supplement_deepseek_only'):
        budget.reserve(provider='local',request_key='local',packet_sha='b'*64,phase=phase,reserve_micro=0)
    with pytest.raises(ExecutionStop,match='invalid_campaign_group'):store.campaign('old','c'*64,cap_each=100,group_key='route5')
    assert budget.snapshot()['total_attempts']==100
