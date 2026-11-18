"""Authored fixtures check scorer arithmetic; these are not source-suite results."""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import evaluate_public_references as evaluation
from graphrisk.agent.public_references import ReferenceRetrievalResult


def case(case_id, gold):
    return {"case_id": case_id, "split": "development" if case_id.startswith("D") else "frozen",
            "query": "authored fixture " + case_id, "retrieval_kind": "relevant" if gold else "no_answer",
            "gold_sections": [{"doc_id": "fixture", "section": section} for section in gold],
            "application_expected": "unknown" if gold else "no_rule_in_corpus", "expected_scope": "unknown",
            "context": {"as_of": "2026-11-12"}}


def test_locked_24_cases_reject_snapshot_or_manifest_hash_mismatch(tmp_path, monkeypatch):
    doc = SimpleNamespace(doc_id="fixture", snapshot_sha256="b" * 64)
    manifest = SimpleNamespace(corpus_version="a" * 64, documents=[doc])
    monkeypatch.setattr(evaluation, "load_reference_corpus", lambda path: (
        manifest, ({"document": doc, "span": SimpleNamespace(section="alpha")},)))
    suite = {"schema_version": "route7-reference-queries-v1", "corpus_version": "a" * 64,
             "snapshot_sha256s": {"fixture": "b" * 64},
             "cases": [case(f"{split}{i:02}", ["alpha"] if i <= 8 else [])
                       for split in ("D", "F") for i in range(1, 13)]}
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(suite), encoding="utf-8")
    loaded, digest, version = evaluation.load_suite(path, tmp_path / "manifest.json")
    assert len(loaded["cases"]) == 24 and len(digest) == 64 and version == "a" * 64
    suite["snapshot_sha256s"]["fixture"] = "c" * 64
    path.write_text(json.dumps(suite), encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot hashes differ"):
        evaluation.load_suite(path, tmp_path / "manifest.json")
    suite["snapshot_sha256s"]["fixture"] = "b" * 64
    suite["corpus_version"] = "d" * 64
    path.write_text(json.dumps(suite), encoding="utf-8")
    with pytest.raises(ValueError, match="corpus version differs"):
        evaluation.load_suite(path, tmp_path / "manifest.json")


def test_partial_gold_and_no_answer_noise_do_not_become_refusal_pass(tmp_path, monkeypatch):
    suite = {"cases": [case("D01", ["alpha", "beta"]), case("D02", [])]}
    monkeypatch.setattr(evaluation, "load_suite", lambda *args: (suite, "suite-digest", "corpus-version"))

    def hit(section, **changes):
        return {"doc_id": "fixture", "section": section, "chunk_id": "fixture-" + section,
                "applicability": "reference_only", "scope_status": "unknown",
                "legal_conclusion": "not_determined", **changes}

    calls = []

    class FakeRetriever:
        def __init__(self, *args, **kwargs):
            pass

        def retrieve(self, query, context, top_k, *, planner):
            calls.append((query, top_k, planner))
            hits = (hit("distractor"), hit("alpha")) if query.endswith("D01") else (hit("alpha"),)
            return ReferenceRetrievalResult(status="ok", planner="literal", queries=(query,), hits=hits)

    monkeypatch.setattr(evaluation, "PublicReferenceRetriever", FakeRetriever)
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    report = evaluation.evaluate(manifest, tmp_path / "suite.json", "development", "literal")
    relevant, no_answer = report["cases"]
    assert (relevant["hit_at_1"], relevant["hit_at_3"], relevant["recall_at_3"],
            relevant["all_gold_at_3"], relevant["reciprocal_rank"]) == (0, 1, .5, 0, .5)
    assert no_answer["no_answer_any_hit"] is True
    assert no_answer["recall_at_3"] is None
    assert no_answer["final_answer_refusal"] == "NotRun"
    assert report["summary"]["no_answer_any_hit_rate"] == 1
    assert report["summary"]["final_answer_refusal"] == "NotRun"
    assert report["summary"]["answer_correctness"] == "NotRun"
    assert report["summary"]["llm_planner"] == "NotRun"
    assert report["summary"]["unsafe_legal_conclusion_hit_count"] == 0
    assert len(calls) == 2 and all(call[1:] == (5, "literal") for call in calls)


def test_unknown_scope_escalation_is_diagnostic_not_answer_scoring(tmp_path, monkeypatch):
    suite = {"cases": [case("D01", ["alpha"]), case("D02", [])]}
    monkeypatch.setattr(evaluation, "load_suite", lambda *args: (suite, "suite-digest", "corpus-version"))

    class FakeRetriever:
        def __init__(self, *args, **kwargs):
            pass

        def retrieve(self, query, context, top_k, *, planner):
            hits = ({"doc_id": "fixture", "section": "alpha", "chunk_id": "fixture-alpha",
                     "applicability": "applicable", "scope_status": "conditions_supported",
                     "legal_conclusion": "determined"},) if query.endswith("D01") else ()
            return ReferenceRetrievalResult(status="ok" if hits else "no_match", planner="literal",
                                            queries=(query,), hits=hits)

    monkeypatch.setattr(evaluation, "PublicReferenceRetriever", FakeRetriever)
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    report = evaluation.evaluate(manifest, tmp_path / "suite.json", "development", "literal")
    assert report["summary"]["unsafe_legal_conclusion_hit_count"] == 1
    assert report["summary"]["gold_scope_mismatch_hit_count"] == 1
    assert report["summary"]["legal_applicability_correctness"] == "NotEvaluated"
    assert report["summary"]["end_to_end_report_benefit"] == "NotRun"


@pytest.fixture
def plan_evaluation(tmp_path, monkeypatch):
    suite = {"cases": [case("D01", ["alpha", "beta"]), case("D02", [])]}
    monkeypatch.setattr(evaluation, "load_suite", lambda *args: (suite, "suite-digest", "corpus-version"))
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    calls = []

    def reject_network(*args, **kwargs):
        pytest.fail("offline plan replay must not open a network transport")

    monkeypatch.setattr(socket.socket, "connect", reject_network)

    class FakeRetriever:
        def __init__(self, *args, **kwargs):
            pass

        def retrieve(self, query, context, top_k, *, planner, supplied_plan):
            calls.append((query, context.model_dump(mode="json"), top_k, planner, supplied_plan.queries))
            hits = ({"doc_id": "fixture", "section": "alpha", "chunk_id": "fixture-alpha",
                     "applicability": "reference_only", "scope_status": "unknown",
                     "legal_conclusion": "not_determined"},) if query.endswith("D01") else ()
            return ReferenceRetrievalResult(status="ok" if hits else "no_match", planner=planner,
                                            queries=supplied_plan.queries, hits=hits)

    monkeypatch.setattr(evaluation, "PublicReferenceRetriever", FakeRetriever)
    plans = {c["case_id"]: {"queries": [c["query"], "authored English expansion"]} for c in suite["cases"]}
    return manifest, tmp_path / "suite.json", suite, plans, calls


def test_supplied_plans_use_same_budget_and_partial_gold_scoring_without_network(plan_evaluation):
    manifest, suite_path, suite, plans, calls = plan_evaluation
    plans["D01"]["queries"].append("third bounded expansion")
    report = evaluation.evaluate(manifest, suite_path, "development", "llm", plans)
    relevant, negative = report["cases"]
    assert relevant["recall_at_3"] == .5 and relevant["all_gold_at_3"] == 0
    assert relevant["query_count"] == 3 and relevant["queries"] == plans["D01"]["queries"]
    assert all(call[2:4] == (5, "llm") for call in calls)
    assert [call[1] for call in calls] == [c["context"] | {"jurisdiction": None, "payment_rail": None, "facts": {}}
                                          for c in suite["cases"]]
    assert negative["no_answer_any_hit"] is False
    assert report["summary"]["retrieve_calls"] == 2 and report["summary"]["query_count"] == 5
    assert report["summary"]["plan_error_count"] == 0
    assert report["summary"]["no_answer_noise_comparison_valid"] is True
    assert report["summary"]["llm_planner"] == "supplied_plans_unverified"
    assert report["summary"]["answer_correctness"] == report["summary"]["final_answer_refusal"] == "NotRun"


@pytest.mark.parametrize("bad", [
    None, {}, {"queries": []}, {"queries": ["different original query"]},
    {"queries": ["authored fixture D01", "authored fixture D01"]},
    {"queries": ["authored fixture D01", " "]},
    {"queries": ["authored fixture D01", "x" * 1001]},
    {"queries": ["authored fixture D01", "a", "b", "c"]},
    {"queries": ["authored fixture D01"], "approved": True},
])
def test_invalid_or_missing_plans_keep_denominators_without_fallback(plan_evaluation, bad):
    manifest, suite_path, _, plans, calls = plan_evaluation
    if bad is None:
        plans = {}
    else:
        plans = {"D01": bad, "D02": bad}
    report = evaluation.evaluate(manifest, suite_path, "development", "llm", plans)
    assert calls == []
    relevant, negative = report["cases"]
    assert relevant["status"] == negative["status"] == "error"
    assert relevant["recall_at_3"] == relevant["hit_at_3"] == relevant["reciprocal_rank"] == 0
    assert relevant["retrieve_calls"] == negative["retrieve_calls"] == 0
    assert relevant["queries"] == negative["queries"] == []
    assert negative["no_answer_any_hit"] is None
    summary = report["summary"]
    assert summary["case_count"] == 2 and summary["relevant_count"] == summary["no_answer_count"] == 1
    assert summary["error_count"] == summary["plan_error_count"] == summary["nonexecuted_retrieve_count"] == 2
    assert summary["recall_at_3"] == 0 and summary["retrieve_calls"] == 0
    assert summary["no_answer_completed_count"] == 0 and summary["no_answer_failed_count"] == 1
    assert summary["no_answer_any_hit_rate"] is summary["no_answer_completed_any_hit_rate"] is None
    assert summary["no_answer_noise_comparison_valid"] is False
    assert summary["final_answer_refusal"] == "NotRun"


def test_missing_positive_plan_is_not_dropped_and_negative_success_is_retained(plan_evaluation):
    manifest, suite_path, _, plans, calls = plan_evaluation
    del plans["D01"]
    report = evaluation.evaluate(manifest, suite_path, "development", "llm", plans)
    assert len(calls) == 1 and calls[0][0] == "authored fixture D02"
    assert report["summary"]["relevant_count"] == 1 and report["summary"]["recall_at_5"] == 0
    assert report["summary"]["no_answer_completed_count"] == 1
    assert report["summary"]["no_answer_any_hit_rate"] == 0
    assert report["summary"]["error_count"] == 1


def test_plan_map_does_not_silently_accept_unknown_case_or_unrequested_plans(plan_evaluation):
    manifest, suite_path, _, plans, calls = plan_evaluation
    with pytest.raises(ValueError, match="unknown case IDs"):
        evaluation.evaluate(manifest, suite_path, "development", "llm", plans | {"UNKNOWN": {"queries": ["x"]}})
    with pytest.raises(ValueError, match="requires a case_id"):
        evaluation.evaluate(manifest, suite_path, "development", "llm")
    with pytest.raises(ValueError, match="only for llm"):
        evaluation.evaluate(manifest, suite_path, "development", "literal", plans)
    assert calls == []


@pytest.mark.parametrize("planner, supplied", [("llm", False), ("rules", True)])
def test_cli_requires_plan_file_only_in_explicit_llm_mode(tmp_path, monkeypatch, capsys, planner, supplied):
    args = ["evaluate_public_references.py", "--manifest", str(tmp_path / "manifest.json"),
            "--suite", str(tmp_path / "suite.json"), "--split", "development", "--planner", planner,
            "--output", str(tmp_path / "output.json")]
    if supplied:
        args.extend(("--plans", str(tmp_path / "plans.json")))
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as stopped:
        evaluation.main()
    assert stopped.value.code == 2 and "--plans is required only" in capsys.readouterr().err
    assert not (tmp_path / "output.json").exists()
