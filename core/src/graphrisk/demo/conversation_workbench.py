"""Readable investigation UI; reruns only read, POST requires explicit submit."""
import os
from uuid import uuid4
import httpx
import streamlit as st

RATINGS={'low':'较低可疑','medium':'中等可疑','high':'较高可疑','undetermined':'暂不能判断'}
DAILY_QUOTES_VERSION='separated-risk-report-v12-daily-quotes-v2'
TASKS={'investigate':'调查风险','explain_evidence':'查看依据与反证','policy_conditions':'核对政策前提','continue_checks':'继续核查','risk_assessment':'生成中文研判'}
METRICS={'graph_neighbors_truncated':'图查询是否只返回一部分','transaction_amount':'交易金额','primary_model_score':'XGBoost 主评分','experimental_graph_score':'GraphSAGE 实验分数',
 'history_transaction_count':'按匿名身份规则匹配的历史交易数','history_amount_mean':'历史平均金额','history_amount_population_std':'历史金额波动',
 'amount_minus_history_mean':'当前金额与历史均值之差','total_related_count':'符合共享字段规则的历史交易总数','returned_related_count':'本次返回关联交易数',
 'relations_truncated':'关联查询是否只返回一部分','returned_relation_type_count':'该类实体关联交易数','returned_graph_neighbor_count':'图查询返回邻居数'}
CONDITIONS={'guidance_only':'一般调查指导，不代表触发规则','unverified_conditions':'必要条件尚未测量，不能断言适用',
 'measured_conditions_only':'当前已测条件成立，仍需核实业务前提','conditions_not_met':'当前已测条件不满足','unmapped_requirements':'尚未建立条件对应'}
ERRORS={'investigation_generation_disabled':'当前是只读证据模式。尚未启用真实模型生成。',
 'clarification_required':'请使用支持的任务表达，如“解释依据与反证”或“生成中文研判”。',
 'turn_in_progress_recovery_required':'上次请求尚未确认完成。请刷新查看原轮次，不要重新创建请求。',
 'conversation_expired':'会话已过期，请重新建立会话。','conversation_service_unconfigured':'尚未启动自主调查 API。',
 'parent_turn_conflict':'另一个请求已更新会话，请刷新后再提交。','conversation_budget_limit':'本会话的调查额度已用完。',
 'transaction_outside_registered_cases':'该编号不在当前登记的开发案例内。',
 'source_version_changed':'证据文件版本已变化，请先检查来源绑定。'}
ERRORS['narrative_unconfigured']='请启动启用中文研判合同的 API（--narrative）；当前仅配置了证据会话。'
ERRORS.update({'daily_risk_not_configured':'当前服务尚未配置日终封批数据。',
 'daily_risk_unavailable':'日终批次尚未封批、版本已变化或当前不可读取。',
 'model_score_unavailable':'所选模型当前无法评分；请核对批次，或显式选择 x3 回退。'})


def api_call(method,path,*,body=None,key=None):
    base=os.getenv('GRAPHRISK_CONVERSATION_API_URL','http://127.0.0.1:8001')
    # Deployment configuration, not a user-entered URL. No proxy/redirects.
    try:
        with httpx.Client(base_url=base,timeout=610,trust_env=False,follow_redirects=False) as client:
            response=client.request(method,path,json=body,headers={'Idempotency-Key':key} if key else {})
        value=response.json()
    except (httpx.HTTPError,ValueError):
        return None,'API 无法连接或响应尚未确认。保留当前请求，先刷新查看，避免重复生成。'
    if response.is_error:
        code=value.get('error_code',value.get('detail')) if isinstance(value,dict) else None
        return None,ERRORS.get(code if isinstance(code,str) else None,'请求未通过服务校验。请检查当前会话和证据状态。')
    return value,None


from graphrisk.agent.fact_presentation import fact_text,relation_summary


def scoring_context_lines(value):
    """Explain the bound relative-day cutoff without inventing a calendar date."""
    evidence_context=value.get('evidence',{}).get('scoring_context')
    context=value.get('scoring_context',evidence_context)
    if context is None:return []
    if (context != evidence_context or context.get('temporal_scope') != 'relative_day_end'):
        return ['日终评分来源与当前证据不一致；暂停展示模型档位。']
    levels={'low':'低','medium':'中','high':'高'}
    if context.get('risk_level') not in levels:
        return ['日终模型调查优先级暂不可用，不能据此判低风险。']
    return ['日终评分 · XGBoost 216+49（主模型候选）',
        '模型版本：'+context['model_version'],
        f"数据范围：相对日 {context['relative_day']}；日终截止为相对秒 {context['batch_end_second']}（不含该秒）。",
        f"封批水位：相对秒 {context['available_through_second']}；批次 {context['batch_id']} / 修订 {context['revision']}。",
        '这是数据集相对时间，尚未映射真实日历日期或时区；封批后迟到交易单独隔离记录，不回写本修订。',
        'XGBoost 模型调查优先级（试用）：'+levels[context['risk_level']],
        '中、高档合计按当天约16.46%的关注量排序；低档仍可能包含欺诈。模型优先级与 Agent 综合研判独立，不是欺诈概率。']


