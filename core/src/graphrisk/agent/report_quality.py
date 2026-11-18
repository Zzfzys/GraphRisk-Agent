"""Offline report mapping and review aggregation; no generation or auto-grading."""
from copy import deepcopy
from typing import Literal
import json,re
from pydantic import Field
from graphrisk.agent.autonomous_tools import FrozenModel,ToolBoundaryError,ToolName,SPEC_BY_NAME
from graphrisk.agent.fact_presentation import program_presentation
from graphrisk.agent.judgment import CHECKS,issues_for
from graphrisk.agent.judgment_support import RULES_VERSION
from graphrisk.agent.narrative_repair import safe_source
from graphrisk.agent.upgrade_contract import canonical,digest

STANDARD='report-quality-v1'
MAPPING='report-quality-mapping-v1'
DIMENSIONS=('specificity','analysis','actionability','readability')
LABELS={'specificity':'针对性','analysis':'分析质量','actionability':'核查价值','readability':'可读性'}
RATINGS={'low':'较低可疑','medium':'中等可疑','high':'较高可疑','undetermined':'暂不能判断'}


class AuthoredProcessContext(FrozenModel):
    kind: Literal['authored_scenario']
    available_tools: tuple[ToolName,...]
    tool_calls_remaining: int | None=Field(ge=0)
    unsupported_checks: tuple[Literal['payment_authorization'],...]
    reason: str=Field(min_length=4,max_length=500,pattern=r'\S')


class Rationale(FrozenModel):
    reason: str=Field(min_length=4,max_length=1200,pattern=r'\S')
    anchors: tuple[str,...]=Field(min_length=1,max_length=12)


class FactReview(Rationale):
    status: Literal['Pass','Fail','Pending']


class DimensionReview(Rationale):
    score: int | None=Field(ge=0,le=2)


class Scores(FrozenModel):
    specificity: DimensionReview
    analysis: DimensionReview
    actionability: DimensionReview
    readability: DimensionReview


class RoleReview(Rationale):
    path: str
    role: Literal['counterevidence','alternative','evidence_gap','mixed','unreviewed']


class Reviewer(FrozenModel):
    kind: Literal['unassigned','assistant_development','human','llm']
    reviewer_id: str=Field(min_length=2,max_length=100,pattern=r'\S')
    version: str=Field(min_length=2,max_length=100,pattern=r'\S')


class QualityReview(FrozenModel):
    version: Literal['report-quality-review-v1']='report-quality-review-v1'
    standard: Literal['report-quality-v1']='report-quality-v1'
    target_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    reviewer: Reviewer
    facts: FactReview
    scores: Scores
    roles: tuple[RoleReview,...]


def _seal(value,key):
    result=deepcopy(value);result[key]=digest(value)
    return result


def _check_seal(value,key):
    if not isinstance(value,dict) or value.get(key)!=digest({k:v for k,v in value.items() if k!=key}):
        raise ToolBoundaryError('quality_artifact_hash_changed')


