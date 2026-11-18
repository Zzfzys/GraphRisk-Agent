import pytest
from test_narrative import evidence,draft
from test_autonomous_tool_contracts import index,registry,scope
from test_autonomous_action_providers import profile
from graphrisk.agent.narrative import NarrativeDraft,verify_narrative,narrative_packet
from graphrisk.agent.upgrade_contract import canonical

@pytest.mark.parametrize('phrase',['共享实体关系数量有限且未截断，尚不能排除正常关联。','关联结果完整返回，可以继续核查。','已查看全部关联，没有遗漏。','共享关系数量很少，可以继续核查。'])
@pytest.mark.parametrize('field',['counterevidence','limitation'])
def test_scope_assertions_rejected_in_report_and_limitation(evidence,phrase,field):
    _,packet,bundle=evidence;value=draft()
    if field=='limitation':value[field]=phrase
    else:value[field]=[dict(text=phrase,refs=['F9','F11'])]
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(value)),packet,bundle)
    assert report is None
    assert set(errors)&{'completeness_assertion_must_use_program_facts','relation_count_judgment_without_validated_threshold'}

def test_known_but_unattached_inline_citation_rejected(evidence):
    _,packet,bundle=evidence;value=draft()
    value['basis'][0]=dict(text='依据P1，应结合历史进一步核查。',refs=['F1'])
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(value)),packet,bundle)
    assert 'inline_citation_not_attached_to_statement' in errors and report is None

@pytest.mark.parametrize('phrase',['不能确认结果完整，需要进一步核查。','是否未截断仍需核实，不能判断全量关系。'])
def test_explicit_uncertainty_not_promoted_to_completeness(evidence,phrase):
    _,packet,bundle=evidence;value=draft();value['limitation']=phrase
    errors,report=verify_narrative(NarrativeDraft.model_validate_json(canonical(value)),packet,bundle)
    assert not errors and report['semantic_quality']=='requires_review'

def test_repair_feedback_gives_action_without_duplicate_evidence(evidence,scope,registry,profile):
    executor,_,_=evidence
    packet=narrative_packet(scope,executor.observations,registry,profile,['completeness_assertion_must_use_program_facts'])
    assert '不要换同义词' in packet['view']['repair_instruction']
    assert packet['prompt_utf8_bytes']<=profile['max_prompt_utf8_bytes']
