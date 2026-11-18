"""Route 4 validates bounded orchestration without model or network calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import pytest

from graphrisk.agent.config import Stage05Limits
from graphrisk.agent.policy_corpus import load_policy_corpus
from graphrisk.agent.policy_retrieval import PolicyRetriever, RetrievalResult, build_policy_index, write_policy_index
from graphrisk.agent.workflow import Stage05GraphWorkflow
from graphrisk.investigation.contracts import FindRiskyNeighborsResult, GetTransactionResult, ToolError
from graphrisk.investigation.workflow import CapabilityGap, InvestigationAudit, InvestigationReport, InvestigationRun


ROOT = Path(__file__).resolve().parents[2]


class FakeInvestigation:
    def __init__(self, report: InvestigationReport):
        self.report = report
        self.calls: list[dict[str, object]] = []

    def run(self, request: Mapping[str, object]) -> InvestigationRun:
        self.calls.append(dict(request))
        return InvestigationRun(report=self.report, audit=InvestigationAudit(investigation_id="fake-stage04", events=[]))


class NoMatchPlanner:
    def plan(self, request, report, top_k):
        return {"tool": "retrieve_policy", "transaction_id": request.transaction_id,
                "dataset_source": request.dataset_source, "query": "zzzxxyyqv",
                "policy_as_of": request.policy_as_of, "top_k": top_k}


class BadPlanner:
    def __init__(self, change: dict[str, object]):
        self.change = change

    def plan(self, request, report, top_k):
        result = NoMatchPlanner().plan(request, report, top_k)
        return {**result, **self.change}


class BadDraft:
    def draft(self, request, report, retrieval):
        return {"status": "placeholder", "policy_chunk_ids": ["policy-invented"]}


class EmptyDraft:
    def draft(self, request, report, retrieval):
        return {"status": "placeholder", "policy_chunk_ids": []}


@pytest.fixture
def completed_report() -> InvestigationReport:
    source = ROOT / "tests/fixtures/stage04_investigation_report.json"
    return InvestigationReport.model_validate(json.loads(source.read_text(encoding="utf-8")))


@pytest.fixture
def policy_retriever(tmp_path: Path) -> PolicyRetriever:
    corpus = load_policy_corpus(ROOT / "data/policies/stage05")
    path = write_policy_index(build_policy_index(corpus), tmp_path / "policy-index.json")
    return PolicyRetriever(path)


def _limits(steps: int = 12) -> Stage05Limits:
    return Stage05Limits(max_steps=steps, max_model_calls=1, max_policy_queries=1,
                         max_retries=0, max_total_seconds=300, api_budget_cny=0, api_stop_cny=0)


def _workflow(report, retriever, *, steps=12, planner=None, draft_provider=None):
    investigation = FakeInvestigation(report)
    graph = Stage05GraphWorkflow(investigation, retriever, dataset_source="synthetic",
                                 limits=_limits(steps), top_k=3, planner=planner, draft_provider=draft_provider)
    return graph, investigation


def _request(transaction_id: str = "T3") -> dict[str, object]:
    return {"transaction_id": transaction_id, "dataset_source": "synthetic",
            "policy_as_of": "2026-07-01", "relation_limit": 3}


def test_complete_path_is_deterministic_and_only_structurally_checked(completed_report, policy_retriever) -> None:
    graph, investigation = _workflow(completed_report, policy_retriever)
    first = graph.run(_request())
    second = graph.run(_request())
    assert first == second
    assert first.status == "stub_ready"
    assert first.node_trace == ["validate", "investigate", "plan_policy", "retrieve_policy", "draft_stub", "verify_stub"]
    assert (first.steps, first.policy_queries, first.model_calls) == (6, 1, 1)
    assert first.policy_retrieval is not None and first.policy_retrieval.status == "ok"
    assert first.validation is not None and first.validation.status == "structure_only"
    assert first.validation.semantic_validation_pending is True
    assert first.approval_status == "not_requested"
    assert investigation.calls == [{"transaction_id": "T3", "dataset_source": "synthetic", "relation_limit": 3}] * 2
    assert first.investigation_audit is not None and first.investigation_audit.investigation_id == "fake-stage04"


def test_missing_transaction_stops_before_policy_or_draft(policy_retriever) -> None:
    transaction = GetTransactionResult.failure(code="transaction_not_found", transaction_id="missing", dataset_source="synthetic")
    report = InvestigationReport(status="not_found", transaction_id="missing", dataset_source="synthetic", transaction=transaction)
    graph, investigation = _workflow(report, policy_retriever)
    result = graph.run(_request("missing"))
    assert result.status == "not_found" and result.node_trace == ["validate", "investigate"]
    assert result.policy_queries == result.model_calls == 0
    assert result.policy_retrieval is None and result.draft is None
    assert len(investigation.calls) == 1


def test_partial_graph_evidence_is_not_promoted_to_complete(completed_report, policy_retriever) -> None:
    gap = CapabilityGap(tool="find_risky_neighbors", error=ToolError.from_code("graph_prediction_unavailable"))
    graph_result = FindRiskyNeighborsResult.failure(code="graph_prediction_unavailable", transaction_id="T3", dataset_source="synthetic")
    report = completed_report.model_copy(update={"status": "partial", "unavailable_capabilities": [gap],
                                         "risky_neighbors": graph_result})
    graph, _ = _workflow(report, policy_retriever)
    result = graph.run(_request())
    assert result.status == "partial_evidence"
    assert result.investigation_report is not None and result.investigation_report.status == "partial"
    assert result.policy_retrieval is not None and result.policy_retrieval.status == "ok"
    assert result.validation is not None and result.validation.semantic_validation_pending


def test_no_policy_match_remains_unresolved(completed_report, policy_retriever) -> None:
    graph, _ = _workflow(completed_report, policy_retriever, planner=NoMatchPlanner())
    result = graph.run(_request())
    assert result.status == "no_policy_evidence"
    assert result.policy_retrieval is not None and result.policy_retrieval.status == "no_match"
    assert result.draft is not None and result.draft.policy_chunk_ids == []
    assert result.validation is not None and result.validation.status == "no_policy_evidence"


@pytest.mark.parametrize("change", [
    {"tool": "execute_sql"},
    {"dataset_source": "ieee_cis"},
    {"policy_as_of": "2026-06-30"},
    {"top_k": 4},
    {"unexpected": "hidden"},
])
def test_untrusted_plan_cannot_change_tool_source_date_or_budget(completed_report, policy_retriever, change) -> None:
    graph, _ = _workflow(completed_report, policy_retriever, planner=BadPlanner(change))
    result = graph.run(_request())
    assert result.status == "invalid_plan"
    assert result.policy_queries == result.model_calls == 0
    assert result.policy_retrieval is None


def test_request_source_and_step_limit_stop_before_extra_calls(completed_report, policy_retriever) -> None:
    graph, investigation = _workflow(completed_report, policy_retriever, steps=2)
    mismatch = graph.run({**_request(), "dataset_source": "ieee_cis"})
    assert mismatch.status == "invalid_input" and mismatch.node_trace == ["validate"]
    assert not investigation.calls
    bounded = graph.run(_request())
    assert bounded.status == "step_limit" and bounded.steps == 2
    assert bounded.node_trace == ["validate", "investigate"]
    assert bounded.policy_queries == bounded.model_calls == 0


def test_fake_draft_cannot_invent_policy_chunk(completed_report, policy_retriever) -> None:
    graph, _ = _workflow(completed_report, policy_retriever, draft_provider=BadDraft())
    result = graph.run(_request())
    assert result.status == "draft_invalid"
    assert result.issues[0].code == "draft_reference_invalid"
    assert result.validation is None


def test_policy_hit_cannot_be_called_ready_without_a_citation(completed_report, policy_retriever) -> None:
    graph, _ = _workflow(completed_report, policy_retriever, draft_provider=EmptyDraft())
    result = graph.run(_request())
    assert result.status == "draft_invalid"
    assert result.issues[0].code == "draft_reference_invalid"


def test_stage04_evidence_for_another_transaction_stops_before_retrieval(completed_report, policy_retriever) -> None:
    graph, investigation = _workflow(completed_report, policy_retriever)
    result = graph.run(_request("T4"))
    assert result.status == "dependency_error"
    assert result.policy_queries == result.model_calls == 0
    assert result.node_trace == ["validate", "investigate"]
    assert len(investigation.calls) == 1


def test_retrieval_error_stops_before_draft(completed_report) -> None:
    class BrokenRetriever:
        def retrieve_policy(self, query, policy_as_of, top_k):
            return RetrievalResult(status="error", error_code="index_unavailable", message="corrupt")

    graph, _ = _workflow(completed_report, BrokenRetriever())
    result = graph.run(_request())
    assert result.status == "retrieval_error"
    assert result.policy_queries == 1 and result.model_calls == 0
    assert result.node_trace == ["validate", "investigate", "plan_policy", "retrieve_policy"]
