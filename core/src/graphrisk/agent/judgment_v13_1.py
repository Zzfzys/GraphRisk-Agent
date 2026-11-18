"""Offline experimental factors: idempotent gaps, shared display, useful checks.

No default selection or live execution grant is changed. Original draft prose
remains auditable and every gap impact is checked before presentation merging.
"""
from copy import deepcopy
from typing import Literal
from pydantic import Field,ValidationInfo,model_validator
from graphrisk.agent import judgment_v3 as old,judgment_v13 as prior,fact_support
from graphrisk.agent.fact_presentation import numeric_display,NUMERIC_DISPLAY_VERSION,fact_text,program_presentation
from graphrisk.agent.autonomous_tools import FrozenModel
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_http import strict_json
from graphrisk.agent.upgrade_contract import canonical,digest

RULES=prior.RULES+'+quality-factors-v1'
CONTRACT='program-facts-judgment-v13.1-factors-v1'
GAP_PROMPT=prior.PROMPT.replace('主缺口不要再列入gaps。','缺口重复由程序合并展示，原文仍逐项核验。')
PROMPT=GAP_PROMPT.replace(
    '不添币种或舍入。','数字按程序展示值原样引用，不得自行舍入、截断或补单位。')


class Draft(FrozenModel):
    risk_level: Literal['low','medium','high','undetermined']
    rationale: prior.Cited
    main_limit: old.GapImpact
    basis: tuple[prior.Cited,...]=Field(max_length=2)
    counterevidence: tuple[prior.Cited,...]=Field(max_length=2)
    hypotheses: tuple[prior.Cited,...]=Field(max_length=2)
    gaps: tuple[old.GapImpact,...]=Field(max_length=2)
    policy: prior.Cited|None
    next_check: prior.CheckChoice

    @model_validator(mode='after')
    def bind(self,info:ValidationInfo):
        for item in (self.rationale,*self.basis,*self.counterevidence,*self.hypotheses,*([self.policy] if self.policy else [])):
            if len(set(item.refs))!=len(item.refs):raise ValueError('duplicate_ref')
            if info.context and not set(item.refs)<=set(info.context['refs']):raise ValueError('unknown_ref')
        if info.context:
            if any(g.source not in info.context['gaps'] for g in (self.main_limit,*self.gaps)):
                raise ValueError('unknown_gap')
            if self.next_check.text not in info.context['checks']:raise ValueError('check_direction_unsupported')
        return self


def parse(value,packet):
    return Draft.model_validate_json(canonical(value),strict=True,context={
        'refs':[r[0] for r in packet['view']['facts']]+[p['ref'] for p in packet['view']['policies']],
        'gaps':packet['view']['gap_explanations'],
        'checks':packet['view']['output_contract']['next_check_choices']})


def effective_gaps(value,packet):
    """Idempotent display projection, never an edit of model-authored evidence."""
    parse(value,packet)
    seen={value['main_limit']['source']};result=[]
    for gap in value['gaps']:
        source=gap['source']
        if source not in seen:
            seen.add(source)
            result.append({'source':source,'impact':packet['view']['gap_explanations'][source]['text'],
                           'content_source':'program'})
    return result


def supported_choices(bundle,packet,*,filter_completed=True):
    choices=[]
    # Authorization and card attempts require human/external evidence. A
    # successful get_transaction does not mean those actions were performed.
    tools={'history_stats':'get_transaction_features','graph_scope':'find_risky_neighbors'}
    for kind in old.CHECKS:
        if not prior._supported(kind,bundle,packet):continue
        if filter_completed and kind in tools:
            fact_ids=set(packet['refs_private'].values())
            queries={f.query_ref for f in bundle.facts if f.fact_id in fact_ids and f.source_tool==tools[kind]}
            if any(o.tool==tools[kind] and o.query_ref in queries and o.status=='ok' for o in bundle.tool_outcomes):
                continue
        choices.append(kind)
    return choices


def _display_contract(bundle,packet):
    visible={row[0] for row in packet['view']['facts']};facts={f.fact_id:f for f in bundle.facts}
    return {'version':NUMERIC_DISPLAY_VERSION,'rounding':'decimal_half_even',
        'accepted':'exact raw value or one metric-specific canonical display; no other rounded values',
        'money':'raw exact; no rounding','score_decimals':3,'difference_decimals':2,
        'comma_grouping':'integer transaction counts only',
        'values':[[r,numeric_display(facts[fid].metric,facts[fid].value)] for r,fid in packet['refs_private'].items()
            if r in visible and fid in facts and facts[fid].availability=='available' and type(facts[fid].value) in (int,float)]}


