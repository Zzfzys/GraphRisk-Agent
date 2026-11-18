from copy import deepcopy
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from graphrisk.agent.autonomous_policy import PolicyRegistry
from graphrisk.agent.autonomous_tools import DailyInvestigationScope, DailyScoringContext, ToolBoundaryError
from graphrisk.agent.policy_corpus import load_policy_corpus
from graphrisk.agent.policy_retrieval import PolicyRetriever, build_policy_index, write_policy_index
from graphrisk.data.contracts import build_identity_key
from graphrisk.investigation.contracts import RiskyNeighborsData
from graphrisk.models.champion_daily_features import normalized_raw
from graphrisk.serving.daily_batch_store import DailyBatchStore
from graphrisk.serving.daily_investigation_tools import DailyInvestigationTools, GRAPH_RELATION_VERSION
from test_autonomous_action_providers import profile
from test_daily_batch_store import candidate, event


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def policies(tmp_path):
    index = build_policy_index(load_policy_corpus(ROOT/'data/policies/stage05'))
    index_path = write_policy_index(index, tmp_path/'policy.json')
    registry = PolicyRegistry.from_file(index, ROOT/'configs/agent_investigation_policy_requirements.json')
    return PolicyRetriever(index_path), registry


def make_scope(row, tools):
    context = DailyScoringContext.model_validate({key: row[key] for key in
        DailyScoringContext.model_fields if key in row})
    return DailyInvestigationScope(case_alias='a'*32, transaction_id=row['transaction_id'],
        dataset_source='ieee_cis', target_event_second=row['event_second'],
        policy_as_of=date(2026, 11, 6), transaction_revision=row['sealed_content_sha256'],
        feature_revision=row['feature_protocol_sha256'], model_revision=row['model_sha256'],
        primary_model_version=row['model_version'], graph_model_version=GRAPH_RELATION_VERSION,
        policy_index_version=tools.registry.index_version, scoring_context=context,
        investigation_revision=tools.revision(row))


def make_store(tmp_path, *, history=False):
    service = candidate(tmp_path)
    options = dict(history_cutoff_second=86399 if history else 0, scratch=tmp_path/'work')
    if history:
        old = event('bound-history', 100, 3)
        future = event('hidden-history', 400000, 999)
        pd.DataFrame([dict(transaction_id=item.transaction_id,
            source_transaction_dt_seconds=item.source_transaction_dt_seconds,
            **normalized_raw(item, service.contract)) for item in (old, future)]).to_parquet(
                service.paths['source'], index=False)
        options['history'] = service.paths['source']
    return DailyBatchStore(tmp_path/'journal.sqlite', service, **options)


