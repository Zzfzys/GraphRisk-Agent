"""Bounded fact quotations backed by the existing typed evidence contract.

Only the declared assertion grammar is interpreted. Other Chinese prose is
never promoted to verified fact merely because a citation exists.
"""
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_evidence import FactClaim, fact_layout, validate_fact_claim
from graphrisk.agent.judgment_amounts import CURRENCY
from graphrisk.agent.upgrade_contract import digest

VERSION = 'fact-support-v1'
SCOPE = 'declared_claim_families_only'
WINDOWS = {'1小时': 3600, '24小时': 86400, '7天': 604800}
LABEL_METRICS = {
    '金额与历史均值之差': 'amount_minus_history_mean',
    '当前金额与历史均值之差': 'amount_minus_history_mean',
    '历史平均金额': 'history_amount_mean', '历史均值': 'history_amount_mean',
    '历史金额波动': 'history_amount_population_std', '波动': 'history_amount_population_std',
    '标准差': 'history_amount_population_std',
}
LABEL = '|'.join(sorted(LABEL_METRICS, key=len, reverse=True))
START = r'(?:^|(?<=[，,。；;！？!?]))\s*'
NUMBER = r'(?P<number>[-+]?[0-9]+(?:\.[0-9]+)?)(?![0-9.eE%％])'
GROUPED_NUMBER = r'(?P<number>[-+]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)(?![0-9.eE%％]|,[0-9])'
NUMERIC = (
    ('transaction_amount', re.compile(START + r'(?:目标|本笔|该笔)(?:交易)?金额(?:为|是|[:：])?\s*' + NUMBER)),
    ('history_transaction_count', re.compile(START + r'此前(?P<window>1小时|24小时|7天)(?:按匿名身份规则匹配的历史|匿名匹配历史)交易数(?:为|是|[:：])?\s*' + NUMBER)),
    ('statistic', re.compile(START + r'此前24小时(?:匿名匹配)?(?P<label>' + LABEL + r')(?:为|是|[:：])\s*' + NUMBER)),
    ('primary_model_score', re.compile(START + r'(?P<dayend>日终|日批)?\s*(?:XGBoost\s*)?主评分(?:为|是|[:：])\s*' + NUMBER)),
    ('experimental_graph_score', re.compile(START + r'(?:图分数|GraphSAGE\s*实验分数)(?:为|是|[:：])\s*' + NUMBER)),
    ('returned_relation_type_count', re.compile(START + r'已返回样本中共享(?P<entity>设备|邮箱域|地址|匿名卡片)(?:字段)?的交易数(?:为|是|[:：])\s*' + NUMBER)),
)
STATES = re.compile(r'(?P<labels>(?:' + LABEL + r')(?:(?:、|及|和|与)(?:' + LABEL + r'))*)'
                    r'(?:均)?(?P<state>未取得|已取得|未测量|不可用|可用|缺失)')
LABEL_RE = re.compile(LABEL)
ENTITIES = {'设备': 'device', '邮箱域': 'email_domain', '地址': 'address', '匿名卡片': 'card'}


@dataclass(frozen=True)
class StatementSupport:
    diagnostic_text: str
    issues: tuple[dict, ...]
    witnesses: tuple[dict, ...]
    warnings: tuple[dict, ...]


def _binding(packet):
    return digest({key: packet[key] for key in
                   ('source_evidence_sha256', 'scope_private', 'refs_private')})


def attach_contract(packet, bundle):
    """Bind the new rules without changing the generator's JSON shape."""
    visible = {row[0] for row in packet['view']['facts']}
    fields = ('metric', 'subject', 'unit', 'window_seconds', 'temporal_scope',
              'availability', 'value', 'model_version')
    by_id = {fact.fact_id: fact for fact in bundle.facts}
    contract = {
        'version': VERSION, 'scope': SCOPE,
        'witness_fields': ['ref', *fields],
        'witnesses': [[ref, *(getattr(by_id[fid], key) for key in fields)]
                      for ref, fid in packet['refs_private'].items()
                      if ref in visible and fid in by_id],
        'quote_forms': ['目标交易金额为{value}', '此前{window}匿名匹配历史交易数为{value}',
                        '此前24小时历史均值/历史金额波动/金额与历史均值之差为{value}',
                        '主评分/图分数为{value}，是未校准信号，不是欺诈概率',
                        '已返回样本中共享{字段}字段的交易数为{value}',
                        '历史均值、波动及金额与历史均值之差均未取得'],
        'unknown_numeric_prose': 'review_required',
        'verified_repetition': 'warning_not_fact_error',
    }
    packet['view']['fact_support_contract'] = contract
    if bundle.scoring_context is not None:
        contract['quote_forms'].append('日终XGBoost主评分为{value}，是未校准信号，不是交易发生时可用的即时评分')
    packet['fact_support_contract_sha256'] = digest(contract)
    packet['fact_support_binding_private'] = _binding(packet)


