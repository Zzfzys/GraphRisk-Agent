"""Bounded quality trial over the same two cases; preflight never sends."""
import argparse
import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import digest, file_hash
from extensions.case_qa_live_v1.api import router
from extensions.case_qa_live_v1.parent import PinnedParent
from extensions.case_qa_live_v1.runtime import Catalog, read
from extensions.case_qa_v1.sessions import SessionError
from .budget import ShortBudget
from .service import ShortService, ShortConversationStore, PROMPT_REVISION

ADAPTER_REVISION = "case-qa-live-quality-v3"
ANSWER_REVISION = "case-qa-quality-v1"


def components(config_path, approval_path=None):
    config_path = Path(config_path).resolve()
    cfg = read(config_path)
    approval_path = Path(approval_path).resolve() if approval_path else config_path.parent/"OUTBOUND-APPROVED.json"
    parent = PinnedParent(config_path, approval_path)
    budget = ShortBudget(Path(cfg["ledger_path"]), cfg["base_contract"], config_path,
        parent_protocol=parent, approval_path=approval_path)
    return cfg, parent, budget


def service(cfg, budget):
    catalog = Catalog(cfg)
    return ShortService(ShortConversationStore(Path(cfg["conversation_path"])),
        catalog.resolve, catalog.check, budget=budget,
        id_aliases={item["transaction_id_local_only"]:catalog._load(item)["case_alias"] for item in cfg["case_files"]})


def preflight(config_path, approval_path=None):
    cfg, parent, budget = components(config_path, approval_path)
    ledger = Path(cfg["ledger_path"])
    before = file_hash(ledger)
    with sqlite3.connect(ledger.as_uri()+"?mode=ro", uri=True) as db:
        rows, qa, web, route9 = budget._partition(db)
    try:
        usage = budget.usage()
    except ExecutionStop as error:
        usage = {"budget_status":"unavailable", "block_reason":str(error), "model_requests":0}
    after = file_hash(ledger)
    if before != after:
        raise ValueError("readonly_preflight_mutated_ledger")
    return {"mode":"live_case_qa", "adapter_revision":ADAPTER_REVISION,
        "answer_revision":ANSWER_REVISION, "prompt_revision":PROMPT_REVISION, "execution_revision":cfg["execution_revision"],
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
        return {"status":"ok", "mode":"live_case_qa", "adapter_revision":ADAPTER_REVISION,
            "answer_revision":ANSWER_REVISION, "prompt_revision":PROMPT_REVISION, "execution_revision":cfg["execution_revision"], "model_calls_on_start":0}
    return app


def trial(config_path, approval_path=None):
    cfg, parent, budget = components(config_path, approval_path)
    result_path = Path(cfg["trial_result_path"])
    if result_path.exists():
        raise ValueError("saved_trial_result_exists_use_readonly_view")
    questions = {q["order"]:q["text"] for q in read(cfg["questions_path"])["questions"]}
    # Cases remain in the original scope order; the untouched low case is tried
    # first so its complete three-round conversation can be evaluated.
    expected = [{"case_alias":cfg["cases"][1]["case_alias"], "question_orders":[1,2,3]},
                {"case_alias":cfg["cases"][0]["case_alias"], "question_orders":[1]}]
    if cfg.get("trial_plan") != expected or set(questions) != {1,2,3}:
        raise ValueError("trial_plan_invalid")
    budget.register()
    qa = service(cfg, budget)
    catalog = Catalog(cfg)
    entries = {catalog._load(item)["case_alias"]:item for item in cfg["case_files"]}
    output = {"version":"case-qa-short-trial-v1", "adapter_revision":ADAPTER_REVISION,
        "answer_revision":ANSWER_REVISION, "prompt_revision":PROMPT_REVISION,
        "execution_revision":cfg["execution_revision"], "config_sha256":file_hash(Path(config_path)),
        "cases":[], "automatic_retry":False, "repaired_drafts":0, "paid_judges":0,
        "planned_new_requests":4, "predecessor_failure_is_not_dialogue":True}
    with result_path.open("x", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2)
    def save():
        result_path.write_text(json.dumps(output, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    for planned in cfg["trial_plan"]:
        alias = planned["case_alias"]
        item = entries[alias]
        try:
            current = qa.create({"transaction_id":item["transaction_id_local_only"], "batch_id":None},
                "caseqa-short-"+alias[:12]+"-"+file_hash(Path(config_path))[:16])
        except SessionError as error:
            output.update(stopped_on_pre_send_or_checkpoint_error=True, error_code=str(error))
            save()
            return output
        case_result = {"case_alias":alias, "conversation_id":current["conversation_id"], "turns":[]}
        output["cases"].append(case_result)
        for order in planned["question_orders"]:
            try:
                current = qa.get(current["conversation_id"])
                request = {"message_id":digest([file_hash(Path(config_path)),alias,order])[:32],
                    "parent_turn_id":current["turns"][-1]["message_id"] if current["turns"] else None,
                    "evidence_version":current["evidence_version"], "message":questions[order]}
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
        uvicorn.run(build_app(args.config,args.approval), host="127.0.0.1", port=8003)
        return
    if args.operation == "register":
        cfg, parent, budget = components(args.config,args.approval)
        budget.register()
        result = {"registered":True, "model_requests":0, "adapter_revision":ADAPTER_REVISION}
    else:
        result = {"preflight":preflight, "trial":trial}[args.operation](args.config,args.approval)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
