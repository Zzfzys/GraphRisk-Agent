"""Bounded route-7 execution around the evidence graph and report provider."""

from __future__ import annotations

import time
from datetime import date
from decimal import Decimal
from threading import RLock
from typing import Callable, Literal, Mapping, Protocol

import httpx
from pydantic import Field

from graphrisk.agent.config import Stage05Limits
from graphrisk.agent.policy_retrieval import RetrievalResult
from graphrisk.agent.reporting import ReportProvider, ReportRun, Stage05ReportWorkflow
from graphrisk.agent.workflow import PolicyPlanner, Stage05GraphRun, Stage05GraphWorkflow
from graphrisk.investigation.contracts import DatasetSource, StrictModel


class DeadlineExceeded(ValueError):
    pass


class BudgetExceeded(ValueError):
    pass


class Deadline:
    def __init__(self, seconds: float, monotonic: Callable[[], float] = time.monotonic):
        self.monotonic = monotonic
        self.started = monotonic()
        self.ends = self.started + seconds

    def remaining(self) -> float:
        return self.ends - self.monotonic()

    def check(self) -> None:
        if self.remaining() <= 0:
            raise DeadlineExceeded("Stage 05 total time budget exhausted")

    def elapsed_ms(self) -> float:
        return round((self.monotonic() - self.started) * 1000, 2)


class AttemptRecord(StrictModel):
    kind: Literal["policy_retrieval", "generation"]
    attempt: int = Field(ge=1)
    status: Literal["ok", "transient_error", "permanent_error", "deadline", "budget_stop"]
    error_code: str | None = None
    elapsed_ms: float = Field(ge=0)


class ReliabilityAudit(StrictModel):
    status: str
    elapsed_ms: float = Field(ge=0)
    policy_attempts: int = Field(ge=0)
    generation_attempts: int = Field(ge=0)
    retries: int = Field(ge=0)
    attempts: list[AttemptRecord]
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    run_cost_cny: float | None = None
    cumulative_cost_cny: float = Field(ge=0)
    api_stop_cny: float = Field(ge=0)
    api_budget_cny: float = Field(ge=0)
    cost_remaining_cny: float = Field(ge=0)
    graph_steps: int = Field(ge=0)
    graph_node_trace: list[str]
    model_id: str | None = None
    model_revision: str | None = None


class ReliableRun(StrictModel):
    status: Literal[
        "verified", "partial_evidence", "insufficient_evidence", "invalid_input",
        "not_found", "invalid_plan", "retrieval_error", "generation_error",
        "deadline_exceeded", "budget_exhausted", "upstream_error",
    ]
    graph_run: Stage05GraphRun | None
    report_run: ReportRun | None
    audit: ReliabilityAudit


class Retriever(Protocol):
    def retrieve_policy(self, query: str, policy_as_of: date, top_k: int) -> RetrievalResult: ...


class CostLedger:
    """In-process reserved-cost gate; API callers must provide a per-attempt upper bound."""

    def __init__(self, limits: Stage05Limits, initial_spent_cny: float = 0):
        self.stop = Decimal(str(limits.api_stop_cny))
        self.budget = Decimal(str(limits.api_budget_cny))
        self.spent = Decimal(str(initial_spent_cny))
        if self.spent < 0 or self.spent > self.budget:
            raise ValueError("initial API spending must be within configured budget")
        self._lock = RLock()

    def reserve(self, upper_bound_cny: Decimal) -> None:
        if upper_bound_cny < 0:
            raise ValueError("API cost upper bound must be nonnegative")
        with self._lock:
            if self.spent + upper_bound_cny > self.stop or self.spent + upper_bound_cny > self.budget:
                raise BudgetExceeded("API stop threshold would be exceeded")
            self.spent += upper_bound_cny

    def settle(self, upper_bound_cny: Decimal, actual_cny: Decimal | None) -> None:
        with self._lock:
            if actual_cny is None:
                # An uncertain network outcome may still have incurred the reserved cost.
                return
            if actual_cny < 0:
                raise ValueError("actual API cost must be nonnegative")
            if actual_cny > upper_bound_cny:
                self.spent += actual_cny - upper_bound_cny
                raise BudgetExceeded("actual API cost exceeded reserved upper bound")
            self.spent -= upper_bound_cny - actual_cny


