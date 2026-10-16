"""Stage 07 frozen challenge contract and evidence-based v2 scoring.

Observations are explicit probe outputs. A case without a probe remains not_run;
expectations alone never count as a product pass.
"""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from graphrisk.agent.policy_corpus import load_policy_corpus
from graphrisk.agent.policy_retrieval import PolicyChunk, build_policy_index

STAGE05_SHA256 = "d4442875bd170bc020fbba26c150dd8fa9d2181bf60d936eb8ae00891ddb1ae1"
STAGE06_SHA256 = "f91200b170e0665628312bc24ecfd01b928d6a74d861ce8e9bb9eac31caaf684"
FROZEN_SHA256 = "60fcad25b9dda57ecd243a226b7d41ce2bda88d28dd89ce88fd460784091f1d1"
POLICY_CORPUS_SHA256 = "5944138255497bb595d46513a7100045363d1f35d99fd2607954567e64145513"
FILES = {
    "stage05": "data/evaluation/stage05_agent_cases_v1.yaml",
    "stage06": "data/evaluation/stage06_agent_cases_v1.yaml",
    "frozen": "data/evaluation/stage07_agent_frozen_v2.yaml",
    "development": "data/evaluation/stage07_agent_dev_v2.yaml",
}
INVARIANTS = {
    "same_run_id", "stable_report_hash", "bound_decision", "one_decision_event",
    "no_decision_event", "no_external_action", "no_orphan_pending", "cost_once",
    "no_stale_policy", "no_unverified_citation", "zero_neighbors",
    "no_invented_relation", "no_graph_score", "trace_linked",
    "trace_gap_detected", "bounded_model_attempts",
}
BASES = {"observed_stage06_gap", "controlled_fault", "risk_hypothesis"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _trusted_chunks(repo: Path) -> tuple[dict[str, PolicyChunk], str]:
    index = build_policy_index(load_policy_corpus(repo / "data/policies/stage05"))
    if index.corpus_sha256 != POLICY_CORPUS_SHA256:
        raise ValueError("Stage 07 pinned policy corpus changed")
    return {chunk.chunk_id: chunk for chunk in index.chunks}, index.index_version


def load_contract(repo: Path) -> tuple[dict[str, Any], dict[str, PolicyChunk], str]:
    """Check frozen source bytes, case shape and independent policy provenance."""
    hashes = {key: _sha(repo / relative) for key, relative in FILES.items()}
    for key, pinned in (("stage05", STAGE05_SHA256), ("stage06", STAGE06_SHA256),
                        ("frozen", FROZEN_SHA256)):
        if hashes[key] != pinned:
            raise ValueError(f"Frozen {key} case file changed: {hashes[key]}")
    chunks, index_version = _trusted_chunks(repo)
    suites = {}
    known_ids: set[str] = set()
    for key in ("frozen", "development"):
        suite = yaml.safe_load((repo / FILES[key]).read_text(encoding="utf-8"))
        if not isinstance(suite, dict) or suite.get("version") != 2 or suite.get("split") != key or suite.get("policy_corpus_sha256") != POLICY_CORPUS_SHA256:
            raise ValueError(f"Invalid Stage 07 {key} suite header")
        cases = suite.get("cases")
        if not isinstance(cases, list) or (key == "frozen" and len(cases) < 12):
            raise ValueError(f"Stage 07 {key} suite has too few cases")
        mechanisms: set[str] = set()
        for case in cases:
            if not isinstance(case, dict):
                raise ValueError(f"Invalid Stage 07 {key} case")
            case_id = case.get("id")
            mechanism = case.get("mechanism")
            if not isinstance(case_id, str) or not case_id or case_id in known_ids or not isinstance(mechanism, str) or not mechanism or mechanism in mechanisms:
                raise ValueError(f"Duplicate or missing Stage 07 case ID/mechanism: {case_id}")
            known_ids.add(case_id)
            mechanisms.add(mechanism)
            if case.get("basis") not in BASES or not isinstance(case.get("trigger"), str) or not case["trigger"]:
                raise ValueError(f"Missing failure classification: {case_id}")
            source = case.get("input")
            request = source.get("request") if isinstance(source, dict) else None
            if not isinstance(request, dict) or set(request) != {"transaction_id", "dataset_source", "policy_as_of", "relation_limit"} or not isinstance(source.get("fault"), dict):
                raise ValueError(f"Incomplete input/fault: {case_id}")
            try:
                day = date.fromisoformat(request["policy_as_of"])
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"Invalid policy date: {case_id}") from exc
            if not isinstance(case.get("expected_tool"), str) or not case["expected_tool"] or not isinstance(case.get("expected_parameters"), dict) or not case["expected_parameters"]:
                raise ValueError(f"Missing expected tool/parameters: {case_id}")
            evidence = case.get("expected_evidence")
            response = case.get("expected_response")
            if not isinstance(evidence, dict) or not evidence or not isinstance(response, dict) or set(response) != {"http_status", "branch", "approval", "error_code"}:
                raise ValueError(f"Incomplete evidence/response: {case_id}")
            citations = case.get("expected_policy_chunks")
            if not isinstance(citations, list) or any(not isinstance(chunk_id, str) or not chunk_id for chunk_id in citations) or len(citations) != len(set(citations)):
                raise ValueError(f"Invalid expected citations: {case_id}")
            if any(chunk_id not in chunks or not chunks[chunk_id].applies_on(day) for chunk_id in citations):
                raise ValueError(f"Expected citation is unknown or inactive: {case_id}")
            invariants = case.get("invariants")
            if not isinstance(invariants, list) or not invariants or any(name not in INVARIANTS for name in invariants):
                raise ValueError(f"Invalid invariants: {case_id}")
            if response["http_status"] >= 400 and response["error_code"] != response["branch"]:
                raise ValueError(f"Public error/branch mismatch: {case_id}")
        suites[key] = suite
    return {"hashes": hashes, "suites": suites}, chunks, index_version


