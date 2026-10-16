"""Bounded Stage 05 LangGraph skeleton around Stage 04 evidence and policy retrieval."""

from __future__ import annotations

from datetime import date
from typing import Literal, Mapping, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import Field, ValidationError, model_validator

from graphrisk.agent.config import Stage05Limits
from graphrisk.agent.contracts import Stage05Request
from graphrisk.agent.policy_retrieval import PolicyRetriever, RetrievalResult
from graphrisk.investigation.contracts import DatasetSource, StrictModel, TransactionId
from graphrisk.investigation.workflow import InvestigationAudit, InvestigationReport, InvestigationRun, InvestigationWorkflow


TerminalStatus = Literal[
    "stub_ready", "partial_evidence", "no_policy_evidence", "invalid_input",
    "not_found", "dependency_error", "invalid_plan", "retrieval_error",
    "draft_invalid", "step_limit",
]
IssueCode = Literal[
    "invalid_request", "dataset_source_mismatch", "investigation_failed",
    "invalid_plan", "plan_binding_mismatch", "policy_query_limit",
    "retrieval_failed", "draft_failed", "draft_reference_invalid", "model_call_limit", "step_limit",
]


class PolicyQueryPlan(StrictModel):
    tool: Literal["retrieve_policy"]
    transaction_id: TransactionId
    dataset_source: DatasetSource
    query: str = Field(min_length=1, max_length=2000)
    policy_as_of: date
    top_k: int = Field(strict=True, ge=1, le=20)

    @model_validator(mode="after")
    def query_has_content(self) -> "PolicyQueryPlan":
        if not self.query.strip():
            raise ValueError("policy query must contain text")
        return self


class DraftSkeleton(StrictModel):
    status: Literal["placeholder"] = "placeholder"
    policy_chunk_ids: list[str] = Field(default_factory=list)
    note: Literal["route5_generation_and_semantic_validation_pending"] = "route5_generation_and_semantic_validation_pending"


class SkeletonValidation(StrictModel):
    status: Literal["structure_only", "no_policy_evidence"]
    semantic_validation_pending: Literal[True] = True


class AgentIssue(StrictModel):
    code: IssueCode
    message: str


