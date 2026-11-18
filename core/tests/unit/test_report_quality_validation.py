from test_report_quality_program import authorized
from test_report_pilot_execution import pilot
from copy import deepcopy
import json,zipfile
from pathlib import Path
import httpx
import pytest
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile,cloud_body
from test_judgment_v3 import evidence,v3,good
from graphrisk.agent.report_generation_profile import quality_report_profile
from graphrisk.agent.report_quality_scenarios import SCENARIOS,build_scenario
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.judgment_v3 import packet_for,JudgmentProvider
from graphrisk.agent.report_quality import prepare_quality_packet,blank_review
from graphrisk.agent import report_quality_judge as judge
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash


def test_direct_report_settings_are_isolated_and_not_quota_or_pricing_changes(profile):
    original=deepcopy(profile);cfg=quality_report_profile(profile)
    assert profile==original and cfg['deepseek']==profile['deepseek'] and cfg['local']==profile['local']
    assert cfg['max_output_tokens']==4096 and cfg['max_prompt_utf8_bytes']==20000 and cfg['report_thinking'] is False
    assert 'reasoning_effort' not in cfg
    assert cfg['live_enabled_by_default'] is False


@pytest.mark.parametrize('spec',SCENARIOS,ids=[c['id'] for c in SCENARIOS])
def test_public_boundary_evidence_is_reproducible_typed_and_not_real(scope,index,registry,profile,spec):
    cfg=quality_report_profile(profile)
    s,observations=build_scenario(spec,index,registry);bundle=build_evidence(s,observations,registry)
    assert s.dataset_source=='synthetic' and s.transaction_id.startswith('SYN-')
    assert bundle==build_evidence(*build_scenario(spec,index,registry),registry)
    packet=packet_for(s,observations,registry,cfg)
    formatter=object.__new__(JudgmentProvider);formatter.provider,formatter.profile='deepseek',cfg
    payload=formatter.payload(packet);text=canonical(payload)
    assert payload['thinking']=={'type':'disabled'} and payload['max_tokens']==4096
    assert s.transaction_id not in text and 'is_fraud' not in text and 'scope_private' not in text
    assert packet['prompt_utf8_bytes']<16000
    mean=next(f for f in bundle.facts if f.metric=='history_amount_mean')
    assert mean.value==spec['mean']
    if spec['graph_unavailable']:
        assert next(f for f in bundle.facts if f.metric=='experimental_graph_score').availability=='unavailable'
    if spec['name']=='unmeasured_policy_exception':
        assert any(p.purpose=='exception_condition' and p.applicability=='unverified_conditions' for p in bundle.policies)


def test_paired_judge_has_two_bound_blinded_targets_and_cannot_override_known_guard(v3,profile):
    packet,bundle=v3
    q=prepare_quality_packet(good(),packet,bundle,source_id='paired-check',source_kind='existing_model_output')
    cfg=quality_report_profile(profile);cfg['max_prompt_utf8_bytes']=32000
    paired=judge.paired_packet_for([q,q],[packet,packet],cfg)
    assert paired['quality_targets_sha256']==[q['target_sha256']]*2
    assert [x['case'] for x in paired['view']['reports']]==['C1','C2']
    assert packet['scope_private']['transaction_id'] not in canonical(paired['view'])
    assert not any(x in canonical(paired['view']) for x in ('system_fingerprint','model_identity','api_key'))
    assert paired['prompt_utf8_bytes']<32000
    formatter=object.__new__(judge.JudgeProvider);formatter.provider,formatter.profile='deepseek',cfg
    review=blank_review(q);review['facts']={'status':'Pass','reason':'Fixture-only support.','anchors':['risk_level']}
    review['scores']={n:{'score':2,'reason':'Fixture-only score.','anchors':['risk_level']} for n in review['scores']}
    for r in review['roles']:r.update(role='alternative')
    verdict={k:review[k] for k in ('facts','scores','roles')};verdict['risk_level_review']={'status':'Pass','reason':'Fixture only.','anchors':['risk_level']}
    value={'verdicts':[{'case':c,'verdict':verdict} for c in ('C1','C2')]}
    assert formatter.check_output(canonical(value),paired).verdicts[1].case=='C2'
    value['verdicts'][1]['case']='C1'
    with pytest.raises(ValueError,match='judge_verdict_schema_invalid'):formatter.check_output(canonical(value),paired)
    bad=deepcopy(q);bad['known_findings']=[{'code':'fixture_known_failure'}];bad.pop('target_sha256');bad['target_sha256']=digest(bad)
    assert judge.assess(bad,verdict,identity={'model':'fixture','fingerprint':'fixture'})['assessment']['combined_status']=='Fail'


