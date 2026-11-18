"""Stage09 reports recover without retaining source graphs in SQL."""
import os
from pathlib import Path
import pytest
from graphrisk.agent.approval import ApprovalDecision
from graphrisk.serving.investigation_service import ServiceError
from graphrisk.serving.postgres_service import PostgresInvestigationService
from graphrisk.serving.postgres_store import PostgresStore

DSN = os.getenv('GRAPHRISK_STAGE07_DSN')
pytestmark = pytest.mark.skipif(not DSN, reason='isolated GRAPHRISK_STAGE07_DSN is required')


@pytest.fixture
def graph_run(tmp_path, monkeypatch):
    # Reuse existing deterministic unit fixtures, including when this file runs alone.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'unit'))
    from test_stage05_reporting import graph_run as factory
    return factory.__wrapped__(tmp_path)


@pytest.fixture(scope='module', autouse=True)
def migrated():
    PostgresStore(DSN).migrate()


@pytest.mark.parametrize('quality', [False, True])
@pytest.mark.parametrize('unavailable', [False, True])
def test_decision_and_quality_recover_then_reject_without_regeneration(graph_run, quality, unavailable):
    from test_stage06_service import FakeRunner
    from test_stage09_deployment import modern_result
    from test_stage09_persisted_validation import quality_result
    result, versions = (quality_result if quality else modern_result)(graph_run, unavailable)
    runner = FakeRunner(result)
    kwargs = dict(dsn=DSN, dataset_source='synthetic', versions=versions, runner=runner, max_runs=1000)
    first = PostgresInvestigationService(**kwargs)
    created = first.run(result.graph_run.request)
    stored = first.store.row(created.run_id)['record_json']
    assert stored['result']['graph_run'] is None and stored['persisted_validation']['result_sha256']
    first.close()
    second = PostgresInvestigationService(**kwargs)
    restored = second.get(created.run_id)
    assert restored.result.graph_run is None
    assert restored.result.report_run.report == created.result.report_run.report
    assert restored.report_sha256 == created.report_sha256
    if unavailable:
        assert restored.approval is None and restored.session_id is None
    else:
        decision = ApprovalDecision(session_id=restored.session_id,
            transaction_id=restored.request.transaction_id, report_sha256=restored.report_sha256,
            decision='reject', reviewer_id='isolated-reviewer', note='mechanism check')
        rejected = second.decide(restored.run_id, decision)
        assert rejected.approval.status == 'rejected' and rejected.approval.external_action_count == 0
        assert second.get(restored.run_id).approval.status == 'rejected'
    assert runner.calls == 1
    second.close()


@pytest.mark.parametrize('unavailable', [False, True])
def test_modern_record_without_source_receipt_is_not_recovered(graph_run, unavailable):
    from test_stage06_service import FakeRunner
    from test_stage09_persisted_validation import quality_result
    result, versions = quality_result(graph_run, unavailable)
    service = PostgresInvestigationService(dsn=DSN, dataset_source='synthetic', versions=versions,
        runner=FakeRunner(result), max_runs=1000)
    created = service.run(result.graph_run.request)
    with service.store.connect() as conn:
        conn.execute("UPDATE investigation_runs SET record_json = record_json - 'persisted_validation' WHERE run_id = %s", (created.run_id,))
    with pytest.raises(ServiceError, match='model_decision_binding_mismatch'): service.get(created.run_id)
    service.close()