def model_band_lines(value):
    """Render an optional server projection, never infer a grade from text."""
    if value.get('scoring_context') is not None or value.get('evidence',{}).get('scoring_context') is not None:
        return scoring_context_lines(value)
    item = value.get('model_band')
    if item is None:
        return []
    levels = {'low': '低', 'medium': '中', 'high': '高'}
    if item.get('status') != 'available' or item.get('risk_level') not in levels:
        return ['XGBoost 模型风险档位：暂不可用', '当前模型、交易时间或评分不满足分档条件。']
    return ['XGBoost 模型风险档位（试用）：'+levels[item['risk_level']],
            '根据历史欺诈案例调整分界；请结合交易证据查看。模型档位与下方 Agent 综合判断分别给出。']


def _reference_source_lines(source, label='完整参考节选'):
    dates={'unverified':'尚未核实','verified_interval':'已有生效区间证据'}
    conditions={'effective_interval':'条款生效区间','jurisdiction':'适用地区','payment_rail':'支付工具与交易类型',
        'consumer_asset_account':'消费者账户用途','consumer_error_notice_received':'消费者错误通知',
        'credit_card_transaction':'信用卡交易场景','unauthorized_use_alleged':'持卡人未经授权使用申诉',
        'issuer_seeks_to_impose_liability':'发卡方拟向持卡人追责','unauthorized_transfer_alleged':'未经授权转账主张',
        'notice_timing_verified':'通知时间'}
    lines=[source['ref']+' · '+label,source['text'],
        '出处：'+source['doc_id']+' / '+source['section'],
        '官方来源：'+source['source_url'],'资料版本：'+source['version_label'],
        '快照 SHA256：'+source['snapshot_sha256'],'正文 SHA256：'+source['content_sha256'],
        f"Unicode字符位置：[{source['char_start']}, {source['char_end']})；按保存节选的字符计数，包含起点、不含终点。",
        '生效区间：'+dates[source['date_status']]+'；检索日期不代表生效日期。']
    if source.get('missing_facts'):
        lines.append('参考问题尚未核实的适用条件：'+'、'.join(conditions.get(key,key) for key in source['missing_facts'])+'；不据此补成本案事实。')
    if source.get('unmet_conditions'):
        lines.append('参考问题的范围不符项：'+'、'.join(conditions.get(key,key) for key in source['unmet_conditions']))
    return lines


def public_reference_lines(value, *, details=False):
    """Read-only appendix; browser-supplied paths and unverified quotations never render."""
    if value.get('public_reference') is None:return []
    title='公开资料参考附录'
    cache_path=os.getenv('GRAPHRISK_PUBLIC_REFERENCE_CACHE')
    if not cache_path:
        return [title,'公开资料来源尚未配置受信复核；暂停展示附录引文。']
    try:
        from graphrisk.agent.reference_answer_cache import verify_reference_sidecar
        checked=verify_reference_sidecar(value,cache_path)
        if checked['context_role']!='public_question_scope_not_case_facts':
            raise ValueError('reference context role changed')
        answer=checked['answer']
        if not details:
            from graphrisk.serving.reference_presentation import summary_lines
            return [title, *summary_lines(answer),
                '本节解释固定公开问题，不包含本案已核实事实；完整原文和技术信息可按需查看。']
        states={'reference_excerpt':'可查看完整参考节选','insufficient_evidence':'资料或条件不足',
            'out_of_scope':'资料范围不符','conflicting_evidence':'可能存在冲突，待核对',
            'not_evaluated':'尚未评价，不是最终拒答','error':'资料不可用，不是无答案结论'}
        support={'requires_review':'仍须结合具体问题阅读核对','not_evaluated':'尚未评价内容支持情况'}
        integrity={'verified_saved_excerpt':'已核对保存节选与绑定记录','unavailable':'当前无法核验来源'}
        lines=[title,'固定公开参考问题：'+answer['question'],
            '此处复用已经完成的公开问题模型选择和完整节选；刷新与下载只读取既有记录，不新增模型调用。',
            '这段资料用于解释上面的固定公开问题，不包含本案已核实事实，也不表示条款自动适用于本案。',
            '资料分支：'+states[answer['status']],answer['message'],
            '内容支持：'+support[answer['semantic_support']]+'；引文真实不等于能够支持所有结论。',
            '助手已阅读复核这组公开问题的内容选择；这不代表用户验收，用户验收仍待单独确认。',
            '出处核验：'+integrity[answer['source_integrity']]+'；不代表已确定本案法律适用。',
            '该资料状态与模型调查优先级、Agent 综合判断分别显示，不授予业务处置权限。']
        for source in answer['sources']:
            lines.extend(_reference_source_lines(source))
        return lines
    except Exception:
        # A stale receipt/cache/source must hide quotes even if the browser sent them.
        return [title,'公开资料来源或绑定复核未通过；暂停展示附录引文。']


