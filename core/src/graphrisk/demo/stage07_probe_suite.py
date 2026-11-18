"""Observed Stage 07 v2 API/database probes over frozen inputs.

The probes mutate only isolated test runs. Expected outputs are never copied into observations.
"""
from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import conninfo_to_dict

from api.main import create_app
from graphrisk.agent.agent_evaluation import EvalProvider, EvalRetriever, FixtureInvestigation, _limits
from graphrisk.agent.pgvector_retrieval import PgvectorPolicyRetriever
from graphrisk.agent.policy_retrieval import OllamaEmbeddingProvider, PolicyRetriever, load_policy_index
from graphrisk.agent.reliability import Stage05ReliableRunner
from graphrisk.demo.stage06_evaluation import CountingRunner, _fixture_results, _service
from graphrisk.demo.stage07_evaluation import POLICY_CORPUS_SHA256, load_contract
from graphrisk.demo.stage07_probes import probe_pending_restart, probe_trace_continuity
from graphrisk.investigation.workflow import InvestigationReport
from graphrisk.serving.postgres_service import PostgresInvestigationService
from graphrisk.serving.postgres_store import PostgresStore
from graphrisk.serving.investigation_service import Stage06Service

REQUEST = {"transaction_id": "T3", "dataset_source": "synthetic",
           "policy_as_of": "2026-07-01", "relation_limit": 3}
BAD_DSN = "postgresql://invalid:invalid@127.0.0.1:1/missing?connect_timeout=1"


def _db_service(dsn: str, source, index_version: str, **kwargs):
    memory, _ = _service(source, index_version)
    return PostgresInvestigationService(
        dsn=dsn, dataset_source="synthetic", versions=memory.versions,
        runner=CountingRunner(source), max_runs=1000, **kwargs,
    )


def _decision(body: dict, kind: str = "reject") -> dict:
    return {"run_id": body["run_id"], "session_id": body["session_id"],
            "transaction_id": body["request"]["transaction_id"],
            "report_sha256": body["report_sha256"], "decision": kind,
            "reviewer_id": "stage07-v2-probe", "note": None}


def _events(dsn: str, run_id: str) -> tuple[int, list[str]]:
    with PostgresStore(dsn).connect() as conn:
        rows = conn.execute("SELECT kind FROM approval_events WHERE run_id=%s ORDER BY sequence", (run_id,)).fetchall()
        fees = conn.execute("SELECT attempt_no FROM cost_attempts WHERE run_id=%s AND state='settled'", (run_id,)).fetchall()
    return sum(row["kind"] in {"approved", "rejected"} for row in rows), [f"{run_id}:{row['attempt_no']}" for row in fees]


def _statuses(source) -> dict[str, str]:
    graph = source.graph_run
    if graph is None or graph.investigation_report is None:
        return {}
    report = graph.investigation_report
    retrieval = graph.policy_retrieval
    graph_status = report.risky_neighbors.status if report.risky_neighbors else "unavailable"
    if graph_status == "ok" and report.risky_neighbors and report.risky_neighbors.data and not report.risky_neighbors.data.neighbors:
        graph_status = "ok_empty"
    policy_status = ("invalid" if retrieval and retrieval.status == "error" else
                     "no_active" if retrieval and retrieval.error_code == "no_active_policy" else
                     retrieval.status if retrieval else "unavailable")
    return {"transaction": report.transaction.status if report.transaction else "unavailable",
            "risk_model": report.risk_score.status if report.risk_score else "unavailable",
            "graph": graph_status, "policy": policy_status}


