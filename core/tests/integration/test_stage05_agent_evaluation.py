"""Frozen Agent suite must preserve branch and source-bound safety behavior."""

from pathlib import Path

from graphrisk.agent.agent_evaluation import evaluate_cases


def test_frozen_agent_suite(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    result = evaluate_cases(repo, tmp_path / "index.json")
    assert result["case_count"] >= 12
    assert result["passed"] == result["case_count"]
    assert all(row["citations_ok"] and row["unsupported_conclusions"] == 0 for row in result["cases"])
    assert all(row["external_action_count"] == 0 for row in result["cases"])
    assert all(row["tool"] is None or row["tool"] == "retrieve_policy" and row["tool_params_bound"]
               for row in result["cases"])
