"""Version-pinned pgvector storage for the Stage 05 policy retrieval contract."""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Callable

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from graphrisk.agent.policy_retrieval import (
    _DENSE_HIGH_CONFIDENCE, _DENSE_MIN_SIMILARITY, _bigrams, _unit_vector,
    EmbeddingProvider, OllamaEmbeddingProvider, PolicyEvidence, PolicyHit,
    PolicyIndex, RetrievalResult, load_policy_index, validate_retrieval_request,
)


MIGRATION_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS policy_vector_indexes (
    index_version text PRIMARY KEY,
    corpus_sha256 text NOT NULL,
    model_id text NOT NULL,
    model_revision text NOT NULL,
    embedding_dimensions integer NOT NULL CHECK (embedding_dimensions > 0),
    chunk_count integer NOT NULL CHECK (chunk_count > 0)
);
CREATE TABLE IF NOT EXISTS policy_vector_chunks (
    index_version text NOT NULL REFERENCES policy_vector_indexes(index_version),
    chunk_id text NOT NULL,
    doc_id text NOT NULL,
    policy_id text NOT NULL,
    policy_version integer NOT NULL,
    content_sha256 text NOT NULL,
    source_ref text NOT NULL,
    effective_from date NOT NULL,
    effective_to date,
    model_revision text NOT NULL,
    embedding_dimensions integer NOT NULL,
    evidence_json jsonb NOT NULL,
    embedding vector NOT NULL,
    PRIMARY KEY (index_version, chunk_id),
    CHECK (effective_to IS NULL OR effective_from < effective_to),
    CHECK (vector_dims(embedding) = embedding_dimensions)
);
CREATE INDEX IF NOT EXISTS policy_vector_chunks_dates_idx
    ON policy_vector_chunks(index_version, effective_from, effective_to);
"""


class PolicySourceMismatch(ValueError):
    """Stored policy text or provenance differs from the pinned local corpus."""


def _vector_literal(values: tuple[float, ...]) -> str:
    return "[" + ",".join(str(value) for value in values) + "]"


def migrate_policy_vectors(dsn: str) -> None:
    """Run explicitly with the migration role, before granting read-only app access."""
    if not dsn:
        raise ValueError("PostgreSQL migration DSN is required")
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            conn.execute(MIGRATION_SQL)


def _check_rows(conn: psycopg.Connection, index: PolicyIndex) -> None:
    catalog = conn.execute(
        "SELECT * FROM policy_vector_indexes WHERE index_version = %s", (index.index_version,)
    ).fetchone()
    if (catalog is None or catalog["corpus_sha256"] != index.corpus_sha256
            or catalog["model_id"] != index.model_id or catalog["model_revision"] != index.model_revision
            or catalog["embedding_dimensions"] != index.embedding_dimensions
            or catalog["chunk_count"] != len(index.chunks)):
        raise ValueError("pgvector index metadata differs from pinned local index")
    rows = conn.execute(
        """SELECT *, embedding::text AS vector_text FROM policy_vector_chunks
           WHERE index_version = %s ORDER BY chunk_id""", (index.index_version,)
    ).fetchall()
    if len(rows) != len(index.chunks):
        raise ValueError("pgvector index has missing or extra chunks")
    for row, chunk in zip(rows, index.chunks):
        evidence = chunk.model_dump(mode="json", exclude={"embedding"})
        actual_vector = json.loads(row["vector_text"])
        if row["content_sha256"] != chunk.content_sha256 or row["evidence_json"] != evidence:
            raise PolicySourceMismatch(f"pgvector source differs from pinned index: {chunk.chunk_id}")
        if (row["chunk_id"] != chunk.chunk_id or row["doc_id"] != chunk.doc_id
                or row["policy_id"] != chunk.policy_id or row["policy_version"] != chunk.version
                or row["source_ref"] != chunk.source_ref
                or row["effective_from"] != chunk.effective_from or row["effective_to"] != chunk.effective_to
                or row["model_revision"] != index.model_revision
                or row["embedding_dimensions"] != index.embedding_dimensions
                or len(actual_vector) != index.embedding_dimensions
                or any(abs(a - b) > 1e-6 for a, b in zip(actual_vector, chunk.embedding or ()))):
            raise ValueError(f"pgvector source, model or vector differs from pinned index: {chunk.chunk_id}")


def import_policy_vectors(dsn: str, index: PolicyIndex) -> int:
    """Idempotently import a validated dense index; conflicting content is an error."""
    if index.backend != "local_embedding":
        raise ValueError("pgvector import requires a local_embedding index")
    if not dsn:
        raise ValueError("PostgreSQL migration DSN is required")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(7017, 5)")
            exists = conn.execute(
                "SELECT 1 FROM policy_vector_indexes WHERE index_version = %s", (index.index_version,)
            ).fetchone()
            if exists:
                _check_rows(conn, index)
                return len(index.chunks)
            conn.execute(
                """INSERT INTO policy_vector_indexes
                   (index_version, corpus_sha256, model_id, model_revision, embedding_dimensions, chunk_count)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (index.index_version, index.corpus_sha256, index.model_id,
                 index.model_revision, index.embedding_dimensions, len(index.chunks)),
            )
            for chunk in index.chunks:
                conn.execute(
                    """INSERT INTO policy_vector_chunks
                       (index_version, chunk_id, doc_id, policy_id, policy_version,
                        content_sha256, source_ref, effective_from, effective_to,
                        model_revision, embedding_dimensions, evidence_json, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)""",
                    (index.index_version, chunk.chunk_id, chunk.doc_id, chunk.policy_id,
                     chunk.version, chunk.content_sha256, chunk.source_ref, chunk.effective_from,
                     chunk.effective_to, index.model_revision, index.embedding_dimensions,
                     Jsonb(chunk.model_dump(mode="json", exclude={"embedding"})),
                     _vector_literal(chunk.embedding or ())),
                )
            _check_rows(conn, index)
            return len(index.chunks)


