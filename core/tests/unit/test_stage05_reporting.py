"""Route 5 checks that generated selections cannot promote unsupported evidence."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from graphrisk.agent.config import Stage05Generation, Stage05Limits
from graphrisk.agent.policy_corpus import load_policy_corpus
from graphrisk.agent.policy_retrieval import PolicyHit, PolicyRetriever, build_policy_index, write_policy_index
from graphrisk.agent.reporting import (
    FakeReportProvider, OllamaReportProvider, ProviderResult, ReportDraft, Stage05ReportWorkflow,
    _eligible_hits, _prompt_payload,
)
from graphrisk.agent.workflow import Stage05GraphWorkflow
from graphrisk.investigation.contracts import FindRiskyNeighborsResult, ToolError
from graphrisk.investigation.workflow import CapabilityGap, InvestigationAudit, InvestigationReport, InvestigationRun


ROOT = Path(__file__).resolve().parents[2]


class Investigation:
    def __init__(self, report):
        self.report = report

    def run(self, request):
        return InvestigationRun(report=self.report, audit=InvestigationAudit(investigation_id="route5-fixture", events=[]))


class NoMatchPlanner:
    def plan(self, request, report, top_k):
        return {"tool": "retrieve_policy", "transaction_id": request.transaction_id,
                "dataset_source": request.dataset_source, "policy_as_of": request.policy_as_of,
                "query": "zzzxxyyqv", "top_k": top_k}


@pytest.fixture
def graph_run(tmp_path):
    report = InvestigationReport.model_validate_json((ROOT / "tests/fixtures/stage04_investigation_report.json").read_text(encoding="utf-8"))
    corpus = load_policy_corpus(ROOT / "data/policies/stage05")
    index = write_policy_index(build_policy_index(corpus), tmp_path / "index.json")
    limits = Stage05Limits(max_steps=12, max_model_calls=1, max_policy_queries=1, max_retries=0,
                           max_total_seconds=300, api_budget_cny=0, api_stop_cny=0)

    def make(selected_report=report, planner=None):
        workflow = Stage05GraphWorkflow(Investigation(selected_report), PolicyRetriever(index),
                                        dataset_source="synthetic", limits=limits, planner=planner)
        return workflow.run({"transaction_id": "T3", "dataset_source": "synthetic",
                             "policy_as_of": "2026-07-01", "relation_limit": 3})
    return make


def test_fake_report_quotes_only_current_source_and_keeps_stage04_evidence(graph_run):
    graph = graph_run()
    result = Stage05ReportWorkflow(FakeReportProvider()).run(graph)
    assert result.status == "verified" and result.report is not None
    assert result.report.transaction_amount == graph.investigation_report.transaction.data.transaction_amount
    assert result.report.model_score == graph.investigation_report.risk_score.data.risk_score
    assert result.report.model_version == graph.investigation_report.risk_score.data.model_version
    assert result.report.policy_conclusions[0].quote == graph.policy_retrieval.hits[0].chunk.text
    assert result.report.policy_conclusions[0].version == graph.policy_retrieval.hits[0].chunk.version
    assert result.report.validation_status == "source_quote_verified"
    assert result.report.approval_status == "not_requested"
    assert result.audit.investigation_id == graph.investigation_audit.investigation_id
    assert result.audit.report_sha256 and result.audit.prompt_sha256
    assert result.audit.prompt_tokens is None and result.audit.estimated_cost_cny == 0


def test_partial_graph_is_retained_in_derived_report(graph_run):
    report = InvestigationReport.model_validate_json((ROOT / "tests/fixtures/stage04_investigation_report.json").read_text(encoding="utf-8"))
    gap = CapabilityGap(tool="find_risky_neighbors", error=ToolError.from_code("graph_prediction_unavailable"))
    unavailable = FindRiskyNeighborsResult.failure(code="graph_prediction_unavailable", transaction_id="T3", dataset_source="synthetic")
    partial = report.model_copy(update={"status": "partial", "risky_neighbors": unavailable,
                                        "unavailable_capabilities": [gap]})
    result = Stage05ReportWorkflow(FakeReportProvider()).run(graph_run(partial))
    assert result.status == "partial_evidence" and result.report is not None
    assert result.report.graph_status == "unavailable"
    assert result.report.graph_model_version is None
    assert any("部分调查工具不可用" in item for item in result.report.uncertainty)


def test_no_policy_forces_insufficient_without_model_call(graph_run):
    class MustNotCall:
        def generate(self, prompt, schema):
            raise AssertionError("generation must not run")

    result = Stage05ReportWorkflow(MustNotCall()).run(graph_run(planner=NoMatchPlanner()))
    assert result.status == "insufficient_evidence" and result.report is not None
    assert result.report.policy_conclusions == []
    assert result.report.suggested_action == "insufficient_evidence"
    assert "无法依据现有政策判断" in result.report.uncertainty
    assert result.audit.model_id is None


@pytest.mark.parametrize("policy_id,section", [
    ("trusted-device-exception", "例外条件"),
    ("device-sharing", "触发条件"),
    ("transaction-velocity", "触发条件"),
])
def test_unverified_policy_prerequisite_is_not_promoted_to_conclusion(graph_run, policy_id, section):
    class MustNotCall:
        def generate(self, prompt, schema):
            raise AssertionError("unsupported clause must not reach model")

    graph = graph_run()
    index = build_policy_index(load_policy_corpus(ROOT / "data/policies/stage05"))
    chunk = next(chunk for chunk in index.chunks if chunk.policy_id == policy_id and chunk.section == section)
    hit = PolicyHit(chunk=chunk.model_dump(exclude={"embedding"}), score=1)
    retrieval = graph.policy_retrieval.model_copy(update={"hits": (hit,)})
    altered = graph.model_copy(update={"policy_retrieval": retrieval})
    result = Stage05ReportWorkflow(MustNotCall()).run(altered)
    assert result.status == "insufficient_evidence"
    assert result.issue_code == "policy_prerequisites_unverified"
    assert result.report is not None and result.report.policy_conclusions == []


@pytest.mark.parametrize("alter", [
    {"transaction_id": "T4"}, {"dataset_source": "ieee_cis"},
    {"evidence_ids": ["invented"]}, {"policy_choices": [{"chunk_id": "policy-invented"}]},
    {"suggested_action": "insufficient_evidence"},
])
def test_invalid_draft_is_rejected(graph_run, alter):
    class Altered:
        def generate(self, prompt, schema):
            original = FakeReportProvider().generate(prompt, schema)
            draft = original.draft.model_dump(mode="json") | alter
            return ProviderResult(draft=ReportDraft.model_validate(draft), model_id="fake", elapsed_ms=1)

    result = Stage05ReportWorkflow(Altered()).run(graph_run())
    assert result.status == "rejected" and result.report is None
    assert result.issue_code == "draft_evidence_mismatch"


def test_model_uncertainty_cannot_change_authoritative_investigation_status(graph_run):
    class WrongUncertainty:
        def generate(self, prompt, schema):
            result = FakeReportProvider().generate(prompt, schema)
            wrong = ("partial_evidence" if result.draft.uncertainty == "evidence_complete"
                     else "evidence_complete")
            return result.model_copy(update={"draft": result.draft.model_copy(update={"uncertainty": wrong})})

    complete = Stage05ReportWorkflow(WrongUncertainty()).run(graph_run())
    assert complete.status == "verified" and complete.report.investigation_status == "completed"
    assert not any("部分调查工具不可用" in item for item in complete.report.uncertainty)

    report = InvestigationReport.model_validate_json(
        (ROOT / "tests/fixtures/stage04_investigation_report.json").read_text(encoding="utf-8"))
    partial = report.model_copy(update={
        "status": "partial",
        "risky_neighbors": FindRiskyNeighborsResult.failure(
            code="graph_prediction_unavailable", transaction_id="T3", dataset_source="synthetic"),
        "unavailable_capabilities": [CapabilityGap(
            tool="find_risky_neighbors", error=ToolError.from_code("graph_prediction_unavailable"))],
    })
    degraded = Stage05ReportWorkflow(WrongUncertainty()).run(graph_run(partial))
    assert degraded.status == "partial_evidence" and degraded.report.investigation_status == "partial"
    assert any("部分调查工具不可用" in item for item in degraded.report.uncertainty)


def test_empty_policy_choice_fails_schema_before_evidence_verification(graph_run):
    class Empty:
        def generate(self, prompt, schema):
            original = FakeReportProvider().generate(prompt, schema)
            draft = original.draft.model_dump(mode="json") | {"policy_choices": []}
            return ProviderResult(draft=ReportDraft.model_validate(draft), model_id="fake", elapsed_ms=1)

    result = Stage05ReportWorkflow(Empty()).run(graph_run())
    assert result.status == "provider_error" and result.report is None
    assert result.issue_code == "generation_failed"


def test_stale_policy_and_mutated_text_are_rejected(graph_run):
    graph = graph_run()
    hit = graph.policy_retrieval.hits[0]
    stale = hit.chunk.model_copy(update={"effective_to": graph.request.policy_as_of})
    tampered = hit.chunk.model_copy(update={"text": "fabricated rule"})
    for chunk in (stale, tampered):
        retrieval = graph.policy_retrieval.model_copy(update={
            "hits": (hit.model_copy(update={"chunk": chunk}), *graph.policy_retrieval.hits[1:])
        })
        altered_graph = graph.model_copy(update={"policy_retrieval": retrieval})
        result = Stage05ReportWorkflow(FakeReportProvider()).run(altered_graph)
        assert result.status == "rejected" and result.issue_code == "stale_or_modified_policy"


def test_provider_failure_is_safe_and_does_not_create_report(graph_run):
    class Broken:
        def generate(self, prompt, schema):
            raise httpx.TimeoutException("private endpoint details")

    result = Stage05ReportWorkflow(Broken()).run(graph_run())
    assert result.status == "provider_error" and result.report is None
    assert result.issue_code == "generation_failed"
    assert "private" not in result.model_dump_json()
    assert result.audit.estimated_cost_cny is None


def test_ollama_adapter_pins_digest_and_records_usage(graph_run):
    graph = graph_run()
    draft = FakeReportProvider().generate(
        "fixture\nEVIDENCE_JSON:\n" + json.dumps(_prompt_payload(graph, _eligible_hits(graph))), {},
    ).draft
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "model:1", "digest": "a" * 64}]})
        return httpx.Response(200, json={"model": "model:1", "done": True, "response": draft.model_dump_json(),
                                         "prompt_eval_count": 20, "eval_count": 10})

    config = Stage05Generation(provider="local_http", model_id="model:1", model_revision="a" * 64,
                               endpoint="http://127.0.0.1:11434/api/generate", max_output_tokens=128,
                               request_timeout_seconds=10)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = OllamaReportProvider(config, client)
        result = Stage05ReportWorkflow(provider).run(graph)
    assert result.status == "verified"
    assert (result.audit.prompt_tokens, result.audit.output_tokens, result.audit.estimated_cost_cny) == (20, 10, 0)
    assert calls == ["/api/tags", "/api/generate"]