def test_source_archive_round_trip_excludes_secrets_and_is_deterministic(tmp_path):
    from freeze_report_pilot import source_archive
    root=tmp_path/'source';(root/'src').mkdir(parents=True);(root/'configs').mkdir()
    (root/'src/test.py').write_text('x=1\n');(root/'configs/approved.json').write_text('{}')
    (root/'.env').write_text('PRIVATE-SECRET');(root/'src/hidden.env').write_text('PRIVATE-SECRET')
    snap={'python_files':{'src/test.py':file_hash(root/'src/test.py')},'configs':{'approved.json':file_hash(root/'configs/approved.json')}}
    for name in ('a.zip','b.zip'):source_archive(root,tmp_path/name,snap)
    assert file_hash(tmp_path/'a.zip')==file_hash(tmp_path/'b.zip')
    with zipfile.ZipFile(tmp_path/'a.zip') as z:
        assert set(z.namelist())=={'src/test.py','configs/approved.json','source-inventory.json'}
        assert z.read('src/test.py')==(root/'src/test.py').read_bytes()


def test_runtime_uses_exact_report_payload_and_keeps_action_profile(tmp_path,scope,index,registry,profile,evidence):
    from test_autonomous_loop import make_engine,LIMITS
    from graphrisk.serving.conversation_backend import BoundedConversationBackend
    from graphrisk.agent.narrative import run_narrative
    engine,budget,_,_=make_engine(tmp_path,scope,index,registry,profile)
    executor,_,bundle=evidence
    engine.store.campaign('quality-v3-runtime','e'*64,cap_each=10,group_key='route5')
    backend=BoundedConversationBackend(entries=[dict(scope=scope,registry=registry,observations=executor.observations,guard=lambda:None)],store=engine.store,
        campaign='quality-v3-runtime',provider='deepseek',profile=engine.profile,limits=LIMITS,directory=tmp_path/'runs',provider_factory=None,tool_batch=None)
    original=deepcopy(backend.profile);backend.report_profile=quality_report_profile(backend.profile);backend.report_contract='program-facts-judgment-v3'
    expected=packet_for(scope,executor.observations,registry,backend.report_profile)
    formatter=object.__new__(JudgmentProvider);formatter.provider,formatter.profile='deepseek',backend.report_profile
    payload=formatter.payload(expected);posts=[]
    def handle(request):
        posts.append(json.loads(request.content));return httpx.Response(200,json=cloud_body(good()))
    backend.narrative_factory=lambda campaign,phase,deadline:JudgmentProvider('deepseek',profile=backend.report_profile,budget=budget,campaign=campaign,campaign_sha=digest(campaign),phase=phase,
        output=tmp_path/'receipts',runtime=tmp_path,transport=httpx.MockTransport(handle))
    result=run_narrative(backend,scope.case_alias,scope.binding_sha256,'quality-v3-runtime-test',bundle.evidence_version)
    assert result['report'] is not None and result['model_calls']==1 and posts==[payload]
    assert backend.profile==original and backend.profile['max_output_tokens']==768


def test_blinded_pair_uses_same_ledger_and_saved_verdict_never_resends(authorized,monkeypatch):
    from test_report_quality_program import new_batch
    from test_report_pilot_execution import transport,review
    from run_report_pilot import run_case
    import run_report_quality_judge as runner
    parent,cfg,path=authorized;budget,store,old,output=parent
    candidate=new_batch(budget,store,old,output,cfg,'candidate-for-judge')
    posts=[];mock=transport([good(),good()],posts)
    for case in ('C1','C2'):
        run_case(output,candidate[3],case,budget=budget,transport=mock);review(candidate,case)
    monkeypatch.setattr(runner,'ledger',lambda runtime:budget);monkeypatch.setattr(runner,'connection_preflight',lambda:None)
    target=output.parent/'blinded-pair'
    frozen=runner.freeze(output,output,candidate[3],target,paired=True)
    assert frozen['max_judge_calls']==1
    row=json.loads((candidate[3]/'C1-slot0-quality-private.json').read_text())
    r=blank_review(row);v={k:r[k] for k in ('facts','scores','roles')}
    v['facts']={'status':'Pass','reason':'Fixture-only evaluation.','anchors':['risk_level']}
    v['scores']={n:{'score':2,'reason':'Fixture-only score.','anchors':['risk_level']} for n in v['scores']}
    for item in v['roles']:item['role']='alternative'
    v['risk_level_review']={'status':'Pass','reason':'Fixture only.','anchors':['risk_level']}
    answer={'verdicts':[{'case':c,'verdict':v} for c in ('C1','C2')]}
    transport_mock=transport([answer],posts:=[])
    real=runner.judge.JudgeProvider
    class FixtureProvider(real):
        def __init__(self,*a,**kw):super().__init__(*a,**kw,transport=transport_mock)
    monkeypatch.setattr(runner.judge,'JudgeProvider',FixtureProvider)
    assert runner.run(output,target,'C1')['status']=='Pass'
    assert runner.run(output,target,'C1')['status']=='saved_judgment_no_new_request'
    assert len(posts)==1



