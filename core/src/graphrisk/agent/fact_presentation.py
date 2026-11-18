"""Deterministic fact explanations shared by API reports and the workbench."""
from decimal import Decimal, ROUND_HALF_EVEN

NUMERIC_DISPLAY_VERSION='fact-numeric-display-v1'


def numeric_display(metric,value):
    """Candidate-only canonical display; raw monetary facts are never rounded."""
    if type(value) not in (int,float) or not Decimal(str(value)).is_finite():
        raise ValueError('numeric_display_requires_finite_number')
    counts={'history_transaction_count','returned_relation_type_count','total_related_count','returned_related_count',
            'returned_graph_neighbor_count','scored_graph_neighbor_count','relation_only_graph_neighbor_count'}
    if type(value) is int and metric in counts:return format(value,',')
    places=(3 if metric in ('primary_model_score','experimental_graph_score') else
            2 if metric=='amount_minus_history_mean' else None)
    if places is None:return str(value)
    return format(Decimal(str(value)).quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_EVEN),'f')

METRICS={'graph_neighbors_truncated':'图查询是否只返回一部分','transaction_amount':'交易金额','primary_model_score':'XGBoost 主评分','experimental_graph_score':'GraphSAGE 实验分数',
 'history_transaction_count':'按匿名身份规则匹配的历史交易数','history_amount_mean':'历史平均金额','history_amount_population_std':'历史金额波动',
 'amount_minus_history_mean':'当前金额与历史均值之差','total_related_count':'符合共享字段规则的历史交易总数','returned_related_count':'本次返回关联交易数',
 'relations_truncated':'关联查询是否只返回一部分','returned_relation_type_count':'该类实体关联交易数','returned_graph_neighbor_count':'图查询返回邻居数',
 'scored_graph_neighbor_count':'取得图分数的邻居数','relation_only_graph_neighbor_count':'仅有关系线索的邻居数'}


def fact_text(fact,*,numeric_formatter=None):
    name=METRICS.get(fact['metric'],'其他证据指标')
    if fact['metric']=='primary_model_score' and fact.get('temporal_scope')=='relative_day_end':
        name='日终 XGBoost 主评分'
    if fact['metric']=='returned_relation_type_count':
        entity={'returned_shared_card_history':'匿名卡片字段','returned_shared_device_history':'设备字段',
                'returned_shared_email_domain_history':'邮箱域字段','returned_shared_address_history':'地址字段'}.get(fact.get('subject'),'该类字段')
        name='本次已返回样本中共享'+entity+'的交易数'
    window={3600:'此前1小时',86400:'此前24小时',604800:'此前7天'}.get(fact.get('window_seconds'),'')
    if fact['availability']!='available' or fact.get('value') is None:
        if fact.get('missing_reason') == 'outside_independent_score_scope':
            return f'{name}：当前交易不在独立图评分覆盖范围；只有关系线索，没有图风险分数'
        return f'{name} {window}：没有取得这项数据，不能当成0，也不能据此判断安全'
    value=fact['value']
    if fact['metric'] in ('relations_truncated','graph_neighbors_truncated'):
        if value is True:return name+'：只返回了一部分，尚未查看全部结果'
        if value is False:return name+'：工具标记本次结果未因返回上限而省略；不表示数据源覆盖所有现实交易'
        return name+'：标记格式异常，无法确认是否完整'
    if type(value) is bool:value='是' if value else '否'
    elif numeric_formatter is not None:value=numeric_formatter(fact['metric'],value)
    text=f'{name} {window}：{value}'
    if fact['metric'] in ('primary_model_score','experimental_graph_score'):
        text+='；未校准信号，不是已验证的欺诈概率'
    if fact.get('temporal_scope')=='relative_day_end':
        text+='；使用封批时可用数据，不能称为交易发生时的即时评分'
    return text


