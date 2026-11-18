"""Explicit preflight/register/trial/serve commands; startup never sends a model request."""
import argparse
import json
import sqlite3
from copy import deepcopy
from pathlib import Path

from fastapi import FastAPI
from graphrisk.agent.upgrade_contract import digest,file_hash
from graphrisk.agent.autonomous_budget import ExecutionStop
from .api import router
from .budget import LiveCaseQABudget
from .parent import PinnedParent
from .service import LiveCaseQAService,LiveConversationStore


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def components(config_path,approval_path=None):
    config_path=Path(config_path).resolve()
    cfg=read(config_path)
    approval_path=Path(approval_path).resolve() if approval_path else config_path.parent/"OUTBOUND-APPROVED.json"
    parent=PinnedParent(config_path,approval_path)
    budget=LiveCaseQABudget(Path(cfg["ledger_path"]),cfg["base_contract"],config_path,
        parent_protocol=parent,approval_path=approval_path)
    return cfg,parent,budget


class Catalog:
    def __init__(self,cfg):
        self.entries=cfg["case_files"]

    def _load(self,item):
        path=Path(item["path"])
        if file_hash(path)!=item["sha256"]:
            raise ValueError("case_snapshot_changed")
        return read(path)

    def resolve(self,transaction,batch):
        item=next(item for item in self.entries if item["transaction_id_local_only"]==transaction)
        value=self._load(item)
        if batch not in (None,value["batch_revision"]):
            raise ValueError("approved_batch_required")
        return value

    def check(self,alias,binding):
        for item in self.entries:
            value=self._load(item)
            if value["case_alias"]==alias and value["binding"]==binding:
                return value
        raise ValueError("approved_case_required")


def service(cfg,budget):
    catalog=Catalog(cfg)
    return LiveCaseQAService(LiveConversationStore(Path(cfg["conversation_path"])),
        catalog.resolve,catalog.check,budget=budget,
        id_aliases={item["transaction_id_local_only"]:catalog._load(item)["case_alias"] for item in cfg["case_files"]})


def build_app(config_path,approval_path=None):
    cfg,parent,budget=components(config_path,approval_path)
    app=FastAPI(title="GraphRisk 本案问答")
    app.include_router(router)
    app.state.case_qa_service=service(cfg,budget)
    @app.get("/health")
    def health():
        return {"status":"ok","mode":"live_case_qa","model_calls_on_start":0}
    return app


def preflight(config_path,approval_path=None):
    cfg,parent,budget=components(config_path,approval_path)
    before=file_hash(Path(cfg["ledger_path"]))
    with sqlite3.connect(Path(cfg["ledger_path"]).as_uri()+"?mode=ro",uri=True) as db:
        rows,qa,web,route9=budget._partition(db)
    state={"parent_history_authenticated":True,"global_calls":len(rows),"case_qa_calls":len(qa),
           "web_remaining":6-len(web),"route9_remaining_call_cap":200-len(route9),
           "h1_unknown_preserved":True,"h1_retry_allowed":False}
    try:
        usage=budget.usage()
    except ExecutionStop as error:
        usage={"budget_status":"unavailable","block_reason":str(error),"model_requests":0}
    after=file_hash(Path(cfg["ledger_path"]))
    if before!=after:
        raise ValueError("readonly_preflight_mutated_ledger")
    return {"mode":"live_case_qa","parent":state,"usage":usage,"ledger_sha256":after,
            "model_requests":0,"conversation_database_created":False}


def trial(config_path,approval_path=None):
    cfg,parent,budget=components(config_path,approval_path)
    budget.register()
    qa=service(cfg,budget)
    questions=read(cfg["questions_path"])["questions"]
    result_path=Path(cfg["trial_result_path"])
    if result_path.exists():
        raise ValueError("saved_trial_result_exists_use_readonly_view")
    output={"version":"case-qa-live-trial-result-v1","config_sha256":file_hash(Path(config_path)),
            "cases":[],"automatic_retry":False,"repaired_drafts":0,"paid_judges":0}
    for index,item in enumerate(cfg["case_files"]):
        current=qa.create({"transaction_id":item["transaction_id_local_only"],"batch_id":None},
            "caseqa-live-"+str(index)+"-"+file_hash(Path(config_path))[:16])
        case_result={"case_alias":current["case_alias"],"conversation_id":current["conversation_id"],"turns":[]}
        output["cases"].append(case_result)
        for question in questions:
            current=qa.get(current["conversation_id"])
            request={"message_id":digest([file_hash(Path(config_path)),index,question["order"]])[:32],
                "parent_turn_id":current["turns"][-1]["message_id"] if current["turns"] else None,
                "evidence_version":current["evidence_version"],"message":question["text"]}
            response=qa.follow(current["conversation_id"],request)
            case_result["turns"].append(response)
            result_path.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            if response["turn"]["result"]["status"]!="answered":
                output["stopped_on_non_answered"]=True
                result_path.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
                return output
    return output


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("operation",choices=["preflight","register","trial","api"])
    parser.add_argument("--config",required=True)
    parser.add_argument("--approval")
    args=parser.parse_args()
    if args.operation=="preflight":
        result=preflight(args.config,args.approval)
    elif args.operation=="register":
        cfg,parent,budget=components(args.config,args.approval)
        budget.register()
        result={"registered":True,"model_requests":0}
    elif args.operation=="trial":
        result=trial(args.config,args.approval)
    else:
        import uvicorn
        uvicorn.run(build_app(args.config,args.approval),host="127.0.0.1",port=8003)
        return
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
