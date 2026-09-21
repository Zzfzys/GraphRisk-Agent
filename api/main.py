from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from graphrisk.serving.predictor import RiskPredictor, TransactionNotFoundError
from graphrisk.serving.schemas import (
    HealthResponse,
    ModelInfoResponse,
    RiskPredictionRequest,
    RiskPredictionResponse,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _model_dir() -> Path:
    return Path(
        os.getenv(
            "GRAPHRISK_MODEL_DIR",
            str(PROJECT_ROOT / "artifacts" / "stage-02" / "champion"),
        )
    ).resolve()


def _feature_path() -> Path:
    return Path(
        os.getenv(
            "GRAPHRISK_FEATURE_DATA",
            str(PROJECT_ROOT / "data" / "interim" / "features_all.parquet"),
        )
    ).resolve()


@lru_cache(maxsize=1)
def get_predictor() -> RiskPredictor:
    return RiskPredictor(_model_dir(), _feature_path())


def create_app() -> FastAPI:
    application = FastAPI(
        title="GraphRisk-Agent Risk API",
        version="0.1.0",
        description="Stage 02 transaction risk prediction service",
    )

    @application.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            model_artifact_available=(_model_dir() / "model.joblib").is_file()
            and _feature_path().is_file()
        )

    @application.get("/model/info", response_model=ModelInfoResponse)
    def model_info(
        predictor: RiskPredictor = Depends(get_predictor),
    ) -> ModelInfoResponse:
        return ModelInfoResponse.model_validate(predictor.model_info())

    @application.post("/risk/predict", response_model=RiskPredictionResponse)
    def predict_risk(
        request: RiskPredictionRequest,
        predictor: RiskPredictor = Depends(get_predictor),
    ) -> RiskPredictionResponse:
        try:
            prediction = predictor.predict(request.transaction_id)
        except TransactionNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "transaction_not_found",
                    "transaction_id": str(error),
                },
            ) from error
        return RiskPredictionResponse.model_validate(prediction.__dict__)

    return application


app = create_app()