class RetryingRetriever:
    def __init__(self, source: Retriever, limits: Stage05Limits, deadline: Deadline,
                 attempts: list[AttemptRecord]):
        self.source = source.with_deadline(deadline.remaining) if hasattr(source, "with_deadline") else source
        self.limits, self.deadline, self.attempts = limits, deadline, attempts
        self.count = 0
        self.last_issue: str | None = None

    def retrieve_policy(self, query: str, policy_as_of: date, top_k: int) -> RetrievalResult:
        for _ in range(self.limits.max_retries + 1):
            if self.count >= self.limits.max_policy_queries:
                self.last_issue = "policy_query_limit"
                return RetrievalResult(status="error", error_code="policy_query_limit", message="policy query limit reached")
            try:
                self.deadline.check()
            except DeadlineExceeded:
                self.last_issue = "deadline_exceeded"
                return RetrievalResult(status="error", error_code="deadline_exceeded", message="total time budget exhausted")
            self.count += 1
            started = self.deadline.monotonic()
            try:
                result = RetrievalResult.model_validate(self.source.retrieve_policy(query, policy_as_of, top_k))
                self.deadline.check()
            except DeadlineExceeded:
                self.last_issue = "deadline_exceeded"
                self.attempts.append(AttemptRecord(kind="policy_retrieval", attempt=self.count,
                                                   status="deadline", error_code=self.last_issue,
                                                   elapsed_ms=max(0, (self.deadline.monotonic() - started) * 1000)))
                return RetrievalResult(status="error", error_code="deadline_exceeded", message="total time budget exhausted")
            except (httpx.TimeoutException, httpx.ConnectError):
                result = RetrievalResult(status="error", error_code="embedding_unavailable", message="transient retrieval transport failure")
            except Exception:
                self.last_issue = "retrieval_failed"
                self.attempts.append(AttemptRecord(kind="policy_retrieval", attempt=self.count,
                                                   status="permanent_error", error_code=self.last_issue,
                                                   elapsed_ms=max(0, (self.deadline.monotonic() - started) * 1000)))
                return RetrievalResult(status="error", error_code="retrieval_failed", message="policy retrieval failed")
            transient = result.status == "error" and result.error_code in {"embedding_unavailable", "dependency_timeout"}
            self.attempts.append(AttemptRecord(
                kind="policy_retrieval", attempt=self.count,
                status="transient_error" if transient else "permanent_error" if result.status == "error" else "ok",
                error_code=result.error_code if result.status == "error" else None,
                elapsed_ms=max(0, (self.deadline.monotonic() - started) * 1000),
            ))
            if not transient:
                self.last_issue = result.error_code if result.status == "error" else None
                return result
            self.last_issue = result.error_code
        return result


class RetryingProvider:
    def __init__(self, source: ReportProvider, limits: Stage05Limits, deadline: Deadline,
                 attempts: list[AttemptRecord], cost: CostLedger, *,
                 per_attempt_upper_cny: float = 0):
        self.source = source.with_deadline(deadline.remaining) if hasattr(source, "with_deadline") else source
        self.limits, self.deadline, self.attempts, self.cost = limits, deadline, attempts, cost
        self.upper = Decimal(str(per_attempt_upper_cny))
        if self.upper < 0:
            raise ValueError("per-attempt cost upper bound must be nonnegative")
        self.count = 0
        self.last_issue: str | None = None

    def generate(self, prompt: str, schema: dict[str, object]):
        for _ in range(self.limits.max_retries + 1):
            if self.count >= self.limits.max_model_calls:
                self.last_issue = "model_call_limit"
                raise BudgetExceeded("model call limit reached")
            try:
                self.deadline.check()
            except DeadlineExceeded:
                self.last_issue = "deadline_exceeded"
                raise
            try:
                self.cost.reserve(self.upper)
            except BudgetExceeded:
                self.last_issue = "budget_exhausted"
                self.attempts.append(AttemptRecord(kind="generation", attempt=self.count + 1,
                                                   status="budget_stop", error_code=self.last_issue, elapsed_ms=0))
                raise
            self.count += 1
            started = self.deadline.monotonic()
            try:
                result = self.source.generate(prompt, schema)
                actual = Decimal(str(result.estimated_cost_cny)) if result.estimated_cost_cny is not None else None
                self.cost.settle(self.upper, actual)
                self.deadline.check()
            except DeadlineExceeded:
                self.last_issue = "deadline_exceeded"
                self.attempts.append(AttemptRecord(kind="generation", attempt=self.count,
                                                   status="deadline", error_code=self.last_issue,
                                                   elapsed_ms=max(0, (self.deadline.monotonic() - started) * 1000)))
                raise
            except (httpx.TimeoutException, httpx.ConnectError):
                self.last_issue = "model_transport_unavailable"
                self.attempts.append(AttemptRecord(kind="generation", attempt=self.count,
                                                   status="transient_error", error_code=self.last_issue,
                                                   elapsed_ms=max(0, (self.deadline.monotonic() - started) * 1000)))
                continue
            except BudgetExceeded:
                self.last_issue = "budget_exhausted"
                self.attempts.append(AttemptRecord(kind="generation", attempt=self.count,
                                                   status="budget_stop", error_code=self.last_issue,
                                                   elapsed_ms=max(0, (self.deadline.monotonic() - started) * 1000)))
                raise
            except Exception:
                self.last_issue = "model_output_invalid"
                self.attempts.append(AttemptRecord(kind="generation", attempt=self.count,
                                                   status="permanent_error", error_code=self.last_issue,
                                                   elapsed_ms=max(0, (self.deadline.monotonic() - started) * 1000)))
                raise
            self.attempts.append(AttemptRecord(kind="generation", attempt=self.count,
                                               status="ok", elapsed_ms=max(0, (self.deadline.monotonic() - started) * 1000)))
            self.last_issue = None
            return result
        raise httpx.ConnectError("model transport unavailable after bounded retries")


