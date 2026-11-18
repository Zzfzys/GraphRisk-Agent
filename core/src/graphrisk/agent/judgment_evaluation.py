"""Controlled repair defects; seeds are authored fixtures, not model first drafts."""
from copy import deepcopy
from graphrisk.agent.judgment import JudgmentDraft,verify_judgment,repair_plan
from graphrisk.agent.upgrade_contract import canonical,digest
from graphrisk.agent.narrative_repair import NarrativePatch,apply_patch_result

FAMILIES={'A':'refs_only','B':'display_number_only','C':'program_owned_scope_only','D':'same_sentence_refs_plus_number','E':'three_fields_combined'}


def controlled_seed():
    return {'risk_level':'undetermined','basis':[{'text':'需要核实支付授权，现有材料不足以确认欺诈。','refs':['F1','F8']}],
            'counterevidence':[{'text':'仍需排除正常使用的可能，不能仅凭匿名关联下结论。','refs':['F1']}],
            'policy':{'text':'业务前提仍待测量，政策命中不能代替条件核实。','refs':['P1']},
            'next_check':{'text':'authorization','refs':['F1']}}


def controlled_input(family):
    if family not in FAMILIES:raise ValueError('unknown_defect_family')
    value=controlled_seed()
    if family in ('A','D','E'):value['basis'][0]['refs']=['F1','F8','F16','F9']
    if family in ('B','D'):
        value['basis'][0]['text']='返回关联50笔，仍需核实交易授权。'
        if family=='B':value['basis'][0]['refs']=['F1','F9']
    if family=='C':value['basis'][0]={'text':'图邻居均被截断，仍需核查授权。','refs':['F20']}
    if family=='E':
        value['counterevidence'][0]={'text':'返回关联50笔，仍不能排除正常使用。','refs':['F9']}
        value['policy']={'text':'图邻居均被截断，因此应继续核对政策前提。','refs':['P1','F20']}
    return value


def assert_control(family,packet,bundle):
    errors,_=verify_judgment(JudgmentDraft.model_validate_json(canonical(controlled_seed())),packet,bundle)
    if errors:raise ValueError('healthy_control_not_valid')
    count=next(f for f in bundle.facts if f.metric=='returned_related_count')
    if count.value!=50:raise ValueError('controlled_number_requires_exact_source_value')
    graph=next(f for f in bundle.facts if f.metric=='graph_neighbors_truncated')
    if graph.availability=='available':raise ValueError('controlled_scope_requires_unknown_source')
    plan=repair_plan(controlled_input(family),packet,bundle)
    expected={
        'A':{'too_long'},
        'B':{'numeric_or_probability_prose_not_allowed'},
        'C':{'program_owned_scope_statement'},
        'D':{'too_long','numeric_or_probability_prose_not_allowed'},
        'E':{'too_long','numeric_or_probability_prose_not_allowed','program_owned_scope_statement'}}[family]
    codes={i['code'] for i in plan['issues']}
    if codes!=expected:raise ValueError('uncontrolled_additional_defect:'+str(sorted(codes)))
    return plan


def candidate_for(family):
    """Authored existence witness, kept private; never a model response target."""
    if family not in FAMILIES:raise ValueError('unknown_defect_family')
    candidate=controlled_seed()
    if family in ('B','C','D'):
        candidate['basis'][0]={'text':'需要人工核实目标交易是否获得授权，当前不能确认欺诈。','refs':['F1']}
    if family=='E':
        candidate['counterevidence'][0]={'text':'匿名关联仅是调查线索，仍需排除正常使用的可能。','refs':['F9']}
        candidate['policy']={'text':'应先核实政策要求的业务前提，现有材料不足以确认适用。','refs':['P1']}
    return candidate


def assert_solvable(family,plan,packet,bundle,*,candidate=None):
    """Fail before freeze if an authored expected-aligned witness is rejected.

    This proves existence of one guard-accepted repair for this fixed input,
    not that a model will find it or that free-text semantics are fully proved.
    Neither this candidate nor its patch is added to the model's packet.
    """
    if plan['source']!=controlled_input(family):raise ValueError('solvability_source_mismatch')
    value=deepcopy(candidate if candidate is not None else candidate_for(family))
    def at(path):
        item=value
        for part in path.split('.'):
            item=item[int(part)] if isinstance(item,list) else item[part]
        return item
    patch={'base_sha256':plan['base_sha256'],'changes':[
        {'op':'replace','path':path,'value':at(path)} for path in plan['allowed_paths']]}
    merged=apply_patch_result(NarrativePatch.model_validate_json(canonical(patch),strict=True),plan)
    if merged!=value:raise ValueError('solvability_candidate_changes_correct_fields')
    draft=JudgmentDraft.model_validate_json(canonical(merged),strict=True)
    errors,report=verify_judgment(draft,packet,bundle)
    if errors or report is None:raise ValueError('solvability_candidate_rejected:'+','.join(errors))
    return {'version':'authored-solvability-witness-v1','guard_pass':True,
            'candidate_sha256':digest(value),'patch_sha256':digest(patch),
            'plan_sha256':digest(plan),'source_evidence_sha256':packet['source_evidence_sha256'],
            'scope':'one authored candidate passes known guards; not model output or complete semantic proof'}