def case_reference_lines(value,turn, *, details=False):
    """Rebuild a case-bound report appendix using only trusted deployment sources."""
    if turn.get('report_package') is None:return []
    title='本轮公开资料核对附录'
    manifest=os.getenv('GRAPHRISK_PUBLIC_REFERENCE_MANIFEST')
    version=os.getenv('GRAPHRISK_PUBLIC_REFERENCE_VERSION')
    if not manifest or not version:
        return [title,'本案资料来源尚未配置受信复核；暂停展示包内引文，主报告可独立核验。']
    try:
        from graphrisk.agent.public_references import PublicReferenceRetriever
        from graphrisk.agent.reference_answers import ReferenceAnswerService
        from graphrisk.serving.daily_reference import verify_case_report_package
        service=ReferenceAnswerService(PublicReferenceRetriever(manifest,version))
        package=verify_case_report_package(value,turn,service)
        answer=package['reference']['answer']
        if not details:
            from graphrisk.serving.reference_presentation import summary_lines
            lines=[title, *summary_lines(answer)]
            if package['retrieval_status']=='error':
                lines.append('资料检索或来源暂不可用；这是执行错误，不是语料没有答案或交易低风险。')
            elif package['retrieval_status']=='no_match':
                lines.append('本次检索没有命中；这不代表已经完成最终拒答或排除风险。')
            return lines
        lines=[title,'本轮核对问题：'+answer['question'],
            '尚未完成本案问题回答选择；检索命中仅是待核背景，尚未判断能否支持回答。',
            '这份附录与当前轮次、报告及证据绑定，不替代本案风险判断，也不授予业务处置权。',
            '刷新与导出只读取并重新核验现有记录，不新增模型调用。']
        if package['retrieval_status']=='error':
            lines.append('资料检索或来源暂不可用；这是执行错误，不是语料没有答案或交易低风险。')
        elif package['retrieval_status']=='no_match':
            lines.append('本次检索没有命中；这不代表已经完成最终拒答或排除风险。')
        else:
            lines.append('以下保留完整背景片段和出处；没有已选定的回答依据，内容支持仍待核对。')
        for source in answer['retrieved_sources']:
            lines.extend(_reference_source_lines(source,'待核背景片段（未判断回答支持）'))
        return lines
    except Exception:
        return [title,'本案资料包或来源绑定复核未通过；暂停展示包内引文，主报告可独立核验。']


def policy_lines(policy,ref):
    kind='项目模拟政策，不是真实银行制度' if policy.get('source_type')=='simulated' else '政策来源类型：'+policy.get('source_type','未注明')
    return [ref+' · 引用片段原文（不是整份政策）',policy['text'],
        f"出处：{policy.get('doc_id','未注明')} / {policy['section']} / 版本{policy.get('version','未知')} / 第{policy.get('line_start','未知')}～{policy.get('line_end','未知')}行",
        kind,CONDITIONS.get(policy['applicability'],'适用前提待核实'),
        '找到相关条款，不等于这笔交易已经满足条款条件；本段指导也不是已经执行的处置。']


def reference_lines(item,report,evidence):
    lines=[]
    for ref in item['refs']:
        full=report['references'].get(ref)
        fact=next((f for f in evidence['facts'] if f['fact_id']==full),None)
        policy=next((p for p in evidence['policies'] if p['policy_ref']==full),None)
        if fact:lines.append(ref+' · '+fact_text(fact))
        elif policy:lines.extend(policy_lines(policy,ref))
        else:lines.append(ref+' · 未找到与本轮引用一致的原始片段，不能用其他版本替代。')
    return lines


