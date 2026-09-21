from dataclasses import dataclass

import httpx
import pytest

from api.main import create_app, get_predictor
from graphrisk.serving.predictor import RiskPrediction, TransactionNotFoundError


@dataclass
class FakePredictor:
    def predict(self, transaction_id: str) -> RiskPrediction:
        if transaction_id == "missing":
            raise TransactionNotFoundError(transaction_id)
        return RiskPrediction(
            transaction_id=transaction_id,
            risk_score=0.82,
            risk_level="high",
            model_name="logistic_regression",
            model_version="logistic_regression-v1",
            dataset_source="synthetic",
        )

    def model_info(self) -> dict[str, object]:
        return {
            "model_name": "logistic_regression",
            "model_version": "logistic_regression-v1",
            "dataset_source": "synthetic",
            "selected_by": "validation_pr_auc",
            "validation_metrics": {"pr_auc": 0.15},
            "test_metrics": {"pr_auc": 0.19},
        }


def _transport() -> httpx.ASGITransport:
    application = create_app()
    application.dependency_overrides[get_predictor] = lambda: FakePredictor()
    return httpx.ASGITransport(app=application)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_health_endpoint() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.anyio
async def test_model_info_endpoint() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        response = await client.get("/model/info")

    assert response.status_code == 200
    assert response.json()["selected_by"] == "validation_pr_auc"


@pytest.mark.anyio
async def test_risk_prediction_endpoint() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        response = await client.post(
            "/risk/predict", json={"transaction_id": "T00000001"}
        )

    assert response.status_code == 200
    assert response.json() == {
        "transaction_id": "T00000001",
        "risk_score": 0.82,
        "risk_level": "high",
        "model_name": "logistic_regression",
        "model_version": "logistic_regression-v1",
        "dataset_source": "synthetic",
    }


@pytest.mark.anyio
async def test_unknown_transaction_returns_structured_404() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        response = await client.post("/risk/predict", json={"transaction_id": "missing"})

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "transaction_not_found",
        "transaction_id": "missing",
    }


@pytest.mark.anyio
async def test_invalid_request_returns_422() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        response = await client.post("/risk/predict", json={"transaction_id": "   "})

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "string_too_short"
