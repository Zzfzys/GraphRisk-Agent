from copy import deepcopy
import pytest
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from test_judgment_v3 import evidence,v3,good
from graphrisk.agent.judgment_v3 import JudgmentDraft,repair_plan,packet_for
from graphrisk.agent.report_quality import prepare_quality_packet,render_quality_mapping
from graphrisk.agent.upgrade_contract import canonical


@pytest.mark.parametrize('choice',[['history_stats','card_attempts'],{'bad':'value'},None])
def test_malformed_next_check_is_preserved_flagged_and_never_coerced_or_crashes(v3,choice):
    packet,bundle=v3
    draft=good()
    draft['next_check']['text']=choice
    mapped=prepare_quality_packet(draft,packet,bundle,source_id='bad-choice',source_kind='existing_model_output')
    assert mapped['draft']['next_check']['text']==choice
    assert any(i['field']=='next_check.text' for i in mapped['known_findings'])
    assert mapped['next_check_display']['choice'] is None
    assert '已知拒绝' in render_quality_mapping(mapped)
    plan=repair_plan(draft,packet,bundle)
    assert 'next_check' in plan['allowed_paths']


def test_generation_example_is_real_schema_shape_with_one_string_choice(v3):
    packet,bundle=v3
    example=packet['view']['output_contract']['example']
    JudgmentDraft.model_validate_json(canonical(example),strict=True)
    assert isinstance(example['rationale'],dict)
    assert isinstance(example['next_check']['text'],str)
    assert example['next_check']['text'] in packet['view']['output_contract']['choices']['next_check.text']


def test_combined_repair_preserves_diagnostics_and_enforces_context_without_trimming(evidence,scope,registry,profile):
    executor,_,bundle=evidence
    first=packet_for(scope,executor.observations,registry,profile)
    bad=good()
    bad['rationale']='本案仍需要核实，不能确认交易性质。'
    bad['next_check']['text']=['history_stats','card_attempts']
    bad['counterevidence']=[{'text':'历史交易计数均为0，说明关系安全。','refs':['F2']}]
    plan=repair_plan(bad,first,bundle)
    # An offline diagnostic copy can inspect oversized requests. Production
    # must stop rather than drop evidence, rules, or malformed actual values.
    inspection={**profile,'max_prompt_utf8_bytes':20000}
    repaired=packet_for(scope,executor.observations,registry,inspection,plan)
    if repaired['prompt_utf8_bytes']>profile['max_prompt_utf8_bytes']:
        with pytest.raises(ValueError,match='judgment_v3_context_limit'):
            packet_for(scope,executor.observations,registry,profile,plan)
    else:
        assert packet_for(scope,executor.observations,registry,profile,plan)==repaired
    assert repaired['view']['repair']['previous_fields']['next_check']['text']==['history_stats','card_attempts']
    assert {r['code'] for r in repaired['view']['repair']['rules']} >= {'model_type','literal_error','numeric_or_probability_prose_not_allowed'}


def test_plain_input_exposes_each_bound_fact_subject_window_and_missing_state(evidence,scope,registry,profile):
    from graphrisk.agent.fact_presentation import program_presentation
    executor,_,bundle=evidence
    cfg={**profile,'report_input_presentation':'plain-facts-v1','report_generation_profile':'quality-v2-plain16k','max_prompt_utf8_bytes':16000}
    packet=packet_for(scope,executor.observations,registry,cfg)
    assert packet['input_presentation_version']=='plain-facts-v1' and packet['prompt_utf8_bytes']<=16000
    texts={f['ref']:f['text'] for f in program_presentation(bundle,packet)['facts']}
    originals={f.fact_id:f for f in bundle.facts}
    assert len(packet['view']['facts'])==len(bundle.facts)
    for ref,text,subject,window,temporal,state,tool in packet['view']['facts']:
        fact=originals[packet['refs_private'][ref]]
        assert text==texts[ref]
        assert [subject,window,temporal,state,tool]==[fact.subject,fact.window_seconds,fact.temporal_scope,fact.availability,fact.source_tool]
    assert not {'meanings','subjects','sources'} & set(packet['view'])
    assert packet['source_evidence_sha256']


def test_exact_unmeasured_policy_quote_uses_typed_provenance_without_hiding_other_claims(scope,index,registry,profile):
    from graphrisk.agent.judgment_policy_quotes import policy_quotes
    from graphrisk.agent.judgment_v3 import issues_for,verify_judgment
    from test_autonomous_tool_contracts import executor_for,run_core,policy_result
    from graphrisk.agent.autonomous_evidence import build_evidence
    def replace_policy(name,result):
        if name=='retrieve_policy':return policy_result(index,[c for c in index.chunks if c.policy_id=='transaction-velocity' and c.section=='触发条件'])
        return result
    executor,_=executor_for(scope,index,mutate=replace_policy);run_core(executor)
    bundle=build_evidence(scope,executor.observations,registry)
    packet=packet_for(scope,executor.observations,registry,{**profile,'max_prompt_utf8_bytes':16000})
    quotes=policy_quotes(packet,bundle);assert quotes
    ref,text=next(iter(quotes.items()));draft=good();draft['policy']={'text':text,'refs':[ref]}
    assert not issues_for(draft,packet,bundle)
    errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(draft)),packet,bundle)
    assert not errors and report['draft']==draft
    assert any(n['code']=='verified_unmeasured_policy_quote' for n in report['display_notes'])
    for altered in (text.replace('4','5'),text.replace('至少','超过'),text.replace('同一卡片','该用户'),
                    text.replace('前10分钟','后10分钟'),text.replace('尚未测量','已经达到'),
                    text+'因此已触发规则，应冻结交易。'):
        bad=deepcopy(draft);bad['policy']['text']=altered
        assert issues_for(bad,packet,bundle)
    bad=deepcopy(draft);bad['policy']['refs']=['F1'];assert issues_for(bad,packet,bundle)
    bad=good();bad['basis']=[{'text':text,'refs':[ref]}];assert issues_for(bad,packet,bundle)
    for field,value in [('operator','eq'),('subject','another_card'),('window_seconds',601),('expected',5),('measurement','measured')]:
        selected=next(p for p in bundle.policies if p.policy_id=='transaction-velocity')
        condition=selected.conditions[0].model_copy(update={field:value})
        changed=selected.model_copy(update={'conditions':(condition,)})
        modified=bundle.model_copy(update={'policies':tuple(changed if p==selected else p for p in bundle.policies)})
        rebound={**packet,'source_evidence_sha256':__import__('graphrisk.agent.upgrade_contract',fromlist=['digest']).digest(modified.model_dump(mode='json'))}
        assert not policy_quotes(rebound,modified)
    assert not policy_quotes({**packet,'source_evidence_sha256':'0'*64},bundle)


@pytest.mark.parametrize('text',["主评分与图分数均为未校准实验信号，只作调查线索。","主评分是实验图信号，不能证明欺诈。"])
def test_actual_wrong_primary_graph_role_is_rejected_with_actionable_feedback(v3,text):
    from graphrisk.agent.judgment_v3 import issues_for,repair_plan
    packet,bundle=v3;value=good();value['basis']=[{'text':text,'refs':['F8','F16']}]
    assert any(i['code']=='primary_score_cannot_be_experimental_graph_role' for i in issues_for(value,packet,bundle))
    plan=repair_plan(value,packet,bundle);assert plan['allowed_paths']==['basis.0']
    value['basis'][0]['text']='主评分和实验图分数都未校准，只能用作待核线索，不能直接当作欺诈概率。'
    assert not issues_for(value,packet,bundle)
