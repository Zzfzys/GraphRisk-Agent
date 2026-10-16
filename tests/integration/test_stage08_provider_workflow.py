"""Qwen mock adapter crosses the existing report workflow's source checks."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from graphrisk.agent.agent_evaluation import FixtureInvestigation
from graphrisk.agent.config import Stage05Limits
from graphrisk.agent.hosted_provider import QwenEvaluationConfig, QwenReportProvider, ReviewedCase
from graphrisk.agent.policy_corpus import load_policy_corpus
from graphrisk.agent.policy_retrieval import PolicyRetriever, build_policy_index, write_policy_index
from graphrisk.agent.provider_budget import EvaluationBudget
from graphrisk.agent.reporting import FakeReportProvider, Stage05ReportWorkflow
from graphrisk.agent.workflow import Stage05GraphWorkflow
from graphrisk.investigation.workflow import InvestigationReport

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("wrong_citation", [False, True])
def test_qwen_adapter_reuses_existing_source_bound_workflow(tmp_path, monkeypatch, wrong_citation):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "route1-integration-fake")
    fixture = InvestigationReport.model_validate_json(
        (ROOT / "tests/fixtures/stage04_investigation_report.json").read_text(encoding="utf-8"))
    index = write_policy_index(build_policy_index(load_policy_corpus(ROOT / "data/policies/stage05")), tmp_path / "index.json")
    limits = Stage05Limits(max_steps=12, max_model_calls=1, max_policy_queries=1, max_retries=0,
                           max_total_seconds=300, api_budget_cny=0, api_stop_cny=0)
    graph = Stage05GraphWorkflow(FixtureInvestigation(fixture), PolicyRetriever(index),
        dataset_source="synthetic", limits=limits).run({
            "transaction_id": "T3", "dataset_source": "synthetic",
            "policy_as_of": "2026-07-01", "relation_limit": 3})
    captured = {}
    class Capture(FakeReportProvider):
        def generate(self, prompt, schema):
            captured["prompt"] = prompt
            captured["draft"] = super().generate(prompt, schema).draft
            return super().generate(prompt, schema)
    reference = Stage05ReportWorkflow(Capture()).run(graph)
    assert reference.status == "verified"
    draft = captured["draft"]
    case = ReviewedCase(case_id="workflow-compatibility", provenance="project-original-simulated",
        license="project-original-public-demo", prompt=captured["prompt"], transaction_id="T3",
        evidence_ids=tuple(draft.evidence_ids),
        policy_chunk_ids=tuple(p.chunk_id for p in draft.policy_choices),
        uncertainty=draft.uncertainty, fake_input_token_bound=4096)
    payload = draft.model_dump()
    if wrong_citation:
        payload["policy_choices"] = [{"chunk_id": "unretrieved"}]
    def handler(request):
        return httpx.Response(200, json={
            "model": "qwen-plus-2025-12-01",
            "usage": {"prompt_tokens": 400, "completion_tokens": 90, "total_tokens": 490},
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(payload)}}]})
    provider = QwenReportProvider(QwenEvaluationConfig(
        endpoint="https://route1-fixture.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
        experiment_id="integration"), case, EvaluationBudget(tmp_path / "cost.sqlite3"),
        transport=httpx.MockTransport(handler))
    result = Stage05ReportWorkflow(provider).run(graph)
    if wrong_citation:
        assert result.status == "provider_error" and result.report is None
    else:
        assert result.status == "verified"
        assert result.report.policy_conclusions == reference.report.policy_conclusions
        assert result.report.approval_status == "not_requested"
        assert result.audit.model_id == "qwen-plus-2025-12-01"