def v2_lines(report,evidence):
    from graphrisk.agent.autonomous_evidence import EvidenceBundle
    from graphrisk.agent.fact_presentation import program_presentation
    from graphrisk.agent.autonomous_tools import ToolBoundaryError
    from pydantic import ValidationError
    from graphrisk.agent.upgrade_contract import canonical
    from graphrisk.agent.judgment_amounts import amount_quote_diagnostics
    from graphrisk.agent.judgment_support import RULES_VERSION
    try:
        bundle=EvidenceBundle.model_validate_json(canonical(evidence),strict=True)
        packet={'refs_private':report['references'],'source_evidence_sha256':report['program']['evidence_sha256']}
        current=program_presentation(bundle,packet)
        if current!=report['program']:raise ToolBoundaryError('program_display_changed')
    except (KeyError,ValidationError,ToolBoundaryError):
        return ['报告事实说明与当前证据不一致，请重新核对版本；暂停展示研判。']
    lines=['程序生成的事实说明',current['relation_summary']]
    lines.extend(item['ref']+' · '+item['text'] for item in current['facts'])
    for policy in current['policies']:
        lines.extend([policy['ref']+' · 政策原文：'+policy['text'],*policy['conditions']])
    lines.extend(current['limitations'])
    lines.append('LLM可疑等级：'+RATINGS[report['risk_level']]+'；为待复核判断。')
    judgment=report['judgment']
    # Recompute notes from bound evidence, never trust a model or stored note.
    if report.get('judgment_rules_version')==RULES_VERSION:
        _,amount_issues,notes=amount_quote_diagnostics(judgment,packet,bundle)
        if amount_issues:return ['报告金额复述与当前证据不一致，请重新核对；暂停展示研判。']
        lines.extend(dict.fromkeys(note['message'] for note in notes))
    for title,items in [('LLM支持线索解释',judgment['basis']),('LLM反证与其他可能',judgment['counterevidence']),('LLM政策理解',[judgment['policy']])]:
        lines.append(title)
        for item in items:lines.extend([item['text'],*reference_lines(item,report,evidence)])
    lines.extend(['LLM选择的下一步（程序生成说明，尚未执行）',judgment['next_check']['text']])
    return lines



def v3_lines(report,evidence):
    from graphrisk.agent.autonomous_evidence import EvidenceBundle
    from graphrisk.agent.judgment_v3 import JudgmentDraft,verify_judgment,verification_packet
    from graphrisk.agent.upgrade_contract import canonical,digest
    from pydantic import ValidationError
    from graphrisk.agent.autonomous_tools import ToolBoundaryError
    try:
        if report.get('report_sha256')!=digest({k:v for k,v in report.items() if k!='report_sha256'}):
            raise ToolBoundaryError('report_changed')
        bundle=EvidenceBundle.model_validate_json(canonical(evidence),strict=True)
        packet=verification_packet(report,bundle)
        errors,current=verify_judgment(JudgmentDraft.model_validate_json(canonical(report['draft']),strict=True),packet,bundle)
        if errors or any(current[k]!=report[k] for k in ('program','judgment','gap_sources','display_notes','references','risk_level','judgment_rules_version')):
            raise ToolBoundaryError('report_changed')
    except (KeyError,TypeError,ValueError,ValidationError,ToolBoundaryError):
        return ['报告或缺口来源与当前证据不一致，暂停展示研判。']
    return _judgment_lines(report,evidence)


def daily_quotes_lines(report,evidence):
    """The formal quotes contract has its own checker; never route it through v3."""
    from graphrisk.agent.autonomous_evidence import EvidenceBundle
    from graphrisk.agent.upgrade_contract import canonical
    from graphrisk.serving.daily_narrative import verify_daily_report
    try:
        bundle=EvidenceBundle.model_validate_json(canonical(evidence),strict=True)
        checked=verify_daily_report(report,bundle)
        if checked!=report:raise ValueError('daily report changed')
        return _judgment_lines(checked,evidence)
    except Exception:
        return ['日终正式报告与当前证据或生成合同不一致；暂停展示研判全文。']


def _judgment_lines(report,evidence):
    judgment=report['judgment'];sources=report['gap_sources']
    lines=['当前判断：'+RATINGS[report['risk_level']]+'（LLM待复核判断，不是欺诈概率）',judgment['rationale']['text']]
    lines.extend(reference_lines(judgment['rationale'],report,evidence))
    limit=judgment['main_limit']
    lines.extend(['主要局限：'+sources[limit['source']]['text'],'对判断的影响：'+limit['impact'],'主要依据'])
    if not judgment['basis']:lines.append('本轮没有提出有效风险线索；不能据此认定交易正常。')
    for item in judgment['basis']:lines.extend([item['text'],*reference_lines(item,report,evidence)])
    lines.append('反证与其他解释')
    if not judgment['counterevidence']:lines.append('本轮没有提出直接反证；不等于欺诈成立。')
    for item in judgment['counterevidence']:lines.extend(['反证（仍需核对是否确实削弱判断）：'+item['text'],*reference_lines(item,report,evidence)])
    for item in judgment['hypotheses']:lines.extend(['其他解释【假设，未核实】：'+item['text'],*reference_lines(item,report,evidence)])
    lines.append('关键缺口')
    for gap in judgment['gaps']:lines.extend([sources[gap['source']]['text'],'影响：'+gap['impact']])
    check=judgment['next_check']
    lines.extend(['下一步（计划，尚未执行）',check['text'],'核查目的：'+check['purpose'],'结果用途：'+check['decision_use'],
                  '执行分工：'+('需人工核实，当前工具不支持该业务核验。' if check['responsibility']=='human' else '待续查；先核对工具权限、预算和价值，不代表已经查询或工具不可用。')])
    lines.extend(reference_lines(check,report,evidence))
    if judgment['policy']:lines.extend(['政策如何理解：'+judgment['policy']['text'],*reference_lines(judgment['policy'],report,evidence)])
    lines.extend(['程序生成的事实说明',report['program']['relation_summary']])
    lines.extend(p['ref']+' · '+p['text'] for p in report['program']['facts'])
    for policy in report['program']['policies']:lines.extend([policy['ref']+' · 政策原文：'+policy['text'],*policy['conditions']])
    lines.extend(report['program']['limitations'])
    lines.extend(dict.fromkeys(n['message'] for n in report['display_notes']))
    lines.append('报告语义质量、四项评分和本人可读性仍待复核；无自动处置权。')
    return lines


