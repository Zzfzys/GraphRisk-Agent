"""Opt-in daily v12 candidate; no serving switch, quota or implicit repair."""
from copy import deepcopy
import re
from typing import Annotated

from pydantic import Field

from graphrisk.agent import judgment_v3 as base
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_http import strict_json
from graphrisk.agent.judgment_v13 import metric_mentions
from graphrisk.agent.upgrade_contract import canonical, digest

GENERATION = 'v12-daily-citation-review-v1'
CONTRACT = 'program-facts-judgment-v12-daily-review-v1'
RULES = GENERATION + '+' + base.RULES
Ref = Annotated[str, Field(pattern=r'^[FP][1-9][0-9]*$')]
PROMPT = base.PROMPT + '本候选严格区分F/P引用与G缺口；遵循reference_contract和writing_plan，示例不是本案事实。'


class Cited(base.RationaleText):
    refs: tuple[Ref, ...] = Field(min_length=1, max_length=6)


class Check(base.PurposefulCheck):
    refs: tuple[Ref, ...] = Field(min_length=1, max_length=3)


class Draft(base.JudgmentDraft):
    rationale: Cited
    basis: tuple[Cited, ...] = Field(max_length=2)
    counterevidence: tuple[Cited, ...] = Field(max_length=2)
    hypotheses: tuple[Cited, ...] = Field(max_length=2)
    policy: Cited | None
    next_check: Check


def _visible(packet):
    return {row[0] for row in packet['view']['facts']} | {p['ref'] for p in packet['view']['policies']}


def response_schema(packet):
    schema = Draft.model_json_schema()
    for definition in schema['$defs'].values():
        props = definition.get('properties', {})
        if 'refs' in props:
            props['refs']['items']['enum'] = sorted(_visible(packet))
        if definition.get('title') == 'GapImpact':
            props['source']['enum'] = sorted(packet['view']['gap_explanations'])
    return schema


def _legacy(packet):
    result = deepcopy(packet)
    result['judgment_rules_version'] = base.RULES
    return result


def prepare_packet(packet, bundle, profile):
    """Derive from frozen evidence, never from a previous model draft."""
    if (packet.get('judgment_rules_version') != base.RULES
            or packet.get('input_presentation_version') != 'plain-facts-v2-program-scope'
            or bundle.scoring_context is None):
        raise ActionFailure('daily_review_requires_v12_daily_plain_packet')
    base.gap_sources(bundle, packet)  # Validate source binding before derivation.
    result = deepcopy(packet)
    view = result['view']
    view['narrative_contract'] = CONTRACT
    view['response_schema'] = response_schema(result)
    view['reference_contract'] = {
        'refs': '只允许当前可见F/P；每段按指标、主体和窗口覆盖本段声明。',
        'gap_source': 'G仅用于main_limit.source及gaps[].source，不能放进refs。',
        'examples_are_evidence': False,
        'valid_structure': {'main_limit': {'source': 'G1', 'impact': '格式占位'},
                            'next_check': {'text': 'authorization', 'refs': ['F1'],
                                           'purpose': '格式占位', 'decision_use': '格式占位'}},
        'invalid_structure': {'next_check.refs': ['F1', 'G1']},
        'numeric_ownership': '精确历史计数、分数、阈值和范围仍由程序事实卡展示；正文只定性解释。目标金额及政策逐字引用沿用v12原规则。',
    }
    view['writing_plan'] = (
        'rationale说明当前判断和最重要线索；basis仅在有不同的有效观察时补充，否则留空，不重复rationale。'
        '优先解释本案已测金额比较或可见共享字段为何有调查价值，具体字段与本句引用对应；'
        '若匹配历史有限，引用对应窗口的计数，说明基线解释力有限，零波动不替代样本量。'
        '没有可靠反证或替代解释可留空，不新增家庭、商户或地点背景。'
        'main_limit集中说明主要缺口，gaps仅补充不同缺口，不跨栏重复。'
        'next_check只选能增加信息的一项核查，结果只调整该项待核假说；'
        '已授权不能直接解释金额差额或消除共享关系，也不等于安全。'
        '日终主评分支持模型调查优先级，LLM判断独立；保持简明，不强求覆盖所有字段。'
    )
    view['review_scope'] = (
        '确定性校验覆盖引用域、声明的统计指标/各窗口引用和有限身份措辞；'
        '否定、待核与假设不自动当肯定事实。任意中文的真假和写作质量仍需阅读复核。'
    )
    result.update(generation_variant=GENERATION, judgment_rules_version=RULES,
                  action_schema_sha256=digest(Draft.model_json_schema()))
    result['view_sha256'] = digest(view)
    result['messages_sha256'] = digest([PROMPT, canonical(view)])
    result['prompt_utf8_bytes'] = len((PROMPT + canonical(view)).encode('utf-8'))
    if result['prompt_utf8_bytes'] > profile['max_prompt_utf8_bytes']:
        raise ActionFailure('daily_review_context_limit')
    return result


