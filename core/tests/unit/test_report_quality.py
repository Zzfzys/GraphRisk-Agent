from copy import deepcopy
import json,sys
from pathlib import Path
import pytest
from test_narrative import evidence
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.judgment import packet_for
from graphrisk.agent.report_quality import (
    DIMENSIONS,QualityReview,prepare_quality_packet,blank_review,assess_quality,render_quality_mapping,
)
from graphrisk.agent.upgrade_contract import canonical,digest

FIXTURE=Path(__file__).parents[1]/'fixtures/report_quality_v1.json'
SAMPLES=json.loads(FIXTURE.read_text(encoding='utf-8'))


def sample(case,evidence,scope,registry,profile):
    executor,_,bundle=evidence;observations=executor.observations
    if case.get('variant')=='relations_not_requested':
        observations=tuple(o for o in observations if o.call.name!='get_related_entities')
        bundle=build_evidence(scope,observations,registry)
    packet=packet_for(scope,observations,registry,profile)
    value=deepcopy(SAMPLES['base_draft']);change=case['change']
    if 'basis_text' in change:value['basis'][0]['text']=change['basis_text']
    if 'basis_refs' in change:value['basis'][0]['refs']=change['basis_refs']
    if 'counter_text' in change:value['counterevidence'][0]['text']=change['counter_text']
    if change.get('zero_basis'):value['basis']=[]
    context={'kind':'authored_scenario','available_tools':['get_transaction','get_transaction_features','get_risk_score','get_related_entities','find_risky_neighbors','retrieve_policy'],
             'tool_calls_remaining':2,'unsupported_checks':['payment_authorization'],
             'reason':'仅为人工开发情景的权限和预算前提，不是实时服务快照；关联工具在本情景允许补查，实际授权工具不支持。'}
    target=prepare_quality_packet(value,packet,bundle,source_id=case['id'],source_kind='authored_development',scenario_context=context)
    review=blank_review(target)
    review['reviewer']={'kind':'assistant_development','reviewer_id':'authored-anchor-review','version':'report-quality-v1'}
    if case['id']=='unfilled-review':review['reviewer']['kind']='unassigned'
    review['facts']={'status':case['facts'],'reason':case.get('fact_reason','开发样例逐句核对未发现新增事实硬错；不是完整语义证明。'),
                     'anchors':['risk_level','next_check','F1']}
    for name,score,reason in zip(DIMENSIONS,case['scores'],case['reasons']):
        anchors=['basis.0'] if name=='specificity' and value['basis'] else ['risk_level']
        if name=='analysis':anchors=['counterevidence.0','F1']
        if name=='actionability':anchors=['next_check','process_context','catalog_names',*['tool_outcomes.'+str(i) for i in range(len(bundle.tool_outcomes))]]
        review['scores'][name]={'score':score,'reason':reason,'anchors':anchors}
    for role in review['roles']:
        role.update(role=case['role'],reason='开发预置分类：本句涉及授权或取证信息缺失，混合时另标mixed。')
    return target,review,{'draft':value,'packet':packet,'evidence':bundle.model_dump(mode='json'),
                          'source_id':case['id'],'source_kind':'authored_development','scenario_context':context}


@pytest.mark.parametrize('case',SAMPLES['cases'],ids=lambda c:c['id'])
def test_authored_anchor_outcomes_do_not_masquerade_as_model_results(evidence,scope,registry,profile,case):
    target,review,source=sample(case,evidence,scope,registry,profile)
    result=assess_quality(target,review)
    assert result['status']==case['expected']
    assert result['new_model_calls']==0 and not result['approval_eligible']
    assert result['personal_readability']=='Pending'
    assert result['evaluation_scope']=='offline_development_review_not_frozen_validation'
    if case['id']=='zero-basis-legacy-conflict':
        assert target['known_findings'] and result['reviewed_facts']['status']=='Pass'
        assert any('至少一条basis' in m for p in target['panels'] for m in p['missing'])
    if case['id']=='available-tool-deferred':
        assert not target['known_findings']
        assert any(o['tool']=='get_related_entities' and o['status']=='not_requested' for o in target['tool_outcomes'])
    if case['kind']=='semantic_review_negative':
        assert result['reviewed_facts']['status']=='Fail'
    if case['id']=='six-with-zero-dimension':assert result['total']==6 and result['status']=='Fail'
    if case['id']=='unfilled-review':assert result['total'] is None