def _citation_failures(case: dict[str, Any], observation: dict[str, Any],
                       chunks: dict[str, PolicyChunk]) -> list[str]:
    failures: list[str] = []
    citations = observation.get("report_citations")
    if not isinstance(citations, list):
        return ["citation_list_missing"]
    identifiers = [item.get("chunk_id") if isinstance(item, dict) else None for item in citations]
    if identifiers != case["expected_policy_chunks"]:
        failures.append("expected_policy_chunks")
    if any(not isinstance(identifier, str) or not identifier for identifier in identifiers):
        failures.append("citation_format")
    elif len(identifiers) != len(set(identifiers)):
        failures.append("duplicate_citation")
    if observation.get("policy_conclusion_count") != len(citations):
        failures.append("conclusion_citation_count")
    if observation.get("policy_corpus_sha256") != POLICY_CORPUS_SHA256:
        failures.append("policy_corpus_binding")
    retrieved = observation.get("retrieved_policy_chunk_ids")
    if not isinstance(retrieved, list) or any(identifier not in retrieved for identifier in identifiers):
        failures.append("retrieval_citation_binding")
    day = date.fromisoformat(case["input"]["request"]["policy_as_of"])
    if not isinstance(retrieved, list) or any(not isinstance(identifier, str) or identifier not in chunks or not chunks[identifier].applies_on(day) for identifier in retrieved):
        failures.append("retrieval_source_or_date")
    for item in citations:
        identifier = item.get("chunk_id") if isinstance(item, dict) else None
        chunk = chunks.get(identifier) if isinstance(identifier, str) else None
        if chunk is None or not chunk.applies_on(day):
            failures.append("citation_source_or_date")
            continue
        fields = {"doc_id": chunk.doc_id, "section": chunk.section, "quote": chunk.text,
                  "version": chunk.version, "source_ref": chunk.source_ref}
        if any(item.get(name) != value for name, value in fields.items()):
            failures.append("citation_source_or_date")
        if chunk.content_sha256 != hashlib.sha256(chunk.text.encode("utf-8")).hexdigest() or ("content_sha256" in item and item["content_sha256"] != chunk.content_sha256):
            failures.append("citation_hash")
    return failures


def _invariant_ok(name: str, observation: dict[str, Any]) -> bool:
    run_id = observation.get("created_run_id")
    before = observation.get("report_sha256_before")
    if name == "same_run_id":
        return bool(run_id and run_id == observation.get("target_run_id"))
    if name == "stable_report_hash":
        return bool(before and before == observation.get("report_sha256_after"))
    if name == "bound_decision":
        decision = observation.get("decision_record")
        request = observation.get("source_request")
        return isinstance(decision, dict) and isinstance(request, dict) and all(decision.get(key) == value and value for key, value in {
            "run_id": run_id, "transaction_id": request.get("transaction_id"),
            "session_id": observation.get("session_id"), "report_sha256": before,
        }.items())
    if name == "one_decision_event":
        return observation.get("decision_event_count") == 1
    if name == "no_decision_event":
        return observation.get("decision_event_count") == 0
    if name == "no_external_action":
        return observation.get("external_action_count") == 0
    if name == "no_orphan_pending":
        return observation.get("public_pending_without_checkpoint") is False
    if name == "cost_once":
        keys = observation.get("cost_settlement_keys")
        return observation.get("cost_settlement_count") == 1 and isinstance(keys, list) and len(keys) == 1 and isinstance(keys[0], str) and bool(keys[0])
    if name in {"no_stale_policy", "no_unverified_citation"}:
        return True  # independently checked in _citation_failures
    if name == "zero_neighbors":
        return observation.get("neighbor_count") == 0
    if name == "no_invented_relation":
        return observation.get("reported_neighbor_count") == 0
    if name == "no_graph_score":
        return observation.get("target_graph_risk_score") is None
    if name == "trace_linked":
        events = observation.get("trace_events")
        return (isinstance(events, list) and len(events) >= 3
                and {event.get("operation") for event in events if isinstance(event, dict)} >= {"create", "get", "approval"}
                and all(isinstance(event, dict) and event.get("run_id") == run_id and event.get("request_id") for event in events)
                and len({event["request_id"] for event in events}) >= 3)
    if name == "trace_gap_detected":
        return observation.get("trace_gap_detected") is True
    if name == "bounded_model_attempts":
        attempts = observation.get("model_attempts")
        elapsed = observation.get("elapsed_seconds")
        return isinstance(attempts, int) and 1 <= attempts <= 2 and isinstance(elapsed, (int, float)) and 0 <= elapsed <= 60
    raise ValueError(f"Unknown invariant {name}")


