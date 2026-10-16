from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from graphrisk.serving.request_trace import new_request_id, trace_event

from api.investigation import _service_error, router as investigation_router
from graphrisk.serving.predictor import RiskPredictor, TransactionNotFoundError
from graphrisk.serving.investigation_service import ServiceError, Stage06Service
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


def create_app(investigation_service_factory: Callable[[], Stage06Service] | None = None) -> FastAPI:
    # Raw access logs can contain untrusted URL/query text; structured trace replaces them.
    logging.getLogger("uvicorn.access").disabled = True
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        factory = investigation_service_factory
        if factory is None and (config_path := os.getenv("GRAPHRISK_STAGE06_CONFIG")):
            mode = os.getenv("GRAPHRISK_STAGE07_STORAGE", "memory")
            if mode == "postgres":
                from graphrisk.serving.postgres_service import PostgresInvestigationService
                dsn = os.getenv("GRAPHRISK_STAGE07_DSN", "")
                factory = lambda: PostgresInvestigationService.from_config(config_path, dsn=dsn)
            elif mode == "memory":
                factory = lambda: Stage06Service.from_config(config_path)
            else:
                raise ValueError("GRAPHRISK_STAGE07_STORAGE must be memory or postgres")
        service = factory() if factory is not None else None
        if service is not None:
            application.state.stage06_service = service
        try:
            yield
        finally:
            if service is not None:
                service.close()

    application = FastAPI(
        title="GraphRisk-Agent Risk API",
        version="0.1.0",
        description="Evidence-grounded Stage 06 investigation and Stage 02 risk prediction service",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def request_trace(request: Request, call_next):
        request_id = new_request_id()
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            request.state.error_code = "internal_error"
            response = JSONResponse(status_code=500, content={
                "error_code": "internal_error", "message": "The request could not be completed.",
                "run_id": None, "status": None,
            })
        response.headers["X-Request-ID"] = request_id
        route = request.scope.get("route")
        trace_event(
            request_id=request_id, phase="http",
            tool=route.name if route is not None else "unmatched",
            run_id=getattr(request.state, "run_id", None),
            status="completed" if response.status_code < 400 else "error",
            error_code=getattr(request.state, "error_code", None),
            elapsed_ms=(time.perf_counter() - started) * 1000,
            model_version=getattr(request.state, "model_version", None),
            index_version=getattr(request.state, "index_version", None),
            http_status=response.status_code,
        )
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        if request.url.path.startswith("/api/v1/"):
            request.state.error_code = "invalid_request"
            return JSONResponse(status_code=422, content={
                "error_code": "invalid_request", "message": "The request does not match the investigation schema.",
                "run_id": None, "status": None,
            })
        return await request_validation_exception_handler(request, error)

    @application.exception_handler(ServiceError)
    async def investigation_service_error(request: Request, error: ServiceError):
        return _service_error(error, request)

    application.include_router(investigation_router)

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