def prepare_quality_packet(draft,packet,bundle,*,source_id,source_kind,scenario_context=None):
    """Map an existing v2 draft without rewriting or interpreting its sentences.

    Input is a trusted server/frozen evidence packet, not a newly approved send
    payload. Returned artifacts contain no scope/raw observation/provider name.
    Binding hashes detect accidental version changes, not authorship forgery.
    """
    if source_kind not in ('authored_development','existing_model_output'):
        raise ToolBoundaryError('quality_source_kind_invalid')
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,79}',source_id):
        raise ToolBoundaryError('quality_source_id_invalid')
    context={'kind':'not_supplied','reason':'当前材料未包含可验证的实时工具可用性和剩余额度。'}
    if scenario_context is not None:
        if source_kind!='authored_development':raise ToolBoundaryError('quality_authored_context_for_real_output')
        checked=AuthoredProcessContext.model_validate_json(canonical(scenario_context),strict=True)
        if len(checked.available_tools)!=len(set(checked.available_tools)):
            raise ToolBoundaryError('quality_scenario_tools_duplicate')
        context=checked.model_dump(mode='json')
    from graphrisk.agent import judgment_v3
    is_v3=packet.get('schema_version')=='reviewed-judgment-v3-packet'
    value=(judgment_v3.safe_source if is_v3 else safe_source)(draft)
    if value is None:raise ToolBoundaryError('quality_draft_unusable')
    # Canonical JSON transport must not change list ordering derived from maps.
    value=json.loads(canonical(value));packet=json.loads(canonical(packet))
    # Run existing source binding and known schema/fact/ownership guards. These
    # remain separate from the reviewer-decided semantic fact gate and scores.
    program=program_presentation(bundle,packet)
    findings=(judgment_v3.issues_for if is_v3 else issues_for)(value,packet,bundle)
    refs=packet['refs_private']
    available={f.fact_id for f in bundle.facts}|{p.policy_ref for p in bundle.policies}
    if any(not re.fullmatch(r'[FP][1-9][0-9]*',r) or full not in available for r,full in refs.items()):
        raise ToolBoundaryError('quality_reference_map_invalid')
    if len(set(refs.values()))!=len(refs):raise ToolBoundaryError('quality_reference_map_ambiguous')
    passages=[]
    for name in ('basis','counterevidence','policy'):
        items=value.get(name,[]) if name!='policy' else [value.get(name)]
        if not isinstance(items,list):continue
        for n,item in enumerate(items):
            if isinstance(item,dict) and isinstance(item.get('text'),str):
                passages.append({'path':f'{name}.{n}' if name!='policy' else name,
                                 'text':item['text'],'refs':item.get('refs',[]),
                                 'role':'unreviewed' if name=='counterevidence' else name})
    sources={}
    if is_v3:
        sources=judgment_v3.gap_sources(bundle,packet)
        passages=[]
        for path,item in judgment_v3.passages(value,sources):
            declared=path.split('.')[0]
            passages.append({'path':path,'text':item['text'],'refs':item['refs'],
                             'declared_role':declared,
                             'role':'unreviewed' if declared in ('counterevidence','hypotheses') else declared})
    outcomes=[o.model_dump(mode='json') for o in bundle.tool_outcomes]
    anchors=['risk_level','next_check','program.limitations','program.fact_assessments','catalog_names','process_context',
             *[p['path'] for p in passages],*sorted(refs),*[f'tool_outcomes.{i}' for i in range(len(outcomes))]]
    next_check=value.get('next_check',{})
    next_choice=next_check.get('text') if isinstance(next_check,dict) else None
    # Malformed model values remain in draft/known_findings for review/repair;
    # never coerce a list into a valid choice or crash a dictionary lookup.
    if not isinstance(next_choice,str):next_choice=None
    result={'version':MAPPING,'standard':STANDARD,'checked_rules_version':judgment_v3.RULES if is_v3 else RULES_VERSION,
            'source':{'id':source_id,'kind':source_kind,'draft_sha256':digest(value),
                      'packet_sha256':digest(packet),'evidence_sha256':digest(bundle.model_dump(mode='json'))},
            'draft':value,'references':deepcopy(refs),'program':program,
            'facts':[f.model_dump(mode='json') for f in bundle.facts],
            'policies':[p.model_dump(mode='json') for p in bundle.policies],
            'tool_outcomes':outcomes,'catalog_names':sorted(SPEC_BY_NAME),'process_context':context,
            'passages':passages,'review_anchors':anchors,
            'known_findings':findings,'semantic_fact_gate':'Pending',
            'panels':[
                {'name':'当前判断','source_paths':['risk_level'],'missing':['LLM开场依据与主要局限没有独立字段']},
                {'name':'主要依据','source_paths':[p['path'] for p in passages if p['path'].startswith('basis.')],
                 'missing':['旧schema至少一条basis，新标准允许有效线索为零；尚未升级生成合同']},
                {'name':'反证与其他解释','source_paths':[p['path'] for p in passages if p['path'].startswith('counterevidence.')],
                 'missing':['旧混合文字须复核分类，不自动认定为反证']},
                {'name':'关键缺口','source_paths':['program.limitations','program.fact_assessments',
                                               *[f'tool_outcomes.{i}' for i in range(len(outcomes))]],
                 'missing':['缺口对判断的影响没有独立LLM字段；未请求不等于工具不可用']},
                {'name':'下一步','source_paths':['next_check','policy'],
                 'missing':['旧枚举不包含具体核查目的、结果用途及工具行动理由']}],
            'next_check_display':{'choice':next_choice,'text':CHECKS.get(next_choice,'核查方向未通过旧合同'),
                                  'state':'advice_not_execution'},
            'review_roles_required':[p['path'] for p in passages if p['role']=='unreviewed'],
            'independent_semantic_review_completed':False,'personal_readability':'Pending',
            'approval_eligible':False,'outbound_authorized':False}
    if is_v3:
        result['generation_contract']=judgment_v3.CONTRACT
        result['gap_sources']=sources
        result['review_anchors']=list(dict.fromkeys([*anchors,'main_limit',*[f'gap_sources.{g}' for g in sources]]))
        result['panels']=[
            {'name':'当前判断','source_paths':['risk_level','rationale','main_limit.impact'],'missing':[]},
            {'name':'主要依据','source_paths':[p['path'] for p in passages if p['path'].startswith('basis.')],'missing':[]},
            {'name':'反证与其他解释','source_paths':[p['path'] for p in passages if p['declared_role'] in ('counterevidence','hypotheses')],
             'missing':['模型声明的类别仍需独立复核，不因字段名称而认定语义正确']},
            {'name':'关键缺口','source_paths':['main_limit.impact',*[p['path'] for p in passages if p['path'].startswith('gaps.')]],'missing':[]},
            {'name':'下一步','source_paths':['next_check','next_check.purpose','next_check.decision_use'],'missing':['实时工具权限和剩余额度未随报告提供；本合同不执行补查']}]
        check=value.get('next_check',{})
        result['next_check_display'].update(text=judgment_v3.CHECK_WORDING.get(next_choice,'核查方向未通过新版合同'),
                                            purpose=check.get('purpose'),decision_use=check.get('decision_use'))
    return _seal(result,'target_sha256')


