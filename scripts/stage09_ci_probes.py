"""15 synthetic mechanism probes over a new real PostgreSQL/pgvector database.

The IEEE held-out zero-neighbour case has an explicit synthetic T3 variant.
No original real-local observations or quality denominators are overwritten.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient
from api.main import create_app
from graphrisk.agent.agent_evaluation import EvalProvider, EvalRetriever, FixtureInvestigation, _limits
from graphrisk.agent.policy_corpus import load_policy_corpus
from graphrisk.agent.policy_retrieval import PolicyRetriever, build_policy_index, write_policy_index
from graphrisk.agent.pgvector_retrieval import PgvectorPolicyRetriever, import_policy_vectors
from graphrisk.agent.reliability import Stage05ReliableRunner
from graphrisk.demo.stage06_evaluation import _fixture_results
from graphrisk.demo.stage07_evaluation import load_contract, score_case
from graphrisk.demo.stage07_probes import probe_trace_continuity
from graphrisk.demo.stage07_probe_suite import (
    probe_persistence_cases, _observe, _api_runner_probe, _db_service, _FixedPlanner,
    _WrongDimensionProvider, _DeadlineProvider,
)
from graphrisk.investigation.workflow import InvestigationReport
from stage09_ci_pytest import _connect, _connect_ex, _allowed_ports, _socketpair, _bind


class CIEmbedding:
    """Deliberately simple test vectors, not evidence of semantic retrieval quality."""
    model_id = "ci-device-fixture"
    model_revision = hashlib.sha256(b"stage09-ci-embedding-v1").hexdigest()

    def embed(self, texts):
        return [[1.0, 0.0] + [0.0]*1022 if (t == "共享设备 处理边界" or "同一设备在目标交易发生前" in t or "本规则取代上一版本" in t)
                else [0.0, 1.0]+[0.0]*1022 for t in texts]


def probes(source, output):
    from psycopg.conninfo import conninfo_to_dict
    import socket
    app = os.environ["GRAPHRISK_STAGE07_DSN"]
    admin = os.environ["GRAPHRISK_STAGE07_MIGRATION_DSN"]
    a, b = conninfo_to_dict(app), conninfo_to_dict(admin)
    if (os.getenv("GRAPHRISK_STAGE07_EVAL_ISOLATED") != "1" or
            not a["dbname"].startswith("graphrisk_ci_") or
            any(a.get(k) != b.get(k) for k in ("host", "port", "dbname")) or
            a["host"] != "127.0.0.1"):
        raise ValueError("only the harness-created loopback CI database is accepted")
    _allowed_ports.add(int(a["port"]))
    socket.socket.connect, socket.socket.connect_ex = _connect, _connect_ex
    socket.socketpair = _socketpair
    socket.socket.bind = _bind
    repo = output/"fixture-repo"
    for relative in ("data/policies/stage05", "data/evaluation"):
        shutil.copytree(source/relative, repo/relative)
    fixture_path = repo/"tests/fixtures/stage04_investigation_report.json"
    fixture_path.parent.mkdir(parents=True)
    shutil.copyfile(source/"tests/fixtures/stage04_investigation_report.json", fixture_path)
    index_path = repo/"artifacts/lexical.json"
    observations = probe_persistence_cases(repo, index_path, app)  # seven actual persistence probes
    contract, chunks, index_version = load_contract(repo)
    cases = {v["id"]: v for v in contract["suites"]["frozen"]["cases"]}
    results, _ = _fixture_results(repo, index_path)
    fixture = InvestigationReport.model_validate_json(fixture_path.read_text(encoding="utf-8"))
    provider = CIEmbedding()
    dense = build_policy_index(load_policy_corpus(repo/"data/policies/stage05"), "local_embedding", provider)
    dense_path = write_policy_index(dense, repo/"artifacts/dense.json")
    import_policy_vectors(admin, dense)

    def policy_runner(case, embedding=provider):
        return _api_runner_probe(repo, dense.index_version, Stage05ReliableRunner(
            FixtureInvestigation(fixture), PgvectorPolicyRetriever(dense_path, embedding, app),
            EvalProvider("normal"), dataset_source="synthetic", limits=_limits(max_total_seconds=60),
            planner=_FixedPlanner(case["input"]["fault"].get("query", "共享设备 处理边界"))), case["input"]["request"])

    key = "policy_superseded_version_excluded"
    case = cases[key]
    response, result = policy_runner(case)
    row = _observe(case, result, response, primary_tool="retrieve_policy",
        primary_parameters={"query": result.graph_run.policy_plan.query, "policy_as_of": "2026-07-01", "top_k": 3},
        applied_fault="retrieve_shared_device_policy", probe_kind="controlled_fault")
    row["evidence_statuses"]["policy"] = "v2_active" if result.graph_run.policy_retrieval.hits and result.graph_run.policy_retrieval.hits[0].chunk.version == 2 else "invalid"
    observations[key] = row

    key = "policy_chunk_hash_tamper_rejected"
    case = cases[key]
    chunk = dense.chunks[0]
    with psycopg.connect(admin) as conn:
        conn.execute("UPDATE policy_vector_chunks SET content_sha256=%s WHERE index_version=%s AND chunk_id=%s",
                     ("0"*64, dense.index_version, chunk.chunk_id))
    try:
        response, result = policy_runner(case)
    finally:
        with psycopg.connect(admin) as conn:
            conn.execute("UPDATE policy_vector_chunks SET content_sha256=%s WHERE index_version=%s AND chunk_id=%s",
                         (chunk.content_sha256, dense.index_version, chunk.chunk_id))
    observations[key] = _observe(case, result, response, primary_tool="retrieve_policy",
        primary_parameters={"policy_as_of": "2026-07-01", "top_k": 3}, applied_fault="tamper_policy_content_sha256",
        probe_kind="controlled_fault", tool_error_code=result.graph_run.policy_retrieval.error_code)

    key = "embedding_dimension_mismatch_rejected"
    case = cases[key]
    response, result = policy_runner(case, _WrongDimensionProvider(dense.model_id, dense.model_revision))
    observations[key] = _observe(case, result, response, primary_tool="retrieve_policy",
        primary_parameters={"policy_as_of": "2026-07-01", "top_k": 3, "embedding_dimensions": 1023},
        applied_fault="wrong_query_embedding_dimensions", probe_kind="controlled_fault",
        tool_error_code=result.graph_run.policy_retrieval.error_code)

    key = "empty_graph_neighbors_not_fraud_ring"
    # This is an explicitly documented mechanism variant, NOT the real IEEE case.
    case = deepcopy(cases[key])
    case["input"]["request"].update(transaction_id="T3", dataset_source="synthetic")
    case["expected_parameters"].update(transaction_id="T3", dataset_source="synthetic")
    cases[key] = case
    empty_data = fixture.risky_neighbors.data.model_copy(update={"neighbors": []})
    empty_tool = fixture.risky_neighbors.model_copy(update={"data": empty_data,
        "evidence": [e for e in fixture.risky_neighbors.evidence if e.source_kind != "entity_link"]})
    empty_fixture = fixture.model_copy(update={"risky_neighbors": empty_tool})
    runner = Stage05ReliableRunner(FixtureInvestigation(empty_fixture), PolicyRetriever(index_path),
        EvalProvider("normal"), dataset_source="synthetic", limits=_limits())
    response, result = _api_runner_probe(repo, index_version, runner, case["input"]["request"])
    observations[key] = _observe(case, result, response, primary_tool="find_risky_neighbors",
        primary_parameters={"transaction_id": "T3", "dataset_source": "synthetic", "limit": 3},
        applied_fault="fixed_heldout_zero_neighbors", probe_kind="controlled_fault", neighbor_count=0,
        reported_neighbor_count=len(response.json()["report"]["graph"]["neighbors"]))

    key = "graph_service_failure_is_partial"
    case = cases[key]
    result = results["partial_graph"]
    with TestClient(create_app(lambda: _db_service(app, result, index_version))) as client:
        response = client.post("/api/v1/investigations", json=case["input"]["request"])
    observations[key] = _observe(case, result, response, primary_tool="find_risky_neighbors",
        primary_parameters={"transaction_id": "T3", "dataset_source": "synthetic", "limit": 3},
        applied_fault="graph_service_unavailable", probe_kind="controlled_fault", target_graph_risk_score=None)
    observations["trace_links_create_read_approval"] = probe_trace_continuity(repo, index_path)
    for key, scenario in (("model_connect_failure_bounded", "model_unavailable"), ("model_deadline_bounded", "deadline")):
        case = cases[key]
        clock = [0.0]
        gen = _DeadlineProvider(clock) if scenario == "deadline" else EvalProvider(scenario)
        runner = Stage05ReliableRunner(FixtureInvestigation(fixture),
            EvalRetriever(PolicyRetriever(index_path), "normal", clock), gen, dataset_source="synthetic",
            limits=_limits(max_total_seconds=60), monotonic=(lambda: clock[0]) if scenario == "deadline" else time.monotonic)
        response, result = _api_runner_probe(repo, index_version, runner, case["input"]["request"])
        row = _observe(case, result, response, primary_tool="generate_report",
            primary_parameters={"model_id": "qwen2.5:0.5b", **({"max_attempts": 2} if scenario != "deadline" else {"max_total_seconds": 60})},
            applied_fault=case["input"]["fault"]["kind"], probe_kind="controlled_fault",
            model_attempts=result.audit.generation_attempts, elapsed_seconds=result.audit.elapsed_ms/1000)
        row["evidence_statuses"]["model_generation"] = "timeout" if scenario == "deadline" else "unavailable"
        observations[key] = row
    scored = [score_case(case, observations.get(key), chunks) for key, case in cases.items()]
    (output/"observations.json").write_text(json.dumps(observations, ensure_ascii=True, indent=2), encoding="utf-8")
    (output/"scored.json").write_text(json.dumps(scored, ensure_ascii=True, indent=2), encoding="utf-8")
    summary = dict(total=len(cases), passed=sum(r["status"] == "passed" for r in scored),
        failed=sum(r["status"] == "failed" for r in scored), not_run=sum(r["status"] == "not_run" for r in scored),
        corpus="original_simulated_policy", embedding="ci_fixture_1024d", real_llm_calls=0,
        zero_neighbor_variant="synthetic_T3_not_original_IEEE_observation")
    (output/"summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    probes(args.source, args.output)
