from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

TransactionId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    model_artifact_available: bool


class ModelInfoResponse(BaseModel):
    model_name: str
    model_version: str
    dataset_source: str
    selected_by: str | None = None
    validation_metrics: dict[str, float]
    test_metrics: dict[str, float]


class RiskPredictionRequest(BaseModel):
    transaction_id: TransactionId


class RiskPredictionResponse(BaseModel):
    transaction_id: str
    risk_score: float = Field(ge=0, le=1)
    risk_level: Literal["low", "medium", "high"]
    model_name: str
    model_version: str
    dataset_source: str
