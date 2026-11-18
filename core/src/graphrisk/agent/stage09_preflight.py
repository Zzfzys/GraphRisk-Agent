"""Stage 09 local experiment contract and content-based version freeze.

No generation, embedding, training or remote API is invoked here.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class AgentBudget(ContractModel):
    unique_transactions: Literal[30]
    minimum_generation_transactions: Literal[20]
    repetitions_per_phase: Literal[3]
    max_model_calls_per_case: Literal[2]
    development_cases: Literal[5]
    warmup_cases: Literal[1]
    max_generation_attempts: Literal[144]
    concurrency: Literal[1]
    max_case_seconds: Literal[300]
    max_request_seconds: Literal[180]
    max_output_tokens: Literal[512]
    temperature: Literal[0]
    paid_calls_allowed: Literal[False]
    hosted_fallback_allowed: Literal[False]

    @model_validator(mode="after")
    def capacity_matches(self):
        needed = (2 * (self.unique_transactions + self.repetitions_per_phase)
                  + self.development_cases + self.warmup_cases) * self.max_model_calls_per_case
        if needed != self.max_generation_attempts:
            raise ValueError("generation budget does not match both phases and reserve")
        return self


class Candidate(ContractModel):
    learning_rate: float = Field(gt=0)
    dropout: float = Field(ge=0, lt=1)
    hidden_channels: int = Field(gt=0)


class GraphBudget(ContractModel):
    baseline_seed: int
    confirmation_seeds: list[int] = Field(min_length=3, max_length=3)
    candidates: list[Candidate] = Field(min_length=6, max_length=6)
    max_new_gnn_runs: Literal[13]
    max_epochs: Literal[50]
    patience: Literal[10]
    max_run_seconds: Literal[3600]
    max_total_training_seconds: Literal[21600]
    candidate_test_access_allowed: Literal[False]
    maximum_final_test_batches: Literal[1]
    larger_fanout: list[int] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def unique_search(self):
        if len(set(self.confirmation_seeds)) != 3 or self.confirmation_seeds[0] != self.baseline_seed:
            raise ValueError("confirmation seeds must be distinct and start with baseline_seed")
        keys = {(c.learning_rate, c.dropout, c.hidden_channels) for c in self.candidates}
        if len(keys) != len(self.candidates):
            raise ValueError("hyperparameter candidates must be distinct")
        if self.larger_fanout != [10, 10]:
            raise ValueError("larger sampler must retain entity cap and use ten historical postings")
        return self


class ResourceBudget(ContractModel):
    minimum_available_ram_gib: float = Field(gt=0)
    minimum_free_gpu_mib: int = Field(gt=0)
    maximum_idle_gpu_utilization_percent: int = Field(ge=0, le=100)
    minimum_disk_free_gib: dict[str, float]

    @model_validator(mode="after")
    def positive_disk_floors(self):
        if set(self.minimum_disk_free_gib) != {"C", "D", "E"} or any(
                v <= 0 for v in self.minimum_disk_free_gib.values()):
            raise ValueError("positive C/D/E disk floors are required")
        return self


class Stage09Contract(ContractModel):
    schema_version: Literal["1.0"]
    baseline_config: Literal["configs/stage05.yaml"]
    persistent_probe_config: Literal["configs/stage07_pgvector.yaml"]
    baseline_storage: Literal["memory_single_worker"]
    baseline_retriever: Literal["tfidf"]
    policy_as_of: Literal["2026-07-01"]
    relation_limit: Literal[3]
    agent_selection_split: Literal["validation"]
    score_strata: Literal["xgboost_equal_rank_tertiles_10_each"]
    selection_seed: int
    observed_test_status: Literal["historically_observed_not_blind"]
    output_root: Literal["reports/generated/stage09"]
    artifact_root: Literal["artifacts/stage-09"]
    agent: AgentBudget
    graph: GraphBudget
    resources: ResourceBudget


def load_contract(path: Path) -> Stage09Contract:
    return Stage09Contract.model_validate_json(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _content_hash(value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def safe_input(root: Path, relative: str) -> Path:
    """Only freeze explicit public configuration/code or local data/model inputs."""
    path = Path(relative)
    allowed_files = {"uv.lock", "pyproject.toml", "conftest.py"}
    allowed_roots = (
        "configs/", "src/graphrisk/", "api/", "scripts/",
        "data/interim/", "data/processed/", "data/policies/",
        "artifacts/stage-02/", "artifacts/stage-04/", "artifacts/stage-05/",
    )
    normalized = path.as_posix()
    if path.is_absolute() or ".." in path.parts or (
            normalized not in allowed_files and not normalized.startswith(allowed_roots)):
        raise ValueError("freeze_input_path_not_allowed")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("freeze_input_path_not_allowed")
    resolved_relative = resolved.relative_to(root.resolve())
    if any(part.startswith(".env") or part in {".secrets", ".git"}
           for part in (*path.parts, *resolved_relative.parts)):

        raise ValueError("freeze_input_path_not_allowed")
    return resolved


def build_freeze(root: Path, contract_path: Path) -> dict:
    """Bind actual service inputs; rely on existing loaders for artifact semantics."""
    from graphrisk.agent.config import load_stage05_config
    from graphrisk.agent.reporting import PROMPT_VERSION, ReportDraft
    from graphrisk.config import load_config
    from graphrisk.graph.config import load_graph_config
    from graphrisk.training.config import load_training_config

    root = root.resolve()
    contract_path = contract_path.resolve()
    if not contract_path.is_relative_to(root):
        raise ValueError("contract must reside inside the project")
    contract = load_contract(contract_path)
    config = load_stage05_config(root / contract.baseline_config)
    if (config.dataset_source != "ieee_cis" or config.generation.provider != "local_http"
            or config.retrieval.backend != contract.baseline_retriever):
        raise ValueError("baseline requires IEEE-CIS, local Ollama and TF-IDF")
    expected_limits = {
        "max_model_calls": contract.agent.max_model_calls_per_case,
        "max_total_seconds": contract.agent.max_case_seconds,
    }
    if any(getattr(config.limits, key) != value for key, value in expected_limits.items()):
        raise ValueError("baseline limits differ from the experiment contract")
    if (config.generation.max_output_tokens != contract.agent.max_output_tokens
            or config.generation.request_timeout_seconds != contract.agent.max_request_seconds):
        raise ValueError("generation settings differ from the experiment contract")
    graph = load_graph_config(config.resolve_path(config.paths.graph_config))
    data = load_config(config.resolve_path(config.paths.data_config))
    training = load_training_config(config.resolve_path(config.paths.training_config))
    artifact_root = graph.resolve_path(graph.paths.artifact_dir)
    comparison_dir = artifact_root / "comparisons" / config.graph_comparison_run
    comparison = json.loads((comparison_dir / "metadata.json").read_text(encoding="utf-8"))
    model = Path(comparison["graph_model_path"])
    if not model.is_absolute():
        model = artifact_root / model
    model = model.resolve()
    if not model.is_relative_to(artifact_root):
        raise ValueError("graph checkpoint escapes the configured artifact root")
    metadata_path = model.with_name("metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if _sha256(model.with_name("training-history.json")) != metadata["history_sha256"]:
        raise ValueError("training history does not match checkpoint metadata")
    if metadata["random_seed"] != contract.graph.baseline_seed:
        raise ValueError("registered baseline seed differs from checkpoint")
    manifest_path = graph.resolve_path(graph.paths.graph_manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (_sha256(model) != comparison["graph_model_sha256"]
            or comparison["graph_manifest_sha256"] != manifest["content_sha256"]
            or metadata["graph_manifest_sha256"] != manifest["content_sha256"]
            or metadata["model_sha256"] != _sha256(model)):
        raise ValueError("checkpoint/comparison/manifest binding differs")
    champion = training.resolve_path(training.paths.output_dir) / "champion"
    policy_dir = config.resolve_path(config.paths.policy_artifact_dir)
    index_path = policy_dir / "policy-index-tfidf.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    inputs = {
        contract_path, root / "uv.lock", root / "pyproject.toml", root / "conftest.py",
        root / contract.baseline_config, root / contract.persistent_probe_config,
        root / "configs/stage07_compose.yaml",
        config.resolve_path(config.paths.graph_config),
        config.resolve_path(config.paths.data_config),
        config.resolve_path(config.paths.training_config),
        model, metadata_path, model.with_name("training-history.json"), manifest_path,
        comparison_dir / "metadata.json", comparison_dir / "xgboost-controlled.joblib",
        champion / "model.joblib", champion / "metadata.json",
        index_path, policy_dir / "policy-index-local_embedding.json",
    }
    for section in ("artifacts", "sources"):
        for item in manifest[section].values():
            source = safe_input(root, item["path"])
            if _sha256(source) != item["sha256"]:
                raise ValueError("graph manifest input hash differs")
            inputs.add(source)
    for item in comparison["outputs"].values():
        path = (comparison_dir / item["path"]).resolve()
        if not path.is_relative_to(comparison_dir) or _sha256(path) != item["sha256"]:
            raise ValueError("comparison output hash differs")
        inputs.add(path)
    for directory, suffix in (
            (root / "src/graphrisk", "*.py"), (root / "api", "*.py"),
            (root / "scripts", "*.py"), (root / "scripts", "*.ps1"),
            (config.resolve_path(config.paths.policy_source_dir), "*.md")):
        inputs.update(directory.rglob(suffix))
    artifacts = []
    for path in sorted(inputs, key=lambda p: str(p).casefold()):
        relative = path.relative_to(root).as_posix()
        source = safe_input(root, relative)
        artifacts.append({"path": relative, "bytes": source.stat().st_size, "sha256": _sha256(source)})
    champion_meta = json.loads((champion / "metadata.json").read_text(encoding="utf-8"))
    payload = {
        "schema_version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "contract": contract.model_dump(),
        "bindings": {
            "baseline_config": contract.baseline_config,
            "runtime_factory": "graphrisk.serving.investigation_service.Stage06Service.from_config",
            "persistent_profile": contract.persistent_probe_config,
            "generation": config.generation.model_dump(),
            "limits": config.limits.model_dump(),
            "retrieval": config.retrieval.model_dump(),
            "prompt_version": PROMPT_VERSION,
            "draft_schema_sha256": _content_hash(ReportDraft.model_json_schema()),
            "data_source": data.data.source,
            "data_calendar": "synthetic anchor; relative time only",
            "graph_comparison_run": config.graph_comparison_run,
            "graph_model_path": model.relative_to(root).as_posix(),
            "graph_model_sha256": comparison["graph_model_sha256"],
            "graph_manifest_content_sha256": manifest["content_sha256"],
            "graph_training": {key: metadata[key] for key in (
                "batch_size", "hidden_channels", "dropout", "learning_rate", "max_epochs",
                "early_stopping_patience", "random_seed", "best_epoch", "stopped_after_epoch")},
            "graph_validation_metrics": metadata["validation_metrics"],
            "graph_test_metrics": metadata["test_metrics"],
            "fusion_alpha": comparison["selected_alpha"],
            "champion": {"model_name": champion_meta["model_name"],
                         "model_version": champion_meta["model_version"]},
            "policy_index_version": index["index_version"],
            "policy_corpus_sha256": index["corpus_sha256"],
            "policy_chunks": len(index["chunks"]),
            "target_counts": manifest["selected_target_counts"],
            "sampling_rule": manifest["sampling_rule"],
        },
        "artifacts": artifacts,
        "scope": {"selected_case_ids": "not_selected_route1",
                  "actual_generation_calls": 0, "training_runs": 0, "paid_calls": 0},
    }
    return {**payload, "content_sha256": _content_hash(payload)}


def check_freeze(root: Path, frozen: dict) -> dict:
    """Fail closed on drift; return safe structured errors without file contents."""
    violations = []
    try:
        expected = frozen["content_sha256"]
        payload = {key: value for key, value in frozen.items() if key != "content_sha256"}
        if frozen.get("schema_version") != "1.0" or _content_hash(payload) != expected:
            return {"status": "failed", "violations": ["freeze_content_mismatch"]}
        Stage09Contract.model_validate(frozen["contract"])
        artifacts = frozen["artifacts"]
        if not artifacts or len({a["path"] for a in artifacts}) != len(artifacts):
            return {"status": "failed", "violations": ["invalid_artifact_inventory"]}
        for item in artifacts:
            try:
                path = safe_input(root.resolve(), item["path"])
                if not path.is_file():
                    violations.append("artifact_missing:" + item["path"])
                elif path.stat().st_size != item["bytes"] or _sha256(path) != item["sha256"]:
                    violations.append("artifact_changed:" + item["path"])
            except (OSError, ValueError):
                violations.append("artifact_path_invalid")
    except (KeyError, TypeError, ValueError):
        return {"status": "failed", "violations": ["invalid_freeze"]}
    return {"status": "passed" if not violations else "failed",
            "checked_files": len(artifacts), "violations": violations}


def check_resources(resources: ResourceBudget, observed: dict) -> dict:
    """Check start-of-task floors; snapshots are not continuous monitoring."""
    violations = []
    checks = (
        ("available_ram_gib", resources.minimum_available_ram_gib, "minimum"),
        ("free_gpu_mib", resources.minimum_free_gpu_mib, "minimum"),
        ("gpu_utilization_percent", resources.maximum_idle_gpu_utilization_percent, "maximum"),
    )
    for name, limit, direction in checks:
        value = observed.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            violations.append("missing_resource:" + name)
        elif (direction == "minimum" and value < limit) or (direction == "maximum" and value > limit):
            violations.append("resource_limit:" + name)
    for disk, limit in resources.minimum_disk_free_gib.items():
        value = observed.get("disk_free_gib", {}).get(disk)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < limit:
            violations.append("resource_limit:disk_" + disk)
    return {"status": "passed" if not violations else "failed", "violations": violations}