def blank_review(packet):
    _check_seal(packet,'target_sha256')
    reason={'reason':'待人工或独立评审填写具体理由。','anchors':['risk_level']}
    result={'version':'report-quality-review-v1','standard':STANDARD,'target_sha256':packet['target_sha256'],
            'reviewer':{'kind':'unassigned','reviewer_id':'not-assigned','version':'not-assigned'},
            'facts':{'status':'Pending',**reason},
            'scores':{name:{'score':None,**deepcopy(reason)} for name in DIMENSIONS},
            'roles':[{'path':path,'role':'unreviewed','reason':'待区分反证、假设、缺失或混合句。','anchors':[path]}
                     for path in packet['review_roles_required']]}
    QualityReview.model_validate_json(canonical(result),strict=True)
    return result


def assess_quality(packet,review):
    """Aggregate recorded reviews, never infer scores from wording or headings."""
    _check_seal(packet,'target_sha256')
    if packet.get('version')!=MAPPING or packet.get('standard')!=STANDARD:
        raise ToolBoundaryError('quality_mapping_version_mismatch')
    record=QualityReview.model_validate_json(canonical(review),strict=True)
    if record.target_sha256!=packet['target_sha256']:raise ToolBoundaryError('quality_review_target_changed')
    allowed=set(packet['review_anchors'])
    explanations=[record.facts,*[getattr(record.scores,n) for n in DIMENSIONS],*record.roles]
    if any(not set(item.anchors)<=allowed or len(set(item.anchors))!=len(item.anchors) for item in explanations):
        raise ToolBoundaryError('quality_review_anchor_unknown_or_duplicate')
    required=set(packet['review_roles_required']);paths=[r.path for r in record.roles]
    if len(paths)!=len(set(paths)) or set(paths)!=required:raise ToolBoundaryError('quality_review_roles_incomplete')
    scores={name:getattr(record.scores,name).score for name in DIMENSIONS}
    complete=all(s is not None for s in scores.values())
    total=sum(scores.values()) if complete else None
    reasons=[]
    if packet['known_findings']:
        status='Fail';reasons.append('known_guard_rejection_cannot_be_overridden')
    elif record.facts.status=='Fail':
        status='Fail';reasons.append('reviewed_factual_failure')
    elif (record.facts.status=='Pending' or not complete or record.reviewer.kind=='unassigned'
          or any(r.role=='unreviewed' for r in record.roles)):
        status='Pending';reasons.append('review_not_complete')
    elif total<6 or any(s==0 for s in scores.values()):
        status='Fail';reasons.append('quality_threshold_not_met')
    else:
        status='Pass';reasons.append('development_rubric_threshold_met')
    result={'version':'report-quality-assessment-v1','standard':STANDARD,'target_sha256':packet['target_sha256'],
            'review_sha256':digest(record.model_dump(mode='json')),'reviewer':record.reviewer.model_dump(mode='json'),
            'known_guard_codes':sorted({i['code'] for i in packet['known_findings']}),
            'known_guard_scope':'existing_schema_and_known_rules_not_complete_semantic_validation',
            'reviewed_facts':record.facts.model_dump(mode='json'),'scores':scores,'total':total,'maximum':8,
            'status':status,'reasons':reasons,'role_annotations':[r.model_dump(mode='json') for r in record.roles],
            'evaluation_scope':'offline_development_review_not_frozen_validation',
            'personal_readability':'Pending','approval_eligible':False,'new_model_calls':0}
    return _seal(result,'assessment_sha256')