def packet_for(scope, observations, registry, profile):
    return prepare_packet(base.packet_for(scope, observations, registry, profile),
                          build_evidence(scope, observations, registry), profile)


def _check_packet(packet, bundle):
    if (packet.get('generation_variant') != GENERATION or packet.get('judgment_rules_version') != RULES
            or digest(packet['view']) != packet.get('view_sha256')
            or packet['view'].get('response_schema') != response_schema(packet)
            or packet.get('messages_sha256') != digest([PROMPT, canonical(packet['view'])])):
        raise ActionFailure('daily_review_packet_changed')
    base.gap_sources(bundle, packet)


def _reference_issues(value, packet):
    issues = []
    for path, item in base.passages(value, {}):
        if path.endswith('.impact'):
            continue  # Gap refs are derived by the program, not emitted by the model.
        invalid = set(item['refs']) - _visible(packet)
        if invalid:
            issues.append({'path': path + '.refs',
                           'code': 'reference_namespace_mismatch' if any(r.startswith('G') for r in invalid) else 'unknown_or_hidden_citation',
                           'category': 'citation_contract', 'disallowed_refs': sorted(invalid)})
    # purpose/decision_use share a single refs array; report its root only once.
    unique = {}
    for issue in issues:
        if issue['path'].startswith('next_check.'):
            issue['path'] = 'next_check.refs'
        unique[canonical(issue)] = issue
    gaps = [('main_limit', value.get('main_limit'))]
    if isinstance(value.get('gaps'), list):
        gaps += [(f'gaps.{i}', gap) for i, gap in enumerate(value['gaps'])]
    for path, gap in gaps:
        if isinstance(gap, dict) and isinstance(gap.get('source'), str) and gap['source'] not in packet['view']['gap_explanations']:
            issue = {'path': path, 'code': 'unknown_or_hidden_gap_source', 'category': 'citation_contract'}
            unique[canonical(issue)] = issue
    return list(unique.values())


def parse(value, packet, bundle):
    _check_packet(packet, bundle)
    if not isinstance(value, dict):
        raise ActionFailure('daily_review_schema_invalid')
    if _reference_issues(value, packet):
        raise ActionFailure('daily_review_reference_contract')
    return Draft.model_validate_json(canonical(value), strict=True)


def _identity_assertion(text):
    # ponytail: finite clause grammar, not NLI; expand only with reviewed counterexamples.
    for sentence in re.split(r'[。；;]|但是|然而|而是|但', text):
        previous = ''
        for clause in re.split(r'[，,]', sentence):
            clause = clause.strip()
            for term in ('个人历史', '个人基线'):
                for match in re.finditer(term, clause):
                    before, after = clause[:match.start()], clause[match.end():]
                    if (re.search(r'(?:不是|不等于|不代表|不能|不可|不应|不得|无法|尚未).{0,12}$', before)
                            or re.match(r'(?:参照|情况)?(?:是否|尚待核实|未经核实|未核验|尚未确认)', after)
                            or (re.search(r'不能仅凭[^，,]*$', previous) and clause.startswith('就认定'))):
                        continue
                    return True
            previous = clause
    return False


