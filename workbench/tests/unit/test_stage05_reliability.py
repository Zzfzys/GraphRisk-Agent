"""Route 7 verifies physical attempts, deadlines, and fail-closed cost reservation."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from graphrisk.agent.config import Stage05Limits
from graphrisk.agent.policy_corpus import load_policy_corpus
from graphrisk.agent.policy_retrieval import PolicyRetriever, RetrievalResult, build_policy_index, write_policy_index
from graphrisk.agent.reliability import CostLedger, Stage05ReliableRunner
from graphrisk.agent.reporting import FakeReportProvider
from graphrisk.investigation.workflow import InvestigationAudit, InvestigationReport, InvestigationRun


ROOT = Path(__file__).resolve().parents[2]


class Investigation:
    def __init__(self, report):
        self.report = report
        self.calls = 0

    def run(self, request):
        self.calls += 1
        return InvestigationRun(report=self.report, audit=InvestigationAudit(investigation_id="reliable-fixture", events=[]))


class MeteredProvider:
    def __init__(self):
        self.calls = 0

    def generate(self, prompt, schema):
        self.calls += 1
        result = FakeReportProvider().generate(prompt, schema)
        return result.model_copy(update={"prompt_tokens": 44, "output_tokens": 11, "estimated_cost_cny": 0.0})


class FlakyRetriever:
    def __init__(self, source, failures=1, error_code="embedding_unavailable"):
        self.source, self.failures, self.error_code = source, failures, error_code
        self.calls = 0

    def retrieve_policy(self, query, policy_as_of, top_k):
        self.calls += 1
        if self.calls <= self.failures:
            return RetrievalResult(status="error", error_code=self.error_code, message="temporary")
        return self.source.retrieve_policy(query, policy_as_of, top_k)


class FlakyProvider(MeteredProvider):
    def __init__(self, failures=1, exception=httpx.TimeoutException):
        super().__init__()
        self.failures, self.exception = failures, exception

    def generate(self, prompt, schema):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exception("temporary")
        return FakeReportProvider().generate(prompt, schema).model_copy(update={
            "prompt_tokens": 44, "output_tokens": 11, "estimated_cost_cny": 0.0,
        })


@pytest.fixture
def source(tmp_path):
    report = InvestigationReport.model_validate_json((ROOT / "tests/fixtures/stage04_investigation_report.json").read_text(encoding="utf-8"))
    corpus = load_policy_corpus(ROOT / "data/policies/stage05")
    path = write_policy_index(build_policy_index(corpus), tmp_path / "index.json")
    return Investigation(report), PolicyRetriever(path)


def _limits(**changes):
    base = dict(max_steps=12, max_model_calls=2, max_policy_queries=2, max_retries=1,
                max_total_seconds=30.0, api_budget_cny=30.0, api_stop_cny=24.0)
    return Stage05Limits(**(base | changes))


def _request(**changes):
    return {"transaction_id": "T3", "dataset_source": "synthetic",
            "policy_as_of": "2026-07-01", "relation_limit": 3} | changes


def _runner(source, *, retriever=None, provider=None, limits=None, **kwargs):
    investigation, policy = source
    return Stage05ReliableRunner(
        investigation, retriever or policy, provider or MeteredProvider(),
        dataset_source="synthetic", limits=limits or _limits(), **kwargs,
    )


def test_normal_run_records_branch_citations_usage_and_actual_calls(source):
    result = _runner(source).run(_request())
    assert result.status == "verified"
    assert result.audit.policy_attempts == result.audit.generation_attempts == 1
    assert result.audit.retries == 0
    assert (result.audit.prompt_tokens, result.audit.output_tokens, result.audit.run_cost_cny) == (44, 11, 0)
    assert result.audit.graph_steps == 6
    assert result.report_run.report.policy_conclusions[0].chunk_id in {
        hit.chunk.chunk_id for hit in result.graph_run.policy_retrieval.hits
    }
    assert result.report_run.report.approval_status == "not_requested"


def test_invalid_input_stops_before_external_calls(source):
    provider = MeteredProvider()
    result = _runner(source, provider=provider).run(_request(dataset_source="ieee_cis"))
    assert result.status == "invalid_input"
    assert result.audit.policy_attempts == result.audit.generation_attempts == 0
    assert provider.calls == 0 and source[0].calls == 0


def test_transient_retrieval_retries_once_and_counts_both_attempts(source):
    retriever = FlakyRetriever(source[1])
    result = _runner(source, retriever=retriever).run(_request())
    assert result.status == "verified"
    assert retriever.calls == 2 and result.audit.policy_attempts == 2
    assert result.audit.retries == 1
    assert [item.status for item in result.audit.attempts[:2]] == ["transient_error", "ok"]


def test_permanent_retrieval_error_does_not_retry_or_generate(source):
    retriever = FlakyRetriever(source[1], error_code="index_unavailable")
    provider = MeteredProvider()
    result = _runner(source, retriever=retriever, provider=provider).run(_request())
    assert result.status == "retrieval_error" and retriever.calls == 1
    assert provider.calls == 0 and result.audit.generation_attempts == 0
    assert result.audit.attempts[0].status == "permanent_error"


def test_transient_model_timeout_retries_once(source):
    provider = FlakyProvider()
    result = _runner(source, provider=provider).run(_request())
    assert result.status == "verified" and provider.calls == 2
    assert result.audit.generation_attempts == 2 and result.audit.retries == 1
    assert [item.status for item in result.audit.attempts[-2:]] == ["transient_error", "ok"]


def test_invalid_model_output_stops_without_retry(source):
    provider = FlakyProvider(exception=ValueError)
    result = _runner(source, provider=provider).run(_request())
    assert result.status == "generation_error" and provider.calls == 1
    assert result.report_run.report is None
    assert result.audit.attempts[-1].status == "permanent_error"


def test_total_deadline_stops_after_slow_retrieval_before_model(source):
    clock = [0.0]

    class SlowRetriever:
        def retrieve_policy(self, query, policy_as_of, top_k):
            clock[0] += 2
            return source[1].retrieve_policy(query, policy_as_of, top_k)

    provider = MeteredProvider()
    result = _runner(source, retriever=SlowRetriever(), provider=provider,
                     limits=_limits(max_total_seconds=1.0), monotonic=lambda: clock[0]).run(_request())
    assert result.status == "deadline_exceeded" and provider.calls == 0
    assert result.audit.attempts[0].status == "deadline"


def test_budget_stop_blocks_call_before_provider(source):
    limits = _limits()
    ledger = CostLedger(limits, initial_spent_cny=23.9)
    provider = MeteredProvider()
    result = _runner(source, provider=provider, limits=limits, cost_ledger=ledger,
                     per_attempt_upper_cny=0.2).run(_request())
    assert result.status == "budget_exhausted" and provider.calls == 0
    assert result.audit.generation_attempts == 0
    assert result.audit.attempts[-1].status == "budget_stop"
    assert result.audit.cumulative_cost_cny == 23.9


def test_uncertain_api_timeout_holds_reservation_and_prevents_retry(source):
    limits = _limits()
    ledger = CostLedger(limits, initial_spent_cny=23.8)
    provider = FlakyProvider(failures=2)
    result = _runner(source, provider=provider, limits=limits, cost_ledger=ledger,
                     per_attempt_upper_cny=0.2).run(_request())
    assert provider.calls == 1
    assert result.status == "budget_exhausted"
    assert result.audit.cumulative_cost_cny == 24.0


def test_partial_graph_is_not_promoted_even_with_policy(source):
    from graphrisk.investigation.contracts import FindRiskyNeighborsResult, ToolError
    from graphrisk.investigation.workflow import CapabilityGap

    investigation, policy = source
    partial = investigation.report.model_copy(update={
        "status": "partial",
        "risky_neighbors": FindRiskyNeighborsResult.failure(
            code="graph_prediction_unavailable", transaction_id="T3", dataset_source="synthetic"),
        "unavailable_capabilities": [CapabilityGap(
            tool="find_risky_neighbors", error=ToolError.from_code("graph_prediction_unavailable"))],
    })
    result = _runner((Investigation(partial), policy)).run(_request())
    assert result.status == "partial_evidence"
    assert result.report_run.report.investigation_status == "partial"


def test_external_api_requires_predeclared_cost_bound(source):
    class ApiProvider(MeteredProvider):
        config = type("ApiConfig", (), {"provider": "api"})()

    with pytest.raises(ValueError, match="per-attempt cost upper bound"):
        _runner(source, provider=ApiProvider())
