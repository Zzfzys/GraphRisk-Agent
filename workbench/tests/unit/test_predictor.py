import json

import duckdb
import joblib
import pandas as pd
import pytest

from graphrisk.models.tabular import TabularRiskModel
from graphrisk.serving.predictor import RiskPredictor, TransactionNotFoundError


def _serving_frame() -> pd.DataFrame:
    rows = []
    for index in range(4):
        rows.append(
            {
                "transaction_id": f"T{index + 1}",
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
            }
        )
    return pd.DataFrame(rows)


def _build_predictor(tmp_path) -> RiskPredictor:
    frame = _serving_frame()
    model = TabularRiskModel.create("dummy", random_seed=7, positive_weight=1.0)
    model.train(frame, pd.Series([0, 1, 0, 1]))

    model_dir = tmp_path / "model"
    model_dir.mkdir()
    joblib.dump(model, model_dir / "model.joblib")
    (model_dir / "metadata.json").write_text(
        json.dumps(
            {
                "model_name": "dummy",
                "model_version": "dummy-test-v1",
                "dataset_source": "test",
                "selected_by": "test_fixture",
                "risk_thresholds": {"low": 0.3, "high": 0.7},
                "validation_metrics": {"pr_auc": 0.5},
                "test_metrics": {"pr_auc": 0.5},
            }
        ),
        encoding="utf-8",
    )
    feature_path = tmp_path / "features.parquet"
    with duckdb.connect() as connection:
        connection.register("feature_frame", frame)
        connection.sql("SELECT * FROM feature_frame").write_parquet(str(feature_path))
    return RiskPredictor(model_dir, feature_path)


def test_predictor_loads_model_and_returns_risk(tmp_path) -> None:
    predictor = _build_predictor(tmp_path)

    prediction = predictor.predict("T1")

    assert prediction.transaction_id == "T1"
    assert prediction.risk_score == pytest.approx(0.5)
    assert prediction.risk_level == "medium"
    assert prediction.model_version == "dummy-test-v1"


def test_predictor_rejects_unknown_transaction(tmp_path) -> None:
    predictor = _build_predictor(tmp_path)

    with pytest.raises(TransactionNotFoundError):
        predictor.predict("missing")
