"""Local evidence/report facade and one manual QA path per selected transaction."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3
import psutil

from fastapi import FastAPI, HTTPException
import httpx

from extensions.case_qa_compact_v1.service import CompactService, CompactConversationStore
from extensions.case_qa_live_v1.api import router
from extensions.case_qa_v1.evidence_contract import adapt_evidence, validate_view
from extensions.case_qa_v1.sessions import SessionError
from extensions.case_qa_v1.behavior import PreflightStop
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
from graphrisk.demo.conversation_workbench import readable_report
from .budget import WebBudget, REVISION, read, require


def saved_draft_text(draft):
    """Display original prose without internal check codes; never rewrite it."""
    lines = []
    for field,label in (("rationale","当前判断"),("main_limit","主要限制")):
        item = draft.get(field) or {}
        text = item.get("text") or item.get("impact")
        if text:
            lines.append(label+"："+text)
    for field,label in (("basis","支持线索"),("counterevidence","反证"),("hypotheses","待核实假设"),("gaps","信息缺口")):
        for item in draft.get(field,[]):
            lines.append(label+"："+(item.get("text") or item["impact"]))
    check = draft.get("next_check") or {}
    for field,label in (("purpose","建议核实（尚未执行）"),("decision_use","核实结果如何使用")):
        if check.get(field):
            lines.append(label+"："+check[field])
    return "\n\n".join(lines)


def same_report_facts(original, current):
    """Display-only compatibility: fresh query IDs do not change fact meanings.

    Never reuse an old pass/fail verdict or send the old report as QA evidence.
    Values, missingness, time scopes, models and policy text must all match.
    """
    def facts(bundle):
        return [{k:v for k,v in f.items() if k not in {"fact_id","query_ref","source_version"}}
                for f in bundle["facts"]]
    return (original["case_alias"] == current["case_alias"]
            and original["scoring_context"] == current["scoring_context"]
            and facts(original) == facts(current) and original["policies"] == current["policies"])


class Catalog:
    def __init__(self, cfg, client=None):
        self.cfg = cfg
        self.path = Path(cfg["case_directory"])
        self.path.mkdir(parents=True,exist_ok=True)
        self.client = client or httpx.Client(base_url="http://127.0.0.1:8002",timeout=90,trust_env=False)

    def transactions(self):
        day = self.client.get("/api/v1/risk-batches/"+str(self.cfg["batch"]["relative_day"])).raise_for_status().json()
        require(all(day[k] == self.cfg["batch"][k] for k in ("batch_id","revision","sealed_content_sha256")), "evidence_changed")
        # Raw card/device/address values never leave this local backend response.
        return {"batch_id":day["batch_id"], "relative_day":day["relative_day"], "revision":day["revision"],
                "model_version":day["model_version"], "results":[{k:r[k] for k in
                    ("transaction_id","risk_level","score","transaction_amount")} for r in day["results"]]}

    def by_alias(self, alias):
        if type(alias) is not str or not re.fullmatch(r"[0-9a-f]{32}",alias):
            raise SessionError("invalid_request")
        doc = read(self.path/(alias+".json"))
        require(doc["sha256"] == digest(doc["content"]), "evidence_changed")
        case = doc["content"]["case"]
        validate_view(case["evidence"])
        return case

    def check(self, alias, binding):
        case = self.by_alias(alias)
        require(case["binding"] == binding, "evidence_changed")
        return case

    def view(self, transaction):
        if type(transaction) is not str or not re.fullmatch(r"[0-9]{1,18}",transaction):
            raise SessionError("invalid_request")
        batch = self.cfg["batch"]
        self.transactions()  # Check the current sealed revision before using a cache.
        for path in self.path.glob("*.json"):
            doc = read(path)
            if doc["content"]["transaction_id"] == transaction:
                self.by_alias(doc["content"]["case"]["case_alias"])
                return doc["content"]
        self.transactions()  # Check the current sealed revision before using a cache.
        for path in self.path.glob("*.json"):
            doc = read(path)
            if doc["content"]["transaction_id"] == transaction:
                self.by_alias(doc["content"]["case"]["case_alias"])
                return doc["content"]
        key = digest([REVISION,batch["sealed_content_sha256"],transaction])
        value = self.client.post("/api/v1/conversations",headers={"Idempotency-Key":key},
            json={"transaction_id":transaction,"message":"调查风险","risk_profile":"daily_batch","batch_id":batch["batch_id"]}).raise_for_status().json()
        bundle = value["evidence"]
        context = bundle["scoring_context"]
        require(all(context[k] == batch[k] for k in ("batch_id","revision","sealed_content_sha256")), "evidence_changed")
        # Same policy selection as the QA trial; all data facts remain intact.
        subset = deepcopy(bundle)
        subset["policies"] = [p for p in subset["policies"] if p["doc_id"] == "S05-MANUAL-REVIEW-V1"]
        view = adapt_evidence(subset)
        case = {"case_alias":bundle["case_alias"],"batch_revision":f"{batch['batch_id']}:revision:{batch['revision']}",
                "binding":digest([context,view["view_sha256"]]),"evidence":view}
        for item in self.cfg.get("original_cases",[]):
            if item["transaction_id_local_only"] == transaction:
                require(file_hash(Path(item["path"])) == item["sha256"], "evidence_changed")
                original = read(item["path"])
                require(original["case_alias"] == case["case_alias"] and original["evidence"]["evidence_version"] == bundle["evidence_version"], "evidence_changed")
                case = original
        content = {"transaction_id":transaction,"case":case,"investigation":value,
                   "report_text":readable_report(value),"report_source":"program_from_sealed_batch"}
        # A historical draft may be displayed after a value-by-value comparison;
        # its original evidence version and verdict are never changed.
        for item in self.cfg.get("accepted_reports",[]):
            if item["transaction_id"] == transaction:
                require(file_hash(Path(item["path"])) == item["sha256"], "report_changed")
                result = read(item["path"])
                report = result.get("report")
                if report and report.get("case_alias") == case["case_alias"] and report.get("evidence_version") == bundle["evidence_version"]:
                    from graphrisk.demo.conversation_workbench import daily_quotes_lines
                    content.update(report_text="\n\n".join(daily_quotes_lines(report,bundle)),report_source="deepseek_saved_report")
                elif (item["case_alias"] == case["case_alias"] and item["evidence_version"] == bundle["evidence_version"]
                      and result.get("first_draft") is True and result.get("provider_result",{}).get("action")):
                    content.update(report_text=saved_draft_text(result["provider_result"]["action"]),report_source="deepseek_saved_draft")
                elif item.get("source_path") and result.get("first_draft") is True:
                    require(file_hash(Path(item["source_path"])) == item["source_sha256"], "report_changed")
                    original = read(item["source_path"])["evidence"]
                    if same_report_facts(original,bundle) and result.get("provider_result",{}).get("action"):
                        content.update(report_text=saved_draft_text(result["provider_result"]["action"]),
                                       report_source="deepseek_saved_draft_compatible",report_evidence_version=original["evidence_version"])
        path = self.path/(case["case_alias"]+".json")
        document = {"sha256":digest(content),"content":content}
        if path.exists():
            existing = read(path)
            require(existing == document, "evidence_changed")
        else:
            with path.open("x",encoding="utf-8") as f:
                f.write(canonical(document))
        return content

    def resolve(self, transaction, batch):
        if batch is not None and batch != self.cfg["batch"]["batch_id"]:
            raise SessionError("evidence_changed")
        # Existing catalog lookup is small (only opened transactions), no dataset scan.
        for path in self.path.glob("*.json"):
            doc = read(path)
            if doc["content"]["transaction_id"] == transaction:
                return self.by_alias(doc["content"]["case"]["case_alias"])
        return self.view(transaction)["case"]


def build_app(config_path, *, budget=None, catalog=None):
    cfg = read(config_path)
    budget = budget or WebBudget(config_path)
    catalog = catalog or Catalog(cfg)
    budget.catalog = catalog
    store = CompactConversationStore(Path(cfg["conversation_path"]))
    def preflight():
        if psutil.virtual_memory().available < 2*2**30:
            raise PreflightStop("preflight_failed")
    service = CompactService(store,catalog.resolve,catalog.check,budget=budget,preflight=preflight)
    # Redact any registered local transaction ID in questions before storage/send.
    class Service:
        def __getattr__(self, name):
            return getattr(service,name)

        def follow(self, identifier, body):
            if type(body) is dict and type(body.get("message")) is str:
                body = deepcopy(body)
                tx = {p["transaction_id"] for p in catalog.transactions()["results"]}
                for token in set(re.findall(r"(?<![0-9])[0-9]{7}(?![0-9])",body["message"])) & tx:
                    alias = catalog.resolve(token,None)["case_alias"]
                    body["message"] = re.sub(r"(?<![0-9])"+token+r"(?![0-9])",alias,body["message"])
            return service.follow(identifier,body)
    app = FastAPI(title="GraphRisk 交易工作台")
    app.state.case_qa_service = Service()
    app.include_router(router)

    @app.get("/health")
    def health():
        return {"status":"ok","revision":REVISION,"model_calls_on_start":0}

    @app.get("/api/v1/workbench/status")
    def status():
        return {"revision":REVISION,"usage":budget.usage()}

    @app.get("/api/v1/workbench/transactions")
    def transactions():
        return catalog.transactions()

    @app.get("/api/v1/workbench/transactions/{transaction}")
    def transaction(transaction:str):
        try:
            content = catalog.view(transaction)
            case = content["case"]
            with store.connect() as db:
                documents = [store._value(r) for r in db.execute("SELECT content,content_sha,state,case_alias FROM qa_conversations_v1 WHERE case_alias=?",(case["case_alias"],))]
            documents.sort(key=lambda d:d["expires_at"])
            latest = service.get(documents[-1]["conversation_id"]) if documents else None
            return {**content,"conversation":latest,"usage":budget.usage()}
        except Exception as error:
            raise HTTPException(409,detail="本笔交易的证据或报告暂不可用，请核对封批与本地调查服务。") from error
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",required=True)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(build_app(args.config),host="127.0.0.1",port=8003)


if __name__ == "__main__":
    main()