def daily_missing_summary(evidence):
    labels={'get_transaction_features':'历史行为统计','get_related_entities':'关联交易核查','find_risky_neighbors':'图信号'}
    missing={fact.get('source_tool') for fact in evidence['facts'] if fact['availability']!='available'}
    gaps=[label for tool,label in labels.items() if tool in missing]
    if not evidence['policies']:gaps.append('政策前提')
    return ('证据缺口：'+'、'.join(gaps)+'尚未完整测量；未调用或未取得不等于数值为0，也不代表交易正常或政策条件成立。'
        if gaps else '当前已返回的证据仍需核实；共享字段与模型优先级不能证明欺诈。')


def daily_report_lines(report,evidence,*,include_facts=True):
    """Only show a daily program report that can be reproduced from its evidence."""
    from graphrisk.agent.autonomous_evidence import EvidenceBundle
    from graphrisk.agent.upgrade_contract import canonical
    from graphrisk.serving.daily_conversation_backend import daily_report
    from pydantic import ValidationError
    try:
        bundle=EvidenceBundle.model_validate_json(canonical(evidence),strict=True)
        if daily_report(bundle)!=report:raise ValueError('daily report changed')
    except (KeyError,TypeError,ValueError,ValidationError):
        return ['日终报告与封批证据不一致，暂停展示；请核对批次版本。']
    measured={fact['fact_id'] for fact in evidence['facts'] if fact['availability']=='available'}
    return ['本轮未调用大模型，尚未生成 Agent 综合研判；以下只整理已封批交易证据。',
        *(item['ref']+' · '+item['text'] for item in report['facts'] if include_facts and item['fact_id'] in measured),
        *(line for line in report['limitations'] if not line.startswith('当前会话没有测量'))]


def readable_report(value, *, include_reference_details=False):
    # Derived display only: never edit the model's original report or grade.
    evidence=value['evidence']
    if evidence.get('scoring_context'):
        report_turns=[turn for turn in value['turns'] if (turn.get('report') or {}).get('version') in
                 ('daily-batch-evidence-report-v1','separated-risk-report-v3',DAILY_QUOTES_VERSION)
                 and turn['state']=='completed' and not turn.get('post_review_failures')]
        latest=report_turns[-1] if report_turns else None
        report=latest['report'] if latest else None
        formal=bool(report and report['version'] in ('separated-risk-report-v3',DAILY_QUOTES_VERSION))
        lines=['日终调查阅读版（模型原稿保持不变）' if formal else '日终证据报告（程序生成）',
               *model_band_lines(value),daily_missing_summary(evidence)]
        if report:
            lines.extend(daily_quotes_lines(report,evidence) if report['version']==DAILY_QUOTES_VERSION
                         else v3_lines(report,evidence) if formal else daily_report_lines(report,evidence))
        else:
            lines.append('当前展示仅为封批证据汇总，尚未生成 Agent 综合研判。')
            lines.extend(fact_text(fact) for fact in evidence['facts'] if fact['availability']=='available')
        if latest:lines.extend(case_reference_lines(value,latest,details=include_reference_details))
        elif value['turns']:lines.extend(case_reference_lines(value,value['turns'][-1],details=include_reference_details))
        return '\n\n'.join([*lines,*public_reference_lines(value,details=include_reference_details)])
    lines=['调查阅读版（程序整理，模型原稿保持不变）','事实＝工具查到的数据；研判＝LLM的解释，可能出错；建议＝尚未执行的下一步。',
        relation_summary(evidence),'共享字段指卡片、设备、邮箱域名或地址等字段相同，不证明是同一人或欺诈团伙。',
        '模型分数不是已经验证的欺诈概率；LLM给出的可疑等级是另一项判断，不是分数换算。']
    lines.extend(model_band_lines(value))
    lines.extend(fact_text(f) for f in evidence['facts'])
    for turn in value['turns']:
        report=turn.get('report')
        lines.append('本轮：'+TASKS.get(turn['task'],'调查任务'))
        if report and report.get('version')=='daily-batch-evidence-report-v1' and not turn.get('post_review_failures'):
            lines.extend(daily_report_lines(report,evidence));lines.extend(case_reference_lines(value,turn,details=include_reference_details));continue
        if report and report.get('version')==DAILY_QUOTES_VERSION and not turn.get('post_review_failures'):
            lines.extend(daily_quotes_lines(report,evidence));lines.extend(case_reference_lines(value,turn,details=include_reference_details));continue
        if report and report.get('version')=='separated-risk-report-v3' and not turn.get('post_review_failures'):
            lines.extend(v3_lines(report,evidence));lines.extend(case_reference_lines(value,turn,details=include_reference_details));continue
        if report and report.get('version')=='separated-risk-report-v2' and not turn.get('post_review_failures'):
            lines.extend(v2_lines(report,evidence));continue
        if turn.get('post_review_failures'):
            lines.append('复查未通过。以下是有错误的模型原稿，仅供复盘，不是合格结论。')
        elif not report or report.get('version')!='chinese-risk-judgment-v1':
            lines.append('本轮没有可展示的中文研判；不代表交易安全。')
            lines.extend(case_reference_lines(value,turn,details=include_reference_details));continue
        else:lines.append('LLM可疑程度：'+RATINGS[report['risk_level']]+'；仅通过格式与引用检查，内容仍需复核。')
        if report:
            for title,items in [('支持线索',report['basis']),('反向线索或缺口',report['counterevidence']),('政策解释',[report['policy']]),('建议核查（未执行）',[report['next_check']])]:
                lines.append(title)
                for item in items:lines.extend([item['text'],*reference_lines(item,report,evidence)])
            lines.append('局限：'+report['limitation'])
        lines.extend(case_reference_lines(value,turn,details=include_reference_details))
    return '\n\n'.join([*lines,*public_reference_lines(value,details=include_reference_details)])