def _features(packet,bundle):
    flags=packet.get('quality_factors')
    if (not isinstance(flags,dict) or set(flags)!={'numeric_display','check_options'}
            or any(type(v) is not bool for v in flags.values())
            or packet['view'].get('quality_factors')!=flags):raise ActionFailure('quality_factors_binding_changed')
    expected=_display_contract(bundle,packet) if flags['numeric_display'] else None
    if packet['view'].get('numeric_display_contract')!=expected:raise ActionFailure('numeric_display_contract_changed')
    if packet.get('numeric_display_contract_sha256')!=digest(expected):raise ActionFailure('numeric_display_contract_changed')
    choices=supported_choices(bundle,packet,filter_completed=flags['check_options'])
    if not choices:raise ActionFailure('check_direction_unsupported')
    if (packet['view']['output_contract']['next_check_choices']!=choices or
            packet['view']['response_schema']['$defs']['CheckChoice']['properties']['text']['enum']!=choices):
        raise ActionFailure('check_options_binding_changed')
    return flags


def check_binding(kind,bundle,packet):
    flags=_features(packet,bundle)
    if kind not in supported_choices(bundle,packet,filter_completed=flags['check_options']):
        raise ActionFailure('check_direction_unsupported')
    return prior.check_binding(kind,bundle,packet)


def _expected(issues,packet):
    result=[]
    for issue in issues:
        row=dict(issue)
        if 'expected' not in row:
            required=row.get('required_refs')
            if required:expected='本段须引'+','.join(required)+'，并核对指标、对象与时间窗口。'
            elif row['code'].startswith('claim_missing_metric_citation:'):
                expected='仅保留本段refs所支持的指标声明；缺少的指标为'+row['code'].split(':',1)[1]+'。'
            elif row['code']=='check_direction_unsupported':
                expected='next_check.text仅可选'+','.join(packet['view']['output_contract']['next_check_choices'])+'；不能重复无新来源的已完成查询。'
            else:expected='按本案response_schema与fact_support_contract提供原证据支持的表述；无法证明时保留待复核。'
            row['expected']=expected
        result.append(row)
    return result


def _checked(value,packet,bundle):
    if packet.get('judgment_rules_version')!=RULES:raise ActionFailure('judgment_v13_1_rules_changed')
    flags=_features(packet,bundle)
    source_issues=fact_support.source_issues(packet,bundle)
    if source_issues:return _expected(source_issues,packet),None
    try:parse(value,packet)
    except ValueError as error:
        code='check_direction_unsupported' if 'check_direction_unsupported' in str(error) else 'judgment_v13_1_schema_invalid'
        return _expected([{'path':'$','code':code,'category':'schema'}],packet),None
    bypath=dict(prior.passages(value,packet,bundle))
    diagnostic,support_issues,support=fact_support.assess_report(value,bypath.items(),packet,bundle,
        numeric_formatter=numeric_display if flags['numeric_display'] else None)
    projected=prior.project(diagnostic,packet,bundle);legacy=prior.legacy_packet(packet)
    issues=old.issues_for(projected,legacy,bundle);kept=list(support_issues)
    for issue in issues:
        if issue['path'].startswith('next_check'):continue
        path=issue['path'].removesuffix('.refs').removesuffix('.text');item=bypath.get(path)
        if (item and issue['code']=='claim_missing_metric_citation:history_amount_mean'
                and 'history_amount_mean' not in prior.metric_mentions(item['text'])
                and 'amount_minus_history_mean' in prior.metric_mentions(item['text'])):continue
        if issue['code']=='numeric_or_probability_prose_not_allowed':
            issue={**issue,'code':'fact_support_review_required','category':'review_required',
                'expected':'仍有未核验的数字、窗口或概率表述；按事实支持合同表达或交语义复核，不称为编造。'}
        kept.append(issue)
    roles=prior.fact_roles(bundle,packet)
    for path,item in bypath.items():
        cited={f.metric for f in bundle.facts if any(packet['refs_private'].get(r)==f.fact_id for r in item['refs'])}
        for metric in prior.metric_mentions(item['text'])-cited:
            kept.append({'path':path+'.refs','code':'claim_missing_metric_citation:'+metric,'category':'typed_citation'})
        if '各窗口' in item['text'] and '历史' in item['text']:
            required={r for r,fid in packet['refs_private'].items() if any(f.fact_id==fid and f.metric=='history_transaction_count' for f in bundle.facts)}
            if not required<=set(item['refs']):kept.append({'path':path+'.refs','code':'claim_missing_metric_citation:history_transaction_count','category':'typed_citation'})
        if {'primary_signal','experimental_signal'}<={roles.get(r) for r in item['refs']} and prior.asserts_cross_model_comparison(item['text']):
            kept.append({'path':path,'code':'cross_model_score_comparison_not_evidence','category':'role_contract'})
    try:check_binding(value['next_check']['text'],bundle,packet)
    except ActionFailure:kept.append({'path':'next_check','code':'check_direction_unsupported','category':'program_binding'})
    if value['risk_level']=='high':
        refs={r for item in value['basis'] for r in item['refs']}
        supported=any(f.availability=='available' and f.metric in ('amount_minus_history_mean','history_transaction_count')
            and f.value not in (None,0) and any(packet['refs_private'].get(r)==f.fact_id for r in refs) for f in bundle.facts)
        if not supported:kept.append({'path':'risk_level','code':'high_requires_behavioral_support','category':'risk_support'})
    return _expected(kept,packet),support