class Stage05ReliableRunner:
    def __init__(self, investigation, retriever: Retriever, provider: ReportProvider, *,
                 dataset_source: DatasetSource, limits: Stage05Limits, top_k: int = 3,
                 planner: PolicyPlanner | None = None, monotonic: Callable[[], float] = time.monotonic,
                 cost_ledger: CostLedger | None = None, per_attempt_upper_cny: float = 0,
                 report_mode: Literal["legacy", "quality_v1"] = "legacy"):
        self.investigation, self.retriever, self.provider = investigation, retriever, provider
        self.dataset_source, self.limits, self.top_k, self.planner = dataset_source, limits, top_k, planner
        self.monotonic = monotonic
        self.cost = cost_ledger or CostLedger(limits)
        if getattr(getattr(provider, "config", None), "provider", None) == "api" and per_attempt_upper_cny <= 0:
            raise ValueError("API provider requires a positive per-attempt cost upper bound")
        if report_mode not in {"legacy", "quality_v1"}:
            raise ValueError("unknown report_mode")
        self.report_mode = report_mode
        self.per_attempt_upper_cny = per_attempt_upper_cny

    def run(self, request: Mapping[str, object]) -> ReliableRun:
        deadline = Deadline(self.limits.max_total_seconds, self.monotonic)
        attempts: list[AttemptRecord] = []
        retriever = RetryingRetriever(self.retriever, self.limits, deadline, attempts)
        provider = RetryingProvider(self.provider, self.limits, deadline, attempts, self.cost,
                                    per_attempt_upper_cny=self.per_attempt_upper_cny)
        graph = Stage05GraphWorkflow(
            self.investigation, retriever, dataset_source=self.dataset_source,
            limits=self.limits, top_k=self.top_k, planner=self.planner,
        ).run(request)
        report: ReportRun | None = None
        if deadline.remaining() > 0 and graph.status in ("stub_ready", "partial_evidence", "no_policy_evidence"):
            if self.report_mode == "quality_v1":
                from graphrisk.agent.quality_reporting import QualityReportWorkflow
                report = QualityReportWorkflow(provider).run(graph)
            else:
                report = Stage05ReportWorkflow(provider).run(graph)
        if deadline.remaining() <= 0 or retriever.last_issue == "deadline_exceeded" or provider.last_issue == "deadline_exceeded":
            status = "deadline_exceeded"
        elif provider.last_issue == "budget_exhausted":
            status = "budget_exhausted"
        elif graph.status in ("invalid_input", "not_found", "invalid_plan"):
            status = graph.status
        elif graph.status == "retrieval_error":
            status = "retrieval_error"
        elif report is None:
            status = "upstream_error"
        elif report.status in ("verified", "partial_evidence", "insufficient_evidence"):
            status = report.status
        elif report.status in ("provider_error", "rejected"):
            status = "generation_error"
        else:
            status = "upstream_error"
        run_cost = report.audit.estimated_cost_cny if report else None
        audit = ReliabilityAudit(
            status=status, elapsed_ms=deadline.elapsed_ms(), policy_attempts=retriever.count,
            generation_attempts=provider.count, retries=max(0, retriever.count - 1) + max(0, provider.count - 1),
            attempts=attempts, prompt_tokens=report.audit.prompt_tokens if report else None,
            output_tokens=report.audit.output_tokens if report else None, run_cost_cny=run_cost,
            cumulative_cost_cny=float(self.cost.spent), api_stop_cny=float(self.cost.stop),
            api_budget_cny=float(self.cost.budget),
            cost_remaining_cny=float(max(Decimal(0), min(self.cost.stop, self.cost.budget) - self.cost.spent)),
            graph_steps=graph.steps, graph_node_trace=graph.node_trace,
            model_id=report.audit.model_id if report else None,
            model_revision=report.audit.model_revision if report else None,
        )
        return ReliableRun(status=status, graph_run=graph, report_run=report, audit=audit)