def score_case(case: dict[str, Any], observation: dict[str, Any] | None,
               chunks: dict[str, PolicyChunk]) -> dict[str, Any]:
    """Score one probe; never infer success from an expected response or a missing field."""
    if observation is None:
        return {"id": case["id"], "mechanism": case["mechanism"], "basis": case["basis"],
                "status": "not_run", "failed_checks": []}
    if not isinstance(observation, dict):
        return {"id": case["id"], "mechanism": case["mechanism"], "basis": case["basis"],
                "status": "failed", "failed_checks": ["observation_format"]}
    failures = []
    source = case["input"]["request"]
    if observation.get("source_request") != source:
        failures.append("source_request")
    if observation.get("applied_fault") != case["input"]["fault"].get("kind") or observation.get("probe_kind") not in {"fake_api", "real_local", "real_database", "controlled_fault"}:
        failures.append("fault_probe_provenance")
    if observation.get("primary_tool") != case["expected_tool"]:
        failures.append("expected_tool")
    placeholders = {"$created_run_id": observation.get("created_run_id"),
                    "$report_sha256_before": observation.get("report_sha256_before")}
    expected = {key: placeholders.get(value, value) if isinstance(value, str) else value
                for key, value in case["expected_parameters"].items()}
    actual = observation.get("primary_parameters")
    if not isinstance(actual, dict) or any(value is None or actual.get(key) != value for key, value in expected.items()):
        failures.append("expected_parameters")
    for key, expected_value in case["expected_evidence"].items():
        if not isinstance(observation.get("evidence_statuses"), dict) or observation["evidence_statuses"].get(key) != expected_value:
            failures.append(f"evidence_{key}")
    for key, expected_value in case["expected_response"].items():
        if observation.get(key) != expected_value:
            failures.append(f"response_{key}")
    if "expected_tool_error_code" in case and observation.get("tool_error_code") != case["expected_tool_error_code"]:
        failures.append("tool_error_code")
    failures.extend(_citation_failures(case, observation, chunks))
    for name in case["invariants"]:
        if not _invariant_ok(name, observation):
            failures.append(name)
    return {"id": case["id"], "mechanism": case["mechanism"], "basis": case["basis"],
            "status": "passed" if not failures else "failed", "failed_checks": sorted(set(failures))}


def evaluate_contract(repo: Path, observations: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    contract, chunks, index_version = load_contract(repo)
    observations = observations or {}
    if not isinstance(observations, dict):
        raise ValueError("Observations must be keyed by case ID")
    case_ids = {case["id"] for suite in contract["suites"].values() for case in suite["cases"]}
    unknown = set(observations) - case_ids
    if unknown:
        raise ValueError(f"Unknown Stage 07 observation IDs: {sorted(unknown)}")
    result = {"schema_version": 2, "case_hashes": contract["hashes"],
              "policy_corpus_sha256": POLICY_CORPUS_SHA256, "policy_index_version": index_version,
              "splits": {}}
    for split, suite in contract["suites"].items():
        rows = [score_case(case, observations.get(case["id"]), chunks) for case in suite["cases"]]
        passed = sum(row["status"] == "passed" for row in rows)
        failed = sum(row["status"] == "failed" for row in rows)
        result["splits"][split] = {
            "total": len(rows), "observed": passed + failed, "passed": passed, "failed": failed,
            "not_run": sum(row["status"] == "not_run" for row in rows),
            "pass_rate_observed": (passed / (passed + failed) if passed + failed else None),
            "rows": rows,
        }
    return result