def test_mapping_does_not_rewrite_or_auto_classify_old_mixed_prose(evidence,scope,registry,profile):
    target,review,source=sample(SAMPLES['cases'][0],evidence,scope,registry,profile)
    before=deepcopy(source)
    second=prepare_quality_packet(source['draft'],source['packet'],evidence[2],source_id='second',source_kind='authored_development')
    assert source==before and second['draft']==source['draft']
    assert second['process_context']['kind']=='not_supplied'
    assert second['passages'][1]['role']=='unreviewed'
    assert [p['name'] for p in second['panels']]==['当前判断','主要依据','反证与其他解释','关键缺口','下一步']
    blank=blank_review(second);result=assess_quality(second,blank)
    assert result['status']=='Pending' and result['total'] is None
    text=render_quality_mapping(second)
    assert '旧counterevidence原文' in text and '尚未执行' in text and source['draft']['counterevidence'][0]['text'] in text
    for secret in ('PRIVATE-TARGET','PRIVATE-DEVICE','scope_private','is_fraud','deepseek-flash'):
        assert secret not in canonical(second)


@pytest.mark.parametrize('mutation',['target','score_bool','score_float','score_string','score_range','extra_total','missing_dimension',
                                    'unknown_anchor','duplicate_anchor','missing_role','duplicate_role','other_role','packet_changed'])
def test_review_versions_scores_references_and_classification_are_checked(evidence,scope,registry,profile,mutation):
    target,review,_=sample(SAMPLES['cases'][0],evidence,scope,registry,profile)
    if mutation=='target':review['target_sha256']='0'*64
    elif mutation.startswith('score_'):
        review['scores']['specificity']['score']={'score_bool':True,'score_float':1.0,'score_string':'2','score_range':3}[mutation]
    elif mutation=='extra_total':review['total']=8
    elif mutation=='missing_dimension':review['scores'].pop('analysis')
    elif mutation=='unknown_anchor':review['facts']['anchors']=['F999']
    elif mutation=='duplicate_anchor':review['facts']['anchors']=['F1','F1']
    elif mutation=='missing_role':review['roles']=[]
    elif mutation=='duplicate_role':review['roles']*=2
    elif mutation=='other_role':review['roles'][0]['path']='basis.0'
    else:target['draft']['risk_level']='high'
    with pytest.raises(ValueError):assess_quality(target,review)


def test_source_binding_changed_stops_before_mapping(evidence,scope,registry,profile):
    _,_,source=sample(SAMPLES['cases'][0],evidence,scope,registry,profile)
    source['packet']['source_evidence_sha256']='0'*64
    with pytest.raises(ValueError,match='narrative_source_changed'):
        prepare_quality_packet(source['draft'],source['packet'],evidence[2],source_id='bad-binding',source_kind='authored_development')


def test_cli_read_only_prepare_assess_and_no_overwrite(tmp_path,monkeypatch,capsys,evidence,scope,registry,profile):
    import review_report_quality as cli
    from graphrisk.agent.autonomous_providers import ActionProvider
    def prohibited(*args,**kwargs):pytest.fail('offline quality path must not generate')
    monkeypatch.setattr(ActionProvider,'generate',prohibited)
    target,review,source=sample(SAMPLES['cases'][0],evidence,scope,registry,profile)
    src=tmp_path/'source.json';src.write_text(canonical(source),encoding='utf-8');out=tmp_path/'prepared'
    monkeypatch.setattr(sys,'argv',['review_report_quality.py','prepare','--input',str(src),'--output',str(out)])
    cli.main();assert json.loads(capsys.readouterr().out)['review_status']=='Pending'
    files={p.name:p.read_bytes() for p in out.iterdir()}
    with pytest.raises(SystemExit) as error:cli.main()
    assert error.value.code==2 and {p.name:p.read_bytes() for p in out.iterdir()}==files
    result=tmp_path/'assessment.json';filled=tmp_path/'filled.json';filled.write_text(canonical(review),encoding='utf-8')
    monkeypatch.setattr(sys,'argv',['review_report_quality.py','assess','--packet',str(out/'quality-packet-private.json'),'--review',str(filled),'--output',str(result)])
    cli.main();assert json.loads(result.read_text(encoding='utf-8'))['status']=='Pass'
    with pytest.raises(SystemExit) as error:cli.main()
    assert error.value.code==2