def test_plain_generation_transmits_actual_field_schema_and_bounds(evidence,scope,registry,profile):
    executor,_,_=evidence
    packet=packet_for(scope,executor.observations,registry,quality_report_profile(profile))
    schema=packet['view']['response_schema']
    assert schema['properties']['rationale']['$ref']=='#/$defs/RationaleText'
    assert schema['$defs']['RationaleText']['properties']['refs']['maxItems']==6
    assert schema['$defs']['RationaleText']['properties']['refs']['maxItems']==6
    assert schema['properties']['gaps']['type']=='array'
    assert packet['prompt_utf8_bytes']<16000


@pytest.mark.parametrize('text,blocked',[
    ('主评分未校准，不能当作欺诈概率。',False),
    ('主评分未校准，不能当欺诈概率。',False),
    ('图分数不可视为已验证的欺诈概率。',False),
    ('图分数不能视为已验证欺诈概率。',False),
    ('主评分不应解释为真实的欺诈概率。',False),
    ('主评分不是欺诈概率。',False),
    ('主评分显示欺诈概率很高。',True),
    ('主评分不能当作欺诈概率，但欺诈概率很高。',True),
    ('主评分不能当作欺诈概率，但实际有90%的概率。',True),
])
def test_negated_probability_is_not_an_affirmative_claim(v3,text,blocked):
    from graphrisk.agent.judgment_v3 import issues_for
    packet,bundle=v3;draft=good();draft['rationale']={'text':text,'refs':['F8']}
    issues=issues_for(draft,packet,bundle)
    assert any(i['code']=='numeric_or_probability_prose_not_allowed' and i['path'].startswith('rationale') for i in issues) is blocked


def test_repair_schema_binds_each_path_to_its_own_value_and_identifies_bad_refs(v3):
    from graphrisk.agent.judgment_v3 import repair_plan,repair_response_schema
    packet,bundle=v3;draft=good();draft['next_check']['refs']=['F1','G1']
    plan=repair_plan(draft,packet,bundle)
    assert plan['allowed_paths']==['next_check']
    assert any(i.get('disallowed_refs')==['G1'] for i in plan['issues'])
    schema=repair_response_schema(plan)
    assert schema['properties']['base_sha256']['const']==plan['base_sha256']
    choices=schema['properties']['changes']['items']['oneOf']
    assert choices[0]['properties']['path']['const']=='next_check'
    assert choices[0]['properties']['value']['$ref']=='#/$defs/PurposefulCheck'
    assert '格式占位' not in canonical(schema)


def test_combined_repair_is_solvable_without_empty_required_gap(v3):
    from graphrisk.agent.judgment_v3 import gap_sources,repair_plan,issues_for,apply_patch,NarrativePatch
    packet,bundle=v3;draft=good();sources=gap_sources(bundle,packet)
    g=next(g for g,v in sources.items() if v.get('anchor')=='F20')
    draft['gaps']=[{'source':g,'impact':'图查询是否截断尚未取得，完整性未知。'}]
    draft['next_check']['refs']=['F1','G1']
    plan=repair_plan(draft,packet,bundle)
    assert plan['allowed_paths']==['gaps','next_check']
    assert any(i['path']=='gaps' and '[]' in i['expected'] for i in plan['issues'])
    check={**draft['next_check'],'refs':['F1']}
    patch=NarrativePatch.model_validate_json(canonical({'base_sha256':plan['base_sha256'],'changes':[
        {'op':'replace','path':'gaps','value':[]},{'op':'replace','path':'next_check','value':check}]}),strict=True)
    merged=apply_patch(patch,plan)
    assert not issues_for(merged,packet,bundle)
    assert merged['rationale']==draft['rationale'] and merged['basis']==draft['basis']