def source_issues(packet, bundle):
    """The packet is frozen by its caller; do not silently rebind its witness."""
    contract = packet['view'].get('fact_support_contract')
    if (not isinstance(contract, dict) or contract.get('version') != VERSION
            or digest(contract) != packet.get('fact_support_contract_sha256')):
        raise ActionFailure('fact_support_contract_changed')
    valid = (digest(bundle.model_dump(mode='json')) == packet['source_evidence_sha256']
             and _binding(packet) == packet.get('fact_support_binding_private'))
    scope = packet['scope_private']
    valid = valid and bundle.case_alias == scope['case_alias']
    if bundle.scoring_context is not None:
        valid = valid and scope.get('scoring_context') == bundle.scoring_context.model_dump(mode='json')
    ids = [fact.fact_id for fact in bundle.facts]
    aliases = [fid for ref, fid in packet['refs_private'].items() if ref.startswith('F')]
    valid = valid and len(set(ids)) == len(ids) and len(set(aliases)) == len(aliases)
    valid = valid and set(aliases) <= set(ids)
    for fact in bundle.facts:
        semantics = (fact.metric, fact.subject, fact.unit, fact.window_seconds, fact.temporal_scope)
        revision_key = ('transaction_revision' if fact.source_tool in ('get_transaction', 'get_related_entities')
                        else 'feature_revision' if fact.source_tool == 'get_transaction_features' else 'model_revision')
        if scope.get('investigation_revision') and fact.source_tool in (
                'get_transaction_features', 'get_related_entities', 'find_risky_neighbors'):
            revision_key = 'investigation_revision'
        model_key = ('primary_model_version' if fact.source_tool == 'get_risk_score'
                     else 'graph_model_version' if fact.source_tool == 'find_risky_neighbors' else None)
        outcomes = [outcome for outcome in bundle.tool_outcomes
                    if (outcome.tool, outcome.query_ref) == (fact.source_tool, fact.query_ref)]
        valid = valid and (semantics in fact_layout(scope, fact.source_tool)
                          and fact.source_version == scope[revision_key]
                          and fact.model_version == (scope[model_key] if model_key else None)
                          and len(outcomes) == 1
                          and (fact.availability != 'available' or outcomes[0].status == 'ok'))
    if valid:
        return []
    return [{'path': '$', 'code': 'fact_support_source_invalid', 'category': 'source_binding',
             'expected': '重新核对原始冻结证据、引用和来源；不得重写绑定值以绕过核验。'}]


def _semantics(metric, match=None):
    window = None
    if metric == 'transaction_amount' or metric in ('primary_model_score', 'experimental_graph_score'):
        subject, temporal = 'target_transaction', 'target_event'
        if metric == 'primary_model_score' and match is not None and match.groupdict().get('dayend'):
            temporal = 'relative_day_end'
    elif metric == 'returned_relation_type_count':
        subject = f"returned_shared_{ENTITIES[match['entity']]}_history"
        temporal = 'strictly_before_target'
    else:
        window = WINDOWS[match['window']] if metric == 'history_transaction_count' else 86400
        subject = 'target_against_heuristic_history' if metric == 'amount_minus_history_mean' else 'heuristic_identity_history'
        temporal = 'target_against_strict_history' if metric == 'amount_minus_history_mean' else 'strictly_before_target'
    unit = ('transactions' if metric in ('history_transaction_count', 'returned_relation_type_count')
            else 'uncalibrated_score' if metric in ('primary_model_score', 'experimental_graph_score')
            else 'dataset_amount_unit')
    return metric, subject, unit, window, temporal


