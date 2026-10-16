"""Frozen Stage 06 API and workbench evaluation over deterministic Stage 05 evidence."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from api.main import create_app
from graphrisk.agent.agent_evaluation import (
    EvalProvider, EvalRetriever, FixtureInvestigation, _limits, _report_for_scenario,
    evaluate_cases,
)
from graphrisk.agent.components import build_components
from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.policy_corpus import load_policy_corpus
from graphrisk.agent.policy_retrieval import PolicyRetriever, build_policy_index, load_policy_index, write_policy_index
from graphrisk.agent.reliability import ReliableRun, Stage05ReliableRunner
from graphrisk.agent.reporting import OllamaReportProvider
from graphrisk.demo.api_client import ApiReply, Stage06ApiClient
from graphrisk.investigation.workflow import InvestigationReport
from graphrisk.serving.investigation_service import RunVersions, Stage06Service


LEGACY_SHA256 = "d4442875bd170bc020fbba26c150dd8fa9d2181bf60d936eb8ae00891ddb1ae1"
GROUPS = {"input_time_source", "retrieval_citation", "model_failure", "graph_partial", "approval", "api_ui"}
SCENARIOS = {
    "invalid_input", "ready_all", "ready_ollama_down", "reread", "foreign_run",
    "current_citation", "safe_projection", "score_disclaimer", "partial_graph",
    "no_policy", "old_policy_date", "no_policy_skips_model", "approve", "reject",
    "exact_replay", "opposite_conflict", "wrong_session", "wrong_transaction",
    "wrong_hash", "wrong_url_run", "no_report_approval", "approval_expired",
    "ui_verified", "ui_no_policy", "ui_partial_graph", "ui_rerun",
}


class CountingRunner:
    def __init__(self, result: ReliableRun):
        self.result, self.calls = result, 0

    def run(self, _request: object) -> ReliableRun:
        self.calls += 1
        return self.result


def _load_suite(repo: Path) -> tuple[dict[str, Any], str]:
    path = repo / "data/evaluation/stage06_agent_cases_v1.yaml"
    source = path.read_bytes()
    suite = yaml.safe_load(source)
    if not isinstance(suite, dict) or suite.get("version") != 1:
        raise ValueError("Stage 06 suite must use version 1")
    cases = suite.get("cases")
    if not isinstance(cases, list) or len(cases) != 33:
        raise ValueError("Stage 06 suite must contain exactly 33 new cases")
    ids = [case.get("id") for case in cases]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Stage 06 case IDs must be unique and nonempty")
    for case in cases:
        if case.get("group") not in GROUPS or case.get("scenario") not in SCENARIOS:
            raise ValueError(f"Unknown Stage 06 case group or scenario: {case.get('id')}")
        expected = case.get("expected")
        if not isinstance(expected, dict) or set(expected) != {"http_status", "branch", "approval", "runner_calls"}:
            raise ValueError(f"Incomplete Stage 06 expectation: {case.get('id')}")
        if (case.get("input") != _request(suite, case)
            or not isinstance(case.get("expected_tool"), str)
            or case.get("expected_parameters") != {
                "request": case["input"], "run_binding": "current" if case["group"] == "approval" else "none",
            }
            or not isinstance(case.get("expected_policy_chunks"), list)
            or case.get("expected_failure") != (expected["branch"] if expected["http_status"] >= 400 else None)):
            raise ValueError(f"Stage 06 case input/tool/evidence/failure contract is incomplete: {case.get('id')}")
    legacy_source = (repo / "data/evaluation/stage05_agent_cases_v1.yaml").read_bytes()
    if hashlib.sha256(legacy_source).hexdigest() != LEGACY_SHA256:
        raise ValueError("Frozen Stage 05 17-case suite changed")
    samples = suite.get("real_samples")
    if not isinstance(samples, list) or len(samples) != 3 or len({item.get("id") for item in samples}) != 3:
        raise ValueError("Stage 06 real sample set must contain three distinct IDs")
    return suite, hashlib.sha256(source).hexdigest()


def _fixture_results(repo: Path, index_path: Path) -> tuple[dict[str, ReliableRun], str]:
    report = InvestigationReport.model_validate_json(
        (repo / "tests/fixtures/stage04_investigation_report.json").read_text(encoding="utf-8"))
    built_index = build_policy_index(load_policy_corpus(repo / "data/policies/stage05"))
    index = write_policy_index(built_index, index_path)
    results: dict[str, ReliableRun] = {}
    for scenario in ("normal", "partial_graph", "no_policy", "old_policy_date"):
        clock = [0.0]
        runner = Stage05ReliableRunner(
            FixtureInvestigation(_report_for_scenario(report, scenario)),
            EvalRetriever(PolicyRetriever(index), scenario, clock), EvalProvider(scenario),
            dataset_source="synthetic", limits=_limits(),
        )
        results[scenario] = runner.run({
            "transaction_id": "T3", "dataset_source": "synthetic",
            "policy_as_of": "2025-12-31" if scenario == "old_policy_date" else "2026-07-01",
            "relation_limit": 3,
        })
    return results, built_index.index_version


def _service(result: ReliableRun, index_version: str, *, ready: bool = True,
             clock: Any = None) -> tuple[Stage06Service, CountingRunner]:
    runner = CountingRunner(result)
    service = Stage06Service(
        dataset_source="synthetic", runner=runner, instance_id="stage06eval",
        versions=RunVersions(graph_comparison_run="fixture-v1", policy_index_version=index_version,
                             generation_model_id="fake-route5"),
        readiness_probe=lambda: {name: (ready if name == "ollama" else True)
                                 for name in ("data", "risk_model", "graph", "policy_index", "ollama")},
        clock=clock,
    )
    return service, runner


def _request(suite: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    request = dict(suite["base_request"])
    for key, value in case.get("override", {}).items():
        if value is None:
            request.pop(key, None)
        else:
            request[key] = value
    return request


def _citation_metrics(body: dict[str, Any], source: ReliableRun) -> tuple[bool, int]:
    report = body.get("report")
    if report is None:
        return True, 0
    graph = source.graph_run
    hits = {hit.chunk.chunk_id: hit.chunk for hit in graph.policy_retrieval.hits} if graph and graph.policy_retrieval else {}
    unsupported = 0
    for citation in report.get("policy_citations", []):
        chunk = hits.get(citation["chunk_id"])
        if chunk is None or any((citation["doc_id"] != chunk.doc_id,
                                 citation["section"] != chunk.section,
                                 citation["quote"] != chunk.text,
                                 citation["version"] != chunk.version,
                                 citation["source_ref"] != chunk.source_ref,
                                 chunk.content_sha256 != hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
                                 not chunk.applies_on(date.fromisoformat(report["policy_as_of"])))):
            unsupported += 1
    expected_count = len(source.report_run.report.policy_conclusions) if source.report_run and source.report_run.report else 0
    complete = unsupported == 0 and len(report.get("policy_citations", [])) == expected_count
    return complete, unsupported


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 2)


def _as_reply(response: Any) -> ApiReply:
    return ApiReply(response.status_code, response.json())


def _ui_case(repo: Path, client: TestClient, request: dict[str, Any], scenario: str,
             checks: list[tuple[str, bool]]) -> tuple[int, dict[str, Any]]:
    def create(_self: Stage06ApiClient, tx: str, as_of: str, limit: int) -> ApiReply:
        checks.append(("ui_request_bound", (tx, as_of, limit) ==
                       (request["transaction_id"], request["policy_as_of"], request["relation_limit"])))
        return _as_reply(client.post("/api/v1/investigations", json=request))

    def get(_self: Stage06ApiClient, run_id: str) -> ApiReply:
        return _as_reply(client.get(f"/api/v1/investigations/{run_id}"))

    def approval(_self: Stage06ApiClient, run_id: str) -> ApiReply:
        return _as_reply(client.get(f"/api/v1/investigations/{run_id}/approval"))

    with patch.object(Stage06ApiClient, "create_investigation", new=create), \
         patch.object(Stage06ApiClient, "get_investigation", new=get), \
         patch.object(Stage06ApiClient, "get_approval", new=approval):
        app = AppTest.from_file(repo / "streamlit_app.py", default_timeout=20).run()
        checks.append(("ui_initial_no_exception", not app.exception))
        app.text_input[0].set_value(request["transaction_id"])
        next(button for button in app.button if button.label == "开始新调查").click().run()
        checks.append(("ui_result_no_exception", not app.exception))
        run_id = app.session_state["run_id"]
        response = client.get(f"/api/v1/investigations/{run_id}")
        body = response.json()
        if scenario == "ui_verified":
            checks.append(("ui_policy_warning", any("模拟政策" in item.value for item in app.caption)))
            checks.append(("ui_has_approval", any(item.label == "拒绝复核建议" for item in app.button)))
        elif scenario == "ui_no_policy":
            checks.append(("ui_no_policy_warning", any("没有可引用的适用政策" in item.value for item in app.warning)))
            checks.append(("ui_no_approval", not any(item.label == "批准复核建议" for item in app.button)))
        elif scenario == "ui_partial_graph":
            checks.append(("ui_partial_graph_warning", any("图证据不可用" in item.value for item in app.warning)))
        elif scenario == "ui_rerun":
            app.run()
            checks.append(("ui_rerun_no_exception", not app.exception))
            checks.append(("ui_same_run_id", app.session_state["run_id"] == run_id))
        return 201, body


def _evaluate_one(repo: Path, suite: dict[str, Any], case: dict[str, Any],
                  results: dict[str, ReliableRun], index_version: str) -> dict[str, Any]:
    started = time.perf_counter()
    scenario = case["scenario"]
    request = _request(suite, case)
    fixture = ("partial_graph" if scenario in {"partial_graph", "ui_partial_graph"}
               else "no_policy" if scenario in {"no_policy", "no_policy_skips_model", "no_report_approval", "ui_no_policy"}
               else "old_policy_date" if scenario == "old_policy_date" else "normal")
    now = [datetime(2026, 9, 27, 10, tzinfo=UTC)]
    service, runner = _service(results[fixture], index_version,
                               ready=scenario != "ready_ollama_down", clock=lambda: now[0])
    checks: list[tuple[str, bool]] = []
    http_status = 0
    body: dict[str, Any] = {}
    evidence_body: dict[str, Any] = {}
    tool = "none"
    with TestClient(create_app(investigation_service_factory=lambda: service)) as client:
        path = "/api/v1/investigations"
        if scenario.startswith("ready_"):
            response = client.get("/api/v1/ready")
            http_status, body, tool = response.status_code, response.json(), "GET /api/v1/ready"
            checks.append(("readiness_components", all(body["components"][key]
                           for key in ("data", "risk_model", "graph", "policy_index"))))
            checks.append(("ollama_state", body["components"]["ollama"] is (scenario == "ready_all")))
        elif scenario == "foreign_run":
            response = client.get(path + "/run-other-" + "0" * 32)
            http_status, body, tool = response.status_code, response.json(), "GET /api/v1/investigations/{run_id}"
        elif scenario == "invalid_input":
            response = client.post(path, json=request)
            http_status, body, tool = response.status_code, response.json(), "POST /api/v1/investigations"
        elif scenario.startswith("ui_"):
            http_status, body = _ui_case(repo, client, request, scenario, checks)
            evidence_body = body
            tool = "UI create → API create/get"
        else:
            response = client.post(path, json=request)
            tool = "POST /api/v1/investigations"
            checks.append(("create_http_201", response.status_code == 201))
            body = response.json()
            if response.status_code == 201:
                checks.append(("request_binding", body["request"] == request))
                evidence_body = body
                run_id = body["run_id"]
                if scenario == "reread":
                    reread = client.get(f"{path}/{run_id}")
                    checks.append(("reread_identical", reread.status_code == 200 and reread.json() == body))
                    http_status, body, tool = reread.status_code, reread.json(), "GET /api/v1/investigations/{run_id}"
                elif scenario in {"approve", "reject", "exact_replay", "opposite_conflict",
                                  "wrong_session", "wrong_transaction", "wrong_hash", "wrong_url_run",
                                  "no_report_approval", "approval_expired"}:
                    tool = ("GET /api/v1/investigations/{run_id}/approval" if scenario == "no_report_approval"
                            else "POST /api/v1/investigations/{run_id}/approval")
                    decision = {
                        "run_id": run_id, "session_id": body.get("session_id"),
                        "transaction_id": body["request"]["transaction_id"],
                        "report_sha256": body.get("report_sha256"), "decision": "approve" if scenario == "approve" else "reject",
                        "reviewer_id": "eval-reviewer", "note": "Checked current evidence.",
                    }
                    if scenario == "wrong_session":
                        decision["session_id"] = "session-other"
                    elif scenario == "wrong_transaction":
                        decision["transaction_id"] = "T4"
                    elif scenario == "wrong_hash":
                        decision["report_sha256"] = "f" * 64
                    elif scenario == "wrong_url_run":
                        decision["run_id"] = "run-other-" + "0" * 32
                    if scenario == "approval_expired":
                        now[0] += timedelta(minutes=31)
                    approval_path = f"{path}/{run_id}/approval"
                    if scenario in {"exact_replay", "opposite_conflict"}:
                        first = client.post(approval_path, json=decision)
                        checks.append(("first_rejection", first.status_code == 200 and first.json()["status"] == "rejected"))
                        if scenario == "opposite_conflict":
                            decision["decision"] = "approve"
                    submitted = (client.get(approval_path) if scenario == "no_report_approval"
                                 else client.post(approval_path, json=decision))
                    http_status, body = submitted.status_code, submitted.json()
                    reread = client.get(f"{path}/{run_id}")
                    checks.append(("approval_reread", reread.status_code == 200))
                    if reread.status_code == 200:
                        checks.append(("report_hash_preserved", reread.json()["report_sha256"] == response.json()["report_sha256"]))
                        checks.append(("no_external_action", reread.json().get("approval") is None or
                                       reread.json()["approval"]["external_action_count"] == 0))
                    approval_state = reread.json().get("approval") if reread.status_code == 200 else None
                    body["_observed_approval"] = approval_state["status"] if approval_state else "none"
                else:
                    http_status = response.status_code
                    if scenario == "current_citation":
                        checks.append(("has_current_quote", bool(body["report"]["policy_citations"])))
                    elif scenario == "safe_projection":
                        serialized = response.text
                        checks.append(("no_raw_fields", all(value not in serialized for value in
                                       ("is_fraud", "card_id", "device_id", "identity_key", "EVIDENCE_JSON"))))
                    elif scenario == "score_disclaimer":
                        checks.append(("scores_uncalibrated", body["scores_calibrated"] is False and
                                       body["report"]["model"]["probability_calibrated"] is False))
                    elif scenario == "partial_graph":
                        checks.append(("graph_unavailable_distinct", body["report"]["graph"]["status"] == "unavailable" and
                                       body["report"]["graph"]["target_graph_risk_score"] is None))
                    elif scenario in {"no_policy", "old_policy_date", "no_policy_skips_model"}:
                        checks.append(("no_invented_policy", not body["report"]["policy_citations"] and
                                       body["approval"] is None))
                        if scenario == "no_policy_skips_model":
                            checks.append(("model_not_called", body["audit"]["generation_attempts"] == 0))
            else:
                http_status = response.status_code

    expected = case["expected"]
    branch = body.get("error_code") or body.get("status")
    approval = body.get("_observed_approval", "none")
    if http_status < 400 and "_observed_approval" not in body:
        approval = (body.get("approval") or {}).get("status", "none")
    citation_ok, unsupported = _citation_metrics(evidence_body, results[fixture])
    if evidence_body.get("report"):
        checks.append(("citation_source_bound", citation_ok))
    actual_chunks = ([item["chunk_id"] for item in evidence_body["report"]["policy_citations"]]
                     if evidence_body.get("report") else [])
    checks.append(("expected_tool", tool == case["expected_tool"]))
    checks.append(("expected_policy_chunks", actual_chunks == case["expected_policy_chunks"]))
    checks.append(("expected_failure", (branch if http_status >= 400 else None) == case["expected_failure"]))
    checks.extend((f"expected_{key}", value == expected[key]) for key, value in {
        "http_status": http_status, "branch": branch, "approval": approval,
        "runner_calls": runner.calls,
    }.items())
    audit = evidence_body.get("audit")
    if not isinstance(audit, dict):
        audit = results[fixture].audit.model_dump(mode="json") if runner.calls else {}
    failed = [name for name, success in checks if not success]
    return {
        "id": case["id"], "group": case["group"], "scenario": scenario,
        "input": request, "expected": expected, "expected_tool": case["expected_tool"],
        "expected_policy_chunks": case["expected_policy_chunks"],
        "actual_policy_chunks": actual_chunks, "http_status": http_status,
        "actual_branch": branch, "actual_approval": approval, "tool": tool,
        "tool_params_bound": next((success for name, success in checks if name in {"request_binding", "ui_request_bound"}),
                                  None),
        "citation_complete": citation_ok if evidence_body.get("report") else None,
        "unsupported_conclusions": unsupported,
        "approval_bypasses": int(scenario in {"wrong_session", "wrong_transaction", "wrong_hash", "wrong_url_run",
                                              "no_report_approval", "approval_expired", "opposite_conflict"}
                                 and approval in {"approved", "rejected"} and scenario != "opposite_conflict"),
        "external_action_count": (body.get("approval") or {}).get("external_action_count", 0),
        "runner_calls": runner.calls, "prompt_tokens": audit.get("prompt_tokens"),
        "output_tokens": audit.get("output_tokens"), "estimated_cost_cny": audit.get("run_cost_cny"),
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "passed": not failed, "failed_checks": failed,
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["group"]].append(row)
    group_metrics = {}
    for name, items in sorted(groups.items()):
        durations = [float(item["latency_ms"]) for item in items]
        cited = [item for item in items if item.get("citation_complete") is not None]
        bound = [item for item in items if item.get("tool_params_bound") is not None]
        group_metrics[name] = {
            "count": len(items), "passed": sum(item["passed"] for item in items),
            "pass_rate": round(sum(item["passed"] for item in items) / len(items), 4),
            "tool_parameter_accuracy": (round(sum(item["tool_params_bound"] for item in bound) / len(bound), 4)
                                        if bound else None),
            "citation_completeness": (round(sum(item["citation_complete"] for item in cited) / len(cited), 4)
                                      if cited else None),
            "unsupported_conclusions": sum(item.get("unsupported_conclusions", 0) for item in items),
            "approval_bypasses": sum(item.get("approval_bypasses", 0) for item in items),
            "p50_ms": _percentile(durations, 0.5), "p95_ms": _percentile(durations, 0.95),
            "prompt_tokens": sum(item.get("prompt_tokens") or 0 for item in items),
            "output_tokens": sum(item.get("output_tokens") or 0 for item in items),
            "estimated_cost_cny": round(sum(item.get("estimated_cost_cny") or 0 for item in items), 6),
        }
    durations = [float(item["latency_ms"]) for item in rows]
    return {
        "count": len(rows), "passed": sum(item["passed"] for item in rows),
        "pass_rate": round(sum(item["passed"] for item in rows) / len(rows), 4),
        "groups": group_metrics,
        "p50_ms": _percentile(durations, 0.5), "p95_ms": _percentile(durations, 0.95),
        "unsupported_conclusions": sum(item.get("unsupported_conclusions", 0) for item in rows),
        "approval_bypasses": sum(item.get("approval_bypasses", 0) for item in rows),
    }


def _legacy_rows(legacy: dict[str, Any]) -> list[dict[str, Any]]:
    groups = {
        "normal": "retrieval_citation", "no_policy": "retrieval_citation",
        "old_policy_date": "input_time_source", "stale_version": "retrieval_citation",
        "injected_action": "retrieval_citation", "model_unavailable": "model_failure",
        "model_retry_success": "model_failure", "retrieval_retry_success": "retrieval_citation",
        "retrieval_permanent": "retrieval_citation", "partial_graph": "graph_partial",
        "wrong_source": "input_time_source", "invalid_tool_plan": "input_time_source",
        "missing_transaction": "input_time_source", "total_timeout": "budget_timeout",
        "budget_stop": "budget_timeout", "approval_approve": "approval",
        "approval_reject": "approval",
    }
    rows = []
    for old in legacy["cases"]:
        audit = old["audit"]
        rows.append({
            "id": "stage05/" + old["id"], "group": groups[old["scenario"]],
            "scenario": old["scenario"], "passed": old["passed"],
            "expected": {"branch": old["expected_status"]}, "actual_branch": old["actual_status"],
            "actual_approval": old["approval_status"], "tool": old["tool"],
            "tool_params_bound": old["tool_params_bound"] if old["tool"] else None,
            "citation_complete": old["citations_ok"] if old["actual_status"] in
                                 {"verified", "partial_evidence", "insufficient_evidence"} else None,
            "unsupported_conclusions": old["unsupported_conclusions"],
            "approval_bypasses": int(old["external_action_count"] != 0),
            "external_action_count": old["external_action_count"],
            "latency_ms": audit["elapsed_ms"], "prompt_tokens": audit.get("prompt_tokens"),
            "output_tokens": audit.get("output_tokens"),
            "estimated_cost_cny": audit.get("run_cost_cny"),
            "failed_checks": [] if old["passed"] else ["stage05_case_failed"],
        })
    return rows


def _real_samples(repo: Path, samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    config = load_stage05_config(repo / "configs/stage05.yaml")
    generation = config.generation.model_copy(update={
        "max_output_tokens": 128, "request_timeout_seconds": 45.0,
    })
    limits = config.limits.model_copy(update={
        "max_total_seconds": 60.0, "max_model_calls": 1,
        "max_policy_queries": 1, "max_retries": 0,
    })
    investigation, retriever = build_components(config)
    rows = []
    for sample in samples:
        provider = OllamaReportProvider(generation)
        runner = Stage05ReliableRunner(
            investigation, retriever, provider, dataset_source=config.dataset_source,
            limits=limits, top_k=config.retrieval.top_k,
        )
        request = {key: sample[key] for key in ("transaction_id", "policy_as_of", "relation_limit")}
        request["dataset_source"] = config.dataset_source
        started = time.perf_counter()
        try:
            result = runner.run(request)
            report = result.report_run.report if result.report_run else None
            retrieval = result.graph_run.policy_retrieval if result.graph_run else None
            cited = {quote.chunk_id for quote in report.policy_conclusions} if report else set()
            available = {hit.chunk.chunk_id: hit.chunk for hit in retrieval.hits} if retrieval else {}
            invalid = [quote for quote in report.policy_conclusions if (
                (chunk := available.get(quote.chunk_id)) is None
                or quote.doc_id != chunk.doc_id or quote.version != chunk.version
                or quote.section != chunk.section or quote.quote != chunk.text
                or quote.source_ref != chunk.source_ref
                or chunk.content_sha256 != hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
                or not chunk.applies_on(report.policy_as_of)
            )] if report else []
            row = {
                "id": sample["id"], "status": result.status,
                "citation_complete": not invalid and
                                     (bool(cited) if result.status in {"verified", "partial_evidence"} else not cited),
                "unsupported_conclusions": len(invalid),
                "approval_bypasses": 0, "policy_attempts": result.audit.policy_attempts,
                "generation_attempts": result.audit.generation_attempts,
                "prompt_tokens": result.audit.prompt_tokens,
                "output_tokens": result.audit.output_tokens,
                "estimated_cost_cny": result.audit.run_cost_cny,
                "latency_ms": result.audit.elapsed_ms,
                "model_id": result.audit.model_id or generation.model_id,
                "model_revision": result.audit.model_revision or generation.model_revision,
                "policy_index_version": retrieval.index_version if retrieval else None,
                "error": result.report_run.issue_code if result.report_run else None,
            }
        except Exception as error:
            row = {"id": sample["id"], "status": "evaluation_error", "error": type(error).__name__,
                   "citation_complete": None, "unsupported_conclusions": 0, "approval_bypasses": 0,
                   "prompt_tokens": None, "output_tokens": None, "estimated_cost_cny": None,
                   "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                   "model_id": generation.model_id, "model_revision": generation.model_revision}
        rows.append(row)
    return rows


def evaluate_stage06(repo: Path, index_path: Path, *, include_real: bool = False) -> dict[str, Any]:
    suite, suite_sha = _load_suite(repo)
    legacy = evaluate_cases(repo, index_path.parent / "stage05-index.json")
    results, index_version = _fixture_results(repo, index_path)
    corpus_sha256 = load_policy_index(index_path).corpus_sha256
    new_rows = [_evaluate_one(repo, suite, case, results, index_version) for case in suite["cases"]]
    legacy_rows = _legacy_rows(legacy)
    all_rows = legacy_rows + new_rows
    if len(all_rows) != 50 or len({item["id"] for item in all_rows}) != 50:
        raise ValueError("Combined Stage 06 suite must contain 50 unique cases")
    real = _real_samples(repo, suite["real_samples"]) if include_real else []
    return {
        "suite_version": 1, "suite_sha256": suite_sha,
        "legacy_suite_sha256": legacy["suite_sha256"],
        "policy_corpus_sha256": corpus_sha256, "policy_index_version": index_version,
        "fixture_provider": "fake-route5", "fixture_source": "synthetic",
        "fixture_policy_type": "project-original simulated policies",
        "fake": {"summary": _summarize(all_rows), "cases": all_rows,
                 "legacy_count": len(legacy_rows), "new_count": len(new_rows)},
        "real_local": {
            "model_id": real[0].get("model_id") if real else None,
            "model_revision": real[0].get("model_revision") if real else None,
            "policy_corpus_sha256": corpus_sha256 if include_real else None,
            "policy_index_version": index_version if include_real else None,
            "max_output_tokens": 128 if include_real else None,
            "max_total_seconds": 60 if include_real else None,
            "samples": real,
            "p50_ms": _percentile([row["latency_ms"] for row in real], 0.5),
            "p95_ms": _percentile([row["latency_ms"] for row in real], 0.95),
        },
    }
