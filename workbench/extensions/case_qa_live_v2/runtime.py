"""Identity-compatible QA entry; every send still requires its exact revision approval."""
import argparse
import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from graphrisk.agent.upgrade_contract import digest, file_hash
from graphrisk.agent.autonomous_budget import ExecutionStop
from extensions.case_qa_live_v1.runtime import read, service
from extensions.case_qa_live_v1.api import router
from extensions.case_qa_live_v1.parent import PinnedParent
from extensions.case_qa_v1.sessions import SessionError
from .budget import LiveCaseQABudgetV2


def components(config_path, approval_path=None):
    config_path = Path(config_path).resolve()
    cfg = read(config_path)
    approval_path = Path(approval_path).resolve() if approval_path else config_path.parent/"OUTBOUND-APPROVED.json"
    parent = PinnedParent(config_path, approval_path)
    budget = LiveCaseQABudgetV2(Path(cfg["ledger_path"]), cfg["base_contract"], config_path,
        parent_protocol=parent, approval_path=approval_path)
    return cfg, parent, budget


def preflight(config_path, approval_path=None):
    cfg, parent, budget = components(config_path, approval_path)
    before = file_hash(Path(cfg["ledger_path"]))
    with sqlite3.connect(Path(cfg["ledger_path"]).as_uri()+"?mode=ro", uri=True) as db:
        rows, qa, web, route9 = budget._partition(db)
    try:
        usage = budget.usage()
    except ExecutionStop as error:
        usage = {"budget_status":"unavailable", "block_reason":str(error), "model_requests":0}
    after = file_hash(Path(cfg["ledger_path"]))
    if before != after:
        raise ValueError("readonly_preflight_mutated_ledger")
    return {"mode":"live_case_qa", "execution_revision":"case-qa-live-identity-v2",
        "parent":{"parent_history_authenticated":True, "global_calls":len(rows),
            "case_qa_calls":len(qa), "web_remaining":6-len(web),
            "route9_remaining_call_cap":200-len(route9), "h1_unknown_preserved":True, "h1_retry_allowed":False},
        "usage":usage, "ledger_sha256":after, "model_requests":0,
        "conversation_database_exists":Path(cfg["conversation_path"]).exists()}


def build_app(config_path, approval_path=None):
    cfg, parent, budget = components(config_path, approval_path)
    app = FastAPI(title="GraphRisk 本案问答")
    app.include_router(router)
    app.state.case_qa_service = service(cfg, budget)
    @app.get("/health")
    def health():
        return {"status":"ok", "mode":"live_case_qa",
            "execution_revision":"case-qa-live-identity-v2", "model_calls_on_start":0}
    return app


def trial(config_path, approval_path=None):
    cfg, parent, budget = components(config_path, approval_path)
    result_path = Path(cfg["trial_result_path"])
    if result_path.exists():
        raise ValueError("saved_trial_result_exists_use_readonly_view")
    budget.register()
    qa = service(cfg, budget)
    output = {"version":"case-qa-live-trial-result-v2", "execution_revision":"case-qa-live-identity-v2",
        "config_sha256":file_hash(Path(config_path)), "cases":[], "automatic_retry":False,
        "repaired_drafts":0, "paid_judges":0}
    def save():
        result_path.write_text(json.dumps(output, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    for index, item in enumerate(cfg["case_files"]):
        current = qa.create({"transaction_id":item["transaction_id_local_only"], "batch_id":None},
            "caseqa-live-"+str(index)+"-"+file_hash(Path(config_path))[:16])
        case_result = {"case_alias":current["case_alias"], "conversation_id":current["conversation_id"], "turns":[]}
        output["cases"].append(case_result)
        for question in read(cfg["questions_path"])["questions"]:
            current = qa.get(current["conversation_id"])
            request = {"message_id":digest([file_hash(Path(config_path)),index,question["order"]])[:32],
                "parent_turn_id":current["turns"][-1]["message_id"] if current["turns"] else None,
                "evidence_version":current["evidence_version"], "message":question["text"]}
            try:
                response = qa.follow(current["conversation_id"], request)
            except SessionError as error:
                output.update(stopped_on_pre_send_or_checkpoint_error=True, error_code=str(error))
                save()
                return output
            case_result["turns"].append(response)
            save()
            if response["turn"]["result"]["status"] != "answered":
                output["stopped_on_non_answered"] = True
                save()
                return output
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["preflight","register","trial","api"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--approval")
    args = parser.parse_args()
    if args.operation == "api":
        import uvicorn
        uvicorn.run(build_app(args.config,args.approval),host="127.0.0.1",port=8003)
        return
    if args.operation == "register":
        cfg, parent, budget = components(args.config,args.approval)
        budget.register()
        result = {"registered":True, "model_requests":0}
    else:
        result = {"preflight":preflight, "trial":trial}[args.operation](args.config,args.approval)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
