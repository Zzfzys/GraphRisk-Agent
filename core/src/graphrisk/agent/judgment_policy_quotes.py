"""Exact, provenance-bound policy quotation, not observed transaction facts.

Only the complete program-supplied sentence is exempt from numeric display
ownership. Free prose and all other fields keep the existing checks.
"""
from copy import deepcopy
from graphrisk.agent.upgrade_contract import digest


def policy_quotes(packet,bundle):
    if digest(bundle.model_dump(mode='json'))!=packet.get('source_evidence_sha256'):return {}
    result={}
    for p in bundle.policies:
        if p.policy_id!='transaction-velocity' or p.applicability!='unverified_conditions' or len(p.conditions)!=1:continue
        c=p.conditions[0]
        if (c.metric,c.subject,c.unit,c.window_seconds,c.operator,c.expected,c.measurement,c.fact_ref,c.observed,c.satisfied)!=(
            'card_attempt_count','target_card','attempts',600,'gte',4,'unmeasured',None,None,None):continue
        refs=[ref for ref,value in packet['refs_private'].items() if value==p.policy_ref]
        if len(refs)!=1:continue
        result[refs[0]]='政策要求同一卡片在目标交易前10分钟至少4次尝试；本案尝试次数尚未测量，不能认定满足条件。'
    return result


def policy_quote_diagnostics(item,packet,bundle):
    diagnostic=deepcopy(item);notes=[]
    for ref,text in policy_quotes(packet,bundle).items():
        if item=={'text':text,'refs':[ref]}:
            diagnostic['text']='该政策的尝试次数前提尚未测量，不能认定满足条件。'
            notes=[{'path':'policy','code':'verified_unmeasured_policy_quote',
                    'message':'政策条件已核对；本案未测，不能视为触发记录。','ref':ref}]
    return diagnostic,notes
