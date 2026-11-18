"""Exact-source policy annotations, measured premises, and unknown prerequisites."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from graphrisk.agent.autonomous_tools import FrozenModel, ToolBoundaryError
from graphrisk.agent.policy_retrieval import PolicyEvidence, PolicyIndex, RetrievalResult
from graphrisk.agent.upgrade_contract import digest


class Requirement(FrozenModel):
    metric: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    subject: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    unit: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    window_seconds: int | None = Field(ge=1)
    operator: Literal["gte", "eq"]
    expected: int | bool


class PolicyAnnotation(FrozenModel):
    chunk_id: str
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    purpose: Literal["trigger_condition", "exception_condition", "evidence_guidance", "general_guidance"]
    requirements: tuple[Requirement, ...]


class AnnotationManifest(FrozenModel):
    schema_version: Literal["policy-requirements-v1"]
    index_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    annotations: tuple[PolicyAnnotation, ...]


class PolicyRegistry:
    """Server-approved index and paragraph-hash annotations. No LLM extraction.

    An unknown paragraph has unknown applicability; it cannot inherit a threshold
    just because its policy name or a number happens to match an old rule.
    """
    def __init__(self, index: PolicyIndex, manifest: AnnotationManifest):
        # Revalidate even if an external caller used model_copy(update=...).
        index = PolicyIndex.model_validate_json(index.model_dump_json())
        manifest = AnnotationManifest.model_validate_json(manifest.model_dump_json())
        if index.index_version != manifest.index_version:
            raise ToolBoundaryError("policy_annotation_index_mismatch")
        self.index_version = index.index_version
        self.annotation_version = digest(manifest.model_dump(mode="json"))
        self._chunks = {c.chunk_id: PolicyEvidence.model_validate(c.model_dump(exclude={"embedding"})).model_dump_json() for c in index.chunks}
        if len({a.chunk_id for a in manifest.annotations}) != len(manifest.annotations):
            raise ToolBoundaryError("duplicate_policy_annotation")
        self._annotations = {a.chunk_id: a for a in manifest.annotations}
        for key, annotation in self._annotations.items():
            if key not in self._chunks or PolicyEvidence.model_validate_json(self._chunks[key]).content_sha256 != annotation.content_sha256:
                raise ToolBoundaryError("policy_annotation_source_mismatch")
            if annotation.purpose == "general_guidance" and annotation.requirements:
                raise ToolBoundaryError("policy_annotation_purpose_mismatch")
            if annotation.purpose != "general_guidance" and not annotation.requirements:
                raise ToolBoundaryError("policy_annotation_requirement_missing")

    @classmethod
    def from_file(cls, index: PolicyIndex, path: Path):
        try:
            manifest = AnnotationManifest.model_validate_json(path.read_bytes())
        except (OSError, ValidationError):
            raise ToolBoundaryError("policy_annotation_unavailable") from None
        return cls(index, manifest)

    def evaluate(self, retrieval: RetrievalResult, *, policy_as_of: date, facts: tuple) -> tuple[dict, ...]:
        if retrieval.status != "ok":
            if retrieval.hits:
                raise ToolBoundaryError("policy_result_shape_invalid")
            if retrieval.index_version is not None and retrieval.index_version != self.index_version:
                raise ToolBoundaryError("policy_index_binding_mismatch")
            return ()
        if retrieval.index_version != self.index_version or not retrieval.hits:
            raise ToolBoundaryError("policy_index_binding_mismatch")
        if len({h.chunk.chunk_id for h in retrieval.hits}) != len(retrieval.hits):
            raise ToolBoundaryError("duplicate_policy_hit")
        records = []
        for hit in retrieval.hits:
            chunk = hit.chunk
            expected = self._chunks.get(chunk.chunk_id)
            if expected is None or json.loads(chunk.model_dump_json()) != json.loads(expected):
                raise ToolBoundaryError("policy_source_binding_mismatch")
            if not chunk.applies_on(policy_as_of):
                raise ToolBoundaryError("policy_date_binding_mismatch")
            annotation = self._annotations.get(chunk.chunk_id)
            conditions = []
            for requirement in annotation.requirements if annotation else ():
                matches = [f for f in facts if (f.metric, f.subject, f.unit, f.window_seconds) ==
                           (requirement.metric, requirement.subject, requirement.unit, requirement.window_seconds)]
                fact = matches[-1] if matches else None
                measured = fact is not None and fact.availability == "available" and fact.value is not None
                satisfied = None
                if measured:
                    # bool is not an integer count. Reject semantic type collisions.
                    if type(fact.value) is not type(requirement.expected):
                        raise ToolBoundaryError("policy_measurement_type_mismatch")
                    satisfied = (fact.value >= requirement.expected if requirement.operator == "gte"
                                 else fact.value == requirement.expected)
                conditions.append({**requirement.model_dump(mode="json"),
                                   "measurement": "measured" if measured else "unmeasured",
                                   "fact_ref": fact.fact_id if measured else None,
                                   "observed": fact.value if measured else None, "satisfied": satisfied})
            applicability = "unmapped_requirements"
            if annotation:
                if annotation.purpose == "general_guidance":
                    applicability = "guidance_only"
                elif any(c["measurement"] == "unmeasured" for c in conditions):
                    applicability = "unverified_conditions"
                elif all(c["satisfied"] for c in conditions):
                    applicability = "measured_conditions_only"
                else:
                    applicability = "conditions_not_met"
            records.append({"policy_ref": "P-" + digest([self.index_version, chunk.chunk_id])[:24],
                            "chunk_id": chunk.chunk_id, "policy_id": chunk.policy_id, "doc_id": chunk.doc_id,
                            "version": chunk.version, "section": chunk.section,
                            "line_start": chunk.line_start, "line_end": chunk.line_end,
                            "text": chunk.text, "content_sha256": chunk.content_sha256,
                            "source_type": chunk.source_type, "license": chunk.license,
                            "effective_from": chunk.effective_from.isoformat(),
                            "effective_to": chunk.effective_to.isoformat() if chunk.effective_to else None,
                            "purpose": annotation.purpose if annotation else "unmapped",
                            "applicability": applicability, "conditions": conditions,
                            "conflicts_with": list(chunk.conflicts_with), "conflict_state": "not_observed",
                            "content_trust": "untrusted_policy_data"})
        return tuple(records)


def mark_potential_conflicts(records: tuple[dict, ...]) -> tuple[dict, ...]:
    """Known conflict metadata is not proof that either trigger is satisfied."""
    policy_ids = {r["policy_id"] for r in records}
    return tuple({**r, "conflict_state": "potential_conflict_requires_review" if
                  any(other in policy_ids for other in r["conflicts_with"]) or
                  any(r["policy_id"] in other["conflicts_with"] for other in records)
                  else "not_observed"} for r in records)
