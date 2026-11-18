from copy import deepcopy
import json
import httpx
import pytest
from test_narrative import evidence
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile,ledger,cloud_body
from test_judgment import good
from graphrisk.agent.judgment import (
    JudgmentDraft,JudgmentProvider,packet_for,repair_plan,patch_output_contract,
    verify_judgment,REPAIR,PATCH_EXAMPLE_TEXT,
)
from graphrisk.agent.narrative_repair import NarrativePatch,apply_patch_result
from graphrisk.agent.upgrade_contract import canonical,digest


def too_many_refs(evidence,scope,registry,profile):
    executor,_,bundle=evidence
    original=packet_for(scope,executor.observations,registry,profile)
    value=good();value['basis'][0]['refs']=['F1','F8','F16','F9']
    plan=repair_plan(value,original,bundle)
    return packet_for(scope,executor.observations,registry,profile,plan),plan,bundle


def test_actual_http_payload_contains_complete_precise_patch_contract(tmp_path,evidence,scope,registry,profile):
    packet,plan,bundle=too_many_refs(evidence,scope,registry,profile)
    packet['logical_key']='repair';campaign={'profile_sha256':digest(profile),'packets':[packet]}
    budget=ledger(tmp_path,campaign);posts=[]
    patch={'base_sha256':plan['base_sha256'],'changes':[{'op':'replace','path':'basis.0','value':good()['basis'][0]}]}
    def handler(request):
        sent=json.loads(request.content);posts.append(sent)
        assert sent['response_format']=={'type':'json_object'}
        system=sent['messages'][0]['content'];view=json.loads(sent['messages'][1]['content'])
        assert '不能用action代替op' in system and 'issues.field仅定位错误' in system
        contract=view['output_contract']
        assert contract['change_fields']==['op','path','value'] and contract['no_extra_fields']
        assert contract['specs']==[['basis.0','replace','cited_text']]
        example=NarrativePatch.model_validate_json(canonical(contract['example']),strict=True)
        assert example.base_sha256==view['repair']['base_sha256']
        assert [c.path for c in example.changes]==view['repair']['allowed_paths']==['basis.0']
        assert set(contract['example']['changes'][0]['value'])=={'text','refs'}
        assert 'scope_private' not in canonical(sent) and 'PRIVATE-TARGET' not in canonical(sent)
        return httpx.Response(200,json=cloud_body(patch))
    provider=JudgmentProvider('deepseek',profile=profile,budget=budget,campaign=campaign,campaign_sha=digest(campaign),phase='pilot',output=tmp_path/'receipts',runtime=tmp_path,transport=httpx.MockTransport(handler))
    result=provider.generate('repair')
    assert result['code']=='ok' and len(posts)==1
    merged=apply_patch_result(NarrativePatch.model_validate_json(canonical(result['action'])),plan)
    errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(merged)),packet,bundle)
    assert not errors and report['semantic_quality']=='requires_review'
    provider.generate('repair');assert len(posts)==1  # saved receipt, no second request


def test_prior_failed_subpath_and_action_key_are_still_rejected(evidence,scope,registry,profile):
    packet,plan,_=too_many_refs(evidence,scope,registry,profile)
    subpath={'base_sha256':plan['base_sha256'],'changes':[{'op':'replace','path':'basis.0.refs','value':['F1','F8','F16']}]}
    with pytest.raises(ValueError,match='repair_paths_changed'):
        apply_patch_result(NarrativePatch.model_validate_json(canonical(subpath)),plan)
    action={'base_sha256':plan['base_sha256'],'changes':[{'action':'replace','path':'basis.0','value':good()['basis'][0]}]}
    with pytest.raises(ValueError):NarrativePatch.model_validate_json(canonical(action),strict=True)


def test_copying_example_cannot_become_an_accepted_report(evidence,scope,registry,profile):
    packet,plan,bundle=too_many_refs(evidence,scope,registry,profile)
    patch=NarrativePatch.model_validate_json(canonical(packet['view']['output_contract']['example']))
    merged=apply_patch_result(patch,plan)
    errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(merged)),packet,bundle)
    assert 'repair_example_requires_real_judgment' in errors and report is None
    assert merged['basis'][0]['text']==PATCH_EXAMPLE_TEXT


@pytest.mark.parametrize('path,op,kind',[
    ('basis.0','replace','cited_text'),('counterevidence.1','replace','cited_text'),
    ('policy','replace','cited_text'),('basis','replace','cited_text[1..2]'),
    ('counterevidence','replace','cited_text[1..2]'),
    ('next_check','replace','check_choice'),('risk_level','replace','low|medium|high|undetermined'),
    ('fact_assessments','remove','null'),('limitation','remove','null'),('version','remove','null'),
])
def test_every_allowed_value_kind_has_a_complete_typed_example(path,op,kind):
    plan={'base_sha256':'a'*64,'allowed_paths':[path],'removable_paths':['fact_assessments','limitation','version']}
    output=patch_output_contract(plan)
    assert output['specs']==[[path,op,kind]]
    example=NarrativePatch.model_validate_json(canonical(output['example']),strict=True)
    change=example.changes[0];assert change.path==path and change.op==op
    if op=='remove':assert change.value is None
    if path=='next_check':assert change.value['text'] in output['check_choices']


def test_combined_patch_example_covers_all_paths_once_and_protects_siblings(evidence,scope,registry,profile):
    executor,_,bundle=evidence;packet=packet_for(scope,executor.observations,registry,profile)
    source=good();source['basis'][0]['refs']=['F1','F8','F16','F9']
    source['counterevidence'][0]['text']='图邻居均被截断，仍需核查。'
    plan=repair_plan(source,packet,bundle)
    output=patch_output_contract(plan)
    patch=NarrativePatch.model_validate_json(canonical(output['example']))
    assert len(patch.changes)==len(set(c.path for c in patch.changes))==len(plan['allowed_paths'])==2
    merged=apply_patch_result(patch,plan)
    assert merged['policy']==source['policy'] and merged['next_check']==source['next_check']
    forged=deepcopy(output['example']);forged['changes'].append({'op':'replace','path':'risk_level','value':'high'})
    with pytest.raises(ValueError,match='repair_paths_changed'):
        apply_patch_result(NarrativePatch.model_validate_json(canonical(forged)),plan)


def test_unsupported_patch_value_shape_stops_before_sending():
    with pytest.raises(ValueError,match='judgment_patch_value_shape_unavailable'):
        patch_output_contract({'base_sha256':'a'*64,'allowed_paths':['basis.0.refs']})


def test_larger_repair_contract_still_respects_original_message_budget(evidence,scope,registry,profile):
    executor,_,bundle=evidence;original=packet_for(scope,executor.observations,registry,profile)
    value=good();value['basis'][0]['refs']=['F1','F8','F16','F9']
    plan=repair_plan(value,original,bundle)
    tiny=deepcopy(profile);tiny['max_prompt_utf8_bytes']=original['prompt_utf8_bytes']
    # The upstream action packet can stop first; both guards must fail closed.
    with pytest.raises(ValueError,match='judgment_context_limit|minimized_context_exceeds_byte_guard|narrative_context_limit'):
        packet_for(scope,executor.observations,registry,tiny,plan)
