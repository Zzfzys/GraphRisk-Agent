"""Bounded field patches over an immutable prior draft, never a full rewrite."""
from copy import deepcopy
from types import SimpleNamespace
import re
from typing import Any, Literal
from pydantic import Field, ValidationError
from graphrisk.agent.autonomous_tools import FrozenModel
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.upgrade_contract import canonical,digest


class FieldPatch(FrozenModel):
    path: str = Field(min_length=1,max_length=80)
    op: Literal['replace','remove']
    value: Any = None


class NarrativePatch(FrozenModel):
    base_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    changes: tuple[FieldPatch,...] = Field(min_length=1,max_length=12)


ROOTS={'fact_assessments','risk_level','basis','counterevidence','policy','limitation','next_check'}
PATH=re.compile(r'^(?:risk_level|limitation|version|fact_assessments(?:\.(?:relation_scope|graph_scope|amount_vs_24h_mean))?|(?:basis|counterevidence)(?:\.[01])?(?:\.(?:text|refs))?|(?:policy|next_check)(?:\.(?:text|refs))?)$')


def schema_issues(source,model=None):
    if model is None:
        from graphrisk.agent.narrative import NarrativeDraft
        model=NarrativeDraft
    try:model.model_validate_json(canonical(source),strict=True)
    except ValidationError as exc:
        rows=[]
        for error in exc.errors(include_input=False,include_url=False):
            loc=list(error['loc'])
            # Pydantic can report too_short after discarding an invalid item.
            # Keep the actual child defect; do not authorize replacing siblings.
            if len(loc)==1 and loc[0] in ('basis','counterevidence') and error['type'] in ('too_short','too_long'):
                original=source.get(loc[0])
                if isinstance(original,list) and 1<=len(original)<=2 and any(len(e['loc'])>1 and e['loc'][0]==loc[0] for e in exc.errors()):continue
            # An invalid refs element is repaired as one bounded refs field.
            if len(loc)>1 and loc[-2]=='refs':loc.pop()
            path='.'.join(map(str,loc))
            if not PATH.fullmatch(path):path='$'
            code=error['type']
            row={'path':path,'field':path,'code':code,'category':'schema',
                 'expected':'删除不允许的字段' if code=='extra_forbidden' else '修正指定子字段的类型或长度；句子与引用一起保持一致'}
            ctx=error.get('ctx',{})
            if code in ('too_long','too_short','string_too_long','string_too_short'):
                value=at(source,path)
                if isinstance(value,(str,list,tuple,dict)):row['actual']=len(value)
                if 'max_length' in ctx:row['max']=ctx['max_length']
                if 'min_length' in ctx:row['min']=ctx['min_length']
            rows.append(row)
        return rows
    return []


def safe_source(value):
    """Only bounded known-schema material may enter a model repair request."""
    if not isinstance(value,dict) or set(value)-ROOTS-{'version'}:return None
    if len(canonical(value).encode())>12000:return None
    def valid(v,depth=0):
        if depth>5:return False
        if isinstance(v,str):return len(v)<=512
        if isinstance(v,list):return len(v)<=12 and all(valid(x,depth+1) for x in v)
        if isinstance(v,dict):return len(v)<=12 and all(isinstance(k,str) and re.fullmatch(r'[a-z_][a-z0-9_]{0,39}',k) and valid(x,depth+1) for k,x in v.items())
        return v is None or type(v) in (int,float,bool)
    if not valid(value):return None
    if 'version' in value and (not isinstance(value['version'],str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',value['version'])):return None
    return deepcopy(value)


def at(source,path):
    node=source
    for part in path.split('.'):
        try:node=node[int(part)] if isinstance(node,list) else node[part]
        except (KeyError,IndexError,TypeError,ValueError):return None
    return node


def diagnostic_view(source):
    """Inspect bounded, correctly typed fragments without accepting an invalid draft.

    This view is only for error collection. It is never a publishable model and
    never replaces the immutable source or the final strict schema validation.
    """
    def item(value):
        if not isinstance(value,dict) or not isinstance(value.get('text'),str):return None
        refs=value.get('refs')
        return SimpleNamespace(text=value['text'],refs=tuple(r for r in refs if isinstance(r,str)) if isinstance(refs,list) else ())
    def items(name):
        values=source.get(name)
        return tuple(item(v) for v in values[:2]) if isinstance(values,list) else ()
    assessments=source.get('fact_assessments')
    assessments=assessments if isinstance(assessments,dict) else {}
    return SimpleNamespace(fact_assessments=SimpleNamespace(**{k:assessments.get(k) for k in ('relation_scope','graph_scope','amount_vs_24h_mean')}),
        risk_level=source.get('risk_level'),basis=items('basis'),counterevidence=items('counterevidence'),
        policy=item(source.get('policy')),next_check=item(source.get('next_check')),
        limitation=source.get('limitation') if isinstance(source.get('limitation'),str) else '')


def make_plan(source,packet,bundle):
    from graphrisk.agent.narrative import NarrativeDraft,narrative_issues
    source=safe_source(source)
    if source is None:raise ActionFailure('repair_source_unusable')
    issues=schema_issues(source)
    issues+=narrative_issues(diagnostic_view(source),packet,bundle)
    # A cited sentence is one repair unit: refs and prose must stay consistent.
    for issue in issues:
        issue.setdefault('field',issue['path'])
        issue['path']=re.sub(r'^(basis\.[01]|counterevidence\.[01]|policy|next_check)\.(text|refs)$',r'\1',issue['path'])
    if not issues or any(i['path']=='$' for i in issues):raise ActionFailure('repair_paths_unavailable')
    paths=sorted(set(i['path'] for i in issues))
    # A parent replacement already owns the child; never authorize overlaps.
    paths=[p for p in paths if not any(p.startswith(q+'.') for q in paths if p!=q)]
    if len(paths)>12:raise ActionFailure('repair_paths_limit')
    return {'version':'feedback-v6','base_sha256':digest(source),'source':source,
        'allowed_paths':paths,'issues':issues,'previous_fields':{p:at(source,p) for p in paths}}


def apply_patch_result(patch,plan):
    if patch.base_sha256!=plan['base_sha256'] or digest(plan['source'])!=plan['base_sha256']:
        raise ActionFailure('repair_base_changed')
    changes={c.path:c for c in patch.changes}
    if len(changes)!=len(patch.changes) or set(changes)!=set(plan['allowed_paths']):
        raise ActionFailure('repair_paths_changed')
    merged=deepcopy(plan['source'])
    for path,c in changes.items():
        if not PATH.fullmatch(path):raise ActionFailure('repair_path_invalid')
        if path=='version' or path in plan.get('removable_paths',()):
            if c.op!='remove' or c.value is not None:raise ActionFailure('repair_extra_field_must_be_removed')
            merged.pop(path,None);continue
        if c.op!='replace':raise ActionFailure('repair_required_field_cannot_be_removed')
        node=merged;parts=path.split('.')
        try:
            for part in parts[:-1]:node=node[int(part)] if isinstance(node,list) else node[part]
            if isinstance(node,list):node[int(parts[-1])]=c.value
            else:node[parts[-1]]=c.value
        except (KeyError,IndexError,TypeError,ValueError):raise ActionFailure('repair_parent_missing') from None
    return merged