def _support_issues(value, packet, bundle):
    issues = []
    facts = {r: next((f for f in bundle.facts if f.fact_id == fid), None)
             for r, fid in packet['refs_private'].items() if r in _visible(packet)}
    for path, item in base.passages(value, base.gap_sources(bundle, packet)):
        if _identity_assertion(item['text']):
            issues.append({'path': path, 'code': 'heuristic_identity_wording_review_required', 'category': 'review_required'})
        if path.startswith('next_check') or path.endswith('.impact'):
            continue  # Plans and gap impacts are not factual assertions of measured statistics.
        for clause in re.split(r'[，,。；;]', item['text']):
            if re.search(r'是否|^(?:若|如果|假如)|尚待核实|尚未确认|(?:不能|不应|不可|不得)(?:把|将|以)', clause):
                continue
            required = {r for r, fact in facts.items() if fact and fact.metric in metric_mentions(clause)}
            if '各窗口' in clause and '历史' in clause:
                required |= {r for r, fact in facts.items() if fact and fact.metric == 'history_transaction_count'}
            if '历史样本有限' in clause or '匹配历史样本有限' in clause:
                windows = [seconds for token, seconds in (('1小时', 3600), ('24小时', 86400), ('7天', 604800)) if token in clause]
                counts = {r for r, fact in facts.items() if fact and fact.metric == 'history_transaction_count'
                          and (not windows or fact.window_seconds in windows)}
                # Unspecified windows need an actual count anchor, not every window.
                if windows or not counts.intersection(item['refs']):
                    required |= counts
            missing = required - set(item['refs'])
            if missing:
                issues.append({'path': path + '.refs', 'code': 'declared_metric_citation_required',
                               'category': 'citation_support', 'required_refs': sorted(required),
                               'missing_refs': sorted(missing)})
    return issues


def issues_for(value, packet, bundle):
    _check_packet(packet, bundle)
    if not isinstance(value, dict):
        return [{'path': '$', 'code': 'daily_review_schema_invalid', 'category': 'schema'}]
    references = _reference_issues(value, packet)
    try:
        Draft.model_validate_json(canonical(value), strict=True)
    except ValueError:
        return references or [{'path': '$', 'code': 'daily_review_schema_invalid', 'category': 'schema'}]
    return references + base.issues_for(value, _legacy(packet), bundle) + _support_issues(value, packet, bundle)


def verify(draft, packet, bundle):
    value = draft.model_dump(mode='json')
    issues = issues_for(value, packet, bundle)
    if issues:
        return issues, None
    _, report = base.verify_judgment(draft, _legacy(packet), bundle)
    report.update(version='separated-risk-report-v12-daily-review-candidate', contract=CONTRACT,
                  judgment_rules_version=RULES, generation_variant=GENERATION,
                  semantic_quality='requires_review', approval_eligible=False)
    report.pop('report_sha256')
    report['report_sha256'] = digest(report)
    return [], report


def verify_saved(report, packet, bundle):
    """Rebuild from the frozen input; a self-consistent display hash is not proof."""
    try:
        draft = parse(report['draft'], packet, bundle)
        issues, rebuilt = verify(draft, packet, bundle)
        if issues or rebuilt != report:
            raise ValueError('reconstructed report differs')
    except (KeyError, TypeError, ValueError):
        raise ActionFailure('candidate_saved_report_changed') from None
    return rebuilt


class Provider(base.JudgmentProvider):
    def system_prompt(self, packet):
        return PROMPT

    def output_schema(self, packet):
        return packet['view']['response_schema']

    def check_output(self, content, packet):
        try:
            value = strict_json(content)
            if not isinstance(value, dict):
                raise ValueError('object required')
            draft = Draft.model_validate_json(canonical(value), strict=True)
            if _reference_issues(value, packet):
                raise ValueError('current visible references and gap sources required')
            return draft
        except (TypeError, ValueError):
            raise ActionFailure('daily_review_schema_invalid') from None
