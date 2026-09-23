from __future__ import annotations

import json
from pathlib import Path

import duckdb
import joblib
import pandas as pd
import pytest

from graphrisk.config import load_config
from graphrisk.investigation.risk_tool import RiskModelTool
from graphrisk.investigation.workflow import InvestigationWorkflow
from graphrisk.models.tabular import TabularRiskModel
from graphrisk.training.config import load_training_config


@pytest.fixture
def workflow(tmp_path: Path) -> InvestigationWorkflow:
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
                "transaction_time": pd.Timestamp("2025-01-01")
                + pd.Timedelta(minutes=index),
                "transaction_amount": float(index + 1),
                "tx_count_last_1h": index,
                "tx_count_last_24h": index,
                "tx_count_last_7d": index,
                "amount_mean_last_24h": float(index) if index else None,
                "amount_std_last_24h": 1.0 if index else None,
                "amount_deviation_last_24h": 1.0 if index else None,
                "transaction_hour": 0,
                "transaction_day_of_week": 3,
                "card_id": "card-shared" if index < 3 else "card-other",
                "device_id": f"device-{index}",
                "email_domain": "example.test",
                "address_id": f"address-{index}",
                "product_code": "W",
                "is_fraud": index % 2,
                "identity_key": "internal-only",
            }
        )
    frame = pd.DataFrame(rows)
    feature_path = data_config.resolve_path(data_config.paths.interim_features)
    transaction_path = data_config.resolve_path(data_config.paths.interim_transactions)
    with duckdb.connect(":memory:") as connection:
        connection.register("frame", frame)
        connection.sql("SELECT * FROM frame").write_parquet(str(feature_path))
        connection.sql(
            "SELECT transaction_id, transaction_time, transaction_amount, card_id, "
            "device_id, email_domain, address_id, product_code, is_fraud, identity_key "
            "FROM frame"
        ).write_parquet(str(transaction_path))

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
                "model_version": "dummy-workflow-v1",
                "risk_thresholds": {"low": 0.3, "high": 0.7},
            }
        ),
        encoding="utf-8",
    )
    ids = iter(("run-1", "run-2", "run-3", "run-4", "run-5", "run-6"))
    return InvestigationWorkflow(
        RiskModelTool.from_configs(data_config, training_config),
        investigation_id_factory=lambda: next(ids),
    )


def _request(transaction_id: str = "T3", limit: object = 20) -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        "dataset_source": "synthetic",
        "relation_limit": limit,
    }


def test_success_report_is_deterministic_and_audit_is_separate(workflow) -> None:
    first = workflow.run(_request())
    second = workflow.run(_request())

    assert first.report.status == "completed"
    assert first.report == second.report
    expected_report = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "stage03_investigation_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert first.report.model_dump(mode="json") == expected_report
    assert first.audit.investigation_id != second.audit.investigation_id
    assert [event.tool for event in first.audit.events] == [
        "get_transaction",
        "get_transaction_features",
        "get_risk_score",
        "get_related_entities",
        "find_risky_neighbors",
    ]
    assert [event.sequence for event in first.audit.events] == [1, 2, 3, 4, 5]
    assert [event.status for event in first.audit.events] == [
        "ok", "ok", "ok", "ok", "unavailable"
    ]
    assert first.report.unavailable_capabilities[0].planned_stage == "Stage 04"
    assert first.report.issues == []
    assert first.report.related_entities is not None
    assert first.report.related_entities.data is not None
    assert first.report.related_entities.data.total_matches == 2
    assert [binding.section for binding in first.report.evidence_bindings] == [
        "transaction", "historical_features", "risk_score", "related_entities"
    ]
    report_json = first.report.model_dump_json()
    audit_json = first.audit.model_dump_json()
    assert "investigation_id" not in report_json
    assert "started_at" not in report_json
    assert "is_fraud" not in report_json
    assert "identity_key" not in report_json
    assert "card-shared" not in audit_json
    assert "T3" not in audit_json


def test_missing_transaction_stops_after_first_tool(workflow) -> None:
    run = workflow.run(_request("missing"))
    assert run.report.status == "not_found"
    assert run.report.historical_features is None
    assert run.report.risk_score is None
    assert run.report.related_entities is None
    assert run.report.risky_neighbors is None
    assert len(run.audit.events) == 1
    assert run.audit.events[0].error_code == "transaction_not_found"


@pytest.mark.parametrize(
    ("raw_request", "code"),
    [
        (_request(" "), "invalid_transaction_id"),
        ({**_request(), "unexpected": "secret"}, "invalid_request"),
    ],
)
def test_invalid_request_stops_before_data_query(workflow, raw_request, code) -> None:
    run = workflow.run(raw_request)
    assert run.report.status == "invalid_input"
    assert run.report.transaction.error is not None
    assert run.report.transaction.error.code == code
    assert len(run.audit.events) == 1


def test_invalid_relation_limit_keeps_prior_evidence_and_reports_partial(workflow) -> None:
    run = workflow.run(_request(limit=0))
    assert run.report.status == "partial"
    assert run.report.transaction.status == "ok"
    assert run.report.historical_features is not None
    assert run.report.historical_features.status == "ok"
    assert run.report.risk_score is not None and run.report.risk_score.status == "ok"
    assert run.report.related_entities is not None
    assert run.report.related_entities.error is not None
    assert run.report.related_entities.error.code == "invalid_limit"
    assert run.report.risky_neighbors is not None
    assert run.report.risky_neighbors.error is not None
    assert run.report.risky_neighbors.error.code == "invalid_limit"
    assert [issue.tool for issue in run.report.issues] == [
        "get_related_entities", "find_risky_neighbors"
    ]


def test_model_unavailable_allows_other_evidence_to_complete(workflow, tmp_path) -> None:
    assert workflow.risk_tool._predictor is not None
    workflow.risk_tool._startup_error = "model_artifact_unavailable"
    workflow.risk_tool._predictor = None

    run = workflow.run(_request())
    assert run.report.status == "partial"
    assert run.report.risk_score is not None
    assert run.report.risk_score.error is not None
    assert run.report.risk_score.error.code == "model_artifact_unavailable"
    assert run.report.related_entities is not None
    assert run.report.related_entities.status == "ok"
    assert [issue.tool for issue in run.report.issues] == ["get_risk_score"]
    assert len(run.audit.events) == 5


def test_unexpected_tool_exception_is_contained_and_audited(workflow, monkeypatch) -> None:
    def explode(*_args, **_kwargs):
        raise RuntimeError("private path C:/secret and stack")

    monkeypatch.setattr(
        "graphrisk.investigation.workflow.get_related_entities", explode
    )
    run = workflow.run(_request())
    assert run.report.status == "partial"
    assert run.report.related_entities is not None
    assert run.report.related_entities.error is not None
    assert run.report.related_entities.error.code == "query_failed"
    assert run.audit.events[3].error_code == "query_failed"
    serialized = run.model_dump_json()
    assert "C:/secret" not in serialized
    assert "stack" not in serialized
