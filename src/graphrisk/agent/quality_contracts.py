"""Bounded model judgments; authoritative facts/IDs stay outside the draft."""
from typing import Literal
from pydantic import Field
from graphrisk.investigation.contracts import StrictModel

QUALITY_VERSION = "stage09-agent-quality-v1"

class QualityDraft(StrictModel):
    policy_ref: str = Field(pattern=r"^P[1-9][0-9]*$")
    history_activity: Literal["observed", "none", "unavailable"]
    relationship_evidence: Literal["observed", "none", "unavailable"]
    graph_evidence: Literal["available", "unavailable"]
    policy_applicability: Literal["not_established", "established"]
    risk_interpretation: Literal["uncalibrated_signal", "confirmed_fraud"]
    evidence_refs: list[str] = Field(min_length=1, max_length=5)
    unknowns: list[Literal["policy_conditions", "verified_identity", "calibration", "graph_evidence"]] = Field(min_length=1, max_length=4)

class QualityAnalysis(StrictModel):
    protocol_version: Literal["stage09-agent-quality-v1"] = QUALITY_VERSION
    packet_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    draft: QualityDraft
    statements: list[str]
    gaps: list[str]
    source_records: dict[str, list[str]]
