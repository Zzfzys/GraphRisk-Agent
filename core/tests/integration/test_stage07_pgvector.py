"""Real PostgreSQL/pgvector policy retrieval contracts (isolated test database)."""

from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from graphrisk.agent.pgvector_retrieval import (
    PgvectorPolicyRetriever, import_policy_vectors, migrate_policy_vectors,
)
from graphrisk.agent.policy_corpus import load_policy_corpus
from graphrisk.agent.policy_retrieval import PolicyRetriever, build_policy_index, write_policy_index


ROOT = Path(__file__).resolve().parents[2]

class IndexedFixture:
    def __init__(self, dsn, index, path, provider):
        self.dsn, self.index, self.path, self.provider = dsn, index, path, provider

    def __iter__(self):
        return iter((self.dsn, self.index, self.path, self.provider))

    def __repr__(self):
        return "<isolated pgvector fixture>"


class FakeEmbedding:
    model_id = "stage07-test-embedding"

    def __init__(self, revision: str, vector: list[float] | None = None):
        self.model_revision = revision
        self.vector = vector or [1.0, 0.0, 0.0]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.vector[:] for _ in texts]


@pytest.fixture
def indexed(tmp_path: Path):
    dsn = os.getenv("GRAPHRISK_STAGE07_MIGRATION_DSN")
    if not dsn:
        pytest.skip("Set GRAPHRISK_STAGE07_MIGRATION_DSN for real pgvector tests")
    migrate_policy_vectors(dsn)
    provider = FakeEmbedding(hashlib.sha256(uuid4().bytes).hexdigest())
    index = build_policy_index(load_policy_corpus(ROOT / "data/policies/stage05"), "local_embedding", provider)
    path = write_policy_index(index, tmp_path / "dense.json")
    import_policy_vectors(dsn, index)
    try:
        yield IndexedFixture(dsn, index, path, provider)
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM policy_vector_chunks WHERE index_version = %s", (index.index_version,))
            conn.execute("DELETE FROM policy_vector_indexes WHERE index_version = %s", (index.index_version,))


def test_import_is_idempotent_and_dates_precede_exact_ranking(indexed) -> None:
    dsn, index, path, provider = indexed
    assert import_policy_vectors(dsn, index) == len(index.chunks) == 16
    local = PolicyRetriever(path, provider)
    remote = PgvectorPolicyRetriever(path, provider, dsn)
    assert remote.check()
    for day in ("2026-06-30", "2026-07-01"):
        expected = local.retrieve_policy("共享设备 人工复核", day, 20)
        actual = remote.retrieve_policy("共享设备 人工复核", day, 20)
        assert actual.status == expected.status == "ok"
        assert [h.chunk.chunk_id for h in actual.hits] == [h.chunk.chunk_id for h in expected.hits]
        assert all(h.chunk.applies_on(date.fromisoformat(day)) for h in actual.hits)
        assert all(h.chunk.content_sha256 and h.chunk.source_ref for h in actual.hits)
    assert remote.retrieve_policy("共享设备", "2025-01-01").error_code == "no_active_policy"


def test_no_answer_and_invalid_inputs_do_not_produce_citations(indexed) -> None:
    dsn, _, path, provider = indexed
    provider.vector = [0.0, 1.0, 0.0]
    remote = PgvectorPolicyRetriever(path, provider, dsn)
    assert remote.retrieve_policy("不相关问题", "2026-07-01").status == "no_match"
    for query, day, k, code in [
        ("", "2026-07-01", 3, "empty_query"),
        ("共享设备", "2026-02-30", 3, "invalid_policy_date"),
        ("共享设备", "2026-07-01", 0, "invalid_top_k"),
    ]:
        result = remote.retrieve_policy(query, day, k)
        assert result.error_code == code and not result.hits


def test_model_digest_dimension_and_database_outage_fail_closed(indexed) -> None:
    dsn, _, path, provider = indexed
    wrong_model = FakeEmbedding("b" * 64)
    assert not PgvectorPolicyRetriever(path, wrong_model, dsn).check()
    assert PgvectorPolicyRetriever(path, wrong_model, dsn).retrieve_policy("共享设备", "2026-07-01").error_code == "embedding_model_mismatch"
    provider.vector = [1.0, 0.0]
    assert PgvectorPolicyRetriever(path, provider, dsn).retrieve_policy("共享设备", "2026-07-01").error_code == "embedding_dimension_mismatch"
    bad_dsn = "postgresql://bad:bad@127.0.0.1:1/missing?connect_timeout=1"
    result = PgvectorPolicyRetriever(path, provider, bad_dsn).retrieve_policy("共享设备", "2026-07-01")
    assert result.status == "error" and result.error_code == "index_unavailable" and not result.hits


def test_metadata_or_vector_corruption_rejects_all_evidence(indexed) -> None:
    dsn, index, path, provider = indexed
    chunk = index.chunks[0]
    remote = PgvectorPolicyRetriever(path, provider, dsn)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        original = conn.execute("SELECT evidence_json FROM policy_vector_chunks WHERE index_version=%s AND chunk_id=%s",
                                (index.index_version, chunk.chunk_id)).fetchone()["evidence_json"]
        changed = {**original, "title": "Tampered"}
        conn.execute("UPDATE policy_vector_chunks SET evidence_json=%s WHERE index_version=%s AND chunk_id=%s",
                     (Jsonb(changed), index.index_version, chunk.chunk_id))
    try:
        result = remote.retrieve_policy("共享设备", "2026-07-01")
        assert result.error_code == "policy_source_mismatch" and not result.hits
        with pytest.raises(ValueError, match="differs"):
            import_policy_vectors(dsn, index)
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("UPDATE policy_vector_chunks SET evidence_json=%s WHERE index_version=%s AND chunk_id=%s",
                         (Jsonb(original), index.index_version, chunk.chunk_id))
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE policy_vector_chunks SET embedding='[0,1,0]'::vector WHERE index_version=%s AND chunk_id=%s",
                     (index.index_version, chunk.chunk_id))
    assert remote.retrieve_policy("共享设备", "2026-07-01").error_code == "index_unavailable"


def test_missing_index_version_and_read_only_application_role(indexed) -> None:
    dsn, index, path, provider = indexed
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM policy_vector_chunks WHERE index_version=%s", (index.index_version,))
    remote = PgvectorPolicyRetriever(path, provider, dsn)
    assert not remote.check()
    assert remote.retrieve_policy("共享设备", "2026-07-01").error_code == "index_unavailable"
    app_dsn = os.getenv("GRAPHRISK_STAGE07_APP_DSN")
    if app_dsn:
        with psycopg.connect(app_dsn) as conn:
            row = conn.execute("SELECT has_table_privilege('policy_vector_chunks', 'SELECT'), has_table_privilege('policy_vector_chunks', 'INSERT')").fetchone()
        assert row == (True, False)
