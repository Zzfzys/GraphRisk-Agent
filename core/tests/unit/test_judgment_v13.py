from copy import deepcopy
import pytest
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from test_judgment_v3 import evidence,good
from graphrisk.agent import judgment_v13 as v13,judgment_v3 as v12
from graphrisk.agent.upgrade_contract import canonical


@pytest.fixture
def candidate(evidence,scope,registry,profile):
    executor,_,bundle=evidence
    packet=v13.packet_for(scope,executor.observations,registry,{**profile,'max_prompt_utf8_bytes':20000})
    value=good();value['gaps']=[];value['next_check']={'text':'authorization'}
    return value,packet,bundle


@pytest.mark.parametrize('ref',['G1','F0','P0','F99'])
def test_namespaces_and_packet_membership_reject_invalid_refs(candidate,ref):
    value,packet,_=candidate;value['rationale']['refs']=[ref]
    with pytest.raises(ValueError):v13.parse(value,packet)


def test_duplicate_gap_and_oversized_refs_rejected_before_semantic_grading(candidate):
    value,packet,_=candidate;value['gaps']=[value['main_limit']]
    with pytest.raises(ValueError):v13.parse(value,packet)
    value['gaps']=[];value['rationale']['refs']=['F1']*7
    with pytest.raises(ValueError):v13.parse(value,packet)


def test_program_owns_all_check_text_and_refs(candidate):
    value,packet,bundle=candidate
    value['next_check']['purpose']='冒充查询结果'
    with pytest.raises(ValueError):v13.parse(value,packet)
    value['next_check']={'text':'authorization'}
    parsed=v13.parse(value,packet);issues,report=v13.verify(parsed,packet,bundle)
    assert issues==[] and report['draft']==value
    check=report['judgment']['next_check']
    assert check['source']=='program' and check['target']=='current_transaction' and check['state']=='planned_not_executed'
    assert packet['scope_private']['transaction_id'] not in canonical(check)
    assert report['next_check_content_source']=='program_not_llm' and not report['approval_eligible']


@pytest.mark.parametrize('kind',['authorization','graph_scope','history_stats'])
def test_each_supported_direction_binds_its_own_metric(candidate,kind):
    _,packet,bundle=candidate;check=v13.check_binding(kind,bundle,packet)
    assert check['refs'] and all(r.startswith(('F','P')) for r in check['refs'])


def test_card_attempt_direction_requires_matching_policy(candidate):
    _,packet,bundle=candidate;packet=deepcopy(packet);packet['view']['policies']=[]
    with pytest.raises(ValueError,match='check_direction_unsupported'):v13.check_binding('card_attempts',bundle,packet)
    packet['view']['policies']=[{'ref':'P1','conditions':[['card_attempt_count']]}]
    assert v13.check_binding('card_attempts',bundle,packet)['refs']==['P1']


def test_missing_std_citation_is_caught_without_rewriting_prose(candidate):
    value,packet,bundle=candidate
    mean=next(r for r,fid in packet['refs_private'].items() if any(f.fact_id==fid and f.metric=='history_amount_mean' for f in bundle.facts))
    std=next(r for r,fid in packet['refs_private'].items() if any(f.fact_id==fid and f.metric=='history_amount_population_std' for f in bundle.facts))
    value['rationale']={'text':'历史平均金额与波动缺失，不能据此判断金额异常。','refs':[mean]}
    before=deepcopy(value)
    assert 'claim_missing_metric_citation:history_amount_population_std' in [i['code'] for i in v13.issues_for(value,packet,bundle)]
    assert value==before
    value['rationale']['refs'].append(std)
    assert 'claim_missing_metric_citation:history_amount_population_std' not in [i['code'] for i in v13.issues_for(value,packet,bundle)]


def test_compound_difference_does_not_require_unclaimed_mean(candidate):
    value,packet,bundle=candidate
    ref=next(r for r,fid in packet['refs_private'].items() if any(f.fact_id==fid and f.metric=='amount_minus_history_mean' for f in bundle.facts))
    value['rationale']={'text':'金额与历史均值之差缺失，当前无法评估金额偏离。','refs':[ref]}
    assert 'claim_missing_metric_citation:history_amount_mean' not in [i['code'] for i in v13.issues_for(value,packet,bundle)]


def test_cross_model_claim_is_rejected_but_exact_noncomparability_statement_is_not(candidate):
    value,packet,bundle=candidate
    refs=[r for r,fid in packet['refs_private'].items() if any(f.fact_id==fid and f.metric in ('primary_model_score','experimental_graph_score') for f in bundle.facts)]
    value['basis']=[{'text':'主评分与图分数方向相反，说明本笔可能存在异常。','refs':refs}]
    assert 'cross_model_score_comparison_not_evidence' in [i['code'] for i in v13.issues_for(value,packet,bundle)]
    value['basis'][0]['text']=v13.SAFE_DUAL
    assert 'cross_model_score_comparison_not_evidence' not in [i['code'] for i in v13.issues_for(value,packet,bundle)]


def test_raw_score_is_not_a_hard_business_threshold(candidate):
    value,packet,bundle=candidate
    # The proposal's 0.23+behavior=>high conflicts with its 0.30 ceiling.
    # No calibrated threshold is invented; high needs measured behavioral support.
    value['risk_level']='high';value['basis']=[]
    assert 'high_requires_behavioral_support' in [i['code'] for i in v13.issues_for(value,packet,bundle)]
    assert '0.3/0.7' in packet['view']['risk_bounds']['high_requires']


def test_noncomparability_is_not_misclassified_as_comparison():
    assert not v13.asserts_cross_model_comparison('两种信号都未校准，不能相互抵消或平均为概率。')
    assert v13.asserts_cross_model_comparison('不能直接比较，但图分数高于主评分，因此提高风险。')


def test_v12_schema_still_accepts_its_original_contract(candidate):
    value=good()
    assert v12.JudgmentDraft.model_validate_json(canonical(value)).next_check.purpose==value['next_check']['purpose']


def test_factory_packet_runs_through_real_adapter_metering_and_free_replay(candidate,tmp_path,profile):
    from test_autonomous_action_providers import ledger,transport_for,cloud_body
    from graphrisk.agent.upgrade_contract import digest
    value,packet,bundle=candidate
    profile={**profile,'max_prompt_utf8_bytes':20000}
    campaign={'profile_sha256':digest(profile),'packets':[packet]}
    budget=ledger(tmp_path,campaign);posts=[]
    client=v13.Provider('deepseek',profile=profile,budget=budget,campaign=campaign,campaign_sha=digest(campaign),
        phase='pilot',output=tmp_path/'receipts',runtime=tmp_path,
        transport=transport_for(profile,posts,mutate=lambda _:cloud_body(value)))
    result=client.generate('0')
    assert result['code']=='ok' and len(posts)==1
    assert v13.verify(v13.parse(result['action'],packet),packet,bundle)[1]['next_check_content_source']=='program_not_llm'
    assert client.generate('0')['replayed_without_model_request'] and len(posts)==1
    assert budget.snapshot()['total_attempts']==1