def show_conversation(value):
    evidence=value['evidence']
    daily=bool(evidence.get('scoring_context'))
    with st.container(border=True):
        st.subheader('① 工具查到了什么')
        st.caption('以下是当前数据源与查询规则下的结果，并不等于已核实真实客户身份。')
        for fact in evidence['facts']:
            if fact['metric'] in ('transaction_amount','primary_model_score','experimental_graph_score','history_transaction_count','total_related_count') and (not daily or fact['availability']=='available'):
                st.text(fact_text(fact))
        band_lines = model_band_lines(value)
        if band_lines:
            st.markdown('**'+band_lines[0]+'**')
            for line in band_lines[1:]:
                if line.startswith('XGBoost 模型调查优先级'):st.markdown('**'+line+'**')
                else:st.caption(line)
        st.caption('金额单位沿用数据集；标为历史窗口的统计不含当前及同时间交易，日终主评分可使用当天截至封批截止的关联信息。模型分数均未校准，图分数不参与主评分；分数不相加、不平均。')
    st.info(daily_missing_summary(evidence) if daily else relation_summary(evidence))
    st.caption('共享字段：例如两笔交易使用相同邮箱域名。它不证明是同一个人，更不证明属于欺诈团伙。历史身份匹配也是启发式规则。')
    st.caption('日终模型档位与 Agent 综合研判分别列示；程序证据报告没有 LLM 生成内容，LLM 研判仍需复核。' if daily else '阅读顺序：先看事实，再看LLM解释，最后看尚未执行的核查建议。模型分数不是欺诈概率，LLM等级也不是分数换算。')
    st.download_button('下载日终调查阅读版' if daily else '下载带政策原文的阅读版',readable_report(value),file_name='investigation-readable.txt',mime='text/plain')
    appendix=public_reference_lines(value)
    if appendix:
        with st.expander('公开资料参考附录',expanded=True):
            for line in appendix[1:]:st.text(line)
        with st.expander('查看完整原文与技术信息（公开资料）',expanded=False):
            complete=public_reference_lines(value,details=True)
            for line in complete[1:]:st.text(line)
            st.download_button('下载公开资料参考附录','\n\n'.join(complete),
                file_name='public-reference-appendix.txt',mime='text/plain',
                key='public_reference_download',on_click='ignore')
    for turn in value['turns']:
        with st.chat_message('user'):
            st.text(TASKS.get(turn['task'],'受控调查任务'))
        with st.chat_message('assistant'):
            if turn['state']!='completed':
                st.warning('本轮尚未确认结束。刷新只查询状态，不自动重发。');continue
            report=turn.get('report')
            if turn.get('post_review_failures'):
                st.error('报告复查未通过，下面的原稿不能作为合格结论。')
                if 'truncation_claim_conflicts_with_or_lacks_evidence' in turn['post_review_failures']:
                    st.warning('模型说结果完整或“未截断”，但引用数据表明只返回了一部分，或不足以证明结果完整。请以上方返回范围为准。')
                with st.expander('被拒绝原稿（仅供复盘）'):
                    for field in ('basis','counterevidence','policy','next_check'):
                        items=report.get(field,[]) if report else []
                        for item in (items if isinstance(items,list) else [items]):
                            st.text(item['text'])
                            for line in reference_lines(item,report,evidence):st.text(line)
                report=None
            if report and report.get('version')=='daily-batch-evidence-report-v1':
                st.subheader('② 日终证据报告（程序生成）')
                for line in daily_report_lines(report,evidence,include_facts=False):st.text(line)
            elif report and report.get('version')==DAILY_QUOTES_VERSION:
                st.subheader('② Agent 判断、假设与核查目的')
                st.caption('日终正式报告按当前合同重新核验；LLM判断仍需结合案情阅读。')
                for line in daily_quotes_lines(report,evidence):st.text(line)
            elif report and report.get('version')=='separated-risk-report-v3':
                st.subheader('② Agent 判断、假设与核查目的')
                st.caption('程序提供事实和缺口来源；以下解释由LLM生成，内容质量仍需复核。')
                for line in v3_lines(report,evidence):st.text(line)
            elif report and report.get('version')=='separated-risk-report-v2':
                st.subheader('② Agent 研判：'+RATINGS[report['risk_level']])
                st.caption('事实说明由程序生成；以下是LLM研判建议，语义与实用性仍待复核，不是欺诈概率。')
                for line in v2_lines(report,evidence):st.text(line)
            elif report and report.get('version')=='chinese-risk-judgment-v1':
                st.subheader('② Agent 研判：'+RATINGS[report['risk_level']])
                st.caption('这是 LLM 对可疑程度的判断，不是欺诈概率或已确认欺诈。引用与格式检查已通过，语义质量仍需阅读复核。')
                for title,items in [('支持这一判断的线索',report['basis']),('反向证据或尚缺的反证',report['counterevidence']),('政策如何理解',[report['policy']]),('③ 接下来建议查什么（尚未执行）',[report['next_check']])]:
                    st.markdown('**'+title+'**')
                    for item in items:
                        st.text(item['text'])
                        for line in reference_lines(item,report,evidence):st.text(line)
                st.warning(report['limitation'])
            elif turn['task']=='risk_assessment':
                outcome=turn.get('outcome')
                if outcome=='recovery_required':st.warning('模型结果或费用尚未确认，需先核对原请求；不能自动重发。')
                elif outcome=='budget_stopped':st.warning('本轮因预算门禁停止，未形成研判。')
                elif outcome=='provider_unavailable':st.warning('模型服务或配置未就绪，未形成研判。')
                else:st.error('模型研判未通过校验，未发布报告。上方事实仍可查看；本轮失败保留，不代表交易低风险。')
            else:
                st.text((turn.get('answer') or {}).get('message','已保存当前证据。'))
            case_appendix=case_reference_lines(value,turn)
            if case_appendix:
                with st.expander('本轮公开资料核对附录',expanded=True):
                    for line in case_appendix[1:]:st.text(line)
                with st.expander('查看本轮完整原文与技术信息',expanded=False):
                    complete=case_reference_lines(value,turn,details=True)
                    for line in complete[1:]:st.text(line)
                    st.download_button('下载本轮公开资料附录','\n\n'.join(complete),
                        file_name='case-reference-appendix.txt',mime='text/plain',
                        key='case_reference_download_'+str(turn['turn_id']),on_click='ignore')
            st.caption(f"本轮模型尝试 {turn['actual_model_calls']} 次 · 新工具请求 {turn['actual_tool_calls']} 次 · 不执行业务处置")
            with st.expander('本轮操作记录'):
                for item in turn.get('activity',[]):st.text(item.get('label','已完成受控步骤'))
                if not turn.get('activity'):st.text('复用当前证据，没有新的工具操作。')
    with st.expander('政策出处与已测／未测条件'):
        for policy in evidence['policies']:
            st.markdown('**'+policy['section']+'**')
            st.text(policy['text'])
            st.caption(CONDITIONS.get(policy['applicability'],'待核对')+' · 来源类型：'+policy['source_type'])
            for condition in policy['conditions']:
                st.text(METRICS.get(condition['metric'],'该项业务条件')+'：'+('已测量' if condition['measurement']=='measured' else '未测量，需人工核查'))
    with st.expander('技术溯源详情（版本与原始结构）'):
        # Public quotes have their own verified path; raw browser sidecars cannot bypass it.
        technical={key:item for key,item in value.items() if key!='public_reference'}
        technical['turns']=[]
        for turn in value['turns']:
            item={key:entry for key,entry in turn.items() if key!='report_package'}
            report=item.get('report')
            if report and report.get('version')==DAILY_QUOTES_VERSION:
                item['report']={key:report[key] for key in ('version','report_sha256') if key in report}
            technical['turns'].append(item)
        st.json(technical,expanded=False)


