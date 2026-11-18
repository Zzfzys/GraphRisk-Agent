"""Feature schema, missingness, unknown categories and artifact drift."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from graphrisk.agent.stage09_preflight import _content_hash
from graphrisk.features.wide_contract import FeatureContract, CORE_TRANSACTION, raw_fields
from graphrisk.features.wide_preprocessing import WidePreprocessor


def state():
    value = {"schema_version": "stage09-5b-preprocessor-v1", "profile": "core", "fit_scope": {"count": 3},
        "numeric": {"amount": {"median": 5, "mean": 5, "scale": 2}},
        "categorical": {"device": ["__missing__", "known"]}, "dropped": {},
        "input_columns": ["amount", "device"], "output_columns": ["amount", "missing:amount", "device"],
        "label_columns_read": []}
    return {**value, "content_sha256": _content_hash(value)}


def test_zero_missing_and_reserved_category_strings_remain_distinct():
    processor = WidePreprocessor(state())
    frame = pd.DataFrame({"amount": [0, None, 5], "device": ["__missing__", None, "new"]})
    output = processor.transform(frame)
    assert output.amount.tolist() == [0, 5, 5]
    assert output["missing:amount"].tolist() == [0, 1, 0]
    assert output.device.astype(str).tolist() == ["v:__missing__", "__missing__", "__unknown__"]
    assert processor.transform(frame, standardize=True).amount.tolist() == [-2.5, 0, 0]


@pytest.mark.parametrize("column", ["isFraud", "is_fraud", "label", "transaction_id", "risk_score"])
def test_label_and_metadata_cannot_enter_model_projection(column):
    frame = pd.DataFrame({"amount": [1], "device": ["known"], column: [1]})
    with pytest.raises(ValueError, match="exact feature projection"):
        WidePreprocessor(state()).transform(frame)


@pytest.mark.parametrize("value", [float("inf"), "bad", 1e100])
def test_invalid_numeric_does_not_silently_become_missing(value):
    with pytest.raises(ValueError, match="numeric"):
        WidePreprocessor(state()).transform(pd.DataFrame({"amount": [value], "device": [None]}))


def test_state_mutation_and_file_reload(tmp_path):
    value = state()
    path = tmp_path / "preprocessor.json"
    path.write_text(json.dumps(value))
    assert WidePreprocessor.load(path).state == value
    value["numeric"]["amount"]["median"] = 999
    with pytest.raises(ValueError, match="drift"):
        WidePreprocessor(value)


@pytest.mark.parametrize("extra", ["label", "prediction", "is_fraud", "V340", "id_99"])
def test_unknown_source_columns_and_label_aliases_rejected(extra):
    with pytest.raises(ValueError, match="unregistered"):
        raw_fields(["TransactionID", "TransactionDT", "isFraud", *CORE_TRANSACTION, extra], ["TransactionID", "DeviceType", "DeviceInfo"])


def test_feature_contract_cannot_enable_labels():
    root = Path(__file__).resolve().parents[2]
    value = json.loads((root / "configs/stage09_wide_features.json").read_text())
    value["labels_in_side_table"] = True
    with pytest.raises(ValidationError):
        FeatureContract.model_validate(value)