def test_program_scope_is_not_sent_but_full_bound_facts_and_revalidation_remain(evidence,scope,registry,profile):
    from graphrisk.agent.judgment_v3 import program_owned_scope_refs,verify_judgment,verification_packet,JudgmentDraft,issues_for
    from graphrisk.agent.fact_presentation import program_presentation
    executor,_,bundle=evidence;packet=packet_for(scope,executor.observations,registry,quality_report_profile(profile))
    owned=program_owned_scope_refs(bundle,packet)
    assert {'F9','F10','F11','F17','F18','F19','F20'}<=owned
    assert not owned & {r[0] for r in packet['view']['facts']}
    assert len(program_presentation(bundle,packet)['facts'])==len(bundle.facts)
    assert packet['source_evidence_sha256']==digest(bundle.model_dump(mode='json'))
    assert not owned & set(packet['view']['response_schema']['$defs']['RationaleText']['properties']['refs']['items']['enum'])
    draft=good();errors,report=verify_judgment(JudgmentDraft.model_validate_json(canonical(draft),strict=True),packet,bundle)
    assert not errors and report['generation_input_presentation']=='plain-facts-v2-program-scope'
    rebuilt=verification_packet(report,bundle)
    assert not issues_for(draft,rebuilt,bundle)
    hidden=next(g for g,v in report['gap_sources'].items() if v['anchor']=='F20')
    draft['gaps']=[{'source':hidden,'impact':'查询范围还需要核实。'}]
    assert any(i['code']=='program_owned_scope_reference' for i in issues_for(draft,packet,bundle))


@pytest.mark.parametrize('text',['目标金额较小，不能判断是否异常。','该笔交易金额偏大，需要人工核查。','属于大额交易，需要调查。'])
def test_absolute_amount_size_needs_a_defined_threshold(v3,text):
    from graphrisk.agent.judgment_v3 import issues_for
    packet,bundle=v3;draft=good();draft['rationale']={'text':text,'refs':['F1']}
    assert any(i['code']=='amount_size_without_validated_threshold' for i in issues_for(draft,packet,bundle))


def test_judge_gap_alias_is_canonicalized_without_rewriting_raw_verdict_or_allowing_unknowns(v3):
    packet,bundle=v3;q=prepare_quality_packet(good(),packet,bundle,source_id='gap-review',source_kind='existing_model_output')
    review=blank_review(q);verdict={k:review[k] for k in ('facts','scores','roles')}
    verdict['facts']={'status':'Pass','reason':'Fixture-only capability anchor.','anchors':['G1']}
    verdict['risk_level_review']={'status':'Pass','reason':'Fixture-only capability limit.','anchors':['G1']}
    verdict['scores']={n:{'score':2,'reason':'Fixture-only score.','anchors':['risk_level']} for n in verdict['scores']}
    for row in verdict['roles']:row['role']='alternative'
    original=deepcopy(verdict)
    assessment=judge.assess(q,verdict,identity={'model':'fixture','fingerprint':'fixture'})
    assert assessment['assessment']['combined_status']=='Pass' and verdict==original
    assert assessment['record']['facts']['anchors']==['gap_sources.G1']
    assert assessment['assessment']['anchor_alias_normalization']=={'G1':'gap_sources.G1'}
    verdict['facts']['anchors']=['G999']
    with pytest.raises(ValueError,match='quality_review_anchor_unknown'):judge.assess(q,verdict,identity={'model':'fixture','fingerprint':'fixture'})