def conversation_selection():
    """Read only sealed server batches; a form submit selects one model explicitly."""
    refresh=st.button('刷新已封批次')
    if 'conversation_batch_catalog' not in st.session_state or refresh:
        catalog,error=api_call('GET','/api/v1/risk-batches')
        st.session_state.conversation_batch_catalog=catalog or {'batches':[]}
        st.session_state.conversation_catalog_error=error
        st.session_state.conversation_batch_details={}
    batches=st.session_state.conversation_batch_catalog.get('batches',[])
    profile=st.selectbox('调查评分模型',['daily_batch','x3'],
        index=0 if batches else 1,key='conversation_risk_profile',
        format_func=lambda item: '日终 XGBoost 216+49 · 主模型候选' if item=='daily_batch' else '原 x3 · 显式回退')
    if profile=='x3':
        st.caption('本次明确选择原 x3 评分与原有试用分档；日终评分失败不会自动切换模型。')
        if not batches:st.caption(st.session_state.conversation_catalog_error or '当前没有已封日批。')
        with st.form('new_conversation'):
            transaction=st.text_input('已登记的交易编号',key='conversation_transaction')
            submitted=st.form_submit_button('新建调查',disabled=bool(st.session_state.get('conversation_pending')))
        return submitted,{'transaction_id':transaction.strip(),'message':'调查风险','risk_profile':'x3'}
    if not batches:
        st.warning(st.session_state.conversation_catalog_error or '当前没有已封日批；请先封批并刷新。')
        return False,None
    choices={batch['batch_id']:batch for batch in batches}
    selected=st.selectbox('已封日终批次',list(choices),key='conversation_batch_id',
        format_func=lambda key: f"相对日 {choices[key]['relative_day']} · 修订 {choices[key]['revision']}")
    batch=choices[selected]
    token=(selected,batch['revision'])
    details=st.session_state.conversation_batch_details
    if token not in details:
        details[token]=api_call('GET','/api/v1/risk-batches/'+str(batch['relative_day']))
    day,error=details[token]
    if not day or any(day.get(key)!=batch[key] for key in ('batch_id','relative_day','revision')):
        st.error(error or '封批版本已变化，请刷新已封批次后重新选择。')
        return False,None
    rows={row['transaction_id']:row for row in day['results']}
    st.caption(f"模型版本：{batch['model_version']}；相对日终截止 {batch['batch_end_second']}（不含）；"
        f"水位 {batch['available_through_second']}；共 {batch['transaction_count']} 笔。")
    if not rows:
        st.info('该封批没有可供调查的交易。');return False,None
    with st.form('new_conversation'):
        transaction=st.selectbox('已封批交易编号',list(rows),key='conversation_daily_transaction',
            format_func=lambda key: key+' · 模型优先级 '+{'low':'低','medium':'中','high':'高'}[rows[key]['risk_level']])
        submitted=st.form_submit_button('新建调查',disabled=bool(st.session_state.get('conversation_pending')))
    return submitted,{'transaction_id':transaction,'message':'调查风险','risk_profile':'daily_batch','batch_id':selected}


