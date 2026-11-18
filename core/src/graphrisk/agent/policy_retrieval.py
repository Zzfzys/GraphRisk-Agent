"""Versioned, date-gated local policy indexes and a structured retriever."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Callable, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sklearn.feature_extraction.text import TfidfVectorizer

from graphrisk.agent.policy_corpus import PolicyCorpus


# Conservative provisional abstention gate; route 3 evaluates it on a frozen query set.
_DENSE_MIN_SIMILARITY = 0.45
_DENSE_HIGH_CONFIDENCE = 0.65


def _bigrams(value: str) -> set[str]:
    return {token[position : position + 2] for token in re.findall(r"[a-z0-9\u4e00-\u9fff]+", value.lower()) for position in range(len(token) - 1)}


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class PolicyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str
    doc_id: str
    policy_id: str
    title: str
    version: int
    section: str
    paragraph_index: int
    line_start: int
    line_end: int
    text: str
    content_sha256: str
    source_file: str
    source_ref: str
    source_type: str
    license: str
    effective_from: date
    effective_to: date | None
    conflicts_with: tuple[str, ...]

    def applies_on(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_to is None or day < self.effective_to)


class PolicyChunk(PolicyEvidence):
    embedding: tuple[float, ...] | None = None


class PolicyIndex(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    index_version: str
    corpus_sha256: str
    backend: Literal["tfidf", "local_embedding"]
    model_id: str | None = None
    model_revision: str | None = None
    embedding_dimensions: int | None = None
    normalized: bool = False
    chunks: tuple[PolicyChunk, ...]

    @model_validator(mode="after")
    def check_integrity(self) -> "PolicyIndex":
        if not self.chunks or len({c.chunk_id for c in self.chunks}) != len(self.chunks):
            raise ValueError("index needs unique non-empty chunk IDs")
        if tuple(sorted(self.chunks, key=lambda c: c.chunk_id)) != self.chunks:
            raise ValueError("index chunks must be ordered by stable chunk ID")
        for chunk in self.chunks:
            if chunk.content_sha256 != hashlib.sha256(chunk.text.encode("utf-8")).hexdigest():
                raise ValueError(f"chunk text hash mismatch: {chunk.chunk_id}")
            identity = [chunk.doc_id, chunk.version, chunk.section, chunk.paragraph_index, chunk.content_sha256]
            if chunk.chunk_id != "policy-" + _digest(identity)[:24]:
                raise ValueError(f"chunk ID mismatch: {chunk.chunk_id}")
        if self.backend == "tfidf":
            if self.model_id is not None or self.model_revision is not None or self.embedding_dimensions is not None or self.normalized:
                raise ValueError("tfidf index cannot declare embedding model or vectors")
            if any(chunk.embedding is not None for chunk in self.chunks):
                raise ValueError("tfidf index cannot contain embeddings")
        else:
            if not self.model_id or not self.model_revision or not self.embedding_dimensions or not self.normalized:
                raise ValueError("local_embedding index needs pinned model, dimension and normalization")
            for chunk in self.chunks:
                vector = chunk.embedding
                if vector is None or len(vector) != self.embedding_dimensions or not all(math.isfinite(x) for x in vector):
                    raise ValueError(f"embedding dimension or value mismatch: {chunk.chunk_id}")
                if abs(sum(x * x for x in vector) - 1.0) > 0.0001:
                    raise ValueError(f"embedding is not unit normalized: {chunk.chunk_id}")
        payload = self.model_dump(mode="json", exclude={"index_version"})
        if self.index_version != _digest(payload):
            raise ValueError("index version/content hash mismatch")
        return self


class EmbeddingProvider(Protocol):
    model_id: str
    model_revision: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbeddingProvider:
    """Pinned local or Docker host Ollama adapter with per-batch digest checks."""

    def __init__(self, model_id: str, model_revision: str, base_url: str = "http://127.0.0.1:11434", timeout: float = 180.0,
                 time_limit: Callable[[], float] | None = None):
        parsed = httpx.URL(base_url)
        local = parsed.host in {"localhost", "127.0.0.1", "::1"}
        docker_host = parsed.host == "host.docker.internal" and parsed.port == 11434
        if (parsed.scheme != "http" or not (local or docker_host) or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise ValueError("embedding endpoint must use loopback or the fixed Docker host Ollama address")
        self.model_id = model_id
        self.model_revision = model_revision
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.time_limit = time_limit

    def with_deadline(self, remaining: Callable[[], float]) -> "OllamaEmbeddingProvider":
        return OllamaEmbeddingProvider(self.model_id, self.model_revision, self.base_url, self.timeout, remaining)

    def _request_timeout(self) -> float:
        remaining = self.time_limit() if self.time_limit is not None else self.timeout
        if remaining <= 0:
            raise httpx.TimeoutException("Stage 05 total deadline exhausted")
        return min(self.timeout, remaining)

    def embed(self, texts: list[str]) -> list[list[float]]:
        with httpx.Client(timeout=self.timeout) as client:
            tags = client.get(f"{self.base_url}/api/tags", timeout=self._request_timeout())
            tags.raise_for_status()
            models = tags.json().get("models", [])
            if not any(item.get("name") == self.model_id and item.get("digest") == self.model_revision for item in models):
                raise ValueError("embedding model is missing or its digest differs from the pinned revision")
            response = client.post(f"{self.base_url}/api/embed", json={"model": self.model_id, "input": texts, "truncate": False},
                                   timeout=self._request_timeout())
            response.raise_for_status()
            vectors = response.json().get("embeddings")
            if not isinstance(vectors, list) or len(vectors) != len(texts):
                raise ValueError("Ollama returned an invalid embedding batch")
            return vectors


def _unit_vector(values: list[float]) -> tuple[float, ...]:
    if not values or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
        raise ValueError("embedding contains no values or non-finite values")
    norm = math.sqrt(sum(float(v) ** 2 for v in values))
    if norm <= 0:
        raise ValueError("embedding has zero norm")
    return tuple(round(float(v) / norm, 10) for v in values)


def build_policy_index(corpus: PolicyCorpus, backend: Literal["tfidf", "local_embedding"] = "tfidf", provider: EmbeddingProvider | None = None) -> PolicyIndex:
    if backend == "local_embedding" and provider is None:
        raise ValueError("local_embedding index requires a provider")
    if backend == "tfidf" and provider is not None:
        raise ValueError("tfidf index does not use an embedding provider")
    chunks = []
    for document in corpus.documents:
        meta = document.metadata
        for paragraph in document.paragraphs:
            if len(paragraph.text) > 2000:
                raise ValueError(f"policy paragraph exceeds 2000 characters; edit the source without splitting its condition: {meta.doc_id}")
            identity = [meta.doc_id, meta.version, paragraph.section, paragraph.paragraph_index, paragraph.content_sha256]
            chunks.append(PolicyChunk(
                chunk_id="policy-" + _digest(identity)[:24], doc_id=meta.doc_id, policy_id=meta.policy_id,
                title=meta.title, version=meta.version, section=paragraph.section,
                paragraph_index=paragraph.paragraph_index, line_start=paragraph.line_start, line_end=paragraph.line_end,
                text=paragraph.text, content_sha256=paragraph.content_sha256, source_file=document.source_file,
                source_ref=meta.source_ref, source_type=meta.source_type, license=meta.license,
                effective_from=meta.effective_from, effective_to=meta.effective_to,
                conflicts_with=meta.conflicts_with,
            ))
    chunks.sort(key=lambda chunk: chunk.chunk_id)
    dimensions = None
    if provider is not None:
        vectors = provider.embed([chunk.text for chunk in chunks])
        if len(vectors) != len(chunks):
            raise ValueError("embedding provider returned the wrong number of vectors")
        normalized = [_unit_vector(vector) for vector in vectors]
        dimensions = len(normalized[0])
        if any(len(vector) != dimensions for vector in normalized):
            raise ValueError("embedding provider returned inconsistent dimensions")
        chunks = [chunk.model_copy(update={"embedding": vector}) for chunk, vector in zip(chunks, normalized)]
    payload = {
        "schema_version": "1.0", "corpus_sha256": hashlib.sha256(corpus.manifest_bytes()).hexdigest(),
        "backend": backend, "model_id": provider.model_id if provider else None,
        "model_revision": provider.model_revision if provider else None,
        "embedding_dimensions": dimensions, "normalized": provider is not None,
        "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
    }
    return PolicyIndex.model_validate({**payload, "index_version": _digest(payload)})


def write_policy_index(index: PolicyIndex, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_canonical(index.model_dump(mode="json")) + b"\n")
    return target


def load_policy_index(path: str | Path) -> PolicyIndex:
    return PolicyIndex.model_validate_json(Path(path).read_bytes())


class PolicyHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    chunk: PolicyEvidence
    score: float


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["ok", "no_match", "error"]
    error_code: str | None = None
    message: str | None = None
    index_version: str | None = None
    backend: str | None = None
    hits: tuple[PolicyHit, ...] = ()


def validate_retrieval_request(query: str, policy_as_of: date | str, top_k: int) -> tuple[date | None, RetrievalResult | None]:
    """Keep local and PostgreSQL request errors identical."""
    if not isinstance(query, str) or not query.strip():
        return None, RetrievalResult(status="error", error_code="empty_query", message="query must contain text")
    if len(query) > 2000:
        return None, RetrievalResult(status="error", error_code="invalid_query", message="query exceeds 2000 characters")
    if type(top_k) is not int or not 1 <= top_k <= 20:
        return None, RetrievalResult(status="error", error_code="invalid_top_k", message="top_k must be an integer from 1 to 20")
    try:
        day = date.fromisoformat(policy_as_of) if isinstance(policy_as_of, str) else policy_as_of
        if not isinstance(day, date) or (isinstance(policy_as_of, str) and day.isoformat() != policy_as_of):
            raise ValueError("invalid date")
    except ValueError:
        return None, RetrievalResult(status="error", error_code="invalid_policy_date", message="policy_as_of must be YYYY-MM-DD")
    return day, None


class PolicyRetriever:
    def __init__(self, index_path: str | Path, provider: EmbeddingProvider | None = None):
        self.index_path = Path(index_path)
        self.provider = provider

    def with_deadline(self, remaining: Callable[[], float]) -> "PolicyRetriever":
        provider = self.provider.with_deadline(remaining) if isinstance(self.provider, OllamaEmbeddingProvider) else self.provider
        return PolicyRetriever(self.index_path, provider)

    def retrieve_policy(self, query: str, policy_as_of: date | str, top_k: int = 3) -> RetrievalResult:
        day, error = validate_retrieval_request(query, policy_as_of, top_k)
        if error is not None:
            return error
        try:
            index = load_policy_index(self.index_path)
        except (OSError, ValueError) as exc:
            return RetrievalResult(status="error", error_code="index_unavailable", message=f"policy index cannot be loaded or is corrupt: {exc}")
        active = [chunk for chunk in index.chunks if chunk.applies_on(day)]
        if not active:
            return RetrievalResult(status="no_match", error_code="no_active_policy", index_version=index.index_version, backend=index.backend)
        if index.backend == "tfidf":
            texts = [chunk.text for chunk in active]
            vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), lowercase=True, norm="l2")
            matrix = vectorizer.fit_transform(texts)
            scores = (matrix @ vectorizer.transform([query.strip()]).T).toarray().ravel().tolist()
        else:
            if self.provider is None or self.provider.model_id != index.model_id or self.provider.model_revision != index.model_revision:
                return RetrievalResult(status="error", error_code="embedding_model_mismatch", message="configured embedding provider does not match index", index_version=index.index_version, backend=index.backend)
            try:
                values = self.provider.embed([query.strip()])
                if len(values) != 1 or len(values[0]) != index.embedding_dimensions:
                    return RetrievalResult(status="error", error_code="embedding_dimension_mismatch", message="query embedding dimension differs from index", index_version=index.index_version, backend=index.backend)
                vector = _unit_vector(values[0])
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                return RetrievalResult(status="error", error_code="embedding_unavailable", message=str(exc), index_version=index.index_version, backend=index.backend)
            scores = [sum(a * b for a, b in zip(vector, chunk.embedding or ())) for chunk in active]
        minimum = _DENSE_MIN_SIMILARITY if index.backend == "local_embedding" else 0.0
        query_grams = _bigrams(query.strip())
        min_overlap = min(3, max(1, (len(query_grams) + 1) // 2))
        hits = [
            PolicyHit(chunk=PolicyEvidence.model_validate(chunk.model_dump(exclude={"embedding"})), score=round(float(score), 10))
            for chunk, score in zip(active, scores)
            if score > minimum and (
                len(query_grams & _bigrams(chunk.text)) >= min_overlap
                or (index.backend == "local_embedding" and score >= _DENSE_HIGH_CONFIDENCE)
            )
        ]
        hits.sort(key=lambda hit: (-hit.score, hit.chunk.chunk_id))
        if not hits:
            return RetrievalResult(status="no_match", error_code="no_match", index_version=index.index_version, backend=index.backend)
        return RetrievalResult(status="ok", index_version=index.index_version, backend=index.backend, hits=tuple(hits[:top_k]))
