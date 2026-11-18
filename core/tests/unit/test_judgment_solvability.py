from copy import deepcopy
from datetime import date
from pathlib import Path
import sys
import pytest
from test_narrative import evidence
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from graphrisk.agent.judgment import packet_for,JudgmentDraft,verify_judgment
from graphrisk.agent.judgment_evaluation import FAMILIES,assert_control,assert_solvable,candidate_for,controlled_input
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash
from graphrisk.agent.narrative_feedback import feedback_view


def inputs(evidence,scope,registry,profile,family):
    executor,_,bundle=evidence;facts=list(bundle.facts)
    facts=[f.model_copy(update={'value':50}) if f.metric=='returned_related_count' else f for f in facts]
    bundle=bundle.model_copy(update={'facts':tuple(facts)})
    original=packet_for(scope,executor.observations,registry,profile)
    original['source_evidence_sha256']=digest(bundle.model_dump(mode='json'))
    plan=assert_control(family,original,bundle)
    # Witness validity and request-size validity are separate gates.
    # Use the unchanged evidence view here; complete real payloads are audited separately.
    return original,plan,bundle


@pytest.mark.parametrize('family',list(FAMILIES))
def test_each_defect_has_one_guard_accepted_expected_aligned_candidate(evidence,scope,registry,profile,family):
    packet,plan,bundle=inputs(evidence,scope,registry,profile,family)
    before=deepcopy(packet)
    result=assert_solvable(family,plan,packet,bundle)
    assert result['guard_pass'] and result['candidate_sha256']==digest(candidate_for(family))
    assert result['plan_sha256']==digest(plan)
    assert packet==before and 'candidate' not in packet['view'] and 'solvability' not in packet['view']
    assert plan['source']==controlled_input(family)


@pytest.mark.parametrize('family',['C','E'])
def test_range_feedback_requests_removal_not_meta_restatement(evidence,scope,registry,profile,family):
    packet,plan,bundle=inputs(evidence,scope,registry,profile,family)
    issue=next(i for i in plan['issues'] if i['code']=='program_owned_scope_statement')
    assert '删掉范围整句' in issue['expected']
    assert ('不以任何说法提截断/覆盖/返回范围' if family=='C' else '勿提截断/覆盖/返回范围') in issue['expected']
    assert ('授权核查并引交易事实' if family=='C' else '政策前提待核实并引政策') in issue['expected']
    assert '程序提供' not in issue['expected']
    assert any(issue['expected']==r.get('expected') for r in feedback_view(plan)['rules'])
    assert assert_solvable(family,plan,packet,bundle)['guard_pass']


@pytest.mark.parametrize('change',['old_meta','still_truncated','four_refs','missing_ref','correct_sibling','source_binding'])
def test_selfcheck_rejects_unsolvable_or_unbounded_candidate(evidence,scope,registry,profile,change):
    packet,plan,bundle=inputs(evidence,scope,registry,profile,'C');candidate=candidate_for('C')
    if change=='old_meta':candidate['basis'][0]={'text':'图邻居截断状态由程序说明，需核查授权。','refs':['F20']}
    elif change=='still_truncated':candidate['basis'][0]={'text':'图邻居均被截断，仍需核查授权。','refs':['F20']}
    elif change=='four_refs':candidate['basis'][0]['refs']=['F1','F8','F16','F9']
    elif change=='missing_ref':candidate['basis'][0]['refs']=['F999']
    elif change=='correct_sibling':candidate['risk_level']='high'
    else:packet['source_evidence_sha256']='0'*64
    with pytest.raises(ValueError):assert_solvable('C',plan,packet,bundle,candidate=candidate)


def test_current_guards_are_not_relaxed_to_pass_a_candidate(evidence,scope,registry,profile):
    packet,plan,bundle=inputs(evidence,scope,registry,profile,'C')
    for text in ['图邻居均被截断，仍需核查。','图邻居是否被截断，以程序段说明为准。']:
        value=candidate_for('C');value['basis'][0]={'text':text,'refs':['F20']}
        errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(value)),packet,bundle)
        assert 'program_owned_scope_statement' in errors and report is None


def test_failed_selfcheck_blocks_runner_before_freezing_contract(tmp_path,monkeypatch,evidence,scope,registry,profile):
    import evaluate_judgment_repairs as runner
    executor,_,bundle=evidence
    parent=tmp_path/'reports/generated/agent-investigation-upgrade/route5/supplement-v3/batch1'
    parent.mkdir(parents=True)
    hashes={}
    for i in (1,2):
        p=parent/f'evidence-{i}-private.json';p.write_text('fixture',encoding='utf-8');hashes[p.name]=file_hash(p)
    def read(path):
        if path.name=='contract-private.json':return {'profile':profile,'evidence_files':hashes}
        if path.name.startswith('evidence-'):return {'scope':scope.model_dump(mode='json'),'observations':[],'evidence':bundle.model_dump(mode='json')}
        return {}
    class Budget:
        def snapshot(self):return {'unresolved_attempts':0,'integrity_stops':0}
    monkeypatch.setattr(runner,'read',read);monkeypatch.setattr(runner,'ledger',lambda _:Budget())
    monkeypatch.setattr(runner,'registry_for',lambda _:registry)
    monkeypatch.setattr(runner,'restore_observations',lambda *_:executor.observations)
    monkeypatch.setattr(runner,'build_evidence',lambda *_:bundle)
    monkeypatch.setattr(runner,'assert_control',lambda *_:{'test':'plan'})
    monkeypatch.setattr(runner,'packet_for',lambda *_:{'test':'packet'})
    def stop(*_):raise ValueError('solvability_candidate_rejected:fixture')
    monkeypatch.setattr(runner,'assert_solvable',stop)
    output=tmp_path/'reports/generated/agent-investigation-upgrade/route5/blocked'
    monkeypatch.setattr(sys,'argv',['evaluate_judgment_repairs.py','--runtime',str(tmp_path),'--output',str(output),'--pricing-verified-date',date.today().isoformat()])
    with pytest.raises(ValueError,match='solvability_candidate_rejected'):runner.main()
    assert not (output/'contract-private.json').exists() and not list(output.glob('*packet*'))
