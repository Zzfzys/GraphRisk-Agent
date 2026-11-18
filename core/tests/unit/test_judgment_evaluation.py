import pytest
from test_narrative import evidence
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from graphrisk.agent.judgment import packet_for
from graphrisk.agent.judgment_evaluation import FAMILIES,assert_control


@pytest.mark.parametrize('family',list(FAMILIES))
def test_controlled_family_has_only_its_declared_defects(evidence,scope,registry,profile,family):
    executor,_,bundle=evidence
    facts=list(bundle.facts)
    for i,f in enumerate(facts):
        if f.metric=='returned_related_count':facts[i]=f.model_copy(update={'value':50})
    bundle=bundle.model_copy(update={'facts':tuple(facts)})
    packet=packet_for(scope,executor.observations,registry,profile)
    from graphrisk.agent.upgrade_contract import digest
    packet['source_evidence_sha256']=digest(bundle.model_dump(mode='json'))
    plan=assert_control(family,packet,bundle)
    assert plan['allowed_paths'] and all(i.get('field') for i in plan['issues'])
