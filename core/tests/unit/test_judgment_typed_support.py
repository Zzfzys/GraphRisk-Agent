from copy import deepcopy
import pytest
from test_narrative import evidence
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from test_judgment import good
from graphrisk.agent.judgment import packet_for,JudgmentDraft,verify_judgment,repair_plan
from graphrisk.agent.judgment_support import (
    RULES_VERSION,asserts_multiple_entity_types,scope_deferral_only,type_witness_sets,
)
from graphrisk.agent.upgrade_contract import canonical,digest


def case(evidence,scope,registry,profile):
    executor,_,bundle=evidence;facts=list(bundle.facts)
    indices=[i for i,f in enumerate(facts) if f.metric=='returned_relation_type_count']
    assert len(indices)==4
    for n,i in enumerate(indices):facts[i]=facts[i].model_copy(update={'value':(4,7,0,0)[n]})
    bundle=bundle.model_copy(update={'facts':tuple(facts)})
    packet=packet_for(scope,executor.observations,registry,profile)
    packet['source_evidence_sha256']=digest(bundle.model_dump(mode='json'))
    refs={f.fact_id:r for r,fid in packet['refs_private'].items() for f in bundle.facts if f.fact_id==fid}
    positive=[refs[facts[i].fact_id] for i in indices[:2]]
    value=good();value['basis'][0]={'text':'关联实体检索返回多类共享实体，匿名关联本身不能证明欺诈。','refs':positive}
    return packet,bundle,value,indices,positive


@pytest.mark.parametrize('cited',[['F9','F16','F17'],['F12'],['F12','F12'],['F12','F999']])
def test_existing_ids_or_graph_counts_cannot_prove_distinct_entity_types(evidence,scope,registry,profile,cited):
    packet,bundle,value,_,positive=case(evidence,scope,registry,profile)
    value['basis'][0]['refs']=cited
    errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,bundle)
    assert 'claim_missing_relation_type_support' in errors and report is None
    plan=repair_plan(value,packet,bundle)
    issue=next(i for i in plan['issues'] if i['code']=='claim_missing_relation_type_support')
    assert sorted(positive) in issue['required_ref_sets'] and plan['allowed_paths']==['basis.0']


def test_two_nonzero_types_from_same_query_are_real_witnesses(evidence,scope,registry,profile):
    packet,bundle,value,_,positive=case(evidence,scope,registry,profile)
    errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,bundle)
    assert errors==[] and report['judgment_rules_version']==RULES_VERSION
    assert report['semantic_quality']=='requires_review' and not report['approval_eligible']
    assert type_witness_sets(bundle,packet,positive)==[sorted(positive)]


@pytest.mark.parametrize('change',['zero','unavailable','boolean','subject','unit','time','tool','query','version','duplicate'])
def test_witness_semantics_and_provenance_must_match(evidence,scope,registry,profile,change):
    packet,bundle,value,indices,_=case(evidence,scope,registry,profile)
    facts=list(bundle.facts);i=indices[1];f=facts[i]
    updates={'zero':{'value':0},'unavailable':{'value':None,'availability':'unavailable','missing_reason':'not_provided_by_legacy_contract'},
             'boolean':{'value':True},'subject':{'subject':'all_shared_entity_history'},'unit':{'unit':'uncalibrated_score'},
             'time':{'temporal_scope':'target_event'},'tool':{'source_tool':'find_risky_neighbors'},
             'query':{'query_ref':'q-other'},'version':{'source_version':'a'*64}}
    if change=='duplicate':
        duplicate=f.model_copy(update={'fact_id':'F-'+'a'*24});facts.append(duplicate)
        packet['refs_private']['F21']=duplicate.fact_id
    else:facts[i]=f.model_copy(update=updates[change])
    bundle=bundle.model_copy(update={'facts':tuple(facts)});packet['source_evidence_sha256']=digest(bundle.model_dump(mode='json'))
    errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,bundle)
    assert 'claim_missing_relation_type_support' in errors and report is None


@pytest.mark.parametrize('text',[
    '是否存在多类共享实体仍待人工核实。',
    '需要核查是否返回多种关联实体。',
    '无法确认存在不同类型实体，应继续核实。',
])
def test_uncertainty_or_request_is_not_a_positive_type_claim(text):
    assert not asserts_multiple_entity_types(text)


@pytest.mark.parametrize('text',[
    '当前返回多类共享实体，应继续核查。',
    '当前存在多种关联实体，应继续核查。',
    '是否异常仍不清楚且系统已返回多类共享实体。',
    '需要核查授权且已经返回多类共享实体。',
    '需核查是否存在多类共享实体，但当前已经发现多类共享实体。',
])
def test_assertion_in_another_clause_is_not_hidden_by_uncertainty(text):
    assert asserts_multiple_entity_types(text)


@pytest.mark.parametrize('text',[
    '图邻居截断状态由程序说明，需核查授权与卡片尝试记录。',
    '图邻居是否被截断，以程序段说明为准。',
])
def test_deferral_remains_owned_by_program_but_is_not_a_truncation_assertion(evidence,scope,registry,profile,text):
    packet,bundle,value,_,_=case(evidence,scope,registry,profile)
    value['basis'][0]={'text':text,'refs':['F20']}
    errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,bundle)
    assert scope_deferral_only(text) and 'program_owned_scope_statement' in errors and report is None
    assert 'graph_truncation_assertion_not_supported' not in errors


@pytest.mark.parametrize('text',[
    '图邻居均被截断。',
    '图邻居已截断，以程序段说明为准。',
    '图邻居截断状态由程序说明，但图邻居已截断。',
    '图邻居是否被截断，以程序段说明为准，但已确认截断。',
])
def test_mixed_or_actual_scope_assertions_are_never_exempt(evidence,scope,registry,profile,text):
    packet,bundle,value,_,_=case(evidence,scope,registry,profile)
    value['basis'][0]={'text':text,'refs':['F20']}
    errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,bundle)
    assert not scope_deferral_only(text) and 'program_owned_scope_statement' in errors and report is None
