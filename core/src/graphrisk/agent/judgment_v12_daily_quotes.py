"""Opt-in daily contract: bounded supported quotes, unchanged first drafts."""
from copy import deepcopy
from typing import Annotated

from pydantic import Field

from graphrisk.agent import judgment_v3 as base, judgment_v12_daily_review as prior, fact_support
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_http import strict_json
from graphrisk.agent.upgrade_contract import canonical, digest

GENERATION = 'v12-daily-supported-quotes-v2'
CONTRACT = 'program-facts-judgment-v12-daily-quotes-v2'
RULES = GENERATION + '+' + base.RULES
RationaleText = Annotated[str, Field(min_length=4, max_length=160, pattern=r'\S')]
NUMERIC_RULE = ('必要行为数字只用fact_support_contract的明确句式，并引同一指标、主体、窗口的F；'
                '只允许已取得的交易金额、匹配历史计数、金额统计和返回样本共享字段计数，禁止估算或添币种。'
                '分数、阈值和查询范围仍由程序展示；正确数字不等于异常或欺诈。')
PROMPT = base.PROMPT.replace(
    '程序展示数字和范围，正文不抄分数、计数、阈值或截断范围；仅可精确说目标交易金额并引其F，不添币种。',
    NUMERIC_RULE)
PROMPT += 'F/P才是refs，G仅是缺口source；主理由只选一两项线索，其余可留空，不跨栏重复。'
ALLOWED_METRICS = {'transaction_amount', 'history_transaction_count', 'history_amount_mean',
                   'history_amount_population_std', 'amount_minus_history_mean', 'returned_relation_type_count'}


class Rationale(prior.Cited):
    text: RationaleText
    refs: tuple[prior.Ref, ...] = Field(min_length=1, max_length=8)


class Draft(prior.Draft):
    rationale: Rationale


def response_schema(packet):
    schema = Draft.model_json_schema()
    for definition in schema['$defs'].values():
        props = definition.get('properties', {})
        if 'refs' in props:
            props['refs']['items']['enum'] = sorted(prior._visible(packet))
        if definition.get('title') == 'GapImpact':
            props['source']['enum'] = sorted(packet['view']['gap_explanations'])
    return schema


def prepare_packet(packet, bundle, profile):
    result = prior.prepare_packet(packet, bundle, profile)
    view = result['view']
    view['narrative_contract'] = CONTRACT
    view['response_schema'] = response_schema(result)
    view['reference_contract']['numeric_ownership'] = NUMERIC_RULE
    view['writing_constraints'] = view['writing_constraints'].replace('局部零关联计数也只在程序卡显示；', '')
    view['writing_plan'] = (
        '主理由目标约90字，最多160字、8个引用，只选一两项决定性线索，说明观察和意义。'
        '数字只在必要时按明确句式引用，随后用独立短句解释；没有被识别的数值表达留待阅读复核。'
        'basis仅补不同观察，否则留空；无反证或无依据的替代解释也留空。'
        '样本有限须引对应计数，零波动不等于样本充分；共享字段不是已核实身份。'
        'main_limit只写最重要缺口，gaps只补不同项；授权核查仅调整授权疑点。'
        '其余字段保持120字与原引用容量；无需罗列全部数据。')
    bounds = view['output_contract']['bounds']
    bounds['rationale_text'] = [4, 160]
    bounds['rationale_refs'] = [1, 8]
    fact_support.attach_contract(result, bundle)
    support = view['fact_support_contract']
    support['quote_forms'] = [s for s in support['quote_forms'] if '主评分' not in s and '图分数' not in s]
    support['unknown_numeric_prose'] = 'review_required_not_verified'
    result['fact_support_contract_sha256'] = digest(support)
    result.update(generation_variant=GENERATION, judgment_rules_version=RULES,
                  action_schema_sha256=digest(Draft.model_json_schema()))
    result['view_sha256'] = digest(view)
    result['messages_sha256'] = digest([PROMPT, canonical(view)])
    result['prompt_utf8_bytes'] = len((PROMPT + canonical(view)).encode('utf-8'))
    if result['prompt_utf8_bytes'] > profile['max_prompt_utf8_bytes']:
        raise ActionFailure('daily_quotes_context_limit')
    return result