def test_measured_history_excludes_equal_seconds_future_days_and_late_events(tmp_path, policies, profile):
    store = make_store(tmp_path, history=True)
    # Current-day arrivals may arrive after day end but before this seal. The
    # evidence event cutoff remains strictly earlier than this target.
    for item in (event('prior', 86401, 10), event('target', 86402, 30),
                 event('same-second', 86402, 100), event('future-day', 172801, 999)):
        store.ingest(item, received_second=200000)
    store.seal(1, available_through_second=200000)
    retriever, registry = policies
    tools = DailyInvestigationTools(store, retriever=retriever, registry=registry)
    row = store.resolve_transaction('target')
    scope = make_scope(row, tools)
    executor, observations, bundle = tools.register(scope, row)
    features = executor.execute('get_transaction_features', {}).result().data
    assert (features.tx_count_last_1h, features.tx_count_last_24h,
            features.tx_count_last_7d) == (1, 2, 2)
    assert features.amount_mean_last_24h == 6.5
    assert features.amount_std_last_24h == 3.5
    assert features.amount_deviation_last_24h == 23.5
    relations = executor.execute('get_related_entities', {}).result().data
    assert [item.transaction_id for item in relations.matches] == ['prior', 'bound-history']
    assert relations.total_matches == 2
    graph = executor.execute('find_risky_neighbors', {}).result().data
    assert graph.score_role == 'relation_only'
    assert graph.target_graph_risk_score is None
    assert all(item.graph_risk_score is None and item.evidence_role == 'relation_only'
               for item in graph.neighbors)
    graph_result = executor.execute('find_risky_neighbors', {}).result()
    assert graph_result.evidence[0].source_kind == 'relation_snapshot'
    assert {outcome.status for outcome in bundle.tool_outcomes} == {'ok'}
    score = next(fact for fact in bundle.facts if fact.metric == 'primary_model_score')
    assert score.temporal_scope == 'relative_day_end' and score.value == row['score']
    missing = next(fact for fact in bundle.facts if fact.metric == 'experimental_graph_score')
    assert missing.availability == 'unavailable'
    assert missing.missing_reason == 'outside_independent_score_scope'
    assert all(fact.source_version == scope.investigation_revision for fact in bundle.facts
               if fact.source_tool in ('get_transaction_features', 'get_related_entities', 'find_risky_neighbors'))
    assert bundle.policies and all(policy.source_type == 'simulated' for policy in bundle.policies)
    assert bundle.policy_as_of == '2026-11-06'
    assert all(policy.applicability != 'measured_conditions_only' for policy in bundle.policies
               if policy.purpose == 'trigger_condition')
    original = bundle.model_dump_json()
    store.ingest(event('late-prior', 86400, 999), received_second=300000)
    store.seal(2, available_through_second=300000)
    _, _, after = tools.register(scope, row)
    assert after.model_dump_json() == original
    assert len(observations) == 6
    for private in ('target', 'same-second', 'future-day', 'bound-history', 'identity_key', 'is_fraud'):
        assert '"'+private+'"' not in bundle.model_dump_json()
    from graphrisk.agent.fact_support import attach_contract, source_issues
    from graphrisk.agent.report_generation_profile import quality_report_profile
    from graphrisk.serving.daily_narrative import prepare_daily_narrative
    packet = prepare_daily_narrative(
        scope, observations, registry, quality_report_profile(profile))
    attach_contract(packet, bundle)
    assert source_issues(packet, bundle) == []
    tampered = deepcopy(packet)
    tampered['scope_private']['investigation_revision'] = 'f'*64
    attach_contract(tampered, bundle)
    assert source_issues(tampered, bundle)[0]['code'] == 'fact_support_source_invalid'


def test_identity_matches_canonical_constructor_and_unknown_entities_do_not_link(tmp_path, policies):
    store = make_store(tmp_path)
    first = event('first', 86401, 7)
    second = event('target', 86402, 11)
    for item in (first, second):
        item.raw_features.update({name: None for name in ('card1', 'addr1', 'P_emaildomain')})
        item.card_id = 'unknown-card-'+item.transaction_id
        item.address_id = 'unknown-address-'+item.transaction_id
        item.email_domain = 'unknown-email-'+item.transaction_id
        store.ingest(item, received_second=100000)
    store.seal(1, available_through_second=172800)
    tools = DailyInvestigationTools(store, retriever=policies[0], registry=policies[1])
    row = store.resolve_transaction('target')
    scope = make_scope(row, tools)
    executor, _, bundle = tools.register(scope, row)
    assert executor.execute('get_related_entities', {}).result().data.total_matches == 0
    assert executor.execute('get_transaction_features', {}).result().data.tx_count_last_7d == 0
    with store.investigation_snapshot(1) as (_, db):
        from graphrisk.serving.daily_investigation_tools import _canonical_source
        _canonical_source(db)
        identity = db.execute("SELECT identity_key FROM investigation_source WHERE transaction_id='target'").fetchone()[0]
    assert identity == build_identity_key(second.card_id, second.address_id, second.email_domain)
    mean = next(fact for fact in bundle.facts if fact.metric == 'history_amount_mean')
    assert mean.value is None and mean.missing_reason == 'no_history_statistic'


