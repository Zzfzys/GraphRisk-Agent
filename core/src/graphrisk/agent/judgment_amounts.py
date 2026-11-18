"""Narrow, exact target-amount quotation; no general numeric prose exemption."""
from copy import deepcopy
from decimal import Decimal,InvalidOperation
import re
from graphrisk.agent.upgrade_contract import digest

# Only a full explicit target-transaction clause. Historical/neighbor amounts,
# currency, exponent notation, percentages and unsupported formats stay under
# the existing numeric guard. A supported span never masks adjacent prose.
AMOUNT=re.compile(r'(?:^|(?<=[，,。；;！？!?]))\s*(?:目标|该笔|本笔)交易金额(?:为|是)\s*(?P<amount>[-+]?[0-9]+(?:\.[0-9]+)?)(?=\s*(?:[，,。；;！？!?]|$))')
# Versioned v3 opt-in: explicit target ownership still required. Parenthetical
# citation markers may follow the amount, but currency/exponents/other numbers
# are NOT swallowed. Decimal/source/citation checks below remain identical.
NATURAL_AMOUNT=re.compile(r'(?:^|(?<=[，,。；;！？!?]))\s*(?:目标|该笔|本笔)(?:交易)?金额(?:为|是|[:：])?\s*(?P<amount>[-+]?[0-9]+(?:\.[0-9]+)?)(?=\s*(?:[，,。；;！？!?（(]|$))')
NOTE='金额已核对，与事实卡重复。'
# A finite currency recognizer, not a claim of general semantic validation.
CURRENCY=re.compile(r'人民币|美元|美金|港币|港元|欧元|日元|英镑|加元|澳元|[￥¥$€£]|\b(?:CNY|RMB|USD|HKD|EUR|JPY|GBP)\b|(?:币种|货币|单位)(?:是|为|[:：])\s*元|元为单位',re.I)


def target_amount(packet,bundle):
    if digest(bundle.model_dump(mode='json'))!=packet.get('source_evidence_sha256'):return None
    rows=[f for f in bundle.facts if f.metric=='transaction_amount']
    if len(rows)!=1:return None
    f=rows[0]
    if (f.subject,f.unit,f.window_seconds,f.temporal_scope,f.source_tool)!=(
        'target_transaction','dataset_amount_unit',None,'target_event','get_transaction'):return None
    if f.availability!='available' or f.missing_reason is not None or type(f.value) not in (int,float):return None
    if not Decimal(str(f.value)).is_finite():return None
    if f.model_version is not None or not re.fullmatch(r'[a-f0-9]{64}',f.source_version):return None
    outcomes=[o for o in bundle.tool_outcomes if o.tool==f.source_tool and o.query_ref==f.query_ref]
    aliases=[r for r,fid in packet['refs_private'].items() if fid==f.fact_id]
    if len(outcomes)!=1 or outcomes[0].status!='ok' or len(aliases)!=1:return None
    return f,aliases[0]


def amount_quote_diagnostics(value,packet,bundle,*,natural=False):
    """Return a diagnostic copy, hard issues and program-derived display notes.

    Original model text is never edited. Recognized amount spans, including
    incorrect ones, get a specific exact-source check instead of a conflicting
    generic display complaint. All other prose still faces legacy guards.
    """
    diagnostic=deepcopy(value);issues=[];notes=[];witness=target_amount(packet,bundle)
    pattern=NATURAL_AMOUNT if natural else AMOUNT
    for name in ('basis','counterevidence','policy'):
        items=value.get(name,[]) if name!='policy' else [value.get(name)]
        if not isinstance(items,list):continue
        for n,item in enumerate(items[:2]):
            if not isinstance(item,dict) or not isinstance(item.get('text'),str):continue
            path=f'{name}.{n}' if name!='policy' else name
            matches=list(pattern.finditer(item['text']))
            if not matches:continue
            refs=item.get('refs',[])
            for match in matches:
                code=None
                if witness is None:code='target_amount_source_unavailable'
                elif not isinstance(refs,list) or witness[1] not in refs:code='target_amount_citation_required'
                else:
                    try:equal=Decimal(match['amount'])==Decimal(str(witness[0].value))
                    except InvalidOperation:equal=False
                    if not equal:code='target_amount_value_mismatch'
                    elif CURRENCY.search(item['text']):code='target_amount_currency_unverified'
                if code:
                    row={'path':path+'.text','code':code,'category':'exact_amount_support',
                         'expected':'删除金额复述，或核对当前交易金额并引其原证据；不添币种。'}
                    if witness:row['required_refs']=[witness[1]]
                    if row not in issues:issues.append(row)
                else:
                    notes.append({'path':path+'.text','code':'verified_target_amount_repeated',
                                  'message':NOTE,'ref':witness[1],'literal':match['amount']})
            target=diagnostic[name][n] if name!='policy' else diagnostic[name]
            target['text']=pattern.sub('目标交易金额复述',item['text'])
    return diagnostic,issues,notes