def _nonassertive(text, start):
    prefix = re.split(r'[，,。；;！？!?]', text[:start])[-1]
    return bool(re.search(r'是否|(?:不能|无法|尚未)(?:确认|证明|判断)', prefix))


def assess_statement(path, text, refs, packet, bundle,*,numeric_formatter=None):
    """Return a diagnostic copy plus typed witnesses; never change model prose."""
    issues, witnesses, warnings, masked = [], [], [], []
    visible = {row[0] for row in packet['view']['facts']}

    def add(code, category, span, **details):
        row = {'path': path + '.text', 'code': code, 'category': category,
               'span_text': span, **details}
        if row not in issues:
            issues.append(row)

    def witness(semantic, span):
        rows = [fact for fact in bundle.facts if
                (fact.metric, fact.subject, fact.unit, fact.window_seconds, fact.temporal_scope) == semantic]
        if len(rows) != 1:
            add('fact_claim_ambiguous_source', 'review_required', span)
            return None, None
        fact = rows[0]
        aliases = [ref for ref, fid in packet['refs_private'].items() if fid == fact.fact_id and ref in visible]
        if len(aliases) != 1 or aliases[0] not in refs:
            add('fact_claim_citation_required', 'citation_support', span, required_refs=aliases,
                metric=semantic[0], subject=semantic[1], window_seconds=semantic[3])
            return None, None
        # Reuse the existing typed evidence validator for the bound semantic slots.
        claim = FactClaim(**{key: getattr(fact, key) for key in FactClaim.model_fields if key != 'evidence_version'},
                          evidence_version=bundle.evidence_version)
        validate_fact_claim(claim, bundle)
        return fact, aliases[0]

    patterns=NUMERIC if numeric_formatter is None else tuple(
        (family,re.compile(pattern.pattern.replace(NUMBER,GROUPED_NUMBER))) for family,pattern in NUMERIC)
    active_number=NUMBER if numeric_formatter is None else GROUPED_NUMBER
    for family, pattern in patterns:
        matches = list(pattern.finditer(text))
        # Use the same declaration prefix to detect values outside the decimal
        # grammar (for example 十八 or 八成). They are unknown, not verified.
        broad = re.compile(pattern.pattern.replace(active_number, r'(?P<unparsed>[^，,。；;！？!?]+)'))
        for unparsed in broad.finditer(text):
            if not any(match.start() == unparsed.start() for match in matches):
                add('fact_support_review_required', 'review_required', unparsed.group().strip(),
                    expected='该事实声明的数值不在已声明十进制句式内，需复核。')
        for match in matches:
            metric = LABEL_METRICS[match['label']] if family == 'statistic' else family
            semantic = _semantics(metric, match)
            span = match.group().strip()
            if (metric == 'primary_model_score' and bundle.scoring_context is not None and
                    semantic[4] != 'relative_day_end'):
                add('day_end_score_must_be_explicit', 'factual_support', span)
            fact, ref = witness(semantic, span)
            # A recognized claim has its own outcome even when false. Only its
            # number is masked for legacy diagnostics; adjacent text stays live.
            masked.append(match.span('number'))
            if fact is None:
                continue
            if fact.availability != 'available':
                add('fact_claim_unavailable', 'factual_support', span, ref=ref)
                continue
            try:
                literal=match['number'].replace(',','')
                equal = (type(fact.value) in (int, float)
                         and Decimal(str(fact.value)).is_finite()
                         and (Decimal(literal) == Decimal(str(fact.value)) or
                              (numeric_formatter is not None and Decimal(literal)==Decimal(
                                  numeric_formatter(fact.metric,fact.value).replace(',','')))))
                if ',' in match['number'] and fact.unit!='transactions':equal=False
                if fact.unit == 'transactions':
                    equal = equal and type(fact.value) is int and Decimal(literal) % 1 == 0
            except InvalidOperation:
                equal = False
            if not equal:
                details=({'expected':numeric_formatter(fact.metric,fact.value)} if numeric_formatter is not None else {})
                add('fact_claim_value_mismatch', 'factual_support', span, ref=ref,**details)
                continue
            suffix = re.split(r'[，,。；;！？!?]', text[match.end('number'):], maxsplit=1)[0].strip()
            if CURRENCY.search(text) or re.search(r'[0-9]元', text) or re.match(r'(?:USD|RMB|CNY|HKD|EUR|JPY|GBP|元)', suffix, re.I):
                add('fact_claim_currency_unverified', 'factual_support', span, ref=ref)
                continue
            # Reading just the numeric prefix would accept 17万元 as 17. No
            # arithmetic/unit modifiers are silently discarded. Only explicit
            # neutral tails are part of this version's quotation grammar.
            neutral = {'', '已记录', '已测', '已确认'}
            if fact.unit == 'transactions':
                neutral |= {'笔', '笔已记录'}
            if suffix not in neutral:
                add('fact_support_review_required', 'review_required', span,
                    expected='数值后的单位、倍率或其他文字不在已声明句式内，需复核。')
                continue
            if fact.unit == 'uncalibrated_score' and '未校准' not in text:
                add('score_uncalibrated_context_required', 'role_contract', span, ref=ref)
                continue
            witnesses.append({'path': path + '.text', 'ref': ref, 'span_text': span,
                              **fact.model_dump(mode='json')})
            warnings.append({'path': path + '.text', 'code': 'verified_fact_repeated',
                             'category': 'display_hint', 'ref': ref})
            if path.startswith('hypotheses.'):
                add('measured_claim_in_hypothesis_requires_review', 'review_required', span, ref=ref)

    for match in STATES.finditer(text):
        if _nonassertive(text, match.start()):
            continue
        prefix = re.split(r'[，,。；;！？!?]', text[:match.start()])[-1].strip()
        if prefix not in ('', '此前24小时', '此前24小时匿名匹配'):
            add('fact_support_review_required', 'review_required', match.group(),
                expected='可用性声明带有未绑定的主体或时间语境，需复核。')
            continue
        available = match['state'] in ('已取得', '可用')
        for label in LABEL_RE.finditer(match['labels']):
            semantic = _semantics(LABEL_METRICS[label.group()])
            fact, ref = witness(semantic, match.group())
            if fact is None:
                continue
            if (fact.availability == 'available') != available:
                add('fact_claim_availability_mismatch', 'factual_support', match.group(), ref=ref)
            else:
                witnesses.append({'path': path + '.text', 'ref': ref, 'span_text': match.group(),
                                  **fact.model_dump(mode='json')})

    for match in re.finditer(r'(?:欺诈概率|置信度)(?:为|是|[:：])', text):
        if not _nonassertive(text, match.start()):
            add('fact_claim_probability_unsupported', 'unsupported_inference', match.group())
    if re.search(r'持卡人个人历史(?:存在|显示|有|为)', text):
        add('heuristic_identity_claim_requires_review', 'review_required', text)
    if re.search(r'(?:历史均值|历史统计)(?:缺失|未取得)[，,]?(?:说明|证明|因此|所以)(?:本笔|该笔|交易)*(?:安全|正常|低风险)', text):
        add('missing_evidence_cannot_establish_safety', 'unsupported_inference', text)

    diagnostic = text
    for start, end in sorted(set(masked), reverse=True):
        diagnostic = diagnostic[:start] + '待核数值' + diagnostic[end:]
    return StatementSupport(diagnostic, tuple(issues), tuple(witnesses), tuple(warnings))


def assess_report(value, passages, packet, bundle,*,numeric_formatter=None):
    diagnostic = deepcopy(value)
    issues, witnesses, warnings = [], [], []
    for path, item in passages:
        result = assess_statement(path, item['text'], item['refs'], packet, bundle,numeric_formatter=numeric_formatter)
        issues.extend(result.issues)
        witnesses.extend(result.witnesses)
        warnings.extend(result.warnings)
        # Gap impact fields also use this path; no extra model fields are accepted.
        parts = path.split('.')
        node = diagnostic
        for part in parts:
            node = node[int(part)] if isinstance(node, list) else node[part]
        if isinstance(node, dict):
            node['text'] = result.diagnostic_text
        else:
            parent = diagnostic
            for part in parts[:-1]:
                parent = parent[int(part)] if isinstance(parent, list) else parent[part]
            parent[parts[-1]] = result.diagnostic_text
    return diagnostic, issues, {'contract': VERSION, 'scope': SCOPE,
                                'witnesses': witnesses, 'warnings': warnings,
                                'unparsed_prose_requires_semantic_review': True}