class Stage05GraphRun(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    status: TerminalStatus
    request: Stage05Request | None = None
    investigation_report: InvestigationReport | None = None
    investigation_audit: InvestigationAudit | None = None
    policy_plan: PolicyQueryPlan | None = None
    policy_retrieval: RetrievalResult | None = None
    draft: DraftSkeleton | None = None
    validation: SkeletonValidation | None = None
    issues: list[AgentIssue] = Field(default_factory=list)
    steps: int = Field(ge=0)
    policy_queries: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    node_trace: list[str]
    approval_status: Literal["not_requested"] = "not_requested"

    @model_validator(mode="after")
    def terminal_state_is_honest(self) -> "Stage05GraphRun":
        if self.steps != len(self.node_trace):
            raise ValueError("node trace length must equal executed steps")
        if self.status == "stub_ready" and (
            self.investigation_report is None or self.investigation_report.status != "completed"
            or self.policy_retrieval is None or self.policy_retrieval.status != "ok"
            or self.validation is None or self.validation.status != "structure_only"
            or self.draft is None or not self.draft.policy_chunk_ids
        ):
            raise ValueError("stub_ready requires complete investigation and policy evidence")
        if self.investigation_report is not None and self.investigation_report.status in ("not_found", "invalid_input", "failed") and self.policy_retrieval is not None:
            raise ValueError("stopped investigations cannot have a policy retrieval")
        return self


class PolicyPlanner(Protocol):
    def plan(self, request: Stage05Request, report: InvestigationReport, top_k: int) -> Mapping[str, object]: ...


class DraftProvider(Protocol):
    def draft(self, request: Stage05Request, report: InvestigationReport, retrieval: RetrievalResult) -> Mapping[str, object]: ...


class DeterministicPolicyPlanner:
    def plan(self, request: Stage05Request, report: InvestigationReport, top_k: int) -> Mapping[str, object]:
        terms = ["人工复核", "风险证据"]
        if report.related_entities is not None and report.related_entities.status == "ok":
            terms.append("共享设备 卡片")
        if report.risky_neighbors is not None and report.risky_neighbors.status == "ok":
            terms.append("历史风险邻居 图证据")
        else:
            terms.append("图能力缺口 证据不足")
        return {
            "tool": "retrieve_policy", "transaction_id": request.transaction_id,
            "dataset_source": request.dataset_source, "query": " ".join(terms),
            "policy_as_of": request.policy_as_of, "top_k": top_k,
        }


class FakeDraftProvider:
    def draft(self, request: Stage05Request, report: InvestigationReport, retrieval: RetrievalResult) -> Mapping[str, object]:
        return {"status": "placeholder", "policy_chunk_ids": [hit.chunk.chunk_id for hit in retrieval.hits]}


class _AgentState(TypedDict, total=False):
    raw_request: dict[str, object]
    request: dict[str, object]
    investigation_report: dict[str, object]
    investigation_audit: dict[str, object]
    policy_plan: dict[str, object]
    policy_retrieval: dict[str, object]
    draft: dict[str, object]
    validation: dict[str, object]
    issues: list[dict[str, object]]
    terminal_status: TerminalStatus
    steps: int
    policy_queries: int
    model_calls: int
    node_trace: list[str]
    approval_status: Literal["not_requested"]


class Stage05GraphWorkflow:
    """One bounded pass; no arbitrary tools, loops, real LLM output or enforcement."""

    def __init__(
        self,
        investigation: InvestigationWorkflow,
        retriever: PolicyRetriever,
        *,
        dataset_source: DatasetSource,
        limits: Stage05Limits,
        top_k: int = 3,
        planner: PolicyPlanner | None = None,
        draft_provider: DraftProvider | None = None,
    ) -> None:
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        self.investigation = investigation
        self.retriever = retriever
        self.dataset_source = dataset_source
        self.limits = limits
        self.top_k = top_k
        self.planner = planner or DeterministicPolicyPlanner()
        self.draft_provider = draft_provider or FakeDraftProvider()

        graph = StateGraph(_AgentState)
        graph.add_node("validate", self._validate)
        graph.add_node("investigate", self._investigate)
        graph.add_node("plan_policy", self._plan_policy)
        graph.add_node("retrieve_policy", self._retrieve_policy)
        graph.add_node("draft_stub", self._draft_stub)
        graph.add_node("verify_stub", self._verify_stub)
        graph.add_edge(START, "validate")
        for current, following in (
            ("validate", "investigate"), ("investigate", "plan_policy"),
            ("plan_policy", "retrieve_policy"), ("retrieve_policy", "draft_stub"),
            ("draft_stub", "verify_stub"),
        ):
            graph.add_conditional_edges(current, self._route, {"continue": following, "stop": END})
        graph.add_edge("verify_stub", END)
        self._graph = graph.compile()

    @staticmethod
    def _route(state: _AgentState) -> Literal["continue", "stop"]:
        return "stop" if "terminal_status" in state else "continue"

    def _enter(self, state: _AgentState, name: str) -> _AgentState | None:
        if state.get("steps", 0) >= self.limits.max_steps:
            return self._stop("step_limit", "step_limit", "Stage 05 graph step limit reached")
        return {"steps": state.get("steps", 0) + 1, "node_trace": [*state.get("node_trace", []), name]}

    @staticmethod
    def _stop(status: TerminalStatus, code: IssueCode, message: str) -> _AgentState:
        return {"terminal_status": status, "issues": [AgentIssue(code=code, message=message).model_dump(mode="json")]}

    def _validate(self, state: _AgentState) -> _AgentState:
        entered = self._enter(state, "validate")
        if entered is None or "terminal_status" in entered:
            return entered or {}
        try:
            request = Stage05Request.model_validate(state["raw_request"])
        except ValidationError:
            return {**entered, **self._stop("invalid_input", "invalid_request", "Stage 05 request failed validation")}
        if request.dataset_source != self.dataset_source:
            return {**entered, **self._stop("invalid_input", "dataset_source_mismatch", "Request dataset differs from configured source")}
        return {**entered, "request": request.model_dump(mode="json")}

    def _investigate(self, state: _AgentState) -> _AgentState:
        entered = self._enter(state, "investigate")
        if entered is None or "terminal_status" in entered:
            return entered or {}
        request = Stage05Request.model_validate(state["request"])
        try:
            run = InvestigationRun.model_validate(self.investigation.run({
                "transaction_id": request.transaction_id,
                "dataset_source": request.dataset_source,
                "relation_limit": request.relation_limit,
            }))
        except Exception:
            return {**entered, **self._stop("dependency_error", "investigation_failed", "Stage 04 investigation failed")}
        if (run.report.dataset_source is not None and run.report.dataset_source != request.dataset_source) or (
            run.report.transaction_id is not None and run.report.transaction_id != request.transaction_id
        ):
            return {**entered, **self._stop("dependency_error", "investigation_failed", "Stage 04 returned evidence for a different request")}
        update: _AgentState = {
            **entered, "investigation_report": run.report.model_dump(mode="json"),
            "investigation_audit": run.audit.model_dump(mode="json"),
        }
        if run.report.status in ("not_found", "invalid_input", "failed"):
            update["terminal_status"] = {"not_found": "not_found", "invalid_input": "invalid_input", "failed": "dependency_error"}[run.report.status]
        return update

    def _plan_policy(self, state: _AgentState) -> _AgentState:
        entered = self._enter(state, "plan_policy")
        if entered is None or "terminal_status" in entered:
            return entered or {}
        request = Stage05Request.model_validate(state["request"])
        report = InvestigationReport.model_validate(state["investigation_report"])
        try:
            plan = PolicyQueryPlan.model_validate(self.planner.plan(request, report, self.top_k))
        except Exception:
            return {**entered, **self._stop("invalid_plan", "invalid_plan", "Policy planner returned an unsupported tool or invalid arguments")}
        if (plan.transaction_id, plan.dataset_source, plan.policy_as_of) != (request.transaction_id, request.dataset_source, request.policy_as_of) or plan.top_k > self.top_k:
            return {**entered, **self._stop("invalid_plan", "plan_binding_mismatch", "Policy plan differs from validated request or top_k limit")}
        return {**entered, "policy_plan": plan.model_dump(mode="json")}

    def _retrieve_policy(self, state: _AgentState) -> _AgentState:
        entered = self._enter(state, "retrieve_policy")
        if entered is None or "terminal_status" in entered:
            return entered or {}
        if state.get("policy_queries", 0) >= self.limits.max_policy_queries:
            return {**entered, **self._stop("retrieval_error", "policy_query_limit", "Policy query budget exhausted")}
        plan = PolicyQueryPlan.model_validate(state["policy_plan"])
        try:
            result = RetrievalResult.model_validate(self.retriever.retrieve_policy(plan.query, plan.policy_as_of, plan.top_k))
        except Exception:
            return {**entered, "policy_queries": state.get("policy_queries", 0) + 1,
                    **self._stop("retrieval_error", "retrieval_failed", "Policy retrieval raised an error")}
        update: _AgentState = {**entered, "policy_queries": state.get("policy_queries", 0) + 1,
                               "policy_retrieval": result.model_dump(mode="json")}
        if result.status == "error":
            update.update(self._stop("retrieval_error", "retrieval_failed", "Policy retrieval returned an error"))
        return update

    def _draft_stub(self, state: _AgentState) -> _AgentState:
        entered = self._enter(state, "draft_stub")
        if entered is None or "terminal_status" in entered:
            return entered or {}
        if state.get("model_calls", 0) >= self.limits.max_model_calls:
            return {**entered, **self._stop("draft_invalid", "model_call_limit", "Draft provider call budget exhausted")}
        request = Stage05Request.model_validate(state["request"])
        report = InvestigationReport.model_validate(state["investigation_report"])
        retrieval = RetrievalResult.model_validate(state["policy_retrieval"])
        try:
            draft = DraftSkeleton.model_validate(self.draft_provider.draft(request, report, retrieval))
        except Exception:
            return {**entered, "model_calls": state.get("model_calls", 0) + 1,
                    **self._stop("draft_invalid", "draft_failed", "Draft provider returned an invalid skeleton")}
        return {**entered, "model_calls": state.get("model_calls", 0) + 1,
                "draft": draft.model_dump(mode="json")}

    def _verify_stub(self, state: _AgentState) -> _AgentState:
        entered = self._enter(state, "verify_stub")
        if entered is None or "terminal_status" in entered:
            return entered or {}
        draft = DraftSkeleton.model_validate(state["draft"])
        retrieval = RetrievalResult.model_validate(state["policy_retrieval"])
        report = InvestigationReport.model_validate(state["investigation_report"])
        allowed = {hit.chunk.chunk_id for hit in retrieval.hits}
        if (
            len(draft.policy_chunk_ids) != len(set(draft.policy_chunk_ids))
            or not set(draft.policy_chunk_ids) <= allowed
            or (retrieval.status == "ok" and not draft.policy_chunk_ids)
        ):
            return {**entered, **self._stop("draft_invalid", "draft_reference_invalid", "Draft references a chunk outside this retrieval")}
        validation = SkeletonValidation(status="structure_only" if retrieval.status == "ok" else "no_policy_evidence")
        if retrieval.status == "no_match":
            status: TerminalStatus = "no_policy_evidence"
        elif report.status == "partial":
            status = "partial_evidence"
        else:
            status = "stub_ready"
        return {**entered, "validation": validation.model_dump(mode="json"), "terminal_status": status}

    def run(self, raw_request: Mapping[str, object]) -> Stage05GraphRun:
        initial: _AgentState = {
            "raw_request": dict(raw_request), "steps": 0, "policy_queries": 0,
            "model_calls": 0, "node_trace": [], "issues": [], "approval_status": "not_requested",
        }
        state = self._graph.invoke(initial, config={"recursion_limit": self.limits.max_steps + 3})
        fields = Stage05GraphRun.model_fields
        clean = {key: value for key, value in state.items() if key in fields}
        clean["status"] = state["terminal_status"]
        return Stage05GraphRun.model_validate(clean)
