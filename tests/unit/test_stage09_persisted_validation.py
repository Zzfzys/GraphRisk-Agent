"""A persisted attestation cannot replace live source checks or hide edits."""
import hashlib
import pytest
from graphrisk.serving.investigation_service import PersistedValidation, ServiceError, Stage06Service
from graphrisk.agent.quality_reporting import QualityReportWorkflow
from graphrisk.agent.quality_contracts import QUALITY_VERSION
from test_stage05_reporting import graph_run
from test_stage06_service import FakeRunner, REQUEST
from test_stage09_agent_quality import FixtureProvider
from test_stage09_deployment import modern_result


def quality_result(factory, unavailable=False):
    result, versions = modern_result(factory, unavailable)
    result.report_run = QualityReportWorkflow(FixtureProvider()).run(result.graph_run)
    result.status = result.report_run.status
    result.audit.model_id = 'quality-fixture'
    versions.generation_model_id = 'quality-fixture'
    versions.report_protocol = QUALITY_VERSION
    return result, versions


def test_receipt_restores_exact_validated_result_without_raw_graph(graph_run):
    result, versions = quality_result(graph_run)
    service = Stage06Service(dataset_source='synthetic', versions=versions, runner=FakeRunner(result))
    request = result.graph_run.request
    assert service._validate_result(request, result)
    receipt = PersistedValidation.for_validated_result(request, versions, result)
    restored = result.model_copy(update={'graph_run': None})
    assert service._validate_result(request, restored, persisted_validation=receipt) == result.report_run.audit.report_sha256
    with pytest.raises(ServiceError, match='model_decision_binding_mismatch'):
        service._validate_result(request, restored)
    service.close()


@pytest.mark.parametrize('alter', ['request', 'versions', 'audit', 'analysis'])
def test_persisted_receipt_detects_changes_even_with_recomputed_report_hash(graph_run, alter):
    result, versions = quality_result(graph_run)
    request = result.graph_run.request
    receipt = PersistedValidation.for_validated_result(request, versions, result)
    restored = result.model_copy(deep=True, update={'graph_run': None})
    if alter == 'request': request = request.model_copy(update={'transaction_id': 'OTHER'})
    elif alter == 'versions': versions = versions.model_copy(update={'generation_model_id': 'OTHER'})
    elif alter == 'audit': restored.audit.prompt_tokens = 999
    else:
        restored.report_run.report.analysis.statements[0] = '已证实欺诈'
        restored.report_run.audit.report_sha256 = hashlib.sha256(restored.report_run.report.model_dump_json().encode()).hexdigest()
    service = Stage06Service(dataset_source='synthetic', versions=versions, runner=FakeRunner(result))
    with pytest.raises(ServiceError, match='persisted_validation_mismatch'):
        service._validate_result(request, restored, persisted_validation=receipt)
    service.close()


def test_memory_creation_still_demands_live_graph_and_checks_original_analysis(graph_run):
    result, versions = quality_result(graph_run)
    service = Stage06Service(dataset_source='synthetic', versions=versions,
        runner=FakeRunner(result.model_copy(update={'graph_run': None})))
    with pytest.raises(ServiceError, match='model_decision_binding_mismatch'): service.run(REQUEST)
    assert not service._records
    service.close()