def render_quality_mapping(packet):
    _check_seal(packet,'target_sha256')
    if packet.get('generation_contract')=='program-facts-judgment-v3':
        return render_v3_mapping(packet)
    lines=['# 离线报告映射与待复核材料','',
           '原模型／人工草稿保持不变；下列结构映射不是新的模型报告或质量通过证明。',
           '当前语义复核、本人可读性均Pending；无业务处置权；本材料未获外发授权。','',
           '## 当前判断','',RATINGS.get(packet['draft'].get('risk_level'),'原稿等级不合法'),
           '开场的依据与主要局限缺少独立字段，不能由程序冒充LLM补写。','',
           '## 主要依据','']
    for p in packet['passages']:
        if p['path'].startswith('basis.'):lines.extend([p['path']+'：'+p['text'],'引用：'+canonical(p['refs']),''])
    lines.extend(['## 反证与其他解释','', '以下是旧counterevidence原文，尚未区分反证、假设和信息缺失。',''])
    for p in packet['passages']:
        if p['role']=='unreviewed':lines.extend([p['path']+'：'+p['text'],'引用：'+canonical(p['refs']),''])
    lines.extend(['## 关键缺口','',*packet['program']['limitations'],''])
    lines.extend(f'tool_outcomes.{i}：{o["tool"]} / {o["status"]} / {o["error_code"] or "无错误码"}'
                 for i,o in enumerate(packet['tool_outcomes']))
    lines.extend(['','当前只读工具目录：'+','.join(packet['catalog_names']),
                  '流程上下文：'+canonical(packet['process_context']),
                  '工具状态只是当前观察，未请求不证明不可用，也不证明权限或预算允许。人工情景前提不是真实运行快照。',
                  '','## 下一步','',packet['next_check_display']['text'],
                  '这是核查方向建议，尚未执行；旧字段缺少具体目的和结果用途。','','政策解释原文：'])
    lines.extend(p['text'] for p in packet['passages'] if p['path']=='policy')
    lines.extend(['','## 事实与政策出处（程序生成）',''])
    lines.extend(f'{p["ref"]}：{p["text"]}' for p in packet['program']['facts'])
    for p in packet['program']['policies']:lines.extend([p['ref']+'：'+p['text'],*p['conditions']])
    lines.extend(['','## 已知门禁与评分','',
                  '已知拒绝：'+(','.join(sorted({i['code'] for i in packet['known_findings']})) or '无已识别拒绝；尚不代表语义事实通过'),
                  '四项各0～2分：针对性、分析质量、核查价值、可读性；至少6/8且各项至少1，事实门禁需通过。',
                  '评分表初始为Pending，不根据段落、字数或引用数量自动打分。',''])
    return '\n'.join(lines)


def render_v3_mapping(packet):
    """Reading view only: preserve declared roles and pending semantic review."""
    _check_seal(packet,'target_sha256')
    value=packet['draft'];sources=packet['gap_sources']
    lines=['# 新版生成报告：离线复核材料','','这是已有草稿的复核映射，不是新实测、语义通过或个人可读性结论。',
           '## 当前判断','',RATINGS.get(value.get('risk_level'),'等级未通过结构校验')]
    for p in packet['passages']:
        if p['path']=='rationale':lines.extend([p['text'],'引用：'+canonical(p['refs'])])
    limit=value.get('main_limit',{})
    lines.extend(['主要局限来源：'+sources.get(limit.get('source'),{}).get('text','缺口来源无效'),
                  '对判断的影响：'+limit.get('impact','缺少说明')])
    for title,roles in [('主要依据',('basis',)),('反证与其他解释',('counterevidence','hypotheses')),('关键缺口',('gaps',)),('下一步',('next_check',))]:
        lines.extend(['','## '+title,''])
        if title=='反证与其他解释':lines.append('counterevidence声称是反证，hypotheses声称是假设；类别须独立复核，缺失不能当反证。')
        for p in packet['passages']:
            if p['declared_role'] in roles:
                lines.extend([p['path']+'：'+p['text'],'引用：'+canonical(p['refs'])])
        if title=='主要依据' and not value.get('basis'):lines.append('本稿没有提出有效风险线索；不等于正常。')
        if title=='反证与其他解释' and not value.get('counterevidence'):lines.append('本稿没有直接反证；不等于欺诈。')
        if title=='下一步':lines.extend([packet['next_check_display']['text'],'计划尚未执行；工具权限、预算和行动轨迹须另核对。'])
    lines.extend(['','## 程序事实与政策出处',''])
    lines.extend(f'{f["ref"]}：{f["text"]}' for f in packet['program']['facts'])
    for p in packet['program']['policies']:lines.extend([p['ref']+'：'+p['text'],*p['conditions']])
    lines.extend(['','## 事实门禁与评分','',
                  '已知拒绝：'+(','.join(sorted({i['code'] for i in packet['known_findings']})) or '无已识别拒绝；语义仍Pending'),
                  '四项各0～2分，至少6/8且各项至少1；评分初始null，事实和角色复核未完成不得通过。',''])
    return '\n'.join(lines)