def issues_for(value,packet,bundle):return _checked(value,packet,bundle)[0]


def packet_for(scope,observations,registry,profile,*,numeric=True,filter_checks=True):
    if type(numeric) is not bool or type(filter_checks) is not bool:raise ValueError('quality_factors_require_boolean')
    packet=prior.packet_for(scope,observations,registry,profile);view=packet['view']
    bundle=build_evidence(scope,observations,registry);flags={'numeric_display':numeric,'check_options':filter_checks}
    packet['quality_factors']=flags;view['quality_factors']=deepcopy(flags)
    view['narrative_contract']=CONTRACT;view['response_schema']=Draft.model_json_schema()
    refs=[r[0] for r in view['facts']]+[p['ref'] for p in view['policies']]
    view['response_schema']['$defs']['Cited']['properties']['refs']['items']={'type':'string','pattern':r'^[FP][1-9][0-9]*$','enum':refs}
    view['response_schema']['$defs']['GapImpact']['properties']['source']['enum']=list(view['gap_explanations'])
    choices=supported_choices(bundle,packet,filter_completed=filter_checks)
    if not choices:raise ActionFailure('check_direction_unsupported')
    view['response_schema']['$defs']['CheckChoice']['properties']['text']['enum']=choices
    view['output_contract']['next_check_choices']=choices
    view['output_contract']['example']['next_check']={'text':choices[0]}
    if numeric:
        facts={f.fact_id:f for f in bundle.facts}
        view['facts']=[[row[0],fact_text(facts[packet['refs_private'][row[0]]].model_dump(mode='json'),numeric_formatter=numeric_display),*row[2:]] for row in view['facts']]
        view['numeric_display_contract']=_display_contract(bundle,packet)
    packet['numeric_display_contract_sha256']=digest(view.get('numeric_display_contract'))
    packet.update(schema_version='reviewed-judgment-v13.1-factors-v1-packet',judgment_rules_version=RULES,
        view_sha256=digest(view),messages_sha256=digest([system_prompt(packet),canonical(view)]),
        action_schema_sha256=digest(Draft.model_json_schema()))
    packet['prompt_utf8_bytes']=len((system_prompt(packet)+canonical(view)).encode())
    if packet['prompt_utf8_bytes']>profile['max_prompt_utf8_bytes']:raise ActionFailure('judgment_context_limit')
    return packet


def system_prompt(packet):return PROMPT if packet['quality_factors']['numeric_display'] else GAP_PROMPT


class Provider(prior.Provider):
    def system_prompt(self,packet):return system_prompt(packet)
    def check_output(self,content,packet):
        try:return parse(strict_json(content),packet)
        except (ValueError,TypeError):raise ActionFailure('judgment_v13_1_schema_invalid') from None


def verify(draft,packet,bundle):
    value=draft.model_dump(mode='json');issues,support=_checked(value,packet,bundle)
    if issues:return issues,None
    flags=packet['quality_factors'];gaps=effective_gaps(value,packet)
    report={'version':'separated-risk-report-v13.1-factors-v1-candidate','contract':CONTRACT,'judgment_rules_version':RULES,
        'draft':value,'program':program_presentation(bundle,packet,numeric_formatter=numeric_display if flags['numeric_display'] else None),
        'judgment':{**value,'gaps':gaps,'next_check':check_binding(draft.next_check.text,bundle,packet)},
        'gap_normalization':{'raw_count':len(value['gaps']),'effective_count':len(gaps),'content_source':'program',
            'all_raw_gap_impacts_verified':True,'original_draft_preserved':True},
        'next_check_content_source':'program_not_llm','fact_support':support,
        'semantic_quality':'requires_review','approval_eligible':False}
    report['report_sha256']=digest(report);return [],report