def relation_summary(evidence):
    facts={f['metric']:f for f in evidence['facts'] if f['metric'] in ('total_related_count','returned_related_count','relations_truncated')}
    def count(metric):
        f=facts.get(metric,{});value=f.get('value')
        return format(value,',') if f.get('availability')=='available' and type(value) is int and value>=0 else '未知'
    flag=facts.get('relations_truncated',{})
    partial=flag.get('value') if flag.get('availability')=='available' else None
    scope='只返回了一部分，不能把返回数量当成总数。' if partial is True else ('工具标记本次结果未因返回上限而省略；仍受数据源与查询条件限制。' if partial is False else '是否返回完整尚不清楚，不能默认已查看全部。')
    return f"按当前关联规则共找到 {count('total_related_count')} 笔历史交易，本次返回 {count('returned_related_count')} 笔。"+scope


def program_presentation(bundle,packet,*,numeric_formatter=None):
    """Accept only the bound EvidenceBundle, never a model-supplied fact table."""
    from graphrisk.agent.upgrade_contract import digest
    from graphrisk.agent.autonomous_tools import ToolBoundaryError
    from graphrisk.agent.narrative import expected_fact_assessments
    if digest(bundle.model_dump(mode='json'))!=packet['source_evidence_sha256']:
        raise ToolBoundaryError('narrative_source_changed')
    reverse={full:alias for alias,full in packet['refs_private'].items()}
    facts=[{'ref':reverse[f.fact_id],'fact_id':f.fact_id,'text':fact_text(f.model_dump(mode='json'),numeric_formatter=numeric_formatter)} for f in bundle.facts if f.fact_id in reverse]
    policies=[]
    for p in bundle.policies:
        if p.policy_ref not in reverse:continue
        conditions=[]
        for c in p.conditions:
            seconds=c.window_seconds
            window=f'{seconds//60}分钟' if seconds and seconds%60==0 else (f'{seconds}秒' if seconds else '指定范围')
            threshold=f"{'至少' if c.operator=='gte' else '等于'}{c.expected} {c.unit}"
            measurement='已测量' if c.measurement=='measured' else '未测量，不能认定触发'
            conditions.append(f'{c.metric} / {c.subject} / {window} / {threshold}：{measurement}')
        policies.append({'ref':reverse[p.policy_ref],'text':p.text,'conditions':conditions,'applicability':p.applicability,'source_type':p.source_type,'source':'program_from_policy'})
    states=expected_fact_assessments(bundle)
    labels={'partial':'工具标记仅返回部分结果','not_truncated':'工具未因返回上限省略；仍受数据源限制','unknown':'截断状态未知，不能断言完整或仅返回部分'}
    limitations=[f"关联查询：{labels[states['relation_scope']]}。",f"图邻居查询：{labels[states['graph_scope']]}。",
        '主评分未校准，图分数实验性且不融合；共享匿名字段不证明同一客户或欺诈团伙。',
        '政策命中不等于条件成立；核查建议尚未执行。']
    if states['amount_vs_24h_mean']=='unknown':limitations.append('无法取得所需历史统计，当前金额与此前24小时均值的比较未知。')
    if bundle.scoring_context is not None:
        limitations.append('日终关联特征使用相对日结束前发生、截至封批水位已接收的数据；不代表交易发生时已经可用。匿名源字段的实际产生时间仍未核实。')
        if 'strict_event_history_within_sealed_arrivals_not_event_time_arrival_history' in bundle.limitations:
            limitations.append('历史调查按事件严格早于目标查询，但记录截至封批水位才确认已收到；不能声称交易发生时已全部可见。')
        if any(fact.missing_reason=='outside_independent_score_scope' for fact in bundle.facts):
            limitations[2]='主评分未校准，本次没有独立GraphSAGE分数；仅有历史共享字段线索，不证明同一客户或欺诈团伙。'
        if 'policy_as_of_server_selected_not_transaction_calendar' in bundle.limitations:
            limitations.append('政策日期是服务端指定的检索日期，不是已核实的交易日历日期；项目模拟政策不代表真实机构规则。')
    result={'source':'program','evidence_sha256':packet['source_evidence_sha256'],'facts':facts,'policies':policies,
            'relation_summary':relation_summary(bundle.model_dump(mode='json')),'fact_assessments':states,'limitations':limitations}
    if bundle.scoring_context is not None:
        result['scoring_context']=bundle.scoring_context.model_dump(mode='json')
    return result
