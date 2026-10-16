"""Route 2 integrity, date-gating, retrieval and failure contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graphrisk.agent.config import Stage05Retrieval
from graphrisk.agent.policy_corpus import load_policy_corpus
from graphrisk.agent.policy_retrieval import PolicyRetriever, build_policy_index, load_policy_index, write_policy_index


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/policies/stage05"


class FakeEmbedding:
    model_id = "fake-embedding"
    model_revision = "a" * 64

    def __init__(self, query_dimension: int = 3):
        self.query_dimension = query_dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Fixed values isolate dimension, normalization and ranking from Ollama.
        return [[1.0] + [0.0] * (self.query_dimension - 1) for _ in texts]


class OrthogonalQueryEmbedding(FakeEmbedding):
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 1.0, 0.0] for _ in texts]


def test_two_lexical_builds_have_identical_bytes_ids_and_order(tmp_path: Path) -> None:
    corpus = load_policy_corpus(SOURCE)
    first = build_policy_index(corpus)
    second = build_policy_index(corpus)
    path_a = write_policy_index(first, tmp_path / "a.json")
    path_b = write_policy_index(second, tmp_path / "b.json")
    assert path_a.read_bytes() == path_b.read_bytes()
    assert len(first.chunks) == 16
    assert [chunk.chunk_id for chunk in first.chunks] == sorted(chunk.chunk_id for chunk in first.chunks)
    assert load_policy_index(path_a) == first
    assert all(chunk.source_ref and chunk.content_sha256 and chunk.line_start <= chunk.line_end for chunk in first.chunks)


def test_policy_date_excludes_superseded_version_before_ranking(tmp_path: Path) -> None:
    path = write_policy_index(build_policy_index(load_policy_corpus(SOURCE)), tmp_path / "index.json")
    retriever = PolicyRetriever(path)
    old = retriever.retrieve_policy("共享设备 人工复核", "2026-06-30", 20)
    new = retriever.retrieve_policy("共享设备 人工复核", "2026-07-01", 20)
    assert old.status == new.status == "ok"
    assert any(hit.chunk.doc_id == "S05-DEVICE-SHARING-V1" for hit in old.hits)
    assert all(hit.chunk.doc_id != "S05-DEVICE-SHARING-V2" for hit in old.hits)
    assert any(hit.chunk.doc_id == "S05-DEVICE-SHARING-V2" for hit in new.hits)
    assert all(hit.chunk.doc_id != "S05-DEVICE-SHARING-V1" for hit in new.hits)
    assert old.index_version == new.index_version


def test_no_match_and_invalid_input_never_invent_evidence(tmp_path: Path) -> None:
    path = write_policy_index(build_policy_index(load_policy_corpus(SOURCE)), tmp_path / "index.json")
    retriever = PolicyRetriever(path)
    assert retriever.retrieve_policy("zzzxxyyqv", "2026-07-01").status == "no_match"
    assert retriever.retrieve_policy("员工差旅报销政策是什么？", "2026-07-01").status == "no_match"
    assert retriever.retrieve_policy("客户身份证OCR核验的置信度阈值是多少？", "2026-07-01").status == "no_match"
    assert retriever.retrieve_policy("报告修改后还能沿用之前的批准吗？", "2026-07-01").status == "ok"
    for query, day, k, code in [
        ("", "2026-07-01", 3, "empty_query"),
        ("共享设备", "2026-02-30", 3, "invalid_policy_date"),
        ("共享设备", "2026-07-01", 0, "invalid_top_k"),
    ]:
        result = retriever.retrieve_policy(query, day, k)
        assert result.status == "error" and result.error_code == code and not result.hits


def test_corrupted_index_is_structured_failure(tmp_path: Path) -> None:
    path = write_policy_index(build_policy_index(load_policy_corpus(SOURCE)), tmp_path / "index.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["chunks"][0]["text"] += "篡改"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    result = PolicyRetriever(path).retrieve_policy("共享设备", "2026-07-01")
    assert result.status == "error" and result.error_code == "index_unavailable" and not result.hits


def test_dense_index_pins_model_normalizes_and_checks_query_dimension(tmp_path: Path) -> None:
    provider = FakeEmbedding()
    index = build_policy_index(load_policy_corpus(SOURCE), "local_embedding", provider)
    assert index.embedding_dimensions == 3 and index.normalized
    path = write_policy_index(index, tmp_path / "dense.json")
    matching = PolicyRetriever(path, provider).retrieve_policy("设备共享", "2026-07-01", 2)
    assert matching.status == "ok" and len(matching.hits) == 2
    assert "embedding" not in matching.hits[0].chunk.model_dump()
    assert [hit.chunk.chunk_id for hit in matching.hits] == sorted(hit.chunk.chunk_id for hit in matching.hits)
    mismatch = PolicyRetriever(path, FakeEmbedding(query_dimension=2)).retrieve_policy("设备共享", "2026-07-01")
    assert mismatch.status == "error" and mismatch.error_code == "embedding_dimension_mismatch" and not mismatch.hits
    wrong_model = FakeEmbedding()
    wrong_model.model_revision = "b" * 64
    result = PolicyRetriever(path, wrong_model).retrieve_policy("设备共享", "2026-07-01")
    assert result.error_code == "embedding_model_mismatch" and not result.hits
    unrelated = PolicyRetriever(path, OrthogonalQueryEmbedding()).retrieve_policy("无关查询", "2026-07-01")
    assert unrelated.status == "no_match" and not unrelated.hits


def test_dense_config_requires_digest_and_loopback_endpoint() -> None:
    valid = {"backend": "local_embedding", "embedding_model_id": "qwen3-embedding:0.6b", "embedding_model_revision": "a" * 64,
             "embedding_endpoint": "http://127.0.0.1:11434", "top_k": 3}
    assert Stage05Retrieval.model_validate(valid).embedding_model_id == "qwen3-embedding:0.6b"
    for change in ({"embedding_model_revision": None}, {"embedding_endpoint": "https://example.com"}, {"embedding_endpoint": "http://127.0.0.1:11434/path"}):
        with pytest.raises(ValueError):
            Stage05Retrieval.model_validate({**valid, **change})
