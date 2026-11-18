import pandas as pd
import pytest

from graphrisk.models.tabular import FEATURE_COLUMNS, TabularRiskModel


def _feature_frame(row_count: int = 20) -> pd.DataFrame:
    rows = []
    for index in range(row_count):
        rows.append(
            {
                "transaction_amount": float(index + 1),
                "tx_count_last_1h": index % 3,
                "tx_count_last_24h": index % 5,
                "tx_count_last_7d": index,
                "amount_mean_last_24h": float(index) if index else None,
                "amount_std_last_24h": 1.0 if index else None,
                "amount_deviation_last_24h": 1.0 if index else None,
                "transaction_hour": index % 24,
                "transaction_day_of_week": index % 7,
                "card_id": f"card-{index % 4}",
                "device_id": f"device-{index % 3}",
                "email_domain": f"domain-{index % 2}.example",
                "address_id": f"address-{index % 5}",
                "product_code": "A" if index % 2 else "B",
            }
        )
    return pd.DataFrame(rows)


def test_logistic_model_uses_uniform_train_predict_interface() -> None:
    features = _feature_frame()
    labels = pd.Series([0, 1] * 10)
    model = TabularRiskModel.create(
        "logistic_regression",
        random_seed=7,
        positive_weight=1.0,
    )

    model.train(features, labels)
    scores = model.predict_scores(features)

    assert len(scores) == len(features)
    assert ((scores >= 0) & (scores <= 1)).all()
    assert model.transformed_feature_count() >= len(FEATURE_COLUMNS)


def test_model_rejects_missing_features() -> None:
    model = TabularRiskModel.create("dummy", random_seed=7, positive_weight=1.0)

    with pytest.raises(ValueError, match="missing model features"):
        model.train(pd.DataFrame({"transaction_amount": [1.0]}), pd.Series([0]))
