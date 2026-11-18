from copy import deepcopy

import pytest

from test_autonomous_tool_contracts import index,registry,scope,executor_for as source_executor
from test_daily_narrative import daily_scope
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_loop import observation_rows
from graphrisk.agent.autonomous_tools import ToolBoundaryError
from graphrisk.agent.frozen_daily_tools import executor_for
from graphrisk.agent.policy_retrieval import PolicyRetriever,write_policy_index


def envelope(daily_scope,index,registry):
    executor,_=source_executor(daily_scope,index)
    for name,args in [('get_transaction',{}),('get_transaction_features',{}),('get_risk_score',{}),
        ('get_related_entities',{'limit':50}),('find_risky_neighbors',{'limit':50}),
        ('retrieve_policy',{'focus':'manual_review','top_k':6})]:
        executor.execute(name,args)
    return {'observations':observation_rows(executor.observations),
        'evidence':build_evidence(daily_scope,executor.observations,registry).model_dump(mode='json')}


def test_query_replay_starts_empty_and_only_exposes_requested_evidence(tmp_path,daily_scope,index,registry):
    original=envelope(daily_scope,index,registry)
    retriever=PolicyRetriever(write_policy_index(index,tmp_path/'index.json'))
    executor=executor_for(daily_scope,original,registry,retriever,source_guard=lambda:None)
    assert executor.observations==()
    executor.execute('get_transaction',{})
    assert [item.call.name for item in executor.observations]==['get_transaction']
    executor.execute('get_related_entities',{'limit':1})
    bundle=build_evidence(daily_scope,executor.observations,registry)
    assert len(executor.observations)==2
    assert next(item for item in bundle.tool_outcomes if item.tool=='get_risk_score').status=='not_requested'
    assert next(item for item in bundle.facts if item.metric=='primary_model_score').value is None
    assert executor.execute('get_related_entities',{'limit':1}) is executor.observations[-1]
    assert original==envelope(daily_scope,index,registry)


@pytest.mark.parametrize('tamper',['missing_tool','value','maximum','future'])
def test_unbound_source_cannot_be_replayed(tmp_path,daily_scope,index,registry,tamper):
    original=envelope(daily_scope,index,registry);changed=deepcopy(original)
    if tamper=='missing_tool':changed['observations'].pop()
    elif tamper=='value':changed['evidence']['facts'][0]['value']=999999
    elif tamper=='maximum':
        row=next(row for row in changed['observations'] if row['name']=='get_related_entities')
        row['arguments']['limit']=1
    else:
        from graphrisk.agent.upgrade_contract import canonical
        import json
        row=next(row for row in changed['observations'] if row['name']=='get_related_entities')
        value=json.loads(row['result_json']);value['data']['matches'][0]['seconds_before_target']=0
        row['result_json']=canonical(value)
    retriever=PolicyRetriever(write_policy_index(index,tmp_path/'index.json'))
    with pytest.raises((ToolBoundaryError,ValueError)):
        executor_for(daily_scope,changed,registry,retriever,source_guard=lambda:None)
