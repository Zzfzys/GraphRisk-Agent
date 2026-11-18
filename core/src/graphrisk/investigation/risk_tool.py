from __future__ import annotations

from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, Field, ValidationError

from graphrisk.config import ProjectConfig
from graphrisk.investigation.contracts import (
    DatasetSource,
    ErrorCode,
    EvidenceRef,
    GetRiskScoreRequest,
    GetRiskScoreResult,
    RiskScoreData,
    validate_tool_request,
)
from graphrisk.investigation.repository import InvestigationRepository
from graphrisk.investigation.transaction_tools import get_transaction_features
from graphrisk.serving.predictor import RiskPredictor, TransactionNotFoundError
from graphrisk.training.config import BaselineTrainingConfig


class _RiskThresholds(BaseModel):
    low: float = Field(ge=0, le=1)
    high: float = Field(ge=0, le=1)


class _ModelMetadata(BaseModel):
    dataset_source: DatasetSource
    model_name: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=128)
    risk_thresholds: _RiskThresholds


class RiskModelTool:
    """Validate source binding once, then reuse the Stage 02 predictor."""

    def __init__(
        self,
        repository: InvestigationRepository,
        *,
        training_source: str,
        serving_feature_parquet: Path,
        model_dir: Path,
    ) -> None:
        self.repository = repository
        self._predictor: RiskPredictor | None = None
        self._startup_error: ErrorCode | None = None

        if (
            training_source != repository.dataset_source
            or serving_feature_parquet.resolve() != repository.feature_parquet.resolve()
        ):
            self._startup_error = "data_source_mismatch"
            return
        if not repository.feature_parquet.is_file():
            self._startup_error = "feature_artifact_unavailable"
            return

        model_path = model_dir / "model.joblib"
        metadata_path = model_dir / "metadata.json"
        if not model_path.is_file() or not metadata_path.is_file():
            self._startup_error = "model_artifact_unavailable"
            return
        try:
            metadata = _ModelMetadata.model_validate_json(
                metadata_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError):
            self._startup_error = "model_artifact_unavailable"
            return
        if metadata.dataset_source != repository.dataset_source:
            self._startup_error = "data_source_mismatch"
            return
        if metadata.risk_thresholds.low >= metadata.risk_thresholds.high:
            self._startup_error = "model_artifact_unavailable"
            return

        try:
            self._predictor = RiskPredictor(model_dir, repository.feature_parquet)
        except Exception:
            # Artifact loading is an external dependency boundary; never expose its path.
            self._startup_error = "model_artifact_unavailable"

    @classmethod
    def from_configs(
        cls,
        data_config: ProjectConfig,
        training_config: BaselineTrainingConfig,
    ) -> "RiskModelTool":
        repository = InvestigationRepository.from_config(data_config)
        return cls(
            repository,
            training_source=training_config.dataset_source,
            serving_feature_parquet=training_config.resolve_path(
                training_config.paths.serving_features
            ),
            model_dir=training_config.resolve_path(
                training_config.paths.output_dir
            ) / "champion",
        )

    @property
    def startup_error(self) -> ErrorCode | None:
        return self._startup_error

    def get_risk_score(self, raw_request: Mapping[str, object]) -> GetRiskScoreResult:
        request, failure = validate_tool_request(
            GetRiskScoreRequest, GetRiskScoreResult, raw_request
        )
        if failure is not None:
            return failure
        assert request is not None
        if request.dataset_source != self.repository.dataset_source:
            return self._failure(
                "data_source_mismatch", request.transaction_id, request.dataset_source
            )
        if self._startup_error is not None:
            return self._failure(
                self._startup_error, request.transaction_id, request.dataset_source
            )
        assert self._predictor is not None

        feature_result = get_transaction_features(
            request.model_dump(), self.repository
        )
        if feature_result.error is not None:
            return self._failure(
                feature_result.error.code,
                request.transaction_id,
                request.dataset_source,
            )

        try:
            prediction = self._predictor.predict(request.transaction_id)
            if prediction.dataset_source != request.dataset_source:
                return self._failure(
                    "data_source_mismatch",
                    request.transaction_id,
                    request.dataset_source,
                )
            if prediction.transaction_id != request.transaction_id:
                raise ValueError("prediction transaction ID differs from the request")
            data = RiskScoreData.model_validate(
                {
                    "transaction_id": prediction.transaction_id,
                    "risk_score": prediction.risk_score,
                    "risk_level": prediction.risk_level,
                    "model_name": prediction.model_name,
                    "model_version": prediction.model_version,
                    "probability_calibrated": False,
                }
            )
        except TransactionNotFoundError:
            # The canonical transaction was present; the serving feature row vanished.
            return self._failure(
                "feature_record_missing", request.transaction_id, request.dataset_source
            )
        except Exception:
            return self._failure(
                "prediction_failed", request.transaction_id, request.dataset_source
            )
        return GetRiskScoreResult(
            status="ok",
            transaction_id=request.transaction_id,
            dataset_source=request.dataset_source,
            data=data,
            evidence=[
                EvidenceRef(
                    source_kind="model_artifact",
                    record_id=data.model_version,
                    dataset_source=request.dataset_source,
                )
            ],
        )

    @staticmethod
    def _failure(
        code: ErrorCode, transaction_id: str, dataset_source: DatasetSource
    ) -> GetRiskScoreResult:
        return GetRiskScoreResult.failure(
            code=code,
            transaction_id=transaction_id,
            dataset_source=dataset_source,
        )


def get_risk_score(
    raw_request: Mapping[str, object], tool: RiskModelTool
) -> GetRiskScoreResult:
    return tool.get_risk_score(raw_request)
