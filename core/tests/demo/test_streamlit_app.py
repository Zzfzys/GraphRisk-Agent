"""Stage 06 workbench behavior against a fake HTTP-only API client."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from graphrisk.demo.api_client import ApiReply, Stage06ApiClient


RUN_ID = "run-abc123456789-0123456789abcdef0123456789abcdef"
HASH = "a" * 64


def _record(*, status="verified", graph_status="ok", neighbors=True, policies=True):
    report = {
        "transaction": {"transaction_id": "3488959", "amount": 123.5, "product_code": "W"},
        "history": {"status": "ok", "tx_count_last_1h": 1, "tx_count_last_24h": 3,
                    "tx_count_last_7d": 8},
        "model": {"status": "ok", "risk_score": 0.42, "model_version": "xgb-v1"},
        "graph": {"status": graph_status, "error_code": "graph_unavailable" if graph_status != "ok" else None,
                  "graph_model_version": "sage-v1", "target_graph_risk_score": 0.51,
                  "fusion_score": 0.47, "neighbors": ([{
                      "transaction_id": "3488900", "graph_risk_score": 0.67,
                      "seconds_before_target": 60, "shared_entity_types": ["device"],
                  }] if neighbors else [])},
        "policy_as_of": "2026-07-01", "policy_retrieval_status": "ok" if policies else "no_match",
        "policy_citations": ([{
            "chunk_id": "policy-1", "doc_id": "POLICY-1", "title": "共享设备复核",
            "version": 2, "section": "规则", "quote": "应由人工复核。",
            "source_ref": "simulated-policy.md", "source_type": "project_original",
        }] if policies else []),
        "uncertainty": ["政策适用性仍需人工判断"],
    }
    approval = None if not policies else {
        "status": "pending", "session_id": "session-" + RUN_ID,
        "report_sha256": HASH, "expires_at": "2026-09-26T12:30:00Z",
        "external_action_count": 0, "audit": [{"kind": "created"}],
        "reviewer_authenticated": False,
    }
    return {
        "run_id": RUN_ID, "status": status,
        "request": {"transaction_id": "3488959"}, "report_sha256": HASH,
        "report": report, "approval": approval,
        "audit": {"investigation_id": "audit-1", "elapsed_ms": 1200,
                  "policy_attempts": 1, "generation_attempts": 1, "retries": 0,
                  "prompt_tokens": 100, "output_tokens": 50, "run_cost_cny": 0},
        "versions": {"graph_comparison_run": "graph-run", "policy_index_version": "index-1",
                     "generation_model_id": "qwen2.5:0.5b", "generation_model_revision": "digest"},
    }


class FakeApi:
    def __init__(self, record=None, *, create_error=None):
        self.record = deepcopy(record or _record())
        self.create_error = create_error
        self.create_calls = 0
        self.get_calls = 0
        self.decisions = []

    def create(self, *_args):
        self.create_calls += 1
        if self.create_error:
            return ApiReply(503, {"error_code": self.create_error, "run_id": RUN_ID})
        return ApiReply(201, {"run_id": RUN_ID})

    def get(self, _run_id):
        self.get_calls += 1
        if self.create_error:
            return ApiReply(503, {"error_code": self.create_error, "run_id": RUN_ID})
        return ApiReply(200, deepcopy(self.record))

    def approval(self, _run_id):
        return ApiReply(200, deepcopy(self.record["approval"]))

    def decide(self, _run_id, **kwargs):
        self.decisions.append(kwargs)
        approval = self.record["approval"]
        approval["status"] = "approved" if kwargs["decision"] == "approve" else "rejected"
        approval["reviewer_id"] = kwargs["reviewer_id"]
        approval["note"] = kwargs["note"].strip()
        approval["audit"].append({"kind": approval["status"]})
        return ApiReply(200, deepcopy(approval))


def _app(monkeypatch, fake):
    monkeypatch.setattr(Stage06ApiClient, "create_investigation", lambda self, *args: fake.create(*args))
    monkeypatch.setattr(Stage06ApiClient, "get_investigation", lambda self, run_id: fake.get(run_id))
    monkeypatch.setattr(Stage06ApiClient, "get_approval", lambda self, run_id: fake.approval(run_id))
    monkeypatch.setattr(Stage06ApiClient, "decide", lambda self, run_id, **kwargs: fake.decide(run_id, **kwargs))
    return AppTest.from_file(Path(__file__).resolve().parents[2] / "streamlit_app.py", default_timeout=15)


def _click(at, label):
    next(item for item in at.button if item.label == label).click().run()
    assert not at.exception


def test_relation_only_neighbor_remains_visible_without_fabricated_zero(monkeypatch):
    record=_record()
    neighbor=record['report']['graph']['neighbors'][0]
    neighbor.update(graph_risk_score=None,evidence_role='relation_only',
        score_unavailable_reason='outside_independent_score_scope')
    at=_app(monkeypatch,FakeApi(record))
    at.query_params['run_id']=RUN_ID
    at.run()
    assert not at.exception
    assert any('无独立分数' in str(item.value) and '仅关系' in str(item.value) for item in at.dataframe)


def test_coverage_and_new_event_buttons_do_not_create_an_investigation(monkeypatch):
    fake=FakeApi()
    monkeypatch.setattr(Stage06ApiClient,'coverage',lambda self,key:ApiReply(200,{'status':'out_of_coverage'}))
    monkeypatch.setattr(Stage06ApiClient,'prospective',lambda self,event:ApiReply(200,{
        'risk_score':.4,'graph_risk_score':.6,'scope':'prospective_replay'}))
    at=_app(monkeypatch,fake).run()
    _click(at,'查询评分覆盖')
    assert any('不在独立评分' in item.value for item in at.warning)
    _click(at,'试算新交易')
    assert any(m.label=='新事件XGBoost信号' for m in at.metric)
    assert fake.create_calls==0 and not at.exception


@pytest.mark.parametrize("decision,label,final", [
    ("approve", "批准复核建议", "approved"),
    ("reject", "拒绝复核建议", "rejected"),
])
def test_complete_investigation_approval_and_rerun_do_not_recreate(monkeypatch, decision, label, final):
    fake = FakeApi()
    at = _app(monkeypatch, fake).run()
    assert not at.exception and fake.create_calls == 0
    _click(at, "开始新调查")
    assert at.session_state["run_id"] == RUN_ID
    assert fake.create_calls == 1 and fake.get_calls == 1
    assert len(at.dataframe) == 1
    assert any("模拟政策" in item.value for item in at.caption)
    assert any("未经过业务概率校准" in item.value for item in at.caption)
    at.text_input(key="reviewer_id").set_value("demo-reviewer")
    at.text_area(key="approval_note").set_value("  人工核对出处  ")
    _click(at, label)
    assert len(fake.decisions) == 1
    assert fake.decisions[0]["decision"] == decision
    assert fake.decisions[0]["report_sha256"] == HASH
    assert fake.record["approval"]["status"] == final
    assert any("人工" in item.value for item in (at.info if final == "approved" else at.warning))
    at.run()
    assert not at.exception and fake.create_calls == 1
    assert fake.get_calls >= 3 and len(fake.decisions) == 1
    assert not any(item.label in ("批准复核建议", "拒绝复核建议") for item in at.button)


def test_graph_empty_vs_unavailable_are_visibly_distinct(monkeypatch):
    empty = FakeApi(_record(neighbors=False))
    at = _app(monkeypatch, empty).run()
    _click(at, "开始新调查")
    assert any("没有返回风险邻居" in item.value for item in at.info)
    partial = FakeApi(_record(status="partial_evidence", graph_status="unavailable", neighbors=False))
    at = _app(monkeypatch, partial).run()
    _click(at, "开始新调查")
    assert any("图证据不可用" in item.value for item in at.warning)
    assert not any("没有返回风险邻居" in item.value for item in at.info)


def test_no_policy_and_generation_failure_cannot_offer_approval(monkeypatch):
    no_policy = FakeApi(_record(status="insufficient_evidence", policies=False))
    at = _app(monkeypatch, no_policy).run()
    _click(at, "开始新调查")
    assert any("没有可引用的适用政策" in item.value for item in at.warning)
    assert any("没有可审批报告" in item.value for item in at.info)
    assert not any(item.label == "批准复核建议" for item in at.button)
    failed = FakeApi(create_error="generation_unavailable")
    at = _app(monkeypatch, failed).run()
    _click(at, "开始新调查")
    assert at.error and not any(item.label == "批准复核建议" for item in at.button)
    assert failed.create_calls == 1


def test_model_evidence_failure_is_not_presented_as_a_low_score(monkeypatch):
    record = _record(status="partial_evidence")
    record["report"]["model"] = {
        "status": "error", "risk_score": None, "model_version": None,
        "error_code": "risk_model_unavailable",
    }
    at = _app(monkeypatch, FakeApi(record)).run()
    _click(at, "开始新调查")
    assert any("风险模型证据不可用" in item.value for item in at.warning)
    assert next(item for item in at.metric if item.label == "XGBoost 风险分数").value == "不可用"


@pytest.mark.parametrize("unavailable",[False,True])
def test_stage09_primary_and_experimental_labels_never_invent_fusion(monkeypatch,unavailable):
    record=_record(status="partial_evidence" if unavailable else "verified",
        graph_status="unavailable" if unavailable else "ok",neighbors=not unavailable)
    record["versions"].update(score_mode="xgboost_only",model_decision_sha256=HASH)
    record["report"]["model"]["score_mode"]="xgboost_only"
    record["report"]["graph"].update(score_role="experimental",fusion_score=None,
        target_graph_risk_score=None if unavailable else .51)
    at=_app(monkeypatch,FakeApi(record)).run()
    _click(at,"开始新调查")
    metrics=[v for v in at.metric if "分数" in v.label]
    assert [v.label for v in metrics]==["XGBoost 主风险分数","GraphSAGE 实验性分数"]
    assert any("不参与融合评分" in v.value for v in at.caption)
    if unavailable:assert metrics[1].value=="不可用"



def test_quality_analysis_displays_validated_findings_and_gaps(monkeypatch):
    record=_record()
    record['report']['analysis']={'statements':['历史窗口为3笔；未校准分数不能确认欺诈。[F2]'],
        'gaps':['独立授权条件尚未证实'], 'source_records':{'F2':['3488959']}}
    fake=FakeApi(record);app=_app(monkeypatch,fake).run()
    app.text_input[0].set_value('3488959')
    # Existing form submits exactly one API creation; reruns only reread it.
    app.button[0].click().run()
    assert not app.exception
    assert any('已核验的线索与信息缺口' in e.value for e in app.subheader)
    assert any('独立授权条件尚未证实' in e.value for e in app.text)
    assert any('F2：3488959' in e.value for e in app.text)
