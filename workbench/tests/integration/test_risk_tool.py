from __future__ import annotations

import json
from pathlib import Path

import duckdb
import httpx
import joblib
import pandas as pd
import pytest

from api.main import create_app, get_predictor
from graphrisk.config import load_config
from graphrisk.investigation.risk_tool import RiskModelTool, get_risk_score
from graphrisk.models.tabular import TabularRiskModel
from graphrisk.serving.predictor import RiskPredictor, TransactionNotFoundError
from graphrisk.training.config import load_training_config


@pytest.fixture
def configs_and_model(tmp_path: Path):
    data_config = load_config("configs/dev.yaml")
    training_config = load_training_config("configs/train_baseline.yaml")
    data_config.set_project_root(tmp_path)
    training_config.set_project_root(tmp_path)
    data_config.paths.interim_transactions = Path("transactions.parquet")
    data_config.paths.interim_features = Path("features.parquet")
    training_config.paths.serving_features = Path("features.parquet")
    training_config.paths.output_dir = Path("model-output")

    rows = []
    for index in range(4):
        rows.append(
            {
                "transaction_id": f"T{index + 1}",
                "transaction_time": pd.Timestamp("2025-01-01") + pd.Timedelta(minutes=index),
                "transaction_amount": float(index + 1),
                "tx_count_last_1h": index,
                "tx_count_last_24h": index,
                "tx_count_last_7d": index,
                "amount_mean_last_24h": float(index) if index else None,
                "amount_std_last_24h": 1.0 if index else None,
                "amount_deviation_last_24h": 1.0 if index else None,
                "transaction_hour": index,
                "transaction_day_of_week": index,
                "card_id": f"card-{index % 2}",
                "device_id": f"device-{index % 2}",
                "email_domain": "example.com",
                "address_id": f"address-{index % 2}",
                "product_code": "A",
                "is_fraud": index % 2,
            }
        )
    frame = pd.DataFrame(rows)
    model = TabularRiskModel.create("dummy", random_seed=7, positive_weight=1.0)
    model.train(frame, frame["is_fraud"])
    model_dir = training_config.resolve_path(training_config.paths.output_dir) / "champion"
    model_dir.mkdir(parents=True)
    joblib.dump(model, model_dir / "model.joblib")
    (model_dir / "metadata.json").write_text(
        json.dumps(
            {
                "dataset_source": "synthetic",
                "model_name": "dummy",
                "model_version": "dummy-test-v1",
                "risk_thresholds": {"low": 0.3, "high": 0.7},
            }
        ),
        encoding="utf-8",
    )
    with duckdb.connect(":memory:") as connection:
        connection.register("frame", frame)
        connection.sql("SELECT * FROM frame").write_parquet(
            str(data_config.resolve_path(data_config.paths.interim_features))
        )
        connection.sql(
            "SELECT transaction_id, transaction_time, transaction_amount, card_id, "
            "device_id, email_domain, address_id, product_code, is_fraud FROM frame"
        ).write_parquet(str(data_config.resolve_path(data_config.paths.interim_transactions)))
    return data_config, training_config, model_dir


def _request(transaction_id: str = "T1", source: str = "synthetic") -> dict[str, object]:
    return {"transaction_id": transaction_id, "dataset_source": source}


