"""Opt-in v12 input guidance; original schema, verifier and provider unchanged."""
from graphrisk.agent import judgment_v3 as base
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_tools import ToolBoundaryError
from graphrisk.agent.upgrade_contract import canonical,digest

GENERATION='v12-scope-citation-examples-v1'
RULES=base.RULES
CONTRACT=base.CONTRACT
JudgmentDraft=base.JudgmentDraft
JudgmentProvider=base.JudgmentProvider
verify_judgment=base.verify_judgment

EXAMPLES=(
    {'topic':'局部观察的用途','context':'以下是合成写法示意，不是本案事实；仅在本案证据支持时采用。',
     'avoid':'已返回样本中的关联很少，因此未见明显聚集。',
     'prefer':'返回样本提供共享字段线索，可帮助选择核查对象；共享字段本身不足以认定异常。',
     'source_roles':['对应共享字段计数','本次关联查询的范围说明']},
    {'topic':'一句涉及两类事实','context':'合成情景：存在金额差额，同时已测历史计数表明样本少。',
     'avoid':'只引用金额均值和波动，就声称历史样本很少。',
     'prefer':'金额差额值得核查；匹配历史样本少，使该基线的解释力有限。',
     'source_roles':['金额差额引用支持差额','对应窗口的历史计数引用支持样本少'],
     'limit':'只有本案计数支持时才能说样本少；零波动本身不能证明样本少。'},
    {'topic':'证据充分时保留有效解释','context':'合成情景：有可比历史统计，但缺少实际支付授权资料。',
     'prefer':'先说明金额比较能支持哪一项判断，再说明匿名匹配与授权未知怎样限制结论；下一步选择能取得新信息的核查。',
     'limit':'反证、假设或其他缺口可为空；已知事实不必改写成假设。'},
)

WRITING_PLAN=(
    '先写当前判断及主要依据，再说明最关键局限。只选一两条决定性线索，每条说明观察、判断作用和支持边界。'
    '一句涉及多项事实时按指标和窗口配齐引用；历史样本多少用计数，金额差额用差额，零波动不替代样本量。'
    '正文解释意义，精确数值由事实卡呈现；正确金额复述仍沿用原合同。'
    '反证须实际削弱某项疑点；其他原因明确未核实，无合适内容可留空。主缺口集中说明一次，gaps只补充不同缺口。'
    '核查要针对当前交易、能取得新信息，并说明结果用途；本轮只写建议，不冒充执行结果。'
)


def packet_for(scope,observations,registry,profile):
    if profile.get('report_input_presentation')!='plain-facts-v2-program-scope':
        raise ToolBoundaryError('v12_guidance_requires_plain_program_scope')
    packet=base.packet_for(scope,observations,registry,profile)
    bundle=build_evidence(scope,observations,registry)
    visible={row[0] for row in packet['view']['facts']}
    by_id={fact.fact_id:fact for fact in bundle.facts}
    refs={ref:by_id[fid] for ref,fid in packet['refs_private'].items() if ref in visible and fid in by_id}
    def scope_state(metric):
        facts=[fact for fact in bundle.facts if fact.metric==metric]
        if len(facts)!=1:raise ToolBoundaryError('v12_guidance_scope_metric_missing')
        fact=facts[0]
        if fact.availability!='available':return 'unknown'
        if type(fact.value) is not bool:raise ToolBoundaryError('v12_guidance_scope_state_invalid')
        return 'truncated' if fact.value else 'complete_for_registered_query'
    roles=[]
    for ref,fact in refs.items():
        if fact.metric in ('transaction_amount','history_transaction_count','history_amount_mean',
                           'history_amount_population_std','amount_minus_history_mean'):
            roles.append({'ref':ref,'metric':fact.metric,'window_seconds':fact.window_seconds,
                          'availability':fact.availability})
    guidance={'version':GENERATION,
        'relation_scope':{'refs':[ref for ref,f in refs.items() if f.metric=='returned_relation_type_count'],
            'measurement_scope':'returned_sample','query_coverage':scope_state('relations_truncated'),
            'global_clustering_measurement':'not_provided_by_registered_tools'},
        'graph_scope':{'query_coverage':scope_state('graph_neighbors_truncated'),
            'score_role':'experimental','global_clustering_measurement':'not_provided_by_registered_tools'},
        'citation_roles':roles,'examples':list(EXAMPLES),
        'examples_are_case_evidence':False,'example_text_is_not_an_output_template':True}
    packet['view']['generation_guidance']=guidance
    packet['view']['writing_plan']=WRITING_PLAN
    packet['generation_variant']=GENERATION
    packet['guidance_sha256']=digest(guidance)
    packet['view_sha256']=digest(packet['view'])
    packet['messages_sha256']=digest([base.PROMPT,canonical(packet['view'])])
    packet['prompt_utf8_bytes']=len((base.PROMPT+canonical(packet['view'])).encode('utf-8'))
    if packet['prompt_utf8_bytes']>profile['max_prompt_utf8_bytes']:
        raise ToolBoundaryError('v12_guidance_context_limit')
    return packet