def _mark_time(output: dict[str, dict], case_id: str, started: float) -> float:
    output[case_id]["probe_wall_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return time.perf_counter()


def _observe(case: dict, source, response, *, before: dict | None = None,
             primary_tool: str, primary_parameters: dict, applied_fault: str,
             probe_kind: str = "real_database", **extra) -> dict[str, Any]:
    body = response.json()
    public = before if before is not None else (body if response.status_code in (200, 201) else {})
    report = public.get("report") or {}
    run_id = public.get("run_id")
    retrieval = source.graph_run.policy_retrieval if source.graph_run else None
    statuses = _statuses(source)
    approval = (body.get("approval") or public.get("approval") or {}).get("status", "none")
    observed = {
        "probe_kind": probe_kind, "applied_fault": applied_fault,
        "source_request": case["input"]["request"], "primary_tool": primary_tool,
        "primary_parameters": primary_parameters,
        "created_run_id": run_id, "target_run_id": run_id,
        "session_id": public.get("session_id"),
        "report_sha256_before": public.get("report_sha256"),
        "report_sha256_after": body.get("report_sha256") if response.status_code in (200, 201) else public.get("report_sha256"),
        "http_status": response.status_code,
        "branch": (body.get("error_code") or body.get("status")),
        "approval": approval, "error_code": body.get("error_code"),
        "evidence_statuses": statuses,
        "report_citations": report.get("policy_citations", []),
        "policy_conclusion_count": len(source.report_run.report.policy_conclusions)
            if source.report_run and source.report_run.report and report else 0,
        "retrieved_policy_chunk_ids": [hit.chunk.chunk_id for hit in retrieval.hits] if retrieval else [],
        "policy_corpus_sha256": POLICY_CORPUS_SHA256,
        "decision_event_count": 0, "external_action_count":
            (body.get("external_action_count") if body.get("external_action_count") is not None else
             (public.get("approval") or {}).get("external_action_count", 0)),
    }
    observed.update(extra)
    return observed


def probe_persistence_cases(repo: Path, index_path: Path, dsn: str) -> dict[str, dict]:
    if os.getenv("GRAPHRISK_STAGE07_EVAL_ISOLATED") != "1":
        raise ValueError("Set GRAPHRISK_STAGE07_EVAL_ISOLATED=1 only for an isolated evaluation database")
    contract, _, index_version = load_contract(repo)
    cases = {item["id"]: item for item in contract["suites"]["frozen"]["cases"]}
    results, _ = _fixture_results(repo, index_path)
    source = results["normal"]
    output: dict[str, dict] = {}
    started = time.perf_counter()
    output["pending_restart_recovers"] = probe_pending_restart(repo, index_path, dsn=dsn)
    started = _mark_time(output, "pending_restart_recovers", started)

    # The response to the first rejection is deliberately discarded; retry on a new service.
    case = cases["decision_retry_after_lost_response"]
    first = _db_service(dsn, source, index_version)
    with TestClient(create_app(lambda: first)) as client:
        created = client.post("/api/v1/investigations", json=REQUEST).json()
        decision = _decision(created)
        committed = client.post(f"/api/v1/investigations/{created['run_id']}/approval", json=decision)
        if committed.status_code != 200:
            raise RuntimeError("initial rejection did not commit")
    second = _db_service(dsn, source, index_version)
    with TestClient(create_app(lambda: second)) as client:
        repeated = client.post(f"/api/v1/investigations/{created['run_id']}/approval", json=decision)
        after = client.get(f"/api/v1/investigations/{created['run_id']}").json()
    event_count, _ = _events(dsn, created["run_id"])
    observation = _observe(case, source, repeated, before=created,
        primary_tool="POST /api/v1/investigations/{run_id}/approval",
        primary_parameters={"run_id": created["run_id"], "decision": decision["decision"],
                            "report_sha256": decision["report_sha256"]},
        applied_fault="drop_decision_response_then_restart",
        report_sha256_after=after.get("report_sha256"),
        decision_record={k: decision[k] for k in ("run_id", "transaction_id", "session_id", "report_sha256")},
        decision_event_count=event_count)
    observation["branch"] = repeated.json().get("status") if repeated.status_code == 200 else repeated.json().get("error_code")
    observation["approval"] = repeated.json().get("status", "none")
    output[case["id"]] = observation
    started = _mark_time(output, case["id"], started)

    # Two independent service instances contend while the first checkpoint resume is held.
    case = cases["conflicting_decisions_race"]
    first = _db_service(dsn, source, index_version)
    with TestClient(create_app(lambda: first)) as client:
        created = client.post("/api/v1/investigations", json=REQUEST).json()
    reject_service = _db_service(dsn, source, index_version)
    approve_service = _db_service(dsn, source, index_version)
    from graphrisk.agent.approval import Stage05ApprovalWorkflow
    entered, release = Event(), Event()
    original_resume = Stage05ApprovalWorkflow.resume
    def held_resume(self, raw_decision, report_run):
        if raw_decision.get("decision") == "reject":
            entered.set()
            release.wait(5)
        return original_resume(self, raw_decision, report_run)
    Stage05ApprovalWorkflow.resume = held_resume
    try:
        path = f"/api/v1/investigations/{created['run_id']}/approval"
        with TestClient(create_app(lambda: reject_service)) as reject_client, \
             TestClient(create_app(lambda: approve_service)) as approve_client, \
             ThreadPoolExecutor(max_workers=2) as pool:
            rejected_future = pool.submit(reject_client.post, path, json=_decision(created))
            if not entered.wait(5):
                raise RuntimeError("reject contender did not enter checkpoint resume")
            approved_future = pool.submit(approve_client.post, path, json=_decision(created, "approve"))
            release.set()
            conflict = approved_future.result(timeout=5)
            rejected = rejected_future.result(timeout=5)
            if rejected.status_code != 200:
                raise RuntimeError("reject contender did not commit")
    finally:
        release.set()
        Stage05ApprovalWorkflow.resume = original_resume
        reject_service.close()
        approve_service.close()
    read_service = _db_service(dsn, source, index_version)
    with TestClient(create_app(lambda: read_service)) as client:
        after = client.get(f"/api/v1/investigations/{created['run_id']}").json()
    event_count, _ = _events(dsn, created["run_id"])
    observation = _observe(case, source, conflict,
        before=created, primary_tool="POST /api/v1/investigations/{run_id}/approval",
        primary_parameters={"run_id": created["run_id"], "decision": "approve",
                            "report_sha256": created["report_sha256"]},
        applied_fault="concurrent_reject_then_approve",
        report_sha256_after=after.get("report_sha256"), decision_event_count=event_count)
    observation["approval"] = after.get("approval", {}).get("status", "none")
    output[case["id"]] = observation
    started = _mark_time(output, case["id"], started)

    # Compress the approval TTL to one second, then restart after the actual deadline.
    case = cases["approval_expiry_survives_restart"]
    first = _db_service(dsn, source, index_version, approval_ttl=timedelta(seconds=1))
    with TestClient(create_app(lambda: first)) as client:
        created = client.post("/api/v1/investigations", json=REQUEST).json()
    time.sleep(1.1)
    second = _db_service(dsn, source, index_version, approval_ttl=timedelta(seconds=1))
    with TestClient(create_app(lambda: second)) as client:
        expired = client.post(f"/api/v1/investigations/{created['run_id']}/approval", json=_decision(created))
        after = client.get(f"/api/v1/investigations/{created['run_id']}").json()
    event_count, _ = _events(dsn, created["run_id"])
    observation = _observe(case, source, expired, before=created,
        primary_tool="POST /api/v1/investigations/{run_id}/approval",
        primary_parameters={"run_id": created["run_id"], "decision": "reject",
                            "report_sha256": created["report_sha256"]},
        applied_fault="restart_and_advance_clock", clock_mode="compressed_real_ttl_1s",
        report_sha256_after=after.get("report_sha256"), decision_event_count=event_count)
    observation["approval"] = after.get("approval", {}).get("status", "none")
    output[case["id"]] = observation
    started = _mark_time(output, case["id"], started)

    # Drop DB connectivity for GET, then restore it to verify the frozen report still exists.
    case = cases["database_read_outage_fails_closed"]
    first = _db_service(dsn, source, index_version)
    with TestClient(create_app(lambda: first)) as client:
        created = client.post("/api/v1/investigations", json=REQUEST).json()
    second = _db_service(dsn, source, index_version)
    second.store.dsn = BAD_DSN
    with TestClient(create_app(lambda: second)) as client:
        unavailable = client.get(f"/api/v1/investigations/{created['run_id']}")
        second.store.dsn = dsn
        after = client.get(f"/api/v1/investigations/{created['run_id']}").json()
    event_count, _ = _events(dsn, created["run_id"])
    observation = _observe(case, source, unavailable, before=created,
        primary_tool="GET /api/v1/investigations/{run_id}",
        primary_parameters={"run_id": created["run_id"]},
        applied_fault="disconnect_db_on_read",
        report_sha256_after=after.get("report_sha256"), decision_event_count=event_count)
    output[case["id"]] = observation
    started = _mark_time(output, case["id"], started)

    # Interrupt the publish boundary after the report and checkpoint have been prepared.
    case = cases["database_write_interrupt_no_orphan_pending"]
    first = _db_service(dsn, source, index_version)
    key = "stage07-interrupt-" + uuid4().hex
    def interrupted_publish(*args, **kwargs):
        from graphrisk.serving.investigation_service import ServiceError
        raise ServiceError("persistence_unavailable")
    first.store.publish = interrupted_publish
    with TestClient(create_app(lambda: first)) as client:
        interrupted = client.post("/api/v1/investigations", json=REQUEST, headers={"Idempotency-Key": key})
    with PostgresStore(dsn).connect() as conn:
        row = conn.execute("SELECT r.run_id, r.storage_state FROM investigation_runs r JOIN create_idempotency i ON i.run_id=r.run_id WHERE i.key_sha256=%s", (hashlib.sha256(key.encode()).hexdigest(),)).fetchone()
    observation = _observe(case, source, interrupted,
        primary_tool="POST /api/v1/investigations", primary_parameters=REQUEST,
        applied_fault="interrupt_db_write_before_publish",
        public_pending_without_checkpoint=(row is not None and row["storage_state"] == "published"))
    observation["approval"] = "none"
    output[case["id"]] = observation
    started = _mark_time(output, case["id"], started)

    # A real reliable runner uses the DB cost ledger; replay cannot invoke it twice.
    case = cases["creation_retry_cost_once"]
    fixture = InvestigationReport.model_validate_json((repo / "tests/fixtures/stage04_investigation_report.json").read_text(encoding="utf-8"))
    runner = Stage05ReliableRunner(
        FixtureInvestigation(fixture), EvalRetriever(PolicyRetriever(index_path), "normal", [0.0]),
        EvalProvider("normal"), dataset_source="synthetic", limits=_limits(),
    )
    memory, _ = _service(source, index_version)
    first = PostgresInvestigationService(dsn=dsn, dataset_source="synthetic", versions=memory.versions,
                                          runner=runner, max_runs=1000)
    key = case["input"]["fault"]["idempotency_key"]
    with TestClient(create_app(lambda: first)) as client:
        created_response = client.post("/api/v1/investigations", json=REQUEST, headers={"Idempotency-Key": key})
        created = created_response.json()
    second = _db_service(dsn, source, index_version)
    with TestClient(create_app(lambda: second)) as client:
        replay = client.post("/api/v1/investigations", json=REQUEST, headers={"Idempotency-Key": key})
    event_count, keys = _events(dsn, created["run_id"])
    observation = _observe(case, source, replay, before=created,
        primary_tool="POST /api/v1/investigations",
        primary_parameters={**REQUEST, "idempotency_key": key},
        applied_fault="drop_create_response_then_retry",
        cost_settlement_count=len(keys), cost_settlement_keys=keys, decision_event_count=event_count,
        first_create_replayed=(created_response.status_code == 200),
        first_runner_calls=runner.provider.calls if hasattr(runner.provider, "calls") else None)
    output[case["id"]] = observation
    started = _mark_time(output, case["id"], started)
    return output


class _CaptureRunner:
    def __init__(self, runner):
        self.runner, self.result = runner, None

    def run(self, request):
        self.result = self.runner.run(request)
        return self.result


class _FixedPlanner:
    def __init__(self, query: str):
        self.query = query

    def plan(self, request, report, top_k):
        return {"tool": "retrieve_policy", "transaction_id": request.transaction_id,
                "dataset_source": request.dataset_source, "policy_as_of": request.policy_as_of,
                "query": self.query, "top_k": top_k}


class _WrongDimensionProvider:
    def __init__(self, model_id: str, model_revision: str):
        self.model_id, self.model_revision = model_id, model_revision

    def embed(self, texts):
        return [[0.0] * 1023 for _ in texts]


class _DeadlineProvider(EvalProvider):
    def __init__(self, clock):
        super().__init__("normal")
        self.clock = clock

    def generate(self, prompt, schema):
        self.clock[0] = 60.0
        return super().generate(prompt, schema)


def _api_runner_probe(repo: Path, index_version: str, runner, request: dict) -> tuple[Any, Any]:
    capture = _CaptureRunner(runner)
    memory, _ = _service(_fixture_results(repo, repo / "reports/generated/stage07/probe-index.json")[0]["normal"], index_version)
    memory.runner = capture
    with TestClient(create_app(lambda: memory)) as client:
        response = client.post("/api/v1/investigations", json=request)
    return response, capture.result


def probe_agent_cases(repo: Path, index_path: Path, dsn: str, migration_dsn: str) -> dict[str, dict]:
    """Exercise policy, graph, trace and model branches with observed tool outputs."""
    if os.getenv("GRAPHRISK_STAGE07_EVAL_ISOLATED") != "1":
        raise ValueError("Set GRAPHRISK_STAGE07_EVAL_ISOLATED=1 only for an isolated evaluation database")
    app_conn, migration_conn = conninfo_to_dict(dsn), conninfo_to_dict(migration_dsn)
    if any(app_conn.get(key) != migration_conn.get(key) for key in ("host", "port", "dbname")):
        raise ValueError("Application and migration DSNs must name the same isolated database")
    contract, _, index_version = load_contract(repo)
    cases = {item["id"]: item for item in contract["suites"]["frozen"]["cases"]}
    results, _ = _fixture_results(repo, index_path)
    output: dict[str, dict] = {}
    fixture = InvestigationReport.model_validate_json((repo / "tests/fixtures/stage04_investigation_report.json").read_text(encoding="utf-8"))
    dense_path = repo / "artifacts/stage-05/policy/policy-index-local_embedding.json"
    dense = load_policy_index(dense_path)
    embedder = OllamaEmbeddingProvider(dense.model_id, dense.model_revision)
    started = time.perf_counter()

    # Query the actual pgvector-backed V2 corpus at the V1/V2 date boundary.
    case = cases["policy_superseded_version_excluded"]
    runner = Stage05ReliableRunner(FixtureInvestigation(fixture),
        PgvectorPolicyRetriever(dense_path, embedder, dsn), EvalProvider("normal"),
        dataset_source="synthetic", limits=_limits(max_total_seconds=60),
        planner=_FixedPlanner(case["input"]["fault"]["query"]))
    response, source = _api_runner_probe(repo, dense.index_version, runner, case["input"]["request"])
    observation = _observe(case, source, response, primary_tool="retrieve_policy",
        primary_parameters={"query": source.graph_run.policy_plan.query,
                            "policy_as_of": case["input"]["request"]["policy_as_of"], "top_k": 3},
        applied_fault="retrieve_shared_device_policy", probe_kind="real_database")
    observation["evidence_statuses"]["policy"] = (
        "v2_active" if source.graph_run.policy_retrieval.status == "ok"
        and source.graph_run.policy_retrieval.hits[0].chunk.version == 2 else "invalid")
    output[case["id"]] = observation
    started = _mark_time(output, case["id"], started)

    # Corrupt one stored source hash in the isolated evaluation database, then restore it.
    case = cases["policy_chunk_hash_tamper_rejected"]
    chunk = dense.chunks[0]
    with psycopg.connect(migration_dsn) as conn:
        original_hash = conn.execute(
            "SELECT content_sha256 FROM policy_vector_chunks WHERE index_version=%s AND chunk_id=%s",
            (dense.index_version, chunk.chunk_id),
        ).fetchone()
        if original_hash is None or original_hash[0] != chunk.content_sha256:
            raise ValueError("Evaluation database policy source is not the pinned index")
        conn.execute("UPDATE policy_vector_chunks SET content_sha256=%s WHERE index_version=%s AND chunk_id=%s",
                     ("0" * 64, dense.index_version, chunk.chunk_id))
    try:
        runner = Stage05ReliableRunner(FixtureInvestigation(fixture),
            PgvectorPolicyRetriever(dense_path, embedder, dsn), EvalProvider("normal"),
            dataset_source="synthetic", limits=_limits(max_total_seconds=60))
        response, source = _api_runner_probe(repo, dense.index_version, runner, case["input"]["request"])
    finally:
        with psycopg.connect(migration_dsn) as conn:
            conn.execute("UPDATE policy_vector_chunks SET content_sha256=%s WHERE index_version=%s AND chunk_id=%s",
                         (original_hash[0], dense.index_version, chunk.chunk_id))
    observation = _observe(case, source, response, primary_tool="retrieve_policy",
        primary_parameters={"policy_as_of": case["input"]["request"]["policy_as_of"], "top_k": 3},
        applied_fault="tamper_policy_content_sha256", probe_kind="real_database",
        tool_error_code=source.graph_run.policy_retrieval.error_code)
    output[case["id"]] = observation
    started = _mark_time(output, case["id"], started)

    case = cases["embedding_dimension_mismatch_rejected"]
    runner = Stage05ReliableRunner(FixtureInvestigation(fixture),
        PgvectorPolicyRetriever(dense_path, _WrongDimensionProvider(dense.model_id, dense.model_revision), dsn),
        EvalProvider("normal"), dataset_source="synthetic", limits=_limits(max_total_seconds=60))
    response, source = _api_runner_probe(repo, dense.index_version, runner, case["input"]["request"])
    output[case["id"]] = _observe(case, source, response, primary_tool="retrieve_policy",
        primary_parameters={"policy_as_of": case["input"]["request"]["policy_as_of"],
                            "top_k": 3, "embedding_dimensions": 1023},
        applied_fault="wrong_query_embedding_dimensions", probe_kind="controlled_fault",
        tool_error_code=source.graph_run.policy_retrieval.error_code)
    started = _mark_time(output, case["id"], started)

    # The fixed held-out case uses the real IEEE-CIS feature, model and graph artifacts.
    case = cases["empty_graph_neighbors_not_fraud_ring"]
    real_service = Stage06Service.from_config(repo / "configs/stage05.yaml")
    capture = _CaptureRunner(real_service.runner)
    real_service.runner = capture
    with TestClient(create_app(lambda: real_service)) as client:
        response = client.post("/api/v1/investigations", json=case["input"]["request"])
    source = capture.result
    report = response.json().get("report") or {}
    neighbors = ((report.get("graph") or {}).get("neighbors") or [])
    output[case["id"]] = _observe(case, source, response, primary_tool="find_risky_neighbors",
        primary_parameters={"transaction_id": "3488959", "dataset_source": "ieee_cis", "limit": 3},
        applied_fault="fixed_heldout_zero_neighbors", probe_kind="real_local",
        neighbor_count=len(source.graph_run.investigation_report.risky_neighbors.data.neighbors),
        reported_neighbor_count=len(neighbors))
    started = _mark_time(output, case["id"], started)

    case = cases["graph_service_failure_is_partial"]
    source = results["partial_graph"]
    service = _db_service(dsn, source, index_version)
    with TestClient(create_app(lambda: service)) as client:
        response = client.post("/api/v1/investigations", json=case["input"]["request"])
    output[case["id"]] = _observe(case, source, response, primary_tool="find_risky_neighbors",
        primary_parameters={"transaction_id": "T3", "dataset_source": "synthetic", "limit": 3},
        applied_fault="graph_service_unavailable", probe_kind="controlled_fault",
        target_graph_risk_score=(response.json().get("report") or {}).get("graph", {}).get("target_graph_risk_score"))
    started = _mark_time(output, case["id"], started)

    output["trace_links_create_read_approval"] = probe_trace_continuity(repo, index_path)
    started = _mark_time(output, "trace_links_create_read_approval", started)

    for case_id, scenario in (("model_connect_failure_bounded", "model_unavailable"),
                              ("model_deadline_bounded", "deadline")):
        case = cases[case_id]
        clock = [0.0]
        provider = _DeadlineProvider(clock) if scenario == "deadline" else EvalProvider(scenario)
        runner = Stage05ReliableRunner(FixtureInvestigation(fixture),
            EvalRetriever(PolicyRetriever(index_path), "normal", clock), provider,
            dataset_source="synthetic", limits=_limits(max_total_seconds=60),
            monotonic=(lambda: clock[0]) if scenario == "deadline" else time.monotonic)
        response, source = _api_runner_probe(repo, index_version, runner, case["input"]["request"])
        observation = _observe(case, source, response, primary_tool="generate_report",
            primary_parameters={"model_id": "qwen2.5:0.5b", **({"max_attempts": 2} if scenario != "deadline" else {"max_total_seconds": 60})},
            applied_fault=case["input"]["fault"]["kind"], probe_kind="controlled_fault",
            model_attempts=source.audit.generation_attempts, elapsed_seconds=source.audit.elapsed_ms / 1000)
        observation["evidence_statuses"]["model_generation"] = "timeout" if scenario == "deadline" else "unavailable"
        output[case_id] = observation
        started = _mark_time(output, case_id, started)
    return output
