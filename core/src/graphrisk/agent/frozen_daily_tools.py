"""Query replay over measured sealed historical evidence; never seed the Agent.

Only the requested tool result enters its observations. Relation limits are
applied to the saved maximum sample; policies are queried in the frozen local
index. This mode measures investigation choices, not live database latency.
"""
from copy import deepcopy
import json

from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_loop import restore_observations
from graphrisk.agent.autonomous_tools import ReadOnlyToolExecutor, SPEC_BY_NAME, ToolBoundaryError


def executor_for(scope,envelope,registry,retriever,*,source_guard):
    if getattr(retriever,'provider',None) is not None:
        raise ToolBoundaryError('formal_replay_requires_local_policy_retrieval')
    observations=restore_observations(scope,envelope['observations'])
    if (len(observations)!=6 or {item.call.name for item in observations}!=set(SPEC_BY_NAME)
            or build_evidence(scope,observations,registry).model_dump(mode='json')!=envelope['evidence']):
        raise ToolBoundaryError('formal_replay_source_evidence_changed')
    source_guard()
    saved={item.call.name:json.loads(item.result_json) for item in observations}
    for item in observations:
        if item.call.name in ('get_related_entities','find_risky_neighbors') and json.loads(item.call.arguments_json)['limit']!=50:
            raise ToolBoundaryError('formal_replay_requires_maximum_relation_sample')
    if any(row['status']!='ok' for row in saved.values()):
        raise ToolBoundaryError('formal_replay_requires_available_source_tools')

    def value(name,request):
        row=deepcopy(saved[name])
        if name=='get_related_entities':
            data=row['data'];data['matches']=data['matches'][:request['limit']]
            data['truncated']=data['total_matches']>len(data['matches'])
            identifiers={item['transaction_id'] for item in data['matches']}
            row['evidence']=[item for item in row['evidence'] if item['source_kind']!='entity_link' or item['record_id'] in identifiers]
        elif name=='find_risky_neighbors':
            row['data']['neighbors']=row['data']['neighbors'][:request['limit']]
            identifiers={item['transaction_id'] for item in row['data']['neighbors']}
            row['evidence']=[item for item in row['evidence'] if item['source_kind']!='entity_link' or item['record_id'] in identifiers]
        return SPEC_BY_NAME[name].result.model_validate(row)

    backends={name:(lambda request,name=name:value(name,request)) for name in SPEC_BY_NAME if name!='retrieve_policy'}
    backends['retrieve_policy']=lambda request:retriever.retrieve_policy(request['query'],request['policy_as_of'],request['top_k'])
    return ReadOnlyToolExecutor(scope,backends,source_guard)
