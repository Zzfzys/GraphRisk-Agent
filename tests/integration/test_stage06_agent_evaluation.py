"""Stage 06 frozen case suite must exercise all 50 branches without hiding failures."""

from __future__ import annotations

from pathlib import Path

import pytest

from graphrisk.demo.stage06_evaluation import (
    LEGACY_SHA256, _evaluate_one, _fixture_results, _load_suite, evaluate_stage06,
)


REPO = Path(__file__).resolve().parents[2]


def test_frozen_50_case_suite_and_group_metrics(tmp_path):
    result = evaluate_stage06(REPO, tmp_path / "index.json")
    fake = result["fake"]
    assert result["legacy_suite_sha256"] == LEGACY_SHA256
    assert fake["legacy_count"] == 17 and fake["new_count"] == 33
    assert fake["summary"]["count"] == fake["summary"]["passed"] == 50
    assert fake["summary"]["unsupported_conclusions"] == 0
    assert fake["summary"]["approval_bypasses"] == 0
    assert {row["id"] for row in fake["cases"] if row["scenario"].startswith("ui_")} == {
        "ui_verified_evidence", "ui_no_policy_warning", "ui_partial_graph_warning", "ui_rerun_does_not_create",
    }
    assert all(group["passed"] == group["count"] for group in fake["summary"]["groups"].values())
    assert all(row["citation_complete"] is not False for row in fake["cases"])
    assert all(row["external_action_count"] == 0 for row in fake["cases"] if "external_action_count" in row)
    assert result["real_local"]["samples"] == []


def test_case_failure_is_reported_without_removing_the_case(tmp_path):
    suite, _ = _load_suite(REPO)
    results, index_version = _fixture_results(REPO, tmp_path / "index.json")
    case = next(item for item in suite["cases"] if item["id"] == "api_refresh_no_rerun").copy()
    case["expected"] = {**case["expected"], "branch": "not_found"}
    row = _evaluate_one(REPO, suite, case, results, index_version)
    assert row["id"] == case["id"]
    assert row["actual_branch"] == "verified"
    assert row["passed"] is False
    assert "expected_branch" in row["failed_checks"]


def test_contract_detects_changed_legacy_suite_and_duplicate_ids(tmp_path):
    new_cases = tmp_path / "data/evaluation"
    new_cases.mkdir(parents=True)
    new_cases.joinpath("stage06_agent_cases_v1.yaml").write_bytes(
        (REPO / "data/evaluation/stage06_agent_cases_v1.yaml").read_bytes())
    old = new_cases / "stage05_agent_cases_v1.yaml"
    old.write_text("version: 1\ncases: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen Stage 05"):
        _load_suite(tmp_path)
    old.write_bytes((REPO / "data/evaluation/stage05_agent_cases_v1.yaml").read_bytes())
    source = new_cases / "stage06_agent_cases_v1.yaml"
    source.write_text(source.read_text(encoding="utf-8").replace(
        "id: ui_rerun_does_not_create", "id: ui_verified_evidence"), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        _load_suite(tmp_path)
