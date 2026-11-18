from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import duckdb
import joblib

from graphrisk.models.tabular import TabularRiskModel


class TransactionNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class RiskPrediction:
    transaction_id: str
    risk_score: float
    risk_level: str
    model_name: str
    model_version: str
    dataset_source: str


class RiskPredictor:
    def __init__(self, model_dir: Path, feature_parquet: Path) -> None:
        model_path = model_dir / "model.joblib"
        metadata_path = model_dir / "metadata.json"
        if not model_path.is_file():
            raise FileNotFoundError(f"model artifact does not exist: {model_path}")
        if not metadata_path.is_file():
            raise FileNotFoundError(f"model metadata does not exist: {metadata_path}")
        if not feature_parquet.is_file():
            raise FileNotFoundError(f"serving feature data does not exist: {feature_parquet}")

        loaded_model = joblib.load(model_path)
        if not isinstance(loaded_model, TabularRiskModel):
            raise TypeError(f"unsupported model artifact type: {type(loaded_model).__name__}")

        self._model = loaded_model
        self._metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self._feature_parquet = feature_parquet

    def predict(self, transaction_id: str) -> RiskPrediction:
        normalized_transaction_id = transaction_id.strip()
        if not normalized_transaction_id:
            raise ValueError("transaction_id cannot be blank")

        with duckdb.connect() as connection:
            connection.from_parquet(str(self._feature_parquet)).create_view("serving_features")
            frame = connection.execute(
                "SELECT * FROM serving_features WHERE transaction_id = ?",
                [normalized_transaction_id],
            ).fetchdf()
        if frame.empty:
            raise TransactionNotFoundError(normalized_transaction_id)
        if len(frame) != 1:
            raise RuntimeError(
                f"transaction_id {normalized_transaction_id!r} returned {len(frame)} rows"
            )

        score = float(self._model.predict_scores(frame)[0])
        thresholds = self._metadata["risk_thresholds"]
        if score < float(thresholds["low"]):
            risk_level = "low"
        elif score < float(thresholds["high"]):
            risk_level = "medium"
        else:
            risk_level = "high"

        return RiskPrediction(
            transaction_id=normalized_transaction_id,
            risk_score=score,
            risk_level=risk_level,
            model_name=self._metadata["model_name"],
            model_version=self._metadata["model_version"],
            dataset_source=self._metadata["dataset_source"],
        )

    def model_info(self) -> dict[str, object]:
        return {
            "model_name": self._metadata["model_name"],
            "model_version": self._metadata["model_version"],
            "dataset_source": self._metadata["dataset_source"],
            "selected_by": self._metadata.get("selected_by"),
            "validation_metrics": self._metadata["validation_metrics"],
            "test_metrics": self._metadata["test_metrics"],
        }
