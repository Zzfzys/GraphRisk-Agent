"""Bounded claim recognition with typed evidence witnesses, not general NLI."""
from collections import defaultdict
from itertools import combinations
import re
from graphrisk.agent.autonomous_evidence import LAYOUT

RULES_VERSION='typed-citation-support-v2-exact-amount'

# Recognize one declared proposition family. We do not infer arbitrary Chinese
# claims, and citation existence alone never supplies the required witnesses.
MULTI_TYPE=re.compile(r'(?:多类|多种|不同类型|不同种类)(?:的)?(?:共享|关联)?实体|(?:共享|关联)实体(?:类型|种类)(?:多样|不止一种)')
NON_ASSERTIVE=re.compile(r'(?:是否|(?:尚未|无法|不能|尚不能)(?:确认|证明|判断)(?:是否)?|(?:建议|需要|需|待)(?:人工)?(?:核查|核实|确认)(?:是否)?)(?:当前|本次)?(?:存在|出现|返回|涉及|含有)?$')
DEFER_SCOPE=re.compile(r'(?:图邻居|图查询|邻居)(?:的)?(?:是否(?:被)?截断|截断(?:状态|标记|信息)?|覆盖范围|返回范围|完整性)\s*[，,]?\s*(?:由程序(?:段)?(?:单独)?(?:说明|展示)|以程序(?:段)?(?:的)?说明为准)')
SCOPE_REMAINDER=re.compile(r'截断|完整性|完整返回|返回部分|返回范围|覆盖范围')


def scope_deferral_only(text):
    """Only exact nonassertive deferrals; mixed assertions are never exempted.

    This changes a misleading factual diagnostic, not report acceptance:
    program-owned scope prose remains prohibited by the judgment contract.
    """
    rest,count=DEFER_SCOPE.subn('',text)
    return bool(count) and not SCOPE_REMAINDER.search(rest)


def asserts_multiple_entity_types(text):
    for clause in re.split(r'[。；;，,！？!?]|但是|然而|但',text):
        for match in MULTI_TYPE.finditer(clause):
            if not NON_ASSERTIVE.search(clause[:match.start()]):return True
    return False


def type_witness_sets(bundle,packet,refs=None):
    """Pairs of positive distinct types from the same query and source version.

    Metric, subject, unit, window and temporal scope must match canonical tool
    semantics. Duplicate/ambiguous subject rows do not count as witnesses.
    """
    layouts={row for row in LAYOUT['get_related_entities'] if row[0]=='returned_relation_type_count'}
    aliases=defaultdict(list)
    for alias,fid in packet['refs_private'].items():aliases[fid].append(alias)
    groups=defaultdict(lambda:defaultdict(list))
    for f in bundle.facts:
        semantic=(f.metric,f.subject,f.unit,f.window_seconds,f.temporal_scope)
        if f.source_tool=='get_related_entities' and semantic in layouts:
            groups[(f.query_ref,f.source_version)][f.subject].append(f)
    pairs=set()
    for subjects in groups.values():
        valid=[]
        for rows in subjects.values():
            if len(rows)!=1:continue
            f=rows[0]
            if f.availability!='available' or type(f.value) is not int or f.value<=0 or len(aliases[f.fact_id])!=1:continue
            alias=aliases[f.fact_id][0]
            if refs is None or alias in refs:valid.append(alias)
        pairs.update(tuple(sorted(pair)) for pair in combinations(valid,2))
    return [list(pair) for pair in sorted(pairs)]


def statement_support_issues(path,text,refs,packet,bundle):
    if not asserts_multiple_entity_types(text) or type_witness_sets(bundle,packet,refs):return []
    candidates=type_witness_sets(bundle,packet)
    issue={'path':path+'.refs','code':'claim_missing_relation_type_support',
           'expected':'保留多类主张须引同查询两种非零类型，否则删除。',
           'category':'typed_citation_support'}
    if candidates:issue['required_ref_sets']=candidates
    else:issue['expected']='没有同查询两种非零类型证据，删除多类主张；总数和图分数不能替代。'
    return [issue]