def test_frozen_public_validation_is_one_shot_and_cannot_reserve_repair(authorized,index,registry):
    from datetime import date
    from graphrisk.agent import report_quality_program as program,report_pilot_execution as channel
    from graphrisk.agent.autonomous_loop import observation_rows
    from graphrisk.agent.autonomous_budget import ExecutionStop
    from test_report_pilot_execution import write,transport,request
    from run_report_pilot import run_case
    parent,grant,path=authorized;budget,store,old,output=parent
    target=output.parent/'one-shot-validation';target.mkdir();cfg=deepcopy(old)
    files={str(Path(old['registry_index'])):file_hash(Path(old['registry_index']))};cases={}
    for case,spec in zip(('C1','C2'),SCENARIOS[:2]):
        s,observations=build_scenario(spec,index,registry);bundle=build_evidence(s,observations,registry)
        packet=packet_for(s,observations,registry,cfg['profile'])
        raw=target/(case+'-raw.json');write(raw,{'scope':s.model_dump(mode='json'),'observations':observation_rows(observations),'evidence':bundle.model_dump(mode='json')})
        f=object.__new__(JudgmentProvider);f.provider,f.profile='deepseek',cfg['profile'];files[str(raw)]=file_hash(raw)
        cases[case]={'raw_evidence_path':str(raw),'first_payload_sha':digest(f.payload(packet)),'evidence_sha':digest(bundle.model_dump(mode='json')),
                     'session':target.name+'-'+case,'session_manifest':{'public_scenario':spec['id']}}
    cfg.update(version=program.BATCH,campaign=target.name,program_id=grant['program_id'],program_sha256=grant['contract_sha256'],
               data_kind='synthetic_validation',kind='report',cases=cases,files=files)
    cfg['contract_sha256']=digest({k:v for k,v in cfg.items() if k!='contract_sha256'})
    write(target/'contract-private.json',cfg);store.campaign(cfg['campaign'],cfg['contract_sha256'],cap_each=4,group_key='route5_supplement_v3')
    program.register_batch(budget,target/'contract-private.json')
    bad=good();bad['rationale']['refs']=['F999'];posts=[]
    assert run_case(output,target,'C1',budget=budget,transport=transport([bad],posts))['attempts']==1
    assert len(posts)==1 and not (target/'C1-slot1-result-private.json').exists()
    phase,packet,payload,key=request((budget,store,cfg,target),slot=1)
    with pytest.raises(ExecutionStop,match='quality_validation_one_shot_only'):
        budget.reserve(provider='deepseek',request_key=key,packet_sha=digest(payload),phase=phase,reserve_micro=2103296)
    assert len(posts)==1


@pytest.mark.parametrize('spec',__import__('graphrisk.agent.report_quality_scenarios',fromlist=['SCENARIOS_V2']).SCENARIOS_V2,ids=lambda c:c['id'])
def test_second_cohort_is_new_typed_label_free_and_frozen_as_a_set(index,registry,profile,spec):
    from graphrisk.agent.report_quality_scenarios import SCENARIOS_V2
    assert len(SCENARIOS_V2)==6 and not {c['id'] for c in SCENARIOS_V2}&{c['id'] for c in SCENARIOS}
    s,o=build_scenario(spec,index,registry);e=build_evidence(s,o,registry)
    p=packet_for(s,o,registry,quality_report_profile(profile))
    assert s.transaction_id not in canonical(p['view']) and 'is_fraud' not in canonical(p['view'])
    zero_refs={ref for ref,fid in p['refs_private'].items() for f in e.facts if f.fact_id==fid and f.metric=='returned_relation_type_count' and f.value==0}
    assert not zero_refs&{r[0] for r in p['view']['facts']}
    assert len(e.facts)==20 and len(p['view']['facts'])<20


def test_partial_graph_failure_does_not_erase_measured_risk_clues(index,registry,profile):
    from graphrisk.agent.judgment_v3 import issues_for
    spec=SCENARIOS[3];s,o=build_scenario(spec,index,registry);e=build_evidence(s,o,registry)
    p=packet_for(s,o,registry,quality_report_profile(profile));draft=good();draft['risk_level']='medium'
    assert 'incomplete_evidence_requires_undetermined' not in {i['code'] for i in issues_for(draft,p,e)}
    # Still no automatic approval or invented probability from medium.
    draft['rationale']={'text':'欺诈概率很高，应继续核查。','refs':['F8']}
    assert 'numeric_or_probability_prose_not_allowed' in {i['code'] for i in issues_for(draft,p,e)}


def test_missing_target_still_requires_undetermined(scope,index,registry,profile):
    from test_autonomous_tool_contracts import executor_for
    from graphrisk.agent.judgment_v3 import issues_for
    def unavailable(name,result):
        return {'tool':name,'status':'unavailable','transaction_id':scope.transaction_id,'dataset_source':scope.dataset_source,
                'error':{'code':'capability_unavailable','message':'This investigation capability is not available yet.','retryable':False}}
    executor,_=executor_for(scope,index,mutate=unavailable);executor.execute('get_transaction',{})
    bundle=build_evidence(scope,executor.observations,registry);p=packet_for(scope,executor.observations,registry,quality_report_profile(profile))
    draft=good();draft['risk_level']='medium'
    assert 'target_unavailable_requires_undetermined' in {i['code'] for i in issues_for(draft,p,bundle)}


