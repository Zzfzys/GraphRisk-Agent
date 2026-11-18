"""New allowance and one-transaction routing, with synthetic ledgers only."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import httpx
import pytest

from extensions.transaction_workbench.budget import WebBudget, REVISION
from extensions.transaction_workbench.prepare import snapshot
from extensions.transaction_workbench.runtime import build_app
from graphrisk.agent.upgrade_contract import canonical, file_hash
from test_case_qa_web_gate_v1 import make_gate, write


class Cases:
    def __init__(self, cases):
        self.cases = cases

    def by_alias(self, alias):
        return deepcopy(next(c for c in self.cases if c["case_alias"] == alias))

    def check(self, alias, binding):
        case = self.by_alias(alias)
        assert case["binding"] == binding
        return case

    def resolve(self, transaction, batch):
        return deepcopy(self.cases[int(transaction)-1000000])

    def transactions(self):
        return {"batch_id":"fixture-day","relative_day":175,"revision":1,"model_version":"fixture-model",
                "results":[{"transaction_id":str(1000000+i),"risk_level":"low" if i else "medium",
                            "score":0.1,"transaction_amount":50} for i in range(2)]}

    def view(self, transaction):
        return {"transaction_id":transaction,"case":self.resolve(transaction,None),"investigation":{},
                "report_text":"本笔报告 "+transaction,"report_source":"program_from_sealed_batch"}


def make(tmp_path):
    h = make_gate(tmp_path)
    directory = tmp_path/"integrated"
    directory.mkdir()
    with sqlite3.connect(h.path) as db:
        baseline = snapshot(db)
    write(directory/"baseline.json",baseline)
    cfg = deepcopy(h.config)
    h.new_guard = directory/"guard.py"
    h.new_guard.write_text("# fixture source\n",encoding="utf8")
    cfg["files"][str(h.new_guard)] = file_hash(h.new_guard)
    cfg.update(version=REVISION,campaign="case-qa-integrated-test",max_requests=100,max_cost_micro_cny=5_000_000,
        baseline_path=str(directory/"baseline.json"),baseline_sha256=file_hash(directory/"baseline.json"),
        conversation_path=str(directory/"conversations.sqlite3"),
        pricing_verified_date=datetime.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat())
    for receipt in h.old_receipts:
        cfg["files"][str(receipt)] = file_hash(receipt)
    h.new_config = write(directory/"config.json",cfg)
    write(directory/"OUTBOUND-APPROVED.json",{"config_sha256":file_hash(h.new_config),"outbound_authorized":True,
        "max_requests":100,"max_cost_micro_cny":5_000_000,"recipient":"DeepSeek official API","preserve_parent_history":True})
    h.current = WebBudget(h.new_config,transport=h.budget.transport,fixture=True)
    h.catalog = Cases(h.cases)
    h.current.catalog = h.catalog
    h.current.register()
    h.app = build_app(h.new_config,budget=h.current,catalog=h.catalog)
    h.calls.clear()
    return h


def create(client, transaction="1000000", key="create_integrated_1"):
    r = client.post("/api/v1/case-qa/conversations",json={"transaction_id":transaction,"batch_id":None},headers={"Idempotency-Key":key})
    assert r.status_code == 201, r.text
    return r.json()


def question(value, number=1):
    return {"message_id":f"{number:032x}","parent_turn_id":value["turns"][-1]["message_id"] if value["turns"] else None,
            "evidence_version":value["evidence_version"],"message":"有哪些正常解释？"}


def test_read_only_views_restore_same_case_and_keep_old_cost(tmp_path):
    h = make(tmp_path)
    before = h.path.read_bytes()
    with TestClient(h.app) as c:
        assert c.get("/health").json()["model_calls_on_start"] == 0
        status = c.get("/api/v1/workbench/status").json()["usage"]
        assert status["used_requests"] == 5 and status["batch_remaining_calls"] == 95
        assert status["max_cost_cny"] == "5.000000"
        v = create(c)
        opened = c.get("/api/v1/workbench/transactions/1000000").json()
        assert opened["conversation"]["conversation_id"] == v["conversation_id"]
        assert opened["report_text"].endswith("1000000")
        assert c.get("/api/v1/workbench/transactions/1000001").json()["conversation"] is None
    assert h.path.read_bytes() == before and not h.calls


def test_one_send_replay_multiturn_and_restart_share_allowance(tmp_path):
    h = make(tmp_path)
    with TestClient(h.app) as c:
        v = create(c)
        path = "/api/v1/case-qa/conversations/"+v["conversation_id"]
        body = question(v)
        first = c.post(path+"/turns",json=body)
        assert first.status_code == 200, first.text
        assert first.json()["turn"]["result"]["status"] == "answered"
        assert c.post(path+"/turns",json=body).json()["replayed"] is True
        v = c.get(path).json()
        second = c.post(path+"/turns",json=question(v,2))
        assert second.status_code == 200
        assert second.json()["turn"]["context"]["included_turn_ids"] == [body["message_id"]]
        create(c,"1000001","create_other_case")
    current = WebBudget(h.new_config,transport=h.budget.transport,fixture=True)
    current.catalog = h.catalog
    usage = current.usage()
    assert usage["used_requests"] == 7 and usage["batch_remaining_calls"] == 93
    assert len(h.calls) == 2


def test_unknown_result_retains_hold_and_blocks_new_sessions_without_retry(tmp_path):
    h = make(tmp_path)
    def timeout(request):
        raise httpx.ReadTimeout("fixture unknown")
    h.handler = timeout
    with TestClient(h.app) as c:
        v = create(c)
        body = question(v)
        path = "/api/v1/case-qa/conversations/"+v["conversation_id"]+"/turns"
        result = c.post(path,json=body).json()
        assert result["turn"]["result"]["status"] == "response_unknown"
        assert c.post(path,json=body).json()["replayed"]
        other = create(c,"1000001","create_unknown_other")
        assert other["send_block_reason"]
        assert c.post("/api/v1/case-qa/conversations/"+other["conversation_id"]+"/turns",json=question(other,2)).status_code == 409
    assert len(h.calls) == 1 and h.current.usage()["unsettled_hold_cny"] == "2.105344"


@pytest.mark.parametrize("change",["old_history","old_qa","old_receipt","approval","source"])
def test_old_evidence_and_limits_cannot_be_rewritten(tmp_path, change):
    h = make(tmp_path)
    if change in {"old_history","old_qa"}:
        with sqlite3.connect(h.path) as db:
            db.execute("UPDATE execution_requests SET accounted=0" if change == "old_history" else "UPDATE case_qa_requests_v1 SET packet_json='{}'")
    elif change == "old_receipt":
        h.old_receipts[0].write_text("{}",encoding="utf8")
    elif change == "approval":
        p = h.new_config.parent/"OUTBOUND-APPROVED.json"
        a = json.loads(p.read_text()); a["max_requests"] = 101; write(p,a)
    else:
        h.new_guard.write_text("# changed",encoding="utf8")
    before = h.path.read_bytes()
    with pytest.raises(Exception):
        h.current.usage()
    assert h.path.read_bytes() == before and not h.calls


@pytest.mark.parametrize("count,cost,expected",[(99,0,None),(100,0,"quota_exhausted"),(101,0,"quota_exhausted"),
    (99,2_894_656,None),(99,2_894_657,"budget_exhausted"),(99,5_000_000,"budget_exhausted")])
def test_cumulative_count_and_cost_boundaries(tmp_path, monkeypatch, count, cost, expected):
    h = make(tmp_path)
    # Inject an authenticated partition at the status boundary. Link/receipt
    # authentication and the actual atomic reservation are exercised above.
    with sqlite3.connect(h.path) as db:
        template = db.execute("SELECT * FROM case_qa_requests_v1 LIMIT 1").fetchone()
        for n in range(count):
            db.execute("INSERT OR IGNORE INTO case_qa_requests_v1 VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (f"test-{n}",f"key-{n}",f"phase-{n}",*template[3:]))
    rows = [(f"test-{n}","deepseek","settled",0,"key","phase","sha",cost if n == 0 else 0,None,None,"ok",None,None,0) for n in range(count)]
    monkeypatch.setattr(h.current,"_partition",lambda db:(rows,rows,[],[]))
    assert h.current.usage()["block_reason"] == expected


def test_ui_has_one_selection_and_ordered_content_no_paid_startup(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import streamlit as st
    h = make(tmp_path)
    client = TestClient(h.app)
    st.cache_data.clear()
    st.cache_resource.clear()
    monkeypatch.setattr(httpx,"Client",lambda *args,**kwargs:client)
    script = Path("E:/GraphRisk-Agent/extensions/transaction_workbench/web.py")
    at = AppTest.from_file(str(script)).run(timeout=30)
    assert not at.exception
    assert not at.radio and len(at.selectbox) == 1
    assert [s.value for s in at.subheader] == ["风险与证据","调查报告","问问 DeepSeek"]
    assert not at.chat_input[0].disabled
    at.selectbox[0].select("1000001")
    at.button[0].click().run(timeout=30)
    assert not at.exception
    assert any("本笔报告 1000001" in t.value for t in at.text)
    assert not h.calls


def test_ui_manual_question_answers_once_and_refresh_does_not_resend(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import streamlit as st
    h = make(tmp_path)
    client = TestClient(h.app)
    st.cache_data.clear()
    st.cache_resource.clear()
    monkeypatch.setattr(httpx,"Client",lambda *args,**kwargs:client)
    at = AppTest.from_file("E:/GraphRisk-Agent/extensions/transaction_workbench/web.py").run(timeout=30)
    at.chat_input[0].set_value("这些线索有哪些正常解释？").run(timeout=30)
    assert not at.exception
    assert len(h.calls) == 1 and not at.chat_input[0].disabled
    assert any("DeepSeek回答" in c.value for c in at.caption)
    at.run(timeout=30)
    assert len(h.calls) == 1


def test_low_memory_blocks_before_reserving_or_calling(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from extensions.transaction_workbench import runtime
    h = make(tmp_path)
    monkeypatch.setattr(runtime.psutil,"virtual_memory",lambda:SimpleNamespace(available=1*2**30))
    with TestClient(h.app) as c:
        v = create(c)
        before = h.path.read_bytes()
        r = c.post("/api/v1/case-qa/conversations/"+v["conversation_id"]+"/turns",json=question(v))
        assert r.status_code == 409 and r.json()["error_code"] == "preflight_failed"
    assert h.path.read_bytes() == before and not h.calls


def test_saved_draft_compatibility_requires_same_meanings():
    from extensions.transaction_workbench.runtime import same_report_facts
    old = {"case_alias":"a"*32,"scoring_context":{"model":"m","revision":1},"policies":[{"text":"guidance"}],
           "facts":[{"fact_id":"old","query_ref":"old","source_version":"old","metric":"amount","value":50,
                     "availability":"available","temporal_scope":"target_event"}]}
    current = deepcopy(old)
    current["facts"][0].update(fact_id="new",query_ref="new",source_version="new")
    assert same_report_facts(old,current)
    for change in ("value","availability","temporal_scope","scoring_context","policies"):
        changed = deepcopy(current)
        if change in {"scoring_context","policies"}:
            changed[change] = {}
        else:
            changed["facts"][0][change] = "changed"
        assert not same_report_facts(old,changed)
