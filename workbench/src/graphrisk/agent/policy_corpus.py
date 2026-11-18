"""Validated, reproducible source corpus for the Stage 05 simulated policies."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PolicyMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    policy_id: str
    title: str
    version: int = Field(strict=True, ge=1)
    effective_from: date
    effective_to: date | None
    owner: str
    source_type: Literal["simulated"]
    source_ref: str
    license: str
    public_release: bool = Field(strict=True)
    supersedes: str | None = None
    conflicts_with: tuple[str, ...] = ()

    @field_validator("doc_id", "policy_id", "supersedes")
    @classmethod
    def identifiers_are_safe(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_ID.fullmatch(value):
            raise ValueError("policy identifier must be a safe non-empty name")
        return value

    @field_validator("title", "owner", "source_ref", "license")
    @classmethod
    def provenance_is_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("policy title and provenance fields must be non-empty")
        return value.strip()

    @field_validator("effective_from", "effective_to", mode="before")
    @classmethod
    def dates_are_explicit(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, str) or not _DATE.fullmatch(value):
            raise ValueError("policy dates must be quoted YYYY-MM-DD strings")
        return value

    @field_validator("conflicts_with")
    @classmethod
    def conflict_ids_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(not _SAFE_ID.fullmatch(item) for item in value):
            raise ValueError("conflicts_with must contain unique safe policy IDs")
        return value

    @model_validator(mode="after")
    def interval_and_release_are_valid(self) -> "PolicyMetadata":
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from (exclusive end)")
        if not self.public_release:
            raise ValueError("Stage 05 corpus requires public_release=true")
        if self.policy_id in self.conflicts_with:
            raise ValueError("a policy cannot conflict with itself")
        if self.supersedes == self.doc_id:
            raise ValueError("a policy version cannot supersede itself")
        return self


@dataclass(frozen=True)
class PolicyParagraph:
    section: str
    paragraph_index: int
    line_start: int
    line_end: int
    text: str
    content_sha256: str


@dataclass(frozen=True)
class PolicyDocument:
    metadata: PolicyMetadata
    source_file: str
    content_sha256: str
    paragraphs: tuple[PolicyParagraph, ...]

    def applies_on(self, day: date) -> bool:
        return self.metadata.effective_from <= day and (
            self.metadata.effective_to is None or day < self.metadata.effective_to
        )


@dataclass(frozen=True)
class PolicyCorpus:
    documents: tuple[PolicyDocument, ...]

    def active_on(self, day: date) -> tuple[PolicyDocument, ...]:
        return tuple(document for document in self.documents if document.applies_on(day))

    def manifest_bytes(self) -> bytes:
        entries = []
        for document in self.documents:
            entries.append(
                {
                    **document.metadata.model_dump(mode="json"),
                    "source_file": document.source_file,
                    "content_sha256": document.content_sha256,
                    "paragraphs": [
                        {
                            "section": paragraph.section,
                            "paragraph_index": paragraph.paragraph_index,
                            "line_start": paragraph.line_start,
                            "line_end": paragraph.line_end,
                            "content_sha256": paragraph.content_sha256,
                        }
                        for paragraph in document.paragraphs
                    ],
                }
            )
        content = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        manifest = {"schema_version": "1.0", "corpus_sha256": _sha256(content), "documents": entries}
        return (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _read_document(path: Path) -> PolicyDocument:
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not raw.startswith("---\n"):
        raise ValueError(f"{path.name}: missing YAML front matter")
    closing = raw.find("\n---\n", 4)
    if closing < 0:
        raise ValueError(f"{path.name}: missing closing YAML front matter delimiter")
    try:
        metadata = PolicyMetadata.model_validate(yaml.safe_load(raw[4:closing]))
    except Exception as exc:
        raise ValueError(f"{path.name}: invalid policy provenance or date: {exc}") from exc
    if Path(metadata.source_ref).name != path.name:
        raise ValueError(f"{path.name}: source_ref must identify this simulated source file")
    body = raw[closing + 5 :]
    if not body or not body.startswith(f"# 模拟政策：{metadata.title}\n"):
        raise ValueError(f"{path.name}: body must start with the matching 模拟政策 title")
    if not body.endswith("\n"):
        body += "\n"
    paragraphs = _extract_paragraphs(body, raw[: closing + 5].count("\n"), path.name)
    return PolicyDocument(metadata, path.name, _sha256(body), paragraphs)


def _extract_paragraphs(body: str, offset: int, filename: str) -> tuple[PolicyParagraph, ...]:
    section: str | None = None
    sections: set[str] = set()
    lines: list[str] = []
    start = 0
    end = 0
    counts: dict[str, int] = {}
    paragraphs: list[PolicyParagraph] = []

    def flush() -> None:
        nonlocal lines
        if not lines:
            return
        assert section is not None
        counts[section] = counts.get(section, 0) + 1
        content = "\n".join(lines)
        paragraphs.append(
            PolicyParagraph(section, counts[section], start, end, content, _sha256(content))
        )
        lines = []

    for line_number, line in enumerate(body.splitlines(), start=offset + 1):
        if line.startswith("## "):
            flush()
            section = line[3:].strip()
            if not section or section in sections:
                raise ValueError(f"{filename}: sections need unique non-empty headings")
            sections.add(section)
        elif line.startswith("# ") and line_number == offset + 1:
            continue
        elif line.startswith("#"):
            raise ValueError(f"{filename}: only ## section headings are supported")
        elif not line.strip():
            flush()
        else:
            if section is None:
                raise ValueError(f"{filename}: policy text needs a ## section heading")
            if not lines:
                start = line_number
            lines.append(line)
            end = line_number
    flush()
    if not paragraphs:
        raise ValueError(f"{filename}: policy must contain at least one section paragraph")
    return tuple(paragraphs)


def load_policy_corpus(source_dir: str | Path) -> PolicyCorpus:
    root = Path(source_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"policy source directory does not exist: {root}")
    files = sorted(root.glob("*.md"), key=lambda path: path.name)
    if not files:
        raise ValueError(f"policy source directory has no Markdown documents: {root}")
    documents = tuple(_read_document(path) for path in files)
    by_doc_id: dict[str, PolicyDocument] = {}
    by_policy: dict[str, list[PolicyDocument]] = {}
    for document in documents:
        meta = document.metadata
        if meta.doc_id in by_doc_id:
            raise ValueError(f"duplicate policy doc_id: {meta.doc_id}")
        by_doc_id[meta.doc_id] = document
        by_policy.setdefault(meta.policy_id, []).append(document)
    for policy_id, versions in by_policy.items():
        versions.sort(key=lambda document: (document.metadata.effective_from, document.metadata.version))
        for previous, current in zip(versions, versions[1:]):
            if previous.metadata.effective_to is None or previous.metadata.effective_to > current.metadata.effective_from:
                raise ValueError(f"overlapping effective versions for policy_id: {policy_id}")
            if current.metadata.version <= previous.metadata.version:
                raise ValueError(f"policy versions must increase for policy_id: {policy_id}")
            if current.metadata.supersedes != previous.metadata.doc_id:
                raise ValueError(f"new policy version must supersede {previous.metadata.doc_id}")
    policy_ids = set(by_policy)
    for document in documents:
        meta = document.metadata
        if meta.supersedes is not None:
            previous = by_doc_id.get(meta.supersedes)
            if previous is None:
                raise ValueError(f"unknown supersedes doc_id: {meta.supersedes}")
            if previous.metadata.policy_id != meta.policy_id or previous.metadata.version >= meta.version:
                raise ValueError(f"{meta.doc_id}: supersedes must name an older version of the same policy")
        if any(other not in policy_ids for other in meta.conflicts_with):
            raise ValueError(f"{meta.doc_id}: conflicts_with refers to an unknown policy_id")
    ordered = tuple(sorted(documents, key=lambda doc: (doc.metadata.policy_id, doc.metadata.effective_from, doc.metadata.doc_id)))
    return PolicyCorpus(ordered)


def write_policy_manifest(corpus: PolicyCorpus, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(corpus.manifest_bytes())
    return path
