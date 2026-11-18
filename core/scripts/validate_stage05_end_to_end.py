"""Route-8 held-out acceptance: report, human rejection, and safe failure branches."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pyarrow.compute as pc
import pyarrow.parquet as pq

from graphrisk.agent.approval import Stage05ApprovalWorkflow
from graphrisk.agent.components import build_components
from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.reliability import Stage05ReliableRunner
from graphrisk.agent.reporting import FakeReportProvider, OllamaReportProvider
from graphrisk.training.config import load_training_config


class MustNotGenerate:
    def generate(self, prompt, schema):
        raise AssertionError("generation must not run without active policy")


class UnavailableProvider:
    def generate(self, prompt, schema):
        raise httpx.ConnectError("route-8 controlled model outage")


def held_out_membership(config, transaction_id: str) -> dict[str, int]:
    training = load_training_config(config.resolve_path(config.paths.training_config))
    paths = {
        "train": training.paths.train_features,
        "validation": training.paths.validation_features,
        "test": training.paths.test_features,
    }
    counts = {}
    for split, path in paths.items():
        values = pq.read_table(training.resolve_path(path), columns=["transaction_id"])["transaction_id"]
        counts[split] = int(pc.sum(pc.equal(values, transaction_id)).as_py() or 0)
    if counts != {"train": 0, "validation": 0, "test": 1}:
        raise ValueError(f"transaction must appear exactly once in held-out test split: {counts}")
    return counts


def validate_result(normal) -> None:
    report_run = normal.report_run
    if report_run is None:
        raise ValueError("real report is missing")
    if report_run.status not in {"verified", "partial_evidence"} or report_run.report is None:
        raise ValueError(f"real report failed acceptance: {report_run.status}")
    if not report_run.report.policy_conclusions or report_run.report.validation_status != "source_quote_verified":
        raise ValueError("real report has no verified source quotation")
    graph = normal.graph_run
    if graph is None or graph.request is None or graph.policy_retrieval is None:
        raise ValueError("source retrieval is missing")
    allowed = {hit.chunk.chunk_id: hit.chunk for hit in graph.policy_retrieval.hits}
    for quote in report_run.report.policy_conclusions:
        source = allowed.get(quote.chunk_id)
        if (source is None or source.doc_id != quote.doc_id or source.version != quote.version
            or source.text != quote.quote or source.source_ref != quote.source_ref
            or not source.applies_on(graph.request.policy_as_of)
            or hashlib.sha256(source.text.encode()).hexdigest() != source.content_sha256):
            raise ValueError("report quotation is not bound to the current valid source")


def run_acceptance(config, transaction_id: str, *, provider_kind: str):
    membership = held_out_membership(config, transaction_id)
    investigation, retriever = build_components(config)
    provider = FakeReportProvider() if provider_kind == "fake" else OllamaReportProvider(config.generation)
    common = dict(dataset_source=config.dataset_source, limits=config.limits, top_k=config.retrieval.top_k)
    request = {"transaction_id": transaction_id, "dataset_source": config.dataset_source,
               "policy_as_of": "2026-07-01", "relation_limit": 3}
    normal = Stage05ReliableRunner(investigation, retriever, provider, **common).run(request)
    if normal.report_run is None:
        raise ValueError(f"no report was produced: {normal.status}")
    validate_result(normal)
    if provider_kind == "local_http" and (
        normal.audit.model_id != config.generation.model_id
        or normal.audit.model_revision != config.generation.model_revision
        or normal.audit.prompt_tokens is None or normal.audit.output_tokens is None
    ):
        raise ValueError("real model version or token audit is missing")
    if normal.audit.policy_attempts < 1 or normal.audit.generation_attempts < 1:
        raise ValueError("normal path omitted policy retrieval or generation")

    approval = Stage05ApprovalWorkflow()
    session = "route8-" + hashlib.sha256(f"{transaction_id}|{datetime.now(UTC).isoformat()}".encode()).hexdigest()[:16]
    pending = approval.start(normal.report_run, session)
    if pending.status != "pending" or pending.payload is None or pending.external_action_count:
        raise ValueError("report did not pause safely for approval")
    rejected = approval.resume({
        "session_id": session, "transaction_id": pending.payload.transaction_id,
        "report_sha256": pending.payload.report_sha256,
        "decision": "reject", "reviewer_id": "route8-demo-reviewer",
    }, normal.report_run)
    if rejected.status != "rejected" or rejected.external_action_count:
        raise ValueError("human rejection did not terminate without external action")

    no_policy = Stage05ReliableRunner(investigation, retriever, MustNotGenerate(), **common).run(
        {**request, "policy_as_of": "2025-12-31"})
    if (no_policy.status != "insufficient_evidence" or no_policy.audit.generation_attempts
        or no_policy.report_run is None or no_policy.report_run.report is None
        or no_policy.report_run.report.policy_conclusions):
        raise ValueError("no-policy path did not abstain without generation")

    outage = Stage05ReliableRunner(investigation, retriever, UnavailableProvider(), **common).run(request)
    if (outage.status != "generation_error" or outage.audit.generation_attempts != config.limits.max_retries + 1
        or outage.report_run is None or outage.report_run.report is not None):
        raise ValueError("dependency outage did not stop after bounded retries")

    return {
        "schema_version": "1.0", "transaction_id": transaction_id,
        "dataset_source": config.dataset_source, "held_out_membership": membership,
        "provider_kind": provider_kind,
        "normal": normal.model_dump(mode="json"),
        "approval": {"pending": pending.model_dump(mode="json"), "rejected": rejected.model_dump(mode="json")},
        "no_policy": no_policy.model_dump(mode="json"),
        "model_outage": outage.model_dump(mode="json"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run route-8 end-to-end held-out acceptance")
    parser.add_argument("transaction_id", nargs="?", default="3488959")
    parser.add_argument("--config", default="configs/stage05.yaml")
    parser.add_argument("--provider", choices=("fake", "local_http"), default="local_http")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = load_stage05_config(args.config)
    if config.dataset_source != "ieee_cis":
        raise ValueError("route-8 held-out acceptance requires IEEE-CIS configuration")
    result = run_acceptance(config, args.transaction_id, provider_kind=args.provider)
    safe_id = hashlib.sha256(args.transaction_id.encode()).hexdigest()[:16]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = args.output or config.resolve_path(config.paths.run_dir) / f"route8-{safe_id}-{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"normal={result['normal']['status']} approval={result['approval']['rejected']['status']} "
          f"no_policy={result['no_policy']['status']} outage={result['model_outage']['status']} output={output}")


if __name__ == "__main__":
    main()
