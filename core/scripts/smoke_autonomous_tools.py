"""Exercise native read-only tools on ONE registered development case, no LLM.

Uses frozen historical scores, not neural/tree inference; policies use local TFIDF.
Does not read the Agent evaluation-label file or operate Docker/services.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from graphrisk.agent.autonomous_tools import (
    ArtifactGuard, InvestigationScope, ReadOnlyToolExecutor, ToolBoundaryError, catalog_document,
)
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_policy import PolicyRegistry
from graphrisk.agent.policy_corpus import load_policy_corpus
from graphrisk.agent.policy_retrieval import PolicyRetriever, build_policy_index, write_policy_index
from graphrisk.agent.upgrade_contract import digest, file_hash
from graphrisk.investigation.repository import InvestigationRepository

SOURCE = Path(__file__).resolve().parents[1]
ROUTE0 = "reports/generated/agent-investigation-upgrade/route0/20261019-preflight-v2"
PROFILE = "artifacts/stage-09/route5b/20261015-coverage-lowmem-v3/coverage-profile-calendar-v2.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args=parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}",args.run_id):
        raise SystemExit("invalid_run_id")
    runtime=args.runtime.resolve()
    output=runtime/"reports/generated/agent-investigation-upgrade/route1"/args.run_id
    if output.exists():
        raise SystemExit("new_output_directory_required")
    output.mkdir(parents=True)
    receipt={"status":"Pending", "version":"route1-native-tools-v1", "created_utc":datetime.now(timezone.utc).isoformat(),
             "new_llm_calls":0, "new_tree_or_gnn_inference":0, "new_real_model_fits":0,
             "evaluation_labels_read":False, "service_lifecycle_actions":0}
    try:
        from preflight_agent_upgrade import protected_hashes
        from graphrisk.graph.coverage_service import CoverageGraphService, CoverageRiskTool, load_coverage
        before=protected_hashes(runtime)
        budget_path=runtime/"reports/generated/agent-investigation-upgrade/live-budget.sqlite3"
        with sqlite3.connect(budget_path.as_uri()+"?mode=ro",uri=True) as db:
            attempts_before=db.execute("SELECT count(*) FROM model_attempts").fetchone()[0]
        root0=runtime/ROUTE0
        closeout=read(root0/"closeout.json")
        cases_path=root0/"cases-private.json"
        if file_hash(cases_path)!=closeout["private_cases_sha256"] or closeout["status"]!="Pass":
            raise ToolBoundaryError("route0_case_registration_changed")
        case=next(c for c in read(cases_path)["cases"] if c["group"]=="development")
        profile=load_coverage(runtime,runtime/PROFILE,"5ecb2126eb282fc759bbbf261e576c6fe3b5f19d3c0185a9dee7d21a821232e4")
        transaction_path=runtime/"data/interim/ieee-cis/transactions.parquet"
        feature_path=runtime/"data/interim/ieee-cis/features_all.parquet"
        repo=InvestigationRepository("ieee_cis",transaction_path,feature_path)
        graph=CoverageGraphService(runtime,profile,transaction_path,feature_path)
        coverage=graph.coverage(case["transaction_id"])
        if coverage["status"]!="covered":
            raise ToolBoundaryError("development_case_out_of_coverage")
        index=build_policy_index(load_policy_corpus(SOURCE/"data/policies/stage05"))
        index_path=write_policy_index(index,output/"lexical-policy-index.json")
        annotation_path=SOURCE/"configs/agent_investigation_policy_requirements.json"
        registry=PolicyRegistry.from_file(index,annotation_path)
        scope=InvestigationScope(case_alias=case["case_alias"],transaction_id=case["transaction_id"],
            dataset_source="ieee_cis",target_event_second=case["event_second"],policy_as_of=date(2026,10,19),
            transaction_revision=file_hash(transaction_path),feature_revision=file_hash(feature_path),
            model_revision=file_hash(runtime/PROFILE),primary_model_version=coverage["primary_model_version"],
            graph_model_version=coverage["graph_model_version"],policy_index_version=index.index_version)
        bindings={runtime/name:meta["sha256"] for name,meta in profile["files"].items()}
        bindings.update({runtime/PROFILE:file_hash(runtime/PROFILE),index_path:file_hash(index_path),annotation_path:file_hash(annotation_path)})
        guard=ArtifactGuard(bindings)
        executor=ReadOnlyToolExecutor.from_existing(scope,repository=repo,risk_tool=CoverageRiskTool(repo,graph),
            graph_service=graph,retriever=PolicyRetriever(index_path),source_guard=guard)
        sequence=[("get_transaction",{}),("get_transaction_features",{}),("get_risk_score",{}),
                  ("get_related_entities",{"limit":3}),("find_risky_neighbors",{"limit":3}),
                  *[("retrieve_policy",{"focus":focus,"top_k":6}) for focus in ("device_sharing","trusted_device","graph_limits")]]
        for name,arguments in sequence:
            executor.execute(name,arguments)
            build_evidence(scope,executor.observations,registry)  # Validate before any following action.
        bundle=build_evidence(scope,executor.observations,registry)
        guard.verify_full()
        if protected_hashes(runtime)!=before:
            raise ToolBoundaryError("legacy_ledger_changed")
        budget_path=runtime/"reports/generated/agent-investigation-upgrade/live-budget.sqlite3"
        with sqlite3.connect(budget_path.as_uri()+"?mode=ro",uri=True) as db:
            attempts=db.execute("SELECT count(*) FROM model_attempts").fetchone()[0]
        if attempts!=attempts_before:
            raise ToolBoundaryError("unexpected_live_attempts_during_route1")
        write(output/"evidence-private.json",bundle.model_dump(mode="json"))
        write(output/"catalog.json",catalog_document())
        write(output/"raw-observations-private.json",{"scope":scope.model_dump(mode="json"),
              "observations":[{"tool":o.call.name,"request_sha256":o.call.request_sha256,"result_sha256":o.result_sha256,"raw_result":json.loads(o.result_json)} for o in executor.observations]})
        from stage09_ci import source_inventory
        write(output/"source-freeze.json",{"source_inventory":source_inventory(SOURCE),
              "parent_route0_sha256":file_hash(root0/"closeout.json"),"protected_legacy_sha256":before,
              "source_bindings":[{"path":str(path),"sha256":sha} for path,sha in bindings.items()]})
        receipt.update(status="Pass",distinct_tools=len({o.call.name for o in executor.observations}),
                       tool_requests=len(executor.observations),development_transactions=1,
                       formal_validation_transactions=0,facts=len(bundle.facts),policies=len(bundle.policies),
                       fact_bundle_sha256=file_hash(output/"evidence-private.json"),evidence_version=bundle.evidence_version,
                       annotation_version=registry.annotation_version,old_ledgers_unchanged=True,new_budget_attempts=attempts,
                       budget_attempts_before=attempts_before,new_model_attempts_during_smoke=attempts-attempts_before,
                       tool_outcomes=[o.model_dump(mode="json") for o in bundle.tool_outcomes],
                       policies_unverified=sum(p.applicability=="unverified_conditions" for p in bundle.policies),
                       source_freeze_sha256=file_hash(output/"source-freeze.json"),learner_L1="Pending",
                       capability_scope="native_cached_tools_not_new_agent_or_llm_quality")
    except Exception as error:
        receipt["error_code"]=str(error) if isinstance(error,ToolBoundaryError) else "native_tool_smoke_dependency_failure"
    write(output/"acceptance.json",receipt)
    print(json.dumps(receipt,ensure_ascii=False,sort_keys=True))
    return 0 if receipt["status"]=="Pass" else 2


if __name__=="__main__":
    raise SystemExit(main())