def render_workbench():
    st.title('自主调查与中文研判')
    st.caption('工程开发模式 · 模拟政策 · 正式六十案质量尚未验收 · 无自动业务处置')
    st.session_state.setdefault('conversation_view',None)
    st.session_state.setdefault('conversation_pending',None)
    submitted,request=conversation_selection()
    if submitted:
        st.session_state.conversation_pending={'path':'/api/v1/conversations','body':request,'key':uuid4().hex}
    if st.button('刷新已有会话') and st.session_state.conversation_view:
        value,error=api_call('GET','/api/v1/conversations/'+st.session_state.conversation_view['conversation_id'])
        if value:st.session_state.conversation_view=value
        else:st.error(error)
    value=st.session_state.conversation_view
    if value:
        show_conversation(value)
        message=st.chat_input('例如：解释依据与反证、政策前提、生成中文研判、继续核查',disabled=bool(st.session_state.conversation_pending),submit_mode='disable')
        if message:
            st.session_state.conversation_pending={'path':'/api/v1/conversations/'+value['conversation_id']+'/turns',
                'body':{'parent_turn_id':value['turns'][-1]['turn_id'],'evidence_version':value['evidence']['evidence_version'],'message':message},'key':uuid4().hex}
    pending=st.session_state.conversation_pending
    retry=False
    if pending:
        st.caption('尚有待确认的原请求。下方重试沿用原交易、模型与批次；变更选择请先取消当前输入。')
        retry=st.button('使用原请求标识重新查询／提交')
        if st.button('取消当前输入（不撤销服务器已开始的任务）'):
            st.session_state.conversation_pending=None;st.rerun()
    if pending and (submitted or (value and message) or retry):
        with st.status('正在处理本轮任务…',expanded=False):
            result,error=api_call('POST',pending['path'],body=pending['body'],key=pending['key'])
        if result:
            st.session_state.conversation_view=result;st.session_state.conversation_pending=None;st.rerun()
        else:
            st.error(error)
            st.caption('请求标识已保留。刷新页面不会再次调用模型。')
