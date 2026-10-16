"""Frozen-label evaluation for Stage 05 policy retrieval."""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import date
from pathlib import Path
from statistics import median
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from graphrisk.agent.policy_retrieval import PolicyIndex, PolicyRetriever, RetrievalResult


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    split: Literal["development", "frozen"]
    kind: Literal["answerable", "unanswerable"]
    tags: tuple[str, ...]
    query: str = Field(min_length=1)
    policy_as_of: str
    expected_chunk_ids: tuple[str, ...] = ()
    forbidden_chunk_ids: tuple[str, ...] = ()
    refusal_reason: str | None = None

    @model_validator(mode="after")
    def labels_are_consistent(self) -> "EvaluationCase":
        if not self.query.strip() or not self.tags or len(set(self.tags)) != len(self.tags):
            raise ValueError(f"{self.case_id}: query and unique tags are required")
        if len(set(self.expected_chunk_ids)) != len(self.expected_chunk_ids):
            raise ValueError(f"{self.case_id}: expected chunk IDs must be unique")
        if len(set(self.forbidden_chunk_ids)) != len(self.forbidden_chunk_ids):
            raise ValueError(f"{self.case_id}: forbidden chunk IDs must be unique")
        if set(self.expected_chunk_ids) & set(self.forbidden_chunk_ids):
            raise ValueError(f"{self.case_id}: a chunk cannot be both expected and forbidden")
        if self.kind == "answerable" and (not self.expected_chunk_ids or self.refusal_reason is not None):
            raise ValueError(f"{self.case_id}: answerable case needs expected chunks and no refusal reason")
        if self.kind == "unanswerable" and (self.expected_chunk_ids or not self.refusal_reason):
            raise ValueError(f"{self.case_id}: unanswerable case needs a refusal reason and no expected chunks")
        return self


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    corpus_sha256: str
    cases: tuple[EvaluationCase, ...]

    @model_validator(mode="after")
    def enough_independent_cases(self) -> "EvaluationSuite":
        if len(self.cases) < 24 or len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("evaluation needs at least 24 cases with unique IDs")
        for split in ("development", "frozen"):
            members = [case for case in self.cases if case.split == split]
            if len(members) < 12 or sum(case.kind == "answerable" for case in members) < 8 or sum(case.kind == "unanswerable" for case in members) < 3:
                raise ValueError(f"{split} needs at least 12 cases, 8 answerable and 3 unanswerable")
        return self


def load_evaluation_suite(path: str | Path, index: PolicyIndex) -> tuple[EvaluationSuite, str]:
    source = Path(path).read_bytes()
    suite = EvaluationSuite.model_validate(yaml.safe_load(source))
    if suite.corpus_sha256 != index.corpus_sha256:
        raise ValueError("evaluation corpus SHA-256 differs from policy index; relabel before evaluation")
    by_id = {chunk.chunk_id: chunk for chunk in index.chunks}
    for case in suite.cases:
        try:
            day = date.fromisoformat(case.policy_as_of)
            if day.isoformat() != case.policy_as_of:
                raise ValueError("noncanonical date")
        except ValueError as exc:
            raise ValueError(f"{case.case_id}: policy_as_of must be YYYY-MM-DD") from exc
        for chunk_id in (*case.expected_chunk_ids, *case.forbidden_chunk_ids):
            if chunk_id not in by_id:
                raise ValueError(f"{case.case_id}: unknown labeled chunk ID {chunk_id}")
        if any(not by_id[chunk_id].applies_on(day) for chunk_id in case.expected_chunk_ids):
            raise ValueError(f"{case.case_id}: expected chunk is not effective on policy_as_of")
        if "no_active_policy" in case.tags and any(chunk.applies_on(day) for chunk in index.chunks):
            raise ValueError(f"{case.case_id}: no_active_policy tag conflicts with active policies")
    return suite, hashlib.sha256(source).hexdigest()


class CaseEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    split: str
    kind: str
    tags: tuple[str, ...]
    query: str
    policy_as_of: str
    expected_chunk_ids: tuple[str, ...]
    retrieved_chunk_ids: tuple[str, ...]
    scores: tuple[float, ...]
    status: str
    error_code: str | None
    latency_ms: float
    recall_at_1: float | None
    recall_at_3: float | None
    recall_at_5: float | None
    reciprocal_rank: float | None
    false_positive: bool
    old_version_violation: bool
    refusal_reason: str | None


