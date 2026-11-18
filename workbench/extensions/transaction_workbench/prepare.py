"""Freeze a concrete manual-web allowance; never submit a question or reset history."""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from extensions.case_qa_v1.budget import HISTORY_SQL
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
from .budget import REVISION, read

ROOT = Path("E:/GraphRisk-Agent")
OUTPUT = ROOT/"reports/transaction-workbench"


def snapshot(db):
    rows = db.execute(HISTORY_SQL).fetchall()
    qa = db.execute("SELECT * FROM case_qa_requests_v1").fetchall()
    return {"attempt_ids":[r[0] for r in rows],"qa_attempt_ids":[r[0] for r in qa],
            "history_sha256":digest(rows),"qa_sha256":digest(qa),
            "grants_sha256":digest(db.execute("SELECT * FROM case_qa_grants_v1").fetchall()),
            "identity_sha256":digest(db.execute("SELECT * FROM provider_identity").fetchall())}


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("x",encoding="utf-8") as f:
        f.write(json.dumps(value,ensure_ascii=False,indent=2))


def main():
    import httpx
    if (OUTPUT/"config.json").exists():
        raise ValueError("existing_web_allowance_must_not_be_overwritten")
    old_path = ROOT/"reports/case-qa-v1/20261117-compact-live-v1/config.json"
    old = read(old_path)
    ledger = Path(old["ledger_path"])
    before = file_hash(ledger)
    with sqlite3.connect(ledger.as_uri()+"?mode=ro",uri=True) as db:
        baseline = snapshot(db)
        assert len(baseline["qa_attempt_ids"]) == 6
        qa_ids = set(baseline["qa_attempt_ids"])
        rows = db.execute(HISTORY_SQL).fetchall()
        assert sum(r[7] for r in rows if r[0] in qa_ids) == 98108
        old_receipts = {r[9]:r[8] for r in rows if r[0] in qa_ids}
    write(OUTPUT/"baseline.json",baseline)
    files = dict(old["files"])
    files.update(old_receipts)
    files[str(old_path)] = file_hash(old_path)
    prior_manifest = read(ROOT/"reports/case-qa-web-v1/20261118-web-v1/manifest.json")
    files.update(prior_manifest["files"])
    for path in Path(__file__).parent.glob("*.py"):
        files[str(path.resolve())] = file_hash(path)
    launcher = ROOT/"scripts/start_transaction_workbench.ps1"
    files[str(launcher)] = file_hash(launcher)
    for path in (ROOT/"tests/unit").glob("test_transaction_workbench*.py"):
        files[str(path)] = file_hash(path)
    with httpx.Client(timeout=15,trust_env=False) as client:
        batch = client.get("http://127.0.0.1:8002/api/v1/risk-batches").raise_for_status().json()["batches"][0]
    cfg = {"version":REVISION,"campaign":"case-qa-web-100-20261118","fixture":False,
           "purpose":"case_qa","max_requests":100,"max_cost_micro_cny":5_000_000,
           "ledger_path":str(ledger),"base_contract":old["base_contract"],"base_contract_sha256":old["base_contract_sha256"],
           "baseline_path":str(OUTPUT/"baseline.json"),"baseline_sha256":file_hash(OUTPUT/"baseline.json"),
           "profile":old["profile"],"expected_identity":old["expected_identity"],"runtime_root":old["runtime_root"],
           "system_prompt_sha256":old["system_prompt_sha256"],"pricing_verified_date":datetime.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat(),
           "pricing_source":"https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
           "files":files,"original_cases":old["case_files"],"batch":batch,
           "case_directory":str(OUTPUT/"cases"),"conversation_path":str(OUTPUT/"conversations.sqlite3"),"accepted_reports":[]}
    # Pin saved reports; rendering verifies them against the selected evidence.
    reports = ROOT/"reports/generated/agent-investigation-upgrade/route5/report-v3/20261112-daily-quotes-six-v2"
    for tag in ("H1","H2","M1","M2","L1","L2"):
        source = read(reports/"six-payloads"/(tag+"-source-private.json"))
        scope = source["scope"]
        path = reports/"actual-first-drafts-v1"/(tag+"-result-private.json")
        cfg["accepted_reports"].append({"transaction_id":str(scope["transaction_id"]),"path":str(path),"sha256":file_hash(path),
                                       "case_alias":scope["case_alias"],"evidence_version":source["evidence"]["evidence_version"],
                                       "source_path":str(reports/"six-payloads"/(tag+"-source-private.json")),
                                       "source_sha256":file_hash(reports/"six-payloads"/(tag+"-source-private.json"))})
        cfg["files"][str(path)] = file_hash(path)
        cfg["files"][cfg["accepted_reports"][-1]["source_path"]] = cfg["accepted_reports"][-1]["source_sha256"]
    write(OUTPUT/"config.json",cfg)
    write(OUTPUT/"OUTBOUND-APPROVED.json",{
        "version":REVISION,"config_sha256":file_hash(OUTPUT/"config.json"),"recipient":"DeepSeek official API",
        "outbound_authorized":True,"max_requests":100,"max_cost_micro_cny":5_000_000,"preserve_parent_history":True,
        "authorization_source":"user_message_20261118_single_transaction_web_100_5",
        "user_request":"把所有功能整合在一个页面：上方看风险和证据，中间看报告，下方直接提问。网页问答次数改为100次，网页的api费用上限更改为5元。",
        "scope":"Manual questions about the selected sealed-day transaction, deidentified evidence and up to three successful same-case QA pairs; no automatic batch, retries, repairs, paid judge, or business actions.",
        "cumulative_baseline":{"qa_requests":6,"estimated_micro_cny":98108},
        "global_caps":{"total":2119,"deepseek":1813,"local":348},
        "protected":{"route9_local_calls":200,"old_report_calls":4,"h1_resend":False},
        "price_verified_date":cfg["pricing_verified_date"],"price_source":cfg["pricing_source"]})
    # Copy compatible transcripts to the new store. Original databases and their
    # expired/failed verdicts stay intact; opening a record never calls a model.
    from extensions.case_qa_compact_v1.service import CompactConversationStore
    store = CompactConversationStore(Path(cfg["conversation_path"]))
    archives = [Path(old["conversation_path"]),ROOT/"reports/case-qa-web-v1/20261118-web-v1/conversations.sqlite3"]
    with store.connect() as target:
        for archive in archives:
            with sqlite3.connect(archive.as_uri()+"?mode=ro",uri=True) as source:
                for row in source.execute("SELECT * FROM qa_conversations_v1"):
                    # Validate the source document before preserving its exact row.
                    CompactConversationStore._value((row[5],row[6],row[4],row[3]))
                    target.execute("INSERT INTO qa_conversations_v1 VALUES (?,?,?,?,?,?,?)",row)
    assert file_hash(ledger) == before
    print(json.dumps({"config":str(OUTPUT/"config.json"),"qa_used":6,"qa_remaining":94,"cost_micro":98108,
                      "new_model_calls":0,"ledger_unchanged":True},ensure_ascii=False))


if __name__ == "__main__":
    main()