class PgvectorPolicyRetriever:
    """Exact cosine query with date gating and source verification on every request."""

    def __init__(self, index_path: str | Path, provider: EmbeddingProvider, dsn: str):
        if not dsn:
            raise ValueError("GRAPHRISK_STAGE07_DSN is required for pgvector retrieval")
        self.index_path, self.provider, self.dsn = Path(index_path), provider, dsn

    def with_deadline(self, remaining: Callable[[], float]) -> "PgvectorPolicyRetriever":
        provider = self.provider.with_deadline(remaining) if isinstance(self.provider, OllamaEmbeddingProvider) else self.provider
        return PgvectorPolicyRetriever(self.index_path, provider, self.dsn)

    def check(self) -> bool:
        try:
            index = load_policy_index(self.index_path)
            if (index.backend != "local_embedding" or self.provider.model_id != index.model_id
                    or self.provider.model_revision != index.model_revision):
                return False
            with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=3) as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                _check_rows(conn, index)
            return True
        except (OSError, ValueError, psycopg.Error):
            return False

    def retrieve_policy(self, query: str, policy_as_of: date | str, top_k: int = 3) -> RetrievalResult:
        day, error = validate_retrieval_request(query, policy_as_of, top_k)
        if error is not None:
            return error
        try:
            index = load_policy_index(self.index_path)
            if index.backend != "local_embedding":
                raise ValueError("pgvector requires a dense policy index")
        except (OSError, ValueError) as exc:
            return RetrievalResult(status="error", error_code="index_unavailable", message=str(exc))
        if self.provider.model_id != index.model_id or self.provider.model_revision != index.model_revision:
            return RetrievalResult(status="error", error_code="embedding_model_mismatch",
                                   message="configured embedding provider does not match index",
                                   index_version=index.index_version, backend="pgvector")
        try:
            with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=3) as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                conn.execute("SET LOCAL statement_timeout = '5000ms'")
                _check_rows(conn, index)
                active = conn.execute(
                    """SELECT count(*) AS count FROM policy_vector_chunks
                       WHERE index_version = %s AND effective_from <= %s
                         AND (effective_to IS NULL OR %s < effective_to)""",
                    (index.index_version, day, day),
                ).fetchone()["count"]
                if not active:
                    return RetrievalResult(status="no_match", error_code="no_active_policy",
                                           index_version=index.index_version, backend="pgvector")
                try:
                    vectors = self.provider.embed([query.strip()])
                    if len(vectors) != 1 or len(vectors[0]) != index.embedding_dimensions:
                        return RetrievalResult(status="error", error_code="embedding_dimension_mismatch",
                                               message="query embedding dimension differs from index",
                                               index_version=index.index_version, backend="pgvector")
                    vector = _unit_vector(vectors[0])
                except (httpx.HTTPError, ValueError, TypeError) as exc:
                    return RetrievalResult(status="error", error_code="embedding_unavailable",
                                           message=str(exc), index_version=index.index_version, backend="pgvector")
                literal = _vector_literal(vector)
                rows = conn.execute(
                    """SELECT chunk_id, 1 - (embedding <=> %s::vector) AS similarity
                       FROM policy_vector_chunks
                       WHERE index_version = %s AND effective_from <= %s
                         AND (effective_to IS NULL OR %s < effective_to)
                       ORDER BY embedding <=> %s::vector, chunk_id""",
                    (literal, index.index_version, day, day, literal),
                ).fetchall()
        except PolicySourceMismatch as exc:
            return RetrievalResult(status="error", error_code="policy_source_mismatch",
                                   message=str(exc), index_version=index.index_version,
                                   backend="pgvector")
        except (psycopg.Error, ValueError, TypeError) as exc:
            return RetrievalResult(status="error", error_code="index_unavailable",
                                   message=f"pgvector index cannot be verified or queried: {type(exc).__name__}",
                                   index_version=index.index_version, backend="pgvector")
        by_id = {chunk.chunk_id: chunk for chunk in index.chunks}
        query_grams = _bigrams(query.strip())
        min_overlap = min(3, max(1, (len(query_grams) + 1) // 2))
        hits = []
        for row in rows:
            chunk = by_id[row["chunk_id"]]
            score = float(row["similarity"])
            if not math.isfinite(score):
                return RetrievalResult(status="error", error_code="index_unavailable",
                                       index_version=index.index_version, backend="pgvector")
            if score > _DENSE_MIN_SIMILARITY and (
                len(query_grams & _bigrams(chunk.text)) >= min_overlap or score >= _DENSE_HIGH_CONFIDENCE
            ):
                hits.append(PolicyHit(
                    chunk=PolicyEvidence.model_validate(chunk.model_dump(exclude={"embedding"})),
                    score=round(score, 10),
                ))
        if not hits:
            return RetrievalResult(status="no_match", error_code="no_match",
                                   index_version=index.index_version, backend="pgvector")
        return RetrievalResult(status="ok", index_version=index.index_version,
                               backend="pgvector", hits=tuple(hits[:top_k]))
