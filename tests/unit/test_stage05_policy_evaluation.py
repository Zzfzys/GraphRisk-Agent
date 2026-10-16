"""Policy retrieval evaluation uses pinned labels and honest ranking metrics."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from graphrisk.agent.policy_evaluation import EvaluationCase, load_evaluation_suite, score_case, summarize
from graphrisk.agent.policy_corpus import load_policy_corpus
from graphrisk.agent.policy_retrieval import PolicyEvidence, PolicyHit, RetrievalResult, build_policy_index


ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "data/evaluation/stage05_policy_queries_v1.yaml"


@pytest.fixture(scope="module")
def index():
    return build_policy_index(load_policy_corpus(ROOT / "data/policies/stage05"))


def _hit(index, chunk_id: str, score: float) -> PolicyHit:
    chunk = next(chunk for chunk in index.chunks if chunk.chunk_id == chunk_id)
    return PolicyHit(chunk=PolicyEvidence.model_validate(chunk.model_dump(exclude={"embedding"})), score=score)


def test_suite_has_pinned_source_grounded_dev_and_frozen_labels(index) -> None:
    suite, digest = load_evaluation_suite(SUITE, index)
    assert len(suite.cases) == 30
    assert digest == "ae0e508957ab4c3bed6cfa20069ecb56d7ce8180f8e1735f2efa8354c782ae83"
    assert {case.split for case in suite.cases} == {"development", "frozen"}
    assert any("version" in case.tags for case in suite.cases)
    assert any(case.kind == "unanswerable" for case in suite.cases)


def test_recall_mrr_false_positive_and_expired_reference_are_separate(index) -> None:
    active = [chunk.chunk_id for chunk in index.chunks if chunk.applies_on(date(2026, 7, 1))]
    target = "policy-5f3069ff0c7ef7a0ab2f20e0"
    distractor = next(chunk_id for chunk_id in active if chunk_id != target)
    old = "policy-dc6d395f92da538bb6c180d1"
    answerable = EvaluationCase(case_id="T1", split="development", kind="answerable", tags=("version",),
                                query="设备共享", policy_as_of="2026-07-01", expected_chunk_ids=(target,),
                                forbidden_chunk_ids=(old,))
    ranked = RetrievalResult(status="ok", hits=(_hit(index, distractor, .9), _hit(index, target, .8)))
    scored = score_case(answerable, ranked, 12.0, index)
    assert (scored.recall_at_1, scored.recall_at_3, scored.recall_at_5, scored.reciprocal_rank) == (0, 1, 1, .5)
    assert not scored.old_version_violation
    expired = score_case(answerable, RetrievalResult(status="ok", hits=(_hit(index, old, .9),)), 10, index)
    assert expired.old_version_violation and expired.recall_at_3 == 0
    no_answer = EvaluationCase(case_id="T2", split="development", kind="unanswerable", tags=("out_of_scope",),
                               query="差旅报销", policy_as_of="2026-07-01", refusal_reason="not in corpus")
    false_hit = score_case(no_answer, RetrievalResult(status="ok", hits=(_hit(index, target, .2),)), 2, index)
    assert false_hit.false_positive and false_hit.recall_at_3 is None
    summary = summarize([scored, false_hit])
    assert summary["recall_at_3"] == 1.0 and summary["mrr"] == .5
    assert summary["no_answer_false_positive_rate"] == 1.0


def test_suite_rejects_changed_corpus_and_unknown_labeled_chunk(tmp_path: Path, index) -> None:
    bad_index = index.model_copy(update={"corpus_sha256": "0" * 64})
    with pytest.raises(ValueError, match="corpus SHA-256"):
        load_evaluation_suite(SUITE, bad_index)
    source = SUITE.read_text(encoding="utf-8")
    bad_suite = tmp_path / "bad.yaml"
    bad_suite.write_text(source.replace("policy-dc6d395f92da538bb6c180d1", "policy-not-real", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown labeled chunk ID"):
        load_evaluation_suite(bad_suite, index)
