"""One-process Stage 06 composition and bounded investigation state."""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Callable, Literal, Mapping, Protocol
from uuid import uuid4

import httpx
from pydantic import Field, model_serializer
from langgraph.checkpoint.memory import InMemorySaver

from graphrisk.agent.approval import ApprovalDecision, ApprovalResult, Stage05ApprovalWorkflow
from graphrisk.agent.components import build_components
from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.contracts import Stage05Request
from graphrisk.agent.policy_retrieval import load_policy_index
from graphrisk.agent.reliability import CostLedger, ReliableRun, Stage05ReliableRunner
from graphrisk.agent.reporting import OllamaReportProvider
from graphrisk.agent.quality_reporting import OllamaQualityProvider
from graphrisk.agent.quality_evidence import investigation_packet, verified_analysis
from graphrisk.investigation.contracts import DatasetSource, StrictModel


EvidenceState = Literal["source_verified", "partial", "insufficient", "unavailable"]
RunStatus = Literal[
    "verified", "partial_evidence", "insufficient_evidence", "invalid_input",
    "not_found", "invalid_plan", "retrieval_error", "generation_error",
    "deadline_exceeded", "budget_exhausted", "upstream_error",
]


class ServiceError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class RunVersions(StrictModel):
    model_decision_sha256: str | None = None
    score_mode: Literal["legacy", "xgboost_only"] = "legacy"
    primary_model_version: str | None = None
    experimental_graph_model_version: str | None = None
    report_protocol: Literal["stage09-agent-quality-v1"] | None = None
    graph_comparison_run: str
    policy_index_version: str
    generation_model_id: str
    generation_model_revision: str | None = None

    @model_serializer(mode="wrap")
    def serialize_decision(self, handler):
        value = handler(self)
        if self.report_protocol is None:
            value.pop("report_protocol", None)
        if self.score_mode == "legacy" and self.model_decision_sha256 is None:
            value.pop("score_mode", None)
            value.pop("model_decision_sha256", None)
            value.pop("primary_model_version", None)
            value.pop("experimental_graph_model_version", None)
        return value


class PersistedValidation(StrictModel):
    """Bindings attested after source validation; contains no raw tool inputs."""

    schema_version: Literal["stage09-persisted-validation-v1"] = "stage09-persisted-validation-v1"
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    versions_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @classmethod
    def for_validated_result(cls, request: Stage05Request, versions: RunVersions,
                             result: ReliableRun) -> "PersistedValidation":
        snapshot = result.model_copy(update={"graph_run": None})
        def digest(value):
            return hashlib.sha256(value.model_dump_json().encode("utf-8")).hexdigest()
        return cls(request_sha256=digest(request), versions_sha256=digest(versions),
                   result_sha256=digest(snapshot))


class ReadinessStatus(StrictModel):
    status: Literal["ready", "not_ready"]
    components: dict[str, bool]


class InvestigationRecord(StrictModel):
    run_id: str
    session_id: str | None
    request: Stage05Request
    status: RunStatus
    evidence_state: EvidenceState
    report_sha256: str | None = None
    created_request_id: str | None = None
    approval_request_ids: dict[int, str] = Field(default_factory=dict)
    persisted_validation: PersistedValidation | None = None
    public_view: dict[str, object] | None = None
    idempotent_replay: bool = False
    versions: RunVersions
    result: ReliableRun
    approval: ApprovalResult | None = None
    created_at: datetime
    expires_at: datetime

    @model_serializer(mode="wrap")
    def serialize_validation(self, handler):
        value = handler(self)
        if self.persisted_validation is None:
            value.pop("persisted_validation", None)
        return value


class Runner(Protocol):
    def run(self, request: Mapping[str, object]) -> ReliableRun: ...


def _evidence_state(status: str) -> EvidenceState:
    return {
        "verified": "source_verified",
        "partial_evidence": "partial",
        "insufficient_evidence": "insufficient",
    }.get(status, "unavailable")


