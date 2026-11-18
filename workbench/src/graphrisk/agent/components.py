"""Shared Stage 05 dependency assembly for scripts and the Stage 06 API."""

from __future__ import annotations

import os

from graphrisk.agent.config import Stage05Config
from graphrisk.agent.policy_retrieval import OllamaEmbeddingProvider, PolicyRetriever
from graphrisk.agent.pgvector_retrieval import PgvectorPolicyRetriever
from graphrisk.config import load_config
from graphrisk.graph.config import load_graph_config
from graphrisk.graph.service import GraphRiskService
from graphrisk.investigation.risk_tool import RiskModelTool
from graphrisk.investigation.workflow import InvestigationWorkflow
from graphrisk.training.config import load_training_config


def build_components(config: Stage05Config, *, pgvector_dsn: str | None = None) -> tuple[InvestigationWorkflow, PolicyRetriever | PgvectorPolicyRetriever]:
    data = load_config(config.resolve_path(config.paths.data_config))
    training = load_training_config(config.resolve_path(config.paths.training_config))
    graph_config = load_graph_config(config.resolve_path(config.paths.graph_config))
    risk_tool = RiskModelTool.from_configs(data, training)
    graph_service = None
    if config.model_policy is None:
        graph_service = GraphRiskService(
            graph_config,
            comparison_dir=graph_config.resolve_path(graph_config.paths.artifact_dir) / "comparisons" / config.graph_comparison_run,
        )
    decision_sha = None
    if config.model_policy is not None:
        from graphrisk.graph.deployment import load_policy, bind_primary, DecisionGraphService
        profile = load_policy(config._project_root, config.resolve_path(config.model_policy), config.model_policy_sha256)
        if config.graph_comparison_run != profile["comparison_run"]:
            raise ValueError("configured graph comparison differs from model decision")
        bind_primary(config._project_root, profile, risk_tool, training.resolve_path(training.paths.output_dir) / "champion")
        # The same verified store is reused by the decision service; release legacy instance first.
        graph_service = DecisionGraphService(
            graph_config, comparison_dir=graph_config.resolve_path(graph_config.paths.artifact_dir) / "comparisons" / config.graph_comparison_run,
            root=config._project_root, profile=profile,
        )
        decision_sha = profile["decision_sha256"]
    investigation = InvestigationWorkflow(risk_tool, graph_service=graph_service, model_decision_sha256=decision_sha)
    provider = None
    if config.retrieval.backend in {"local_embedding", "pgvector"}:
        provider = OllamaEmbeddingProvider(
            config.retrieval.embedding_model_id,
            config.retrieval.embedding_model_revision,
            base_url=config.retrieval.embedding_endpoint,
        )
    index_path = config.resolve_path(config.paths.policy_artifact_dir) / f"policy-index-{config.retrieval.index_backend}.json"
    if config.retrieval.backend == "pgvector":
        return investigation, PgvectorPolicyRetriever(
            index_path, provider, pgvector_dsn or os.getenv("GRAPHRISK_STAGE07_DSN", ""),
        )
    return investigation, PolicyRetriever(index_path, provider)