@pytest.mark.parametrize('negative',['并非概率','而非概率','不是已验证欺诈概率','不能相互抵消或平均成概率'])
def test_negative_probability_predicate_does_not_mask_positive_claims(v3,negative):
    from graphrisk.agent.judgment_v3 import issues_for
    p,e=v3;draft=good();draft['basis']=[{'text':'主评分是待核线索，'+negative+'。','refs':['F8']}]
    assert not issues_for(draft,p,e)
    draft['basis'][0]['text']+='但其欺诈概率很高。'
    assert 'numeric_or_probability_prose_not_allowed' in {i['code'] for i in issues_for(draft,p,e)}


def test_three_report_judge_binds_each_target_and_rejects_duplicate_case(v3,profile):
    p,e=v3;q=prepare_quality_packet(good(),p,e,source_id='triad-fixture',source_kind='existing_model_output')
    cfg=quality_report_profile(profile);cfg['max_prompt_utf8_bytes']=40000
    packet=judge.triad_packet_for([q]*3,[p]*3,cfg)
    assert packet['quality_targets_sha256']==[q['target_sha256']]*3
    assert [x['case'] for x in packet['view']['reports']]==['C1','C2','C3']
    template=blank_review(q);template['facts']={'status':'Pass','reason':'Fixture only.','anchors':['risk_level']}
    template['scores']={k:{'score':1,'reason':'Fixture only.','anchors':['risk_level']} for k in template['scores']}
    for r in template['roles']:r.update(role='alternative')
    verdict={k:template[k] for k in ('facts','scores','roles')};verdict['risk_level_review']=template['facts']
    value={'verdicts':[{'case':case,'verdict':verdict} for case in ('C1','C2','C3')]}
    formatter=object.__new__(judge.JudgeProvider);formatter.provider,formatter.profile='deepseek',cfg
    assert len(formatter.check_output(canonical(value),packet).verdicts)==3
    value['verdicts'][2]['case']='C1'
    with pytest.raises(ValueError):formatter.check_output(canonical(value),packet)
    with pytest.raises(ValueError):judge.triad_packet_for([q]*2,[p]*2,cfg)


def test_judge_only_identical_role_compatibility_never_allows_conflicts_or_actions():
    from graphrisk.agent.autonomous_http import strict_json
    value='{"path":"hypotheses.0","role":"alternative","role":"alternative"}'
    parsed,notes=judge.parse_judge_json(value)
    assert parsed=={'path':'hypotheses.0','role':'alternative'} and notes[0]['code']=='identical_duplicate_role_normalized'
    with pytest.raises(ValueError):strict_json(value)  # generation/action unchanged
    for bad in ('{"role":"alternative","role":"counterevidence"}', '{"score":2,"score":2}',
                '{"role":true,"role":1}', '{"role":1,"role":1.0}', '{"role":NaN,"role":NaN}'):
        with pytest.raises(ValueError):judge.parse_judge_json(bad)


def test_judge_next_check_text_alias_is_exact_and_unknown_children_still_reject(v3):
    p,e=v3;q=prepare_quality_packet(good(),p,e,source_id='alias-source',source_kind='existing_model_output')
    template=blank_review(q);template['facts']={'status':'Pass','reason':'Fixture only.','anchors':['next_check.text']}
    template['scores']={k:{'score':2,'reason':'Fixture only.','anchors':['risk_level']} for k in template['scores']}
    for r in template['roles']:r.update(role='alternative')
    verdict={k:template[k] for k in ('facts','scores','roles')};verdict['risk_level_review']={'status':'Pass','reason':'Fixture only.','anchors':['next_check.text']}
    result=judge.assess(q,verdict,identity={'model':'fixture','fingerprint':'fixture'})
    assert result['assessment']['anchor_alias_normalization']['next_check.text']=='next_check'
    verdict['facts']['anchors']=['next_check.unprovided_probability']
    with pytest.raises(ValueError):judge.assess(q,verdict,identity={'model':'fixture','fingerprint':'fixture'})
