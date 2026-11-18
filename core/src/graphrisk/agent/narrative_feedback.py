"""Lossless actionable diagnostics for bounded narrative repair requests.

Repeated instructions are interned to save context, rather than discarded.
No inference about whether an arbitrary number is fabricated is made here.
"""
from copy import deepcopy
from graphrisk.agent.autonomous_actions import ActionFailure


def feedback_view(plan):
    rules=[];issues=[]
    for issue in plan['issues']:
        rule={k:issue[k] for k in ('code','expected','category') if k in issue}
        if rule not in rules:rules.append(rule)
        detail={k:deepcopy(v) for k,v in issue.items() if k not in ('path','code','expected','category')}
        detail.update(field=issue.get('field',issue['path']),rule=rules.index(rule))
        if detail not in issues:issues.append(detail)
    if not rules or not issues:raise ActionFailure('repair_diagnostics_missing')
    return {**{k:deepcopy(plan[k]) for k in ('version','base_sha256','allowed_paths','previous_fields')},
            'rules':rules,'issues':issues}
