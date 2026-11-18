"""Offline, exact-source public references; separate from simulated business rules.

No downloader, model call or executable instruction is accepted at this boundary.
Offsets address Unicode code points in the saved UTF-8/LF excerpt, not HTML bytes.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sklearn.feature_extraction.text import TfidfVectorizer

from graphrisk.agent.policy_retrieval import _bigrams, _digest


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReferenceSpan(FrozenModel):
    section: str = Field(min_length=1)
    # Local excerpt locator, not a verified HTML fragment ID.
    anchor: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    char_start: int = Field(strict=True, ge=0)
    char_end: int = Field(strict=True, ge=1)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    # Curated prerequisites, never extracted or satisfied by an LLM.
    required_facts: tuple[str, ...] = ()

    @field_validator("required_facts")
    @classmethod
    def fact_keys(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values) or any(not v.isidentifier() or not v.isascii() for v in values):
            raise ValueError("required_facts must be unique ASCII identifiers")
        return values


class ReferenceDocument(FrozenModel):
    doc_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    title: str = Field(min_length=1)
    issuer: str = Field(min_length=1)
    source_type: Literal["official_public_reference"]
    source_url: str
    version_label: str = Field(min_length=1)
    version_evidence_url: str
    retrieved_on: date
    snapshot_kind: Literal["manually_verified_plain_text_excerpt"]
    snapshot_file: str = Field(pattern=r"^[A-Za-z0-9_.-]+\.txt$")
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    license: str = Field(min_length=1)
    license_evidence_url: str
    jurisdiction: Literal["US"]
    payment_rail: Literal["eft", "credit_card"]
    # A version label or retrieval date is NOT evidence of a legal effective interval.
    verified_from: date | None = None
    verified_to: date | None = None
    validity_evidence_url: str | None = None
    spans: tuple[ReferenceSpan, ...] = Field(min_length=1)

    @field_validator("source_url", "version_evidence_url", "license_evidence_url", "validity_evidence_url")
    @classmethod
    def official_url(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or parsed.hostname != "www.consumerfinance.gov"
                    or parsed.username or parsed.password or parsed.port not in (None, 443)):
                raise ValueError("this corpus accepts only HTTPS CFPB primary-source URLs")
        return value

    @model_validator(mode="after")
    def dates_and_sections(self):
        if self.verified_to is not None and (self.verified_from is None or self.verified_to <= self.verified_from):
            raise ValueError("verified_to needs an earlier verified_from")
        if (self.verified_from is None) != (self.validity_evidence_url is None):
            raise ValueError("a verified effective interval needs its own official evidence")
        if len({s.section for s in self.spans}) != len(self.spans):
            raise ValueError("duplicate document section")
        return self


class ReferenceManifest(FrozenModel):
    schema_version: Literal["public-reference-excerpts-v1"]
    corpus_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    documents: tuple[ReferenceDocument, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def integrity(self):
        if len({d.doc_id for d in self.documents}) != len(self.documents):
            raise ValueError("duplicate reference document ID")
        if self.corpus_version != _digest(self.model_dump(mode="json", exclude={"corpus_version"})):
            raise ValueError("reference manifest hash mismatch")
        return self


class ReferenceContext(FrozenModel):
    as_of: date
    jurisdiction: str | None = None
    payment_rail: Literal["eft", "credit_card", "other"] | None = None
    facts: dict[str, bool | None] = Field(default_factory=dict, strict=True)

    @field_validator("facts", mode="before")
    @classmethod
    def strict_boolean_facts(cls, values):
        if not isinstance(values, dict) or any(type(v) not in (bool, type(None)) for v in values.values()):
            raise ValueError("facts require explicit booleans or null; counts and strings are not booleans")
        return values


class ReferenceQueryPlan(FrozenModel):
    queries: tuple[str, ...] = Field(min_length=1, max_length=3)

    @field_validator("queries")
    @classmethod
    def bounded_queries(cls, values):
        if any(not q.strip() or len(q) > 1000 for q in values) or len(set(values)) != len(values):
            raise ValueError("query plan needs 1..3 unique nonblank queries of at most 1000 characters")
        return values


# ponytail: bounded bilingual terms for four source sections; measure before adding a semantic backend.
_TERMS = (
    (("未授权", "未经授权", "授权", "代用", "权限"), "unauthorized transfer actual authority benefit furnished access device"),
    (("责任", "过失", "密码", "通知", "报告遗失", "免责"), "consumer liability negligence PIN notice loss theft"),
    (("错误", "争议", "调查", "签字", "书面", "余额", "税务", "副本"), "error resolution investigation written signed statement routine inquiry tax duplicate"),
    (("信用卡", "签名", "购买", "配送", "交付", "拒绝申诉"), "credit card unauthorized use reasonable investigation prior purchases location delivery signature affidavit police report"),
)


def plan_reference_query(query: str, planner: Literal["literal", "rules", "llm"] = "rules",
                         supplied_plan: ReferenceQueryPlan | None = None) -> ReferenceQueryPlan:
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise ValueError("query must contain 1..1000 characters")
    if planner == "llm":
        if supplied_plan is None:
            raise ValueError("llm mode requires a separately obtained, bounded query plan; no model is called here")
        plan = ReferenceQueryPlan.model_validate_json(supplied_plan.model_dump_json())
        if plan.queries[0] != query:
            raise ValueError("a supplied plan must retain the exact original query first")
        return plan
    if supplied_plan is not None or planner not in {"literal", "rules"}:
        raise ValueError("a supplied plan is accepted only in explicit llm mode")
    terms = [english for chinese, english in _TERMS if any(t in query for t in chinese)] if planner == "rules" else []
    return ReferenceQueryPlan(queries=(query, " ".join(terms)) if terms else (query,))


def load_reference_corpus(path: str | Path, expected_version: str | None = None) -> tuple[ReferenceManifest, tuple[dict, ...]]:
    path = Path(path)
    manifest = ReferenceManifest.model_validate_json(path.read_bytes())
    if expected_version is not None and manifest.corpus_version != expected_version:
        raise ValueError("reference corpus differs from the server-pinned version")
    chunks = []
    for doc in manifest.documents:
        snapshot = path.parent / doc.snapshot_file
        if snapshot.resolve().parent != path.parent.resolve():
            raise ValueError("snapshot must resolve inside its corpus directory")
        raw = snapshot.read_bytes()
        if hashlib.sha256(raw).hexdigest() != doc.snapshot_sha256:
            raise ValueError(f"snapshot hash mismatch: {doc.doc_id}")
        text = raw.decode("utf-8")
        if "\r" in text or text.startswith("\ufeff"):
            raise ValueError("snapshot must be UTF-8 without BOM and use LF newlines")
        for span in doc.spans:
            quote = text[span.char_start:span.char_end]
            if (span.char_end > len(text) or span.char_start >= span.char_end or len(quote.strip()) < 2
                    or hashlib.sha256(quote.encode("utf-8")).hexdigest() != span.content_sha256):
                raise ValueError(f"source span mismatch: {doc.doc_id}/{span.section}")
            chunks.append({"chunk_id": "reference-" + _digest([doc.doc_id, doc.snapshot_sha256, span.model_dump(mode="json")])[:24],
                           "document": doc, "span": span, "text": quote})
    return manifest, tuple(chunks)


def _scope(doc: ReferenceDocument, span: ReferenceSpan, context: ReferenceContext) -> dict:
    missing = []
    unmet = []
    for key, expected in (("jurisdiction", doc.jurisdiction), ("payment_rail", doc.payment_rail)):
        actual = getattr(context, key)
        if actual is None:
            missing.append(key)
        elif actual != expected:
            unmet.append(key)
    for key in span.required_facts:
        actual = context.facts.get(key)
        if actual is None:
            missing.append(key)
        elif not actual:
            unmet.append(key)
    if doc.verified_from is None:
        missing.append("effective_interval")
    return {"applicability": "reference_only", "scope_status": "out_of_scope" if unmet else "unknown" if missing else "conditions_supported",
            "missing_facts": sorted(missing), "unmet_conditions": sorted(unmet),
            "date_status": "unverified" if doc.verified_from is None else "verified_interval",
            "legal_conclusion": "not_determined"}


class ReferenceRetrievalResult(FrozenModel):
    status: Literal["ok", "no_match", "error"]
    error_code: str | None = None
    message: str | None = None
    corpus_version: str | None = None
    planner: str
    queries: tuple[str, ...] = ()
    hits: tuple[dict, ...] = ()


class PublicReferenceRetriever:
    """A local opt-in reference tool; it never modifies risk bands or approval rights."""
    def __init__(self, manifest_path: str | Path, expected_version: str | None = None):
        self.manifest_path = Path(manifest_path)
        self.expected_version = expected_version

    def retrieve(self, query: str, context: ReferenceContext, top_k: int = 3, *,
                 planner: Literal["literal", "rules", "llm"] = "rules",
                 supplied_plan: ReferenceQueryPlan | None = None,
                 version_label: str | None = None) -> ReferenceRetrievalResult:
        try:
            if type(top_k) is not int or not 1 <= top_k <= 20:
                raise ValueError("top_k must be an integer from 1 to 20")
            context = ReferenceContext.model_validate_json(context.model_dump_json())
            plan = plan_reference_query(query, planner, supplied_plan)
        except (ValueError, TypeError, AttributeError) as exc:
            return ReferenceRetrievalResult(status="error", error_code="invalid_request", message=str(exc), planner=planner)
        try:
            manifest, chunks = load_reference_corpus(self.manifest_path, self.expected_version)
        except (OSError, ValueError) as exc:
            return ReferenceRetrievalResult(status="error", error_code="corpus_unavailable", message=str(exc), planner=planner)
        active = [c for c in chunks if (version_label is None or c["document"].version_label == version_label)
                  and (c["document"].verified_from is None or c["document"].verified_from <= context.as_of)
                  and (c["document"].verified_to is None or context.as_of < c["document"].verified_to)]
        if not active:
            return ReferenceRetrievalResult(status="no_match", error_code="no_requested_version", corpus_version=manifest.corpus_version,
                                            planner=planner, queries=plan.queries)
        vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), lowercase=True, norm="l2")
        matrix = vectorizer.fit_transform([c["text"] for c in active])
        scores = (matrix @ vectorizer.transform(plan.queries).T).toarray()
        hits = []
        for row, chunk in zip(scores, active):
            valid_scores = [float(score) for score, q in zip(row, plan.queries)
                            if score > 0 and len(_bigrams(q) & _bigrams(chunk["text"])) >= min(3, max(1, len(_bigrams(q)) // 2))]
            if not valid_scores:
                continue
            doc, span = chunk["document"], chunk["span"]
            hits.append({"chunk_id": chunk["chunk_id"], "score": round(max(valid_scores), 10),
                         **doc.model_dump(mode="json", exclude={"spans"}), **span.model_dump(mode="json"),
                         "source_ref": doc.source_url,
                         "html_fragment_verified": False,
                         "offset_basis": "unicode_code_points_in_saved_excerpt",
                         "original_html_sha256": None,
                         "scope_checks_exhaustive": False,
                         "text": chunk["text"], "content_trust": "untrusted_reference_data",
                         **_scope(doc, span, context)})
        hits.sort(key=lambda h: (-h["score"], h["chunk_id"]))
        return ReferenceRetrievalResult(status="ok" if hits else "no_match", error_code=None if hits else "no_match",
                                        corpus_version=manifest.corpus_version, planner=planner, queries=plan.queries,
                                        hits=tuple(hits[:top_k]))