def packet_for(scope, observations, registry, profile):
    return prepare_packet(base.packet_for(scope, observations, registry, profile),
                          build_evidence(scope, observations, registry), profile)


def _check_packet(packet, bundle):
    if (packet.get('generation_variant') != GENERATION or packet.get('judgment_rules_version') != RULES
            or digest(packet['view']) != packet.get('view_sha256')
            or packet['view'].get('response_schema') != response_schema(packet)
            or packet.get('action_schema_sha256') != digest(Draft.model_json_schema())
            or packet.get('messages_sha256') != digest([PROMPT, canonical(packet['view'])])):
        raise ActionFailure('daily_quotes_packet_changed')
    base.gap_sources(bundle, packet)
    if fact_support.source_issues(packet, bundle):
        raise ActionFailure('daily_quotes_source_changed')


def parse(value, packet, bundle):
    _check_packet(packet, bundle)
    if not isinstance(value, dict) or prior._reference_issues(value, packet):
        raise ActionFailure('daily_quotes_reference_contract')
    return Draft.model_validate_json(canonical(value), strict=True)


def _checked(value, packet, bundle):
    _check_packet(packet, bundle)
    if not isinstance(value, dict):
        return [{'path': '$', 'code': 'daily_quotes_schema_invalid', 'category': 'schema'}], None
    references = prior._reference_issues(value, packet)
    try:
        Draft.model_validate_json(canonical(value), strict=True)
    except ValueError:
        return references or [{'path': '$', 'code': 'daily_quotes_schema_invalid', 'category': 'schema'}], None
    passages = base.passages(value, base.gap_sources(bundle, packet))
    diagnostic, support_issues, support = fact_support.assess_report(value, passages, packet, bundle)
    legacy = deepcopy(packet)
    legacy['judgment_rules_version'] = base.RULES
    kept = list(references) + list(support_issues) + prior._support_issues(value, packet, bundle)
    for witness in support['witnesses']:
        if witness['metric'] not in ALLOWED_METRICS:
            kept.append({'path': witness['path'], 'code': 'score_or_scope_number_program_owned',
                         'category': 'ownership_contract'})
    for issue in base.issues_for(diagnostic, legacy, bundle):
        field = issue.get('field', issue['path'])
        if (issue['code'] in ('string_too_long', 'too_long')
                and (field in ('rationale.text', 'rationale.refs') or issue['path']=='rationale.refs')):
            continue  # The strict v2 Rationale was already checked: 160 chars / 8 refs.
        if issue['code']=='numeric_or_probability_prose_not_allowed':
            issue = {**issue, 'code': 'unverified_numeric_prose_requires_review', 'category': 'review_required'}
        kept.append(issue)
    return kept, support


def issues_for(value, packet, bundle):
    return _checked(value, packet, bundle)[0]


def verify(draft, packet, bundle):
    issues, support = _checked(draft.model_dump(mode='json'), packet, bundle)
    if issues:
        return issues, None
    report = base.render_checked_judgment(draft, packet, bundle)
    report.update(version='separated-risk-report-v12-daily-quotes-v2', contract=CONTRACT,
                  judgment_rules_version=RULES, generation_variant=GENERATION, fact_support=support)
    report.pop('report_sha256')
    report['report_sha256'] = digest(report)
    return [], report


def verify_saved(report, packet, bundle):
    try:
        issues, rebuilt = verify(parse(report['draft'], packet, bundle), packet, bundle)
        if issues or rebuilt != report:
            raise ValueError('reconstructed report differs')
    except (KeyError, TypeError, ValueError):
        raise ActionFailure('daily_quotes_saved_report_changed') from None
    return rebuilt


class Provider(prior.Provider):
    def system_prompt(self, packet):
        return PROMPT

    def check_output(self, content, packet):
        try:
            value = strict_json(content)
            draft = Draft.model_validate_json(canonical(value), strict=True)
            if prior._reference_issues(value, packet):
                raise ValueError('visible citations and gap sources required')
            return draft
        except (TypeError, ValueError):
            raise ActionFailure('daily_quotes_schema_invalid') from None