def test_seal_source_corruption_scope_changes_and_policy_mutation_fail_closed(tmp_path, policies):
    store = make_store(tmp_path)
    store.ingest(event('target', 86402), received_second=100000)
    store.seal(1, available_through_second=172800)
    tools = DailyInvestigationTools(store, retriever=policies[0], registry=policies[1])
    row = store.resolve_transaction('target')
    scope = make_scope(row, tools)
    with pytest.raises(ToolBoundaryError, match='binding_mismatch'):
        tools.register(scope.model_copy(update={'investigation_revision': 'b'*64}), row)
    with store._connect() as conn:
        conn.execute("UPDATE events SET received_second=received_second+1 WHERE transaction_id='target'")
    with pytest.raises(ValueError, match='source snapshot changed'):
        tools.register(scope, row)
    policies[0].index_path.write_text('{}', encoding='utf-8')
    with pytest.raises(ToolBoundaryError, match='source_binding_changed'):
        tools.revision(row)


def test_relation_only_graph_schema_rejects_scores_and_scored_neighbors():
    base = dict(transaction_id='t', neighbors=[], graph_model_version=GRAPH_RELATION_VERSION,
                score_role='relation_only')
    RiskyNeighborsData.model_validate(base)
    for changes in ({'target_graph_risk_score': .1}, {'fusion_score': .1},
                    {'neighbors': [dict(transaction_id='h', graph_risk_score=.2,
                        seconds_before_target=1, shared_entities=[dict(entity_type='card', entity_id='c')])]}):
        with pytest.raises(ValidationError):
            RiskyNeighborsData.model_validate({**base, **changes})
    # The previous experimental role still requires a measured target GNN score.
    with pytest.raises(ValidationError):
        RiskyNeighborsData.model_validate({**base, 'score_role': 'experimental'})


def test_relation_counts_and_limits_are_separate_from_graph_score_coverage(tmp_path, policies):
    store = make_store(tmp_path)
    for index in range(52):
        store.ingest(event(str(index), 86401+index), received_second=100000)
    store.seal(1, available_through_second=172800)
    tools = DailyInvestigationTools(store, retriever=policies[0], registry=policies[1])
    row = store.resolve_transaction('51')
    scope = make_scope(row, tools)
    executor, _, _ = tools.register(scope, row)
    relations = executor.execute('get_related_entities', {'limit': 50}).result().data
    assert relations.total_matches == 51 and len(relations.matches) == 50 and relations.truncated
    graph = executor.execute('find_risky_neighbors', {'limit': 1}).result().data
    assert len(graph.neighbors) == 1 and graph.neighbors[0].evidence_role == 'relation_only'


def test_full_agent_registration_does_not_prefetch_evidence(tmp_path, policies):
    store = make_store(tmp_path)
    store.ingest(event('target', 86402), received_second=100000)
    store.seal(1, available_through_second=172800)
    tools = DailyInvestigationTools(store, retriever=policies[0], registry=policies[1])
    row = store.resolve_transaction('target')
    scope = make_scope(row, tools)
    executor, observations, bundle = tools.register(scope, row, prefetch=False)
    assert observations == ()
    assert all(outcome.status == 'not_requested' for outcome in bundle.tool_outcomes)
    assert not bundle.policies
    executor.execute('get_transaction', {})
    assert [item.call.name for item in executor.observations] == ['get_transaction']
    executor.execute('get_transaction_features', {})
    executor.execute('get_risk_score', {})
    assert len(executor.observations) == 3
    assert executor.execute('get_risk_score', {}).result().data.risk_score == row['score']
    assert len(executor.observations) == 3
    with pytest.raises(ToolBoundaryError, match='invalid_daily_tool_prefetch'):
        tools.register(scope, row, prefetch=1)
