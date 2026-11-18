from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

ModelName = Literal["dummy", "logistic_regression", "xgboost"]

NUMERIC_FEATURES = (
    "transaction_amount",
    "tx_count_last_1h",
    "tx_count_last_24h",
    "tx_count_last_7d",
    "amount_mean_last_24h",
    "amount_std_last_24h",
    "amount_deviation_last_24h",
    "transaction_hour",
    "transaction_day_of_week",
)

CATEGORICAL_FEATURES = (
    "card_id",
    "device_id",
    "email_domain",
    "address_id",
    "product_code",
)

FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES


def _build_preprocessor() -> ColumnTransformer:
    numeric_pipeline = Pipeline(
        steps=(
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler(with_mean=False)),
        )
    )
    categorical_pipeline = Pipeline(
        steps=(
            ("imputer", SimpleImputer(strategy="most_frequent")),
            (
                "one_hot",
                OneHotEncoder(handle_unknown="ignore", min_frequency=2),
            ),
        )
    )
    return ColumnTransformer(
        transformers=(
            ("numeric", numeric_pipeline, list(NUMERIC_FEATURES)),
            ("categorical", categorical_pipeline, list(CATEGORICAL_FEATURES)),
        ),
        sparse_threshold=0.3,
        verbose_feature_names_out=False,
    )


def _build_estimator(
    model_name: ModelName,
    *,
    random_seed: int,
    positive_weight: float,
):
    if model_name == "dummy":
        return DummyClassifier(strategy="prior")
    if model_name == "logistic_regression":
        return LogisticRegression(
            class_weight="balanced",
            max_iter=1000,
            random_state=random_seed,
            solver="liblinear",
        )
    if model_name == "xgboost":
        return XGBClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            min_child_weight=2,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            scale_pos_weight=positive_weight,
            objective="binary:logistic",
            eval_metric="logloss",
            tree_method="hist",
            n_jobs=4,
            random_state=random_seed,
        )
    raise ValueError(f"unsupported model: {model_name}")


@dataclass
class TabularRiskModel:
    name: ModelName
    pipeline: Pipeline

    @classmethod
    def create(
        cls,
        model_name: ModelName,
        *,
        random_seed: int,
        positive_weight: float,
    ) -> "TabularRiskModel":
        pipeline = Pipeline(
            steps=(
                ("preprocessor", _build_preprocessor()),
                (
                    "classifier",
                    _build_estimator(
                        model_name,
                        random_seed=random_seed,
                        positive_weight=positive_weight,
                    ),
                ),
            )
        )
        return cls(name=model_name, pipeline=pipeline)

    def train(self, features: pd.DataFrame, labels: pd.Series) -> None:
        self._validate_features(features)
        self.pipeline.fit(features.loc[:, FEATURE_COLUMNS], labels)

    def predict_scores(self, features: pd.DataFrame) -> np.ndarray:
        self._validate_features(features)
        probabilities = self.pipeline.predict_proba(features.loc[:, FEATURE_COLUMNS])
        if probabilities.shape[1] != 2:
            raise RuntimeError(
                f"model {self.name} returned {probabilities.shape[1]} probability columns"
            )
        return np.asarray(probabilities[:, 1], dtype=float)

    def transformed_feature_count(self) -> int:
        preprocessor = self.pipeline.named_steps["preprocessor"]
        return int(len(preprocessor.get_feature_names_out()))

    @staticmethod
    def _validate_features(features: pd.DataFrame) -> None:
        missing = sorted(set(FEATURE_COLUMNS) - set(features.columns))
        if missing:
            raise ValueError(f"missing model features: {', '.join(missing)}")