def test_risk_score_matches_existing_predictor_and_has_model_evidence(configs_and_model) -> None:
    data_config, training_config, model_dir = configs_and_model
    tool = RiskModelTool.from_configs(data_config, training_config)
    result = get_risk_score(_request(), tool)
    existing = RiskPredictor(
        model_dir, data_config.resolve_path(data_config.paths.interim_features)
    ).predict("T1")

    assert tool.startup_error is None
    assert result.status == "ok"
    assert result.data is not None
    assert result.data.risk_score == existing.risk_score
    assert result.data.risk_level == existing.risk_level
    assert result.data.model_name == existing.model_name
    assert result.data.model_version == existing.model_version
    assert result.data.probability_calibrated is False
    assert result.evidence[0].source_kind == "model_artifact"
    assert result.evidence[0].record_id == existing.model_version
    assert "is_fraud" not in result.model_dump_json()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_risk_score_matches_api_response(configs_and_model) -> None:
    data_config, training_config, model_dir = configs_and_model
    feature_path = data_config.resolve_path(data_config.paths.interim_features)
    app = create_app()
    app.dependency_overrides[get_predictor] = lambda: RiskPredictor(model_dir, feature_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/risk/predict", json={"transaction_id": "T1"})

    tool_result = get_risk_score(_request(), RiskModelTool.from_configs(data_config, training_config))
    assert response.status_code == 200
    assert tool_result.data is not None
    assert tool_result.data.risk_score == response.json()["risk_score"]
    assert tool_result.data.risk_level == response.json()["risk_level"]


def test_mismatched_training_source_is_rejected_before_loading(configs_and_model) -> None:
    data_config, training_config, _ = configs_and_model
    training_config.dataset_source = "ieee_cis"
    tool = RiskModelTool.from_configs(data_config, training_config)
    result = get_risk_score(_request(), tool)
    assert tool.startup_error == "data_source_mismatch"
    assert result.status == "dependency_error"
    assert result.error is not None and result.error.code == "data_source_mismatch"


def test_same_schema_different_feature_path_is_rejected(configs_and_model, tmp_path: Path) -> None:
    data_config, training_config, _ = configs_and_model
    copy_path = tmp_path / "other-features.parquet"
    copy_path.write_bytes(data_config.resolve_path(data_config.paths.interim_features).read_bytes())
    training_config.paths.serving_features = copy_path
    result = get_risk_score(
        _request(), RiskModelTool.from_configs(data_config, training_config)
    )
    assert result.error is not None and result.error.code == "data_source_mismatch"


def test_model_metadata_source_mismatch_is_rejected(configs_and_model) -> None:
    data_config, training_config, model_dir = configs_and_model
    metadata_path = model_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["dataset_source"] = "ieee_cis"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    result = get_risk_score(
        _request(), RiskModelTool.from_configs(data_config, training_config)
    )
    assert result.error is not None and result.error.code == "data_source_mismatch"


@pytest.mark.parametrize("filename", ["model.joblib", "metadata.json"])
def test_missing_model_artifact_is_structured(configs_and_model, filename: str) -> None:
    data_config, training_config, model_dir = configs_and_model
    (model_dir / filename).unlink()
    result = get_risk_score(
        _request(), RiskModelTool.from_configs(data_config, training_config)
    )
    assert result.status == "dependency_error"
    assert result.error is not None and result.error.code == "model_artifact_unavailable"
    assert str(model_dir) not in result.model_dump_json()


def test_missing_feature_file_is_structured(configs_and_model) -> None:
    data_config, training_config, _ = configs_and_model
    data_config.resolve_path(data_config.paths.interim_features).unlink()
    result = get_risk_score(
        _request(), RiskModelTool.from_configs(data_config, training_config)
    )
    assert result.error is not None and result.error.code == "feature_artifact_unavailable"


def test_invalid_metadata_is_not_exposed(configs_and_model) -> None:
    data_config, training_config, model_dir = configs_and_model
    (model_dir / "metadata.json").write_text("{broken", encoding="utf-8")
    result = get_risk_score(
        _request(), RiskModelTool.from_configs(data_config, training_config)
    )
    assert result.error is not None and result.error.code == "model_artifact_unavailable"
    assert "broken" not in result.model_dump_json()


def test_corrupt_model_is_structured_artifact_failure(configs_and_model) -> None:
    data_config, training_config, model_dir = configs_and_model
    (model_dir / "model.joblib").write_text("not a model", encoding="utf-8")
    result = get_risk_score(
        _request(), RiskModelTool.from_configs(data_config, training_config)
    )
    assert result.error is not None and result.error.code == "model_artifact_unavailable"
    assert "not a model" not in result.model_dump_json()


def test_missing_transaction_and_invalid_request_are_distinct(configs_and_model) -> None:
    data_config, training_config, _ = configs_and_model
    tool = RiskModelTool.from_configs(data_config, training_config)
    missing = get_risk_score(_request("missing"), tool)
    invalid = get_risk_score(_request(" "), tool)
    assert missing.status == "not_found"
    assert missing.error is not None and missing.error.code == "transaction_not_found"
    assert invalid.status == "invalid_input"
    assert invalid.error is not None and invalid.error.code == "invalid_transaction_id"


def test_existing_transaction_without_feature_row_is_dependency_error(
    configs_and_model, tmp_path: Path
) -> None:
    data_config, training_config, _ = configs_and_model
    source = data_config.resolve_path(data_config.paths.interim_features)
    shortened = tmp_path / "shortened-features.parquet"
    with duckdb.connect(":memory:") as connection:
        connection.from_parquet(str(source)).filter(
            "transaction_id <> 'T1'"
        ).write_parquet(str(shortened))
    data_config.paths.interim_features = shortened
    training_config.paths.serving_features = shortened
    result = get_risk_score(
        _request(), RiskModelTool.from_configs(data_config, training_config)
    )
    assert result.error is not None and result.error.code == "feature_record_missing"


def test_request_source_mismatch_is_rejected(configs_and_model) -> None:
    data_config, training_config, _ = configs_and_model
    result = get_risk_score(
        _request(source="ieee_cis"), RiskModelTool.from_configs(data_config, training_config)
    )
    assert result.error is not None and result.error.code == "data_source_mismatch"


def test_prediction_failure_is_structured(configs_and_model, monkeypatch) -> None:
    data_config, training_config, _ = configs_and_model
    tool = RiskModelTool.from_configs(data_config, training_config)
    assert tool._predictor is not None

    def fail(_transaction_id: str):
        raise RuntimeError("private failure with path")

    monkeypatch.setattr(tool._predictor, "predict", fail)
    result = get_risk_score(_request(), tool)
    assert result.error is not None and result.error.code == "prediction_failed"
    assert "private failure" not in result.model_dump_json()


def test_feature_disappears_between_lookup_and_prediction(configs_and_model, monkeypatch) -> None:
    data_config, training_config, _ = configs_and_model
    tool = RiskModelTool.from_configs(data_config, training_config)
    assert tool._predictor is not None

    def disappear(transaction_id: str):
        raise TransactionNotFoundError(transaction_id)

    monkeypatch.setattr(tool._predictor, "predict", disappear)
    result = get_risk_score(_request(), tool)
    assert result.error is not None and result.error.code == "feature_record_missing"