def test_export_offline_matrix(tmp_path,evidence,scope,registry,profile):
    # Exported evidence is synthetic and scored by authored development records;
    # no model or independent human/judge ran. No rate is a quality claim.
    root=tmp_path/'quality-samples';root.mkdir();rows=[]
    for case in SAMPLES['cases']:
        target,review,source=sample(case,evidence,scope,registry,profile);result=assess_quality(target,review)
        out=root/case['id'];out.mkdir()
        for name,value in [('quality-packet-private.json',target),('review-template.json',blank_review(target)),
                           ('authored-review.json',review),('assessment.json',result),('input-private.json',source)]:
            (out/name).write_text(canonical(value)+'\n',encoding='utf-8')
        (out/'reading.md').write_text(render_quality_mapping(target),encoding='utf-8')
        assert result['status']==case['expected']
        rows.append({'id':case['id'],'kind':case['kind'],'expected':case['expected'],'actual':result['status'],
                     'total':result['total'],'known_codes':result['known_guard_codes'],'target_sha256':target['target_sha256'],
                     'review_sha256':result['review_sha256']})
    (root/'audit.json').write_text(canonical({'source':'authored synthetic development anchors; not model output or independent semantic validation',
          'new_model_calls':0,'new_cost_cny':0,'rubric_independently_validated':False,'personal_readability':'Pending',
          'cases':rows,'records_consistent':len(rows)})+'\n',encoding='utf-8')


def test_canonical_file_roundtrip_keeps_target_binding_stable(evidence,scope,registry,profile):
    target,review,source=sample(SAMPLES['cases'][0],evidence,scope,registry,profile)
    saved=json.loads(canonical(source))
    from graphrisk.agent.autonomous_evidence import EvidenceBundle
    bundle=EvidenceBundle.model_validate_json(canonical(saved['evidence']),strict=True)
    restored=prepare_quality_packet(saved['draft'],saved['packet'],bundle,source_id=saved['source_id'],source_kind=saved['source_kind'],scenario_context=saved.get('scenario_context'))
    assert restored==target
    assert assess_quality(restored,json.loads(canonical(review)))['status']=='Pass'


@pytest.mark.parametrize('value',[[],None,42])
def test_invalid_packet_shape_is_rejected_with_stable_error(value):
    with pytest.raises(ValueError,match='quality_artifact_hash_changed'):blank_review(value)


@pytest.mark.parametrize('unfinished',['fact_review','dimension','reviewer_identity'])
def test_each_unfinished_review_condition_stays_pending(evidence,scope,registry,profile,unfinished):
    target,review,_=sample(SAMPLES['cases'][0],evidence,scope,registry,profile)
    if unfinished=='fact_review':review['facts']['status']='Pending'
    elif unfinished=='dimension':review['scores']['specificity']['score']=None
    else:review['reviewer']['kind']='unassigned'
    result=assess_quality(target,review)
    assert result['status']=='Pending' and not result['approval_eligible']
    if unfinished=='dimension':assert result['total'] is None


def test_authored_tool_budget_assumptions_cannot_be_attached_to_real_outputs(evidence,scope,registry,profile):
    target,review,source=sample(SAMPLES['cases'][4],evidence,scope,registry,profile)
    assert target['process_context']['kind']=='authored_scenario'
    assert target['process_context']['tool_calls_remaining']==2
    assert 'get_related_entities' in target['process_context']['available_tools']
    from graphrisk.agent.autonomous_evidence import EvidenceBundle
    bundle=EvidenceBundle.model_validate_json(canonical(source['evidence']),strict=True)
    with pytest.raises(ValueError,match='quality_authored_context_for_real_output'):
        prepare_quality_packet(source['draft'],source['packet'],bundle,source_id='real',source_kind='existing_model_output',scenario_context=source['scenario_context'])
    real=prepare_quality_packet(source['draft'],source['packet'],bundle,source_id='real',source_kind='existing_model_output')
    assert real['process_context']['kind']=='not_supplied'


def test_cli_mismatched_review_is_actionable_and_creates_no_result(tmp_path,monkeypatch,capsys,evidence,scope,registry,profile):
    import review_report_quality as cli
    target,review,_=sample(SAMPLES['cases'][0],evidence,scope,registry,profile)
    review['target_sha256']='0'*64
    packet_path=tmp_path/'packet.json';packet_path.write_text(canonical(target),encoding='utf-8')
    review_path=tmp_path/'review.json';review_path.write_text(canonical(review),encoding='utf-8')
    output=tmp_path/'result.json'
    monkeypatch.setattr(sys,'argv',['review_report_quality.py','assess','--packet',str(packet_path),'--review',str(review_path),'--output',str(output)])
    with pytest.raises(SystemExit) as error:cli.main()
    assert error.value.code==2 and not output.exists()
    assert capsys.readouterr().err.strip()=='quality_review_target_changed'