class Stage06Service:
    """The API's sole owner of the runner, cost ledger and approval checkpoints.

    Records and checkpoints are process-local. A different service instance rejects
    old run IDs, so a restart cannot pretend an approval is still recoverable.
    """

    def __init__(self, *, dataset_source: DatasetSource, versions: RunVersions,
                 runner: Runner, max_runs: int = 32, ttl: timedelta = timedelta(hours=24),
                 approval_ttl: timedelta = timedelta(minutes=30),
                 clock: Callable[[], datetime] | None = None,
                 instance_id: str | None = None,
                 cost_ledger: CostLedger | None = None,
                 readiness_probe: Callable[[], Mapping[str, bool]] | None = None) -> None:
        if max_runs < 1 or ttl <= timedelta(0) or approval_ttl <= timedelta(0):
            raise ValueError("run capacity and TTLs must be positive")
        self.dataset_source, self.versions, self.runner = dataset_source, versions, runner
        self.max_runs, self.ttl = max_runs, ttl
        self.clock = clock or (lambda: datetime.now(UTC))
        self.instance_id = instance_id or uuid4().hex[:12]
        if not 1 <= len(self.instance_id) <= 16 or not self.instance_id.isascii() or not self.instance_id.isalnum():
            raise ValueError("instance_id must be 1-16 ASCII letters or digits")
        self.cost_ledger = cost_ledger
        self.readiness_probe = readiness_probe or (lambda: {
            "data": True, "risk_model": True, "graph": True,
            "policy_index": True, "ollama": True,
        })
        self._lock = RLock()
        self._records: OrderedDict[str, InvestigationRecord] = OrderedDict()
        self._expired: OrderedDict[str, None] = OrderedDict()
        self._inflight = 0
        self._closed = False
        self._checkpointer = InMemorySaver()
        self.approval = Stage05ApprovalWorkflow(
            checkpointer=self._checkpointer, clock=self.clock, ttl=min(ttl, approval_ttl))

    @classmethod
    def from_config(cls, config_path: str | Path = "configs/stage05.yaml", **kwargs) -> "Stage06Service":
        config = load_stage05_config(config_path)
        if config.dataset_source != "ieee_cis":
            raise ValueError("Stage 06 demo requires the IEEE-CIS data source")
        investigation, retriever = build_components(config, pgvector_dsn=kwargs.get("dsn"))
        index_path = config.resolve_path(config.paths.policy_artifact_dir) / f"policy-index-{config.retrieval.index_backend}.json"
        index = load_policy_index(index_path)
        if config.generation.provider not in {"local_http", "host_ollama"}:
            raise ValueError("Stage 06 demo requires a pinned Ollama provider")
        ledger = CostLedger(config.limits)
        runner = Stage05ReliableRunner(
            investigation, retriever, (OllamaQualityProvider if config.report_mode == "quality_v1" else OllamaReportProvider)(config.generation), dataset_source=config.dataset_source,
            limits=config.limits, top_k=config.retrieval.top_k, cost_ledger=ledger, report_mode=config.report_mode,
        )
        versions = RunVersions(
            report_protocol="stage09-agent-quality-v1" if config.report_mode == "quality_v1" else None,
            model_decision_sha256=investigation.model_decision_sha256,
            score_mode="xgboost_only" if investigation.model_decision_sha256 else "legacy",
            primary_model_version=(investigation.graph_service.profile["primary_model_version"]
                if investigation.model_decision_sha256 else None),
            experimental_graph_model_version=(investigation.graph_service.profile["gnn_model_version"]
                if investigation.model_decision_sha256 else None),
            graph_comparison_run=config.graph_comparison_run,
            policy_index_version=index.index_version,
            generation_model_id=config.generation.model_id,
            generation_model_revision=config.generation.model_revision,
        )
        def readiness_probe() -> dict[str, bool]:
            repository = investigation.repository
            components = {
                "data": repository.transaction_parquet.is_file() and repository.feature_parquet.is_file(),
                "risk_model": investigation.risk_tool.startup_error is None,
                "graph": investigation.graph_service is not None and investigation.graph_service.startup_error is None,
                "policy_index": index_path.is_file() and (
                    retriever.check() if config.retrieval.backend == "pgvector" else True
                ),
                "ollama": False,
            }
            if components['graph'] and hasattr(investigation.graph_service,'_intact'):
                try:investigation.graph_service._intact()
                except (OSError,ValueError,RuntimeError):components['graph']=components['risk_model']=False
            try:
                tags_url = config.generation.endpoint.removesuffix("/generate") + "/tags"
                with httpx.Client(timeout=2.0) as client:
                    models = client.get(tags_url).raise_for_status().json().get("models", [])
                components["ollama"] = any(
                    isinstance(item, dict)
                    and item.get("name") == config.generation.model_id
                    and item.get("digest") == config.generation.model_revision
                    for item in models
                )
            except (httpx.HTTPError, ValueError, AttributeError, TypeError):
                pass
            return components

        kwargs.setdefault("readiness_probe", readiness_probe)
        return cls(dataset_source=config.dataset_source, versions=versions, runner=runner,
                   cost_ledger=ledger, **kwargs)

    def readiness(self) -> ReadinessStatus:
        if self._closed:
            raise ServiceError("service_stopped")
        try:
            components = dict(self.readiness_probe())
        except Exception:
            components = {}
        expected = ("data", "risk_model", "graph", "policy_index", "ollama")
        safe = {name: components.get(name) is True for name in expected}
        return ReadinessStatus(status="ready" if all(safe.values()) else "not_ready", components=safe)

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("service clock must be timezone aware")
        return now.astimezone(UTC)

    def _versions_for_request(self, request: Stage05Request) -> RunVersions:
        graph=getattr(getattr(self.runner,'investigation',None),'graph_service',None)
        if graph is not None and getattr(graph,'profile',{}).get('schema_version')=='stage09-historical-coverage-v1':
            coverage=graph.coverage(request.transaction_id)
            if coverage['status']=='covered':
                return self.versions.model_copy(update={
                    'primary_model_version':coverage['primary_model_version'],
                    'experimental_graph_model_version':coverage['graph_model_version']})
        return self.versions

    def _forget(self, run_id: str) -> None:
        record = self._records.pop(run_id)
        if record.session_id:
            self._checkpointer.delete_thread(record.session_id)
        self._expired[run_id] = None
        if len(self._expired) > self.max_runs:
            self._expired.popitem(last=False)

    def _prune(self, now: datetime) -> None:
        for run_id, record in list(self._records.items()):
            if now >= record.expires_at:
                self._forget(run_id)

    def _validate_result(self, request: Stage05Request, result: ReliableRun,
                         versions: RunVersions | None = None, *,
                         persisted_validation: PersistedValidation | None = None) -> str | None:
        expected_versions = versions or self.versions
        graph_request = result.graph_run.request if result.graph_run is not None else None
        if graph_request is not None and (
            graph_request.transaction_id != request.transaction_id
            or graph_request.dataset_source != request.dataset_source
            or graph_request.policy_as_of != request.policy_as_of
        ):
            raise ServiceError("graph_request_binding_mismatch")
        report_run = result.report_run
        if report_run is None or report_run.report is None:
            if result.status in ("verified", "partial_evidence", "insufficient_evidence"):
                raise ServiceError("report_missing")
            return None
        report = report_run.report
        restored = persisted_validation is not None and result.graph_run is None
        if restored and persisted_validation != PersistedValidation.for_validated_result(request, expected_versions, result):
            raise ServiceError("persisted_validation_mismatch")
        if (report.transaction_id != request.transaction_id
            or report.dataset_source != request.dataset_source
            or report.policy_as_of != request.policy_as_of):
            raise ServiceError("report_request_binding_mismatch")
        if expected_versions.score_mode == "xgboost_only" and restored:
            if not expected_versions.model_decision_sha256:
                raise ServiceError("model_decision_binding_mismatch")
            if report.model_version != expected_versions.primary_model_version:
                raise ServiceError("primary_model_binding_mismatch")
            if report.graph_status == "ok":
                if report.graph_model_version != expected_versions.experimental_graph_model_version:
                    raise ServiceError("experimental_graph_binding_mismatch")
            elif report.graph_model_version is not None or report.target_graph_risk_score is not None:
                raise ServiceError("unavailable_graph_has_score")
        elif expected_versions.score_mode == "xgboost_only":
            evidence = result.graph_run.investigation_report if result.graph_run else None
            if (not expected_versions.model_decision_sha256 or evidence is None
                or evidence.model_decision_sha256 != expected_versions.model_decision_sha256
                or evidence.score_mode != "xgboost_only"):
                raise ServiceError("model_decision_binding_mismatch")
            primary = evidence.risk_score.data if evidence.risk_score and evidence.risk_score.status == "ok" else None
            if (primary is None or primary.model_version != expected_versions.primary_model_version
                or report.model_version != primary.model_version or report.model_score != primary.risk_score):
                raise ServiceError("primary_model_binding_mismatch")
            graph = evidence.risky_neighbors.data if evidence.risky_neighbors and evidence.risky_neighbors.status == "ok" else None
            if graph is not None and (
                graph.score_role != "experimental"
                or graph.graph_model_version != expected_versions.experimental_graph_model_version
                or report.graph_model_version != graph.graph_model_version
                or report.target_graph_risk_score != graph.target_graph_risk_score):
                raise ServiceError("experimental_graph_binding_mismatch")
            if graph is None and (report.graph_model_version is not None or report.target_graph_risk_score is not None):
                raise ServiceError("unavailable_graph_has_score")
        if report.analysis is not None and restored:
            if (expected_versions.report_protocol != report.analysis.protocol_version
                or report_run.audit.prompt_version != expected_versions.report_protocol):
                raise ServiceError("analysis_protocol_binding_mismatch")
        elif report.analysis is not None:
            if expected_versions.report_protocol != report.analysis.protocol_version or result.graph_run is None:
                raise ServiceError("analysis_protocol_binding_mismatch")
            try:
                rebuilt = verified_analysis(report.analysis.draft, investigation_packet(result.graph_run))
            except ValueError as error:
                raise ServiceError("analysis_evidence_mismatch") from error
            if rebuilt != report.analysis or report_run.audit.prompt_version != expected_versions.report_protocol:
                raise ServiceError("analysis_evidence_mismatch")
            expected_chunk = next(p["chunk_id"] for p in investigation_packet(result.graph_run)["policies"]
                if p["ref"] == report.analysis.draft.policy_ref)
            if [q.chunk_id for q in report.policy_conclusions] != [expected_chunk]:
                raise ServiceError("analysis_policy_binding_mismatch")
        elif expected_versions.report_protocol and result.status in {"verified", "partial_evidence"}:
            raise ServiceError("analysis_missing")
        digest = hashlib.sha256(report.model_dump_json().encode("utf-8")).hexdigest()
        if digest != report_run.audit.report_sha256:
            raise ServiceError("report_hash_mismatch")
        if result.status in ("verified", "partial_evidence", "insufficient_evidence") and report_run.status != result.status:
            raise ServiceError("report_status_mismatch")
        if report_run.audit.index_version is not None and report_run.audit.index_version != expected_versions.policy_index_version:
            raise ServiceError("policy_index_version_mismatch")
        if result.audit.model_id is not None and result.audit.model_id != expected_versions.generation_model_id:
            raise ServiceError("generation_model_mismatch")
        if (result.audit.model_revision is not None
            and result.audit.model_revision != expected_versions.generation_model_revision):
            raise ServiceError("generation_model_revision_mismatch")
        return digest

    def run(self, raw_request: Mapping[str, object] | Stage05Request, *,
            request_id: str | None = None) -> InvestigationRecord:
        if request_id is not None and re.fullmatch(r"req-[0-9a-f]{32}", request_id) is None:
            raise ValueError("request_id must be server generated")
        request = Stage05Request.model_validate(raw_request)
        if request.dataset_source != self.dataset_source:
            raise ServiceError("dataset_source_mismatch")
        with self._lock:
            if self._closed:
                raise ServiceError("service_stopped")
            self._prune(self._now())
            if len(self._records) + self._inflight >= self.max_runs:
                raise ServiceError("run_capacity_exceeded")
            self._inflight += 1
        run_id = f"run-{self.instance_id}-{uuid4().hex}"
        session_id: str | None = None
        try:
            result = ReliableRun.model_validate(self.runner.run(request.model_dump(mode="json")))
            versions = self._versions_for_request(request)
            digest = self._validate_result(request, result, versions=versions)
            approval = None
            if result.status in ("verified", "partial_evidence") and result.report_run is not None:
                session_id = f"session-{run_id}"
                approval = self.approval.start(result.report_run, session_id)
                if approval.status != "pending" or approval.payload is None or approval.payload.report_sha256 != digest:
                    raise ServiceError("approval_checkpoint_failed")
            created = self._now()
            record = InvestigationRecord(
                run_id=run_id, session_id=session_id, request=request, status=result.status,
                evidence_state=_evidence_state(result.status), report_sha256=digest,
                created_request_id=request_id, approval_request_ids={1: request_id} if approval and request_id else {},
                versions=versions, result=result, approval=approval,
                created_at=created,
                expires_at=created + self.ttl,
            )
            with self._lock:
                if self._closed:
                    raise ServiceError("service_stopped")
                self._records[run_id] = record.model_copy(deep=True)
            return record.model_copy(deep=True)
        except Exception:
            if session_id is not None:
                self._checkpointer.delete_thread(session_id)
            raise
        finally:
            with self._lock:
                self._inflight -= 1

    def get(self, run_id: str, *, request_id: str | None = None) -> InvestigationRecord:
        if request_id is not None and re.fullmatch(r"req-[0-9a-f]{32}", request_id) is None:
            raise ValueError("request_id must be server generated")
        with self._lock:
            if self._closed:
                raise ServiceError("service_stopped")
            if not run_id.startswith(f"run-{self.instance_id}-"):
                raise ServiceError("run_instance_mismatch")
            self._prune(self._now())
            record = self._records.get(run_id)
            if record is None:
                raise ServiceError("run_expired" if run_id in self._expired else "run_not_found")
            if record.approval is not None:
                self._check_approval_report(record)
                if record.approval.status == "pending":
                    old_events = len(record.approval.audit)
                    record.approval = self.approval.start(record.result.report_run, record.session_id)
                    if request_id and len(record.approval.audit) > old_events:
                        record.approval_request_ids.setdefault(len(record.approval.audit), request_id)
            return record.model_copy(deep=True)

    def _check_approval_report(self, record: InvestigationRecord,
                               versions: RunVersions | None = None, *,
                               persisted_validation: PersistedValidation | None = None) -> None:
        digest = self._validate_result(record.request, record.result, versions=versions or record.versions,
                                       persisted_validation=persisted_validation)
        if (record.result.report_run is None or digest != record.report_sha256
            or record.approval is None or record.approval.payload is None
            or digest != record.approval.payload.report_sha256):
            raise ServiceError("report_hash_mismatch")

    def decide(self, run_id: str, decision: ApprovalDecision, *,
               request_id: str | None = None) -> InvestigationRecord:
        if request_id is not None and re.fullmatch(r"req-[0-9a-f]{32}", request_id) is None:
            raise ValueError("request_id must be server generated")
        with self._lock:
            self.get(run_id, request_id=request_id)
            stored = self._records[run_id]
            if stored.approval is None or stored.session_id is None:
                raise ServiceError("approval_not_available")
            if (decision.session_id != stored.session_id
                or decision.transaction_id != stored.request.transaction_id
                or decision.report_sha256 != stored.report_sha256):
                raise ServiceError("approval_binding_mismatch")
            if stored.approval.status == "expired":
                raise ServiceError("approval_expired")
            self._check_approval_report(stored)
            decision_data = {key: getattr(decision, key) for key in ApprovalDecision.model_fields}
            outcome = self.approval.resume(decision_data, stored.result.report_run)
            if outcome.status == "error":
                raise ServiceError({
                    "already_finalized": "approval_conflict",
                    "decision_binding_mismatch": "approval_binding_mismatch",
                    "report_changed": "report_hash_mismatch",
                }.get(outcome.issue_code, "approval_checkpoint_failed"))
            stored.approval = outcome
            if request_id and outcome.status in ("approved", "rejected"):
                stored.approval_request_ids.setdefault(len(outcome.audit), request_id)
            if outcome.status == "expired":
                raise ServiceError("approval_expired")
            return stored.model_copy(deep=True)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for run_id in list(self._records):
                self._forget(run_id)
