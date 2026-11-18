from copy import deepcopy
import pytest
from test_autonomous_tool_contracts import scope,index,registry,executor_for,run_core
from test_autonomous_action_providers import profile
from test_judgment_v3 import good
from graphrisk.agent import judgment_v3 as base,judgment_v12_guidance as candidate
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_tools import ToolBoundaryError
from graphrisk.agent.upgrade_contract import canonical,digest


@pytest.fixture
def prepared(scope,index,registry,profile):
    executor,_=executor_for(scope,index);run_core(executor)
    current={**profile,'max_prompt_utf8_bytes':20000,'report_input_presentation':'plain-facts-v2-program-scope'}
    old=base.packet_for(scope,executor.observations,registry,current)
    new=candidate.packet_for(scope,executor.observations,registry,current)
    return executor,current,old,new,build_evidence(scope,executor.observations,registry)


def test_same_facts_policies_schema_and_rule_version(prepared):
    _,_,old,new,_=prepared
    for key in ('facts','policies','response_schema','gap_explanations','tool_status','writing_constraints'):
        assert old['view'][key]==new['view'][key]
    for key in ('source_evidence_sha256','source_scope_sha256','source_observations_sha256','refs_private','action_schema_sha256','judgment_rules_version'):
        assert old[key]==new[key]
    assert old['view_sha256']!=new['view_sha256'] and new['generation_variant']==candidate.GENERATION
    assert 'generation_guidance' not in old['view']


def test_truncated_relation_is_visible_but_unknown_graph_scope_stays_unknown(prepared):
    guidance=prepared[3]['view']['generation_guidance']
    assert guidance['relation_scope']['query_coverage']=='truncated'
    assert guidance['relation_scope']['measurement_scope']=='returned_sample'
    assert guidance['graph_scope']['query_coverage']=='unknown'
    assert guidance['relation_scope']['global_clustering_measurement']=='not_provided_by_registered_tools'


def test_missing_tool_does_not_become_complete_or_safe(scope,index,registry,profile):
    executor,_=executor_for(scope,index);executor.execute('get_transaction',{})
    cfg={**profile,'max_prompt_utf8_bytes':20000,'report_input_presentation':'plain-facts-v2-program-scope'}
    packet=candidate.packet_for(scope,executor.observations,registry,cfg)
    guidance=packet['view']['generation_guidance']
    assert guidance['relation_scope']['query_coverage']==guidance['graph_scope']['query_coverage']=='unknown'


def test_complete_relation_query_has_different_scope_without_normality_inference(scope,index,registry,profile):
    def mutate(name,value):
        if name=='get_related_entities':
            value['data']['total_matches']=len(value['data']['matches']);value['data']['truncated']=False
        return value
    executor,_=executor_for(scope,index,mutate=mutate);run_core(executor)
    packet=candidate.packet_for(scope,executor.observations,registry,
        {**profile,'max_prompt_utf8_bytes':20000,'report_input_presentation':'plain-facts-v2-program-scope'})
    guidance=packet['view']['generation_guidance']['relation_scope']
    assert guidance['query_coverage']=='complete_for_registered_query'
    assert guidance['global_clustering_measurement']=='not_provided_by_registered_tools'


def test_citation_roles_distinguish_sample_size_from_zero_variance(prepared):
    roles=prepared[3]['view']['generation_guidance']['citation_roles']
    counts=[r for r in roles if r['metric']=='history_transaction_count']
    assert {r['window_seconds'] for r in counts}=={3600,86400,604800}
    variance=next(r for r in roles if r['metric']=='history_amount_population_std')
    assert variance['ref'] not in {r['ref'] for r in counts}
    assert variance['availability']=='unavailable'


def test_only_visible_measurement_refs_are_exposed(prepared):
    new=prepared[3];visible={r[0] for r in new['view']['facts']}
    guidance=new['view']['generation_guidance']
    assert set(guidance['relation_scope']['refs'])<=visible
    assert {r['ref'] for r in guidance['citation_roles']}<=visible
    assert not set(guidance['relation_scope']['refs'])&set(new['program_owned_scope_refs_private'])


def test_examples_are_not_case_evidence_or_case_specific_program_judgments(prepared):
    new=prepared[3];guide=new['view']['generation_guidance']
    assert guide['examples_are_case_evidence'] is False
    assert new['view']['facts']==prepared[2]['view']['facts']
    assert 'risk_level' not in guide and 'quality_scores' not in guide
    assert any('零波动本身不能证明样本少' in e.get('limit','') for e in guide['examples'])


def test_same_runtime_parameters_no_private_identifier_leak(prepared):
    _,profile,old,new,_=prepared
    provider=object.__new__(base.JudgmentProvider);provider.provider,provider.profile='deepseek',profile
    a,b=provider.payload(old),provider.payload(new)
    assert {k:v for k,v in a.items() if k!='messages'}=={k:v for k,v in b.items() if k!='messages'}
    outbound=canonical(b)
    assert 'PRIVATE-' not in outbound and 'scope_private' not in outbound and 'refs_private' not in outbound
    assert all(word not in outbound for word in ('is_fraud','fraud_label','api_key','Authorization'))
    assert new['prompt_utf8_bytes']<=profile['max_prompt_utf8_bytes']


@pytest.mark.parametrize('defect',[None,'wrong_amount','false_execution','probability','unsupported_counterevidence'])
def test_verifier_is_unchanged_and_never_repairs_the_draft(prepared,defect):
    _,_,old,new,bundle=prepared;value=good()
    if defect:
        if defect=='unsupported_counterevidence':value['counterevidence']=[{'text':'已返回样本中关联很少，未见明显聚集。','refs':['F13']}]
        else:value['rationale']['text']={'wrong_amount':'目标交易金额为999999。','false_execution':'已经核实持卡人授权，调查结束。','probability':'主评分就是本案欺诈概率。'}[defect]
    draft=base.JudgmentDraft.model_validate_json(canonical(value),strict=True)
    old_result=base.verify_judgment(draft,old,bundle)
    new_result=candidate.verify_judgment(draft,new,bundle)
    assert old_result[0]==new_result[0]
    assert draft.model_dump(mode='json')==value
    if defect=='false_execution':
        # Existing v12 phrase gap, deliberately preserved rather than claiming a new fix.
        assert old_result[0]==[] and new_result[1]['semantic_quality']=='requires_review'
        assert new_result[1]['approval_eligible'] is False
    elif defect:assert new_result[1] is None


def test_positive_relation_example_respects_the_unchanged_native_contract(prepared):
    _,_,_,new,bundle=prepared;value=good()
    value['basis']=[{'text':candidate.EXAMPLES[0]['prefer'],'refs':['F13']}]
    errors,report=candidate.verify_judgment(base.JudgmentDraft.model_validate_json(canonical(value),strict=True),new,bundle)
    assert errors==[] and report is not None


def test_candidate_rejects_unexpected_input_profile(scope,index,registry,profile):
    executor,_=executor_for(scope,index);run_core(executor)
    with pytest.raises(ToolBoundaryError,match='requires_plain_program_scope'):
        candidate.packet_for(scope,executor.observations,registry,profile)


def test_candidate_context_limit_is_not_raised_to_fit_guidance(prepared,scope,registry):
    executor,profile,old,_,_=prepared
    with pytest.raises(ToolBoundaryError,match='context_limit'):
        candidate.packet_for(scope,executor.observations,registry,{**profile,'max_prompt_utf8_bytes':old['prompt_utf8_bytes']})
