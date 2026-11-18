"""Small authored case: API -> metered mock report -> task references -> export.

This verifies integration and idempotency, not real transaction or writing gains.
"""
from copy import deepcopy
from datetime import date

from fastapi.testclient import TestClient

from api.main import create_app
from test_autonomous_tool_contracts import scope, index, registry, executor_for
from test_autonomous_action_providers import profile
from test_daily_narrative import daily_scope, make_backend
from test_daily_conversation_backend import SealedStore
from test_public_references import source as public_source
from graphrisk.agent.public_references import PublicReferenceRetriever
from graphrisk.agent.reference_answers import ReferenceAnswerService
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.upgrade_contract import canonical
from graphrisk.demo.conversation_workbench import readable_report
from graphrisk.serving.conversation_store import MemoryConversationStore
from graphrisk.serving.conversations import ConversationService
from graphrisk.serving.daily_conversation_backend import DailyConversationBackend
from graphrisk.serving.daily_narrative import verify_daily_report


def test_formal_report_and_case_question_reach_api_export_without_extra_calls(
        tmp_path, daily_scope, index, registry, profile, public_source, monkeypatch):
    delegate, budget, _, posts = make_backend(tmp_path, daily_scope, index, registry, profile)
    source = SealedStore()
    source.row['model_version'] = 'xgb-v1'
    class FixtureTools:
        def __init__(self):
            self.registry = registry
        def revision(self, row):
            return 'f'*64
        def register(self, case_scope, row):
            def mutate(name, result):
                if name == 'get_transaction':
                    result['data']['source_transaction_dt_seconds'] = case_scope.target_event_second
                elif name == 'find_risky_neighbors':
                    result['data'].update(graph_model_version=case_scope.graph_model_version,
                                          target_graph_risk_score=None, score_role='relation_only')
                    # The authored day-end fixture supplies a relation snapshot,
                    # not the generic fixture's independently scored graph model.
                    result['evidence'][0].update(source_kind='relation_snapshot',
                                                record_id=case_scope.graph_model_version)
                    for neighbor in result['data']['neighbors']:
                        neighbor.update(graph_risk_score=None, evidence_role='relation_only',
                                        score_unavailable_reason='outside_independent_score_scope')
                return result
            executor, _ = executor_for(case_scope, index, mutate=mutate)
            for name in ('get_transaction', 'get_transaction_features', 'get_risk_score',
                         'get_related_entities', 'find_risky_neighbors', 'retrieve_policy'):
                executor.execute(name, {'focus': 'device_sharing', 'top_k': 6} if name == 'retrieve_policy' else {})
            bundle = build_evidence(case_scope, executor.observations, registry)
            return executor, executor.observations, bundle
    reference_service = ReferenceAnswerService(PublicReferenceRetriever(public_source[0]))
    monkeypatch.setenv('GRAPHRISK_PUBLIC_REFERENCE_MANIFEST', str(reference_service.retriever.manifest_path))
    monkeypatch.setenv('GRAPHRISK_PUBLIC_REFERENCE_VERSION', reference_service.retriever.expected_version)
    backend = DailyConversationBackend(source, policy_as_of=date(2026, 11, 13),
        evidence_tools=FixtureTools(), narrative_backend=delegate, case_reference_service=reference_service)
    service = ConversationService(MemoryConversationStore(), backend)
    body = {'transaction_id': '123', 'message': '生成风险报告', 'risk_profile': 'daily_batch',
            'batch_id': source.row['batch_id']}
    with TestClient(create_app(conversation_service_factory=lambda: service)) as client:
        response = client.post('/api/v1/conversations', json=body, headers={'Idempotency-Key': 'formal-integrated'})
        assert response.status_code == 201, response.text
        first = response.json()
        report = first['turns'][0]['report']
        assert report is not None and report['version'] == 'separated-risk-report-v12-daily-quotes-v2'
        bundle = backend.entries[first['case_alias']]['evidence']
        assert verify_daily_report(report, bundle) == report
        before = deepcopy(first)
        text = readable_report(first)
        assert report['draft']['rationale']['text'] in text and '本轮未调用大模型' not in text
        assert first == before and len(posts) == 1
        assert 'PRIVATE-DEVICE' not in canonical(first)
        assert '"transaction_id"' not in canonical(report) and '"target_event_second"' not in canonical(report)
        assert client.post('/api/v1/conversations', json=body,
            headers={'Idempotency-Key': 'formal-integrated'}).json() == first
        assert client.get('/api/v1/conversations/'+first['conversation_id']).json() == first
        follow = client.post('/api/v1/conversations/'+first['conversation_id']+'/turns', json={
            'parent_turn_id': first['turns'][0]['turn_id'], 'evidence_version': first['evidence']['evidence_version'],
            'message': '核对政策前提'}, headers={'Idempotency-Key': 'formal-policy-follow'})
        assert follow.status_code == 200, follow.text
        later = follow.json()
        assert later['turns'][0] == first['turns'][0]
        assert later['turns'][-1]['report_package'] != later['turns'][0]['report_package']
        assert later['turns'][-1]['actual_model_calls'] == 0 and len(posts) == 1
        assert later['reserved_model_calls'] == 1
        assert report['draft']['rationale']['text'] in readable_report(later)
        stored = service.store.values[first['conversation_id']]['turns'][0]['report']
        stored['draft']['rationale']['text'] = '被篡改的原稿，不能继续展示。'
        rejected = client.get('/api/v1/conversations/'+first['conversation_id'])
        assert rejected.status_code == 409
        assert rejected.json()['error_code'] == 'daily_report_binding_changed'
        assert len(posts) == 1