def score_case(case: EvaluationCase, result: RetrievalResult, latency_ms: float, index: PolicyIndex) -> CaseEvaluation:
    retrieved = tuple(hit.chunk.chunk_id for hit in result.hits)
    scores = tuple(hit.score for hit in result.hits)
    expected = set(case.expected_chunk_ids)
    day = date.fromisoformat(case.policy_as_of)
    inactive = {chunk.chunk_id for chunk in index.chunks if not chunk.applies_on(day)}

    def recall(k: int) -> float | None:
        if case.kind == "unanswerable":
            return None
        return len(expected.intersection(retrieved[:k])) / len(expected)

    rank = next((position for position, chunk_id in enumerate(retrieved, 1) if chunk_id in expected), None)
    return CaseEvaluation(
        case_id=case.case_id, split=case.split, kind=case.kind, tags=case.tags,
        query=case.query, policy_as_of=case.policy_as_of, expected_chunk_ids=case.expected_chunk_ids,
        retrieved_chunk_ids=retrieved, scores=scores, status=result.status, error_code=result.error_code,
        latency_ms=round(latency_ms, 3), recall_at_1=recall(1), recall_at_3=recall(3), recall_at_5=recall(5),
        reciprocal_rank=(1 / rank if rank else 0.0) if case.kind == "answerable" else None,
        false_positive=case.kind == "unanswerable" and bool(retrieved),
        old_version_violation=bool(set(retrieved) & (set(case.forbidden_chunk_ids) | inactive)),
        refusal_reason=case.refusal_reason,
    )


def summarize(cases: list[CaseEvaluation]) -> dict[str, object]:
    answerable = [case for case in cases if case.kind == "answerable"]
    unanswerable = [case for case in cases if case.kind == "unanswerable"]
    version = [case for case in cases if "version" in case.tags]
    latencies = sorted(case.latency_ms for case in cases)
    if not answerable or not unanswerable:
        raise ValueError("evaluation split needs answerable and unanswerable cases")

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 6)

    return {
        "case_count": len(cases), "answerable_count": len(answerable), "unanswerable_count": len(unanswerable),
        "recall_at_1": mean([case.recall_at_1 or 0.0 for case in answerable]),
        "recall_at_3": mean([case.recall_at_3 or 0.0 for case in answerable]),
        "recall_at_5": mean([case.recall_at_5 or 0.0 for case in answerable]),
        "mrr": mean([case.reciprocal_rank or 0.0 for case in answerable]),
        "no_answer_false_positive_rate": mean([float(case.false_positive) for case in unanswerable]),
        "old_version_violation_rate": mean([float(case.old_version_violation) for case in version]) if version else None,
        "error_count": sum(case.status == "error" for case in cases),
        "latency_ms_p50": round(median(latencies), 3),
        "latency_ms_p95": round(latencies[math.ceil(0.95 * len(latencies)) - 1], 3),
        "latency_ms_max": round(latencies[-1], 3),
        "failed_case_ids": [case.case_id for case in cases if (case.kind == "answerable" and (case.recall_at_3 or 0) < 1) or case.false_positive or case.old_version_violation or case.status == "error"],
    }


def evaluate_suite(suite: EvaluationSuite, suite_sha256: str, index: PolicyIndex, retriever: PolicyRetriever, split: Literal["development", "frozen"]) -> dict[str, object]:
    results = []
    for case in suite.cases:
        if case.split != split:
            continue
        start = time.perf_counter()
        result = retriever.retrieve_policy(case.query, case.policy_as_of, 5)
        latency_ms = (time.perf_counter() - start) * 1000
        results.append(score_case(case, result, latency_ms, index))
    return {
        "schema_version": "1.0", "suite_sha256": suite_sha256, "corpus_sha256": suite.corpus_sha256,
        "index_version": index.index_version, "backend": index.backend, "model_id": index.model_id,
        "model_revision": index.model_revision, "split": split, "top_k": 5,
        "summary": summarize(results), "cases": [case.model_dump(mode="json") for case in results],
    }


def write_evaluation_report(report: dict[str, object], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return target
