"""Offline retrieval diagnostics for pinned public-reference excerpts, not legal answers."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from graphrisk.agent.public_references import (
    PublicReferenceRetriever,
    ReferenceContext,
    ReferenceQueryPlan,
    ReferenceRetrievalResult,
    load_reference_corpus,
    plan_reference_query,
)


def load_suite(path: Path, manifest_path: Path) -> tuple[dict, str, str]:
    raw = path.read_bytes()
    suite = json.loads(raw)
    if not isinstance(suite, dict) or suite.get("schema_version") != "route7-reference-queries-v1":
        raise ValueError("unsupported public-reference suite schema")
    manifest, chunks = load_reference_corpus(manifest_path)
    if suite.get("corpus_version") != manifest.corpus_version:
        raise ValueError("suite corpus version differs from the reference manifest; review labels before scoring")
    actual = {doc.doc_id: doc.snapshot_sha256 for doc in manifest.documents}
    if suite.get("snapshot_sha256s") != actual:
        raise ValueError("suite snapshot hashes differ from the reference manifest; review labels before scoring")
    available = {(chunk["document"].doc_id, chunk["span"].section) for chunk in chunks}
    cases = suite.get("cases")
    if not isinstance(cases, list) or len(cases) != 24:
        raise ValueError("this pilot suite requires exactly 24 cases")
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("suite cases must be JSON objects")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("suite requires unique nonempty case IDs")
        seen.add(case_id)
        if case.get("split") not in {"development", "frozen"}:
            raise ValueError(f"{case_id}: invalid split")
        query = case.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError(f"{case_id}: query must contain 1..1000 characters")
        gold = case.get("gold_sections")
        if not isinstance(gold, list) or any(not isinstance(g, dict) or set(g) != {"doc_id", "section"} for g in gold):
            raise ValueError(f"{case_id}: gold_sections must identify document and exact saved section locator")
        pairs = [(g["doc_id"], g["section"]) for g in gold]
        if len(set(pairs)) != len(pairs) or any(pair not in available for pair in pairs):
            raise ValueError(f"{case_id}: duplicate or unavailable gold section")
        if case.get("retrieval_kind") not in {"relevant", "no_answer"}:
            raise ValueError(f"{case_id}: invalid retrieval kind")
        if bool(gold) != (case["retrieval_kind"] == "relevant"):
            raise ValueError(f"{case_id}: relevant cases need gold; no_answer cases must have none")
        if case.get("expected_scope") not in {"unknown", "out_of_scope"}:
            raise ValueError(f"{case_id}: this excerpt pilot labels only unknown/out_of_scope boundaries")
        if case.get("application_expected") not in {"reference_only", "conditional", "unknown", "not_applicable", "no_rule_in_corpus"}:
            raise ValueError(f"{case_id}: invalid application label")
        ReferenceContext.model_validate(case.get("context"))
    for split in ("development", "frozen"):
        members = [case for case in cases if case["split"] == split]
        if len(members) != 12 or sum(case["retrieval_kind"] == "relevant" for case in members) != 8:
            raise ValueError(f"{split}: require 12 cases, 8 relevant and 4 no_answer")
    return suite, hashlib.sha256(raw).hexdigest(), manifest.corpus_version


def evaluate(manifest_path: Path, suite_path: Path, split: str, planner: str,
             supplied_plans: dict | None = None) -> dict:
    if split not in {"development", "frozen"} or planner not in {"literal", "rules", "llm"}:
        raise ValueError("only development/frozen and literal/rules/llm are supported; no model is called")
    if planner == "llm" and not isinstance(supplied_plans, dict):
        raise ValueError("llm evaluation requires a case_id-to-plan JSON object; no model is called")
    if planner != "llm" and supplied_plans is not None:
        raise ValueError("supplied plans are accepted only for llm evaluation")
    suite, suite_hash, corpus_version = load_suite(suite_path, manifest_path)
    if supplied_plans is not None and set(supplied_plans) - {case["case_id"] for case in suite["cases"]}:
        raise ValueError("supplied plans contain unknown case IDs")
    retriever = PublicReferenceRetriever(manifest_path, expected_version=corpus_version)
    rows = []
    for case in suite["cases"]:
        if case["split"] != split:
            continue
        context = ReferenceContext.model_validate(case["context"])
        start = time.perf_counter()
        plan = None
        plan_error = None
        retrieve_calls = 0
        if planner == "llm":
            try:
                if case["case_id"] not in supplied_plans:
                    raise ValueError("missing supplied plan for this case")
                plan = plan_reference_query(case["query"], "llm", ReferenceQueryPlan.model_validate(
                    supplied_plans[case["case_id"]]))
            except (ValueError, TypeError) as exc:
                plan_error = str(exc)
        if plan_error is not None:
            result = ReferenceRetrievalResult(status="error", error_code="invalid_supplied_plan",
                                              message=plan_error, planner=planner)
        else:
            kwargs = {"supplied_plan": plan} if planner == "llm" else {}
            result = retriever.retrieve(case["query"], context, top_k=5, planner=planner, **kwargs)
            retrieve_calls = 1
        elapsed = (time.perf_counter() - start) * 1000
        gold = {(g["doc_id"], g["section"]) for g in case["gold_sections"]}
        retrieved = [(hit["doc_id"], hit["section"]) for hit in result.hits]
        rank = next((i for i, pair in enumerate(retrieved, 1) if pair in gold), None)
        relevant = case["retrieval_kind"] == "relevant"
        metrics = {}
        for k in (1, 3, 5):
            matched = gold & set(retrieved[:k])
            metrics[f"hit_at_{k}"] = int(bool(matched)) if relevant else None
            metrics[f"recall_at_{k}"] = len(matched) / len(gold) if relevant else None
            metrics[f"all_gold_at_{k}"] = int(matched == gold) if relevant else None
        unsafe = [hit["chunk_id"] for hit in result.hits
                  if hit.get("applicability") != "reference_only" or hit.get("legal_conclusion") != "not_determined"]
        scope_mismatches = [hit["chunk_id"] for hit in result.hits
                            if (hit["doc_id"], hit["section"]) in gold and hit.get("scope_status") != case["expected_scope"]]
        rows.append({"case_id": case["case_id"], "split": split, "query": case["query"],
                     "context": context.model_dump(mode="json"), "retrieval_kind": case["retrieval_kind"],
                     "application_expected": case["application_expected"], "expected_scope": case["expected_scope"],
                     "gold_sections": case["gold_sections"], "status": result.status, "error_code": result.error_code,
                     "error_message": result.message,
                     "plan_status": ("invalid_supplied_plan" if plan_error is not None else "supplied_plan_validated")
                                    if planner == "llm" else "not_requested",
                     "queries": list(result.queries), "query_count": len(result.queries), "retrieve_calls": retrieve_calls,
                     "hits": list(result.hits), "latency_ms": round(elapsed, 3), **metrics,
                     "reciprocal_rank": (1 / rank if rank else 0.0) if relevant else None,
                     "no_answer_any_hit": bool(result.hits) if not relevant and result.status != "error" else None,
                     "boundary_diagnostics": {"unsafe_legal_conclusion_hit_ids": unsafe,
                                               "gold_scope_mismatch_hit_ids": scope_mismatches},
                     "answer_correctness": "NotRun", "final_answer_refusal": "NotRun"})
    positive = [row for row in rows if row["retrieval_kind"] == "relevant"]
    negative = [row for row in rows if row["retrieval_kind"] == "no_answer"]
    completed_negative = [row for row in negative if row["no_answer_any_hit"] is not None]
    no_answer_noise = sum(row["no_answer_any_hit"] is True for row in negative)
    no_answer_failures = len(negative) - len(completed_negative)
    means = {key: round(sum(row[key] for row in positive) / len(positive), 6)
             for key in [*(f"{name}_at_{k}" for k in (1, 3, 5) for name in ("hit", "recall", "all_gold")), "reciprocal_rank"]}
    summary = {"case_count": len(rows), "relevant_count": len(positive), "no_answer_count": len(negative), **means,
               "no_answer_any_hit_count": no_answer_noise,
               "no_answer_any_hit_rate": no_answer_noise / len(negative) if not no_answer_failures else None,
               "no_answer_completed_count": len(completed_negative), "no_answer_failed_count": no_answer_failures,
               "no_answer_completed_any_hit_rate": no_answer_noise / len(completed_negative) if completed_negative else None,
               "no_answer_noise_comparison_valid": not no_answer_failures,
               "error_count": sum(row["status"] == "error" for row in rows),
               "plan_error_count": sum(row["plan_status"] == "invalid_supplied_plan" for row in rows),
               "nonexecuted_retrieve_count": sum(row["retrieve_calls"] == 0 for row in rows),
               "unsafe_legal_conclusion_hit_count": sum(len(row["boundary_diagnostics"]["unsafe_legal_conclusion_hit_ids"]) for row in rows),
               "gold_scope_mismatch_hit_count": sum(len(row["boundary_diagnostics"]["gold_scope_mismatch_hit_ids"]) for row in rows),
               "retrieve_calls": sum(row["retrieve_calls"] for row in rows), "query_count": sum(row["query_count"] for row in rows),
               "answer_correctness": "NotRun", "final_answer_refusal": "NotRun",
               "legal_applicability_correctness": "NotEvaluated",
               "llm_planner": "supplied_plans_unverified" if planner == "llm" else "NotRun",
               "end_to_end_report_benefit": "NotRun"}
    return {"schema_version": "route7-reference-evaluation-v1", "suite_sha256": suite_hash,
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(), "corpus_version": corpus_version,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "split": split, "planner": planner, "top_k": 5, "summary": summary, "cases": rows,
            "limits": ["Questions were authored from real public excerpts, not real customer requests or transactions.",
                       "Frozen means version-locked; the suite has been visible to this engineering team and is not an independent blind test.",
                       "Any hit on a no-answer question is retrieval noise, not a measured false legal answer or failed refusal.",
                       "Unknown/out-of-scope diagnostics check metadata boundaries, not complete legal correctness.",
                       "Each valid plan makes one retrieve call, top5, with at most three queries; invalid supplied plans never fall back.",
                       "Plan failures remain in relevant-case denominators; failed no-answer cases cannot count as successful noise reduction.",
                       "Supplied plans are replayed without authenticating model origin; this script calls no model or external transport.",
                       "No legal answer or live transaction assessment is performed."]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--split", choices=("development", "frozen"), required=True)
    parser.add_argument("--planner", choices=("literal", "rules", "llm"), required=True)
    parser.add_argument("--plans", type=Path, help="offline JSON object mapping case_id to {queries: [...]} (llm only)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("output already exists; retain prior observations and select a new output path")
        if (args.planner == "llm") != (args.plans is not None):
            raise ValueError("--plans is required only with --planner llm")
        plans = json.loads(args.plans.read_bytes()) if args.plans is not None else None
        report = evaluate(args.manifest, args.suite, args.split, args.planner, plans)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output), "summary": report["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
