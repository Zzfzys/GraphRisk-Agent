"""Sparse generation target only; unchanged verdicts and frozen real draft replay."""
from base64 import b64decode
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from extensions.case_qa_short_v1.service import ANSWER_RULE, EXAMPLE, PROMPT_REVISION, ShortService, ShortConversationStore
from extensions.case_qa_live_v3.service import LiveQualityConversationStore
from extensions.case_qa_quality_v1.contract import ANSWER_RULE as OLD_RULE, check_answer
from extensions.case_qa_quality_v1.service import QualityService
from extensions.case_qa_v1.behavior import SYSTEM_PROMPT
from extensions.case_qa_v1.evidence_contract import _review_signals, check_answer as original_check
from extensions.case_qa_v1.sessions import CONTEXT_RULE, MAX_CONTEXT_BYTES, SessionError, canonical
from test_case_qa_sessions_v1 import case


class LocalBudget:
    """Only a deterministic test double; no shared ledger, credentials or HTTP."""
    def __init__(self):
        self.reservations, self.calls = [], []
        self.answer = None

    def usage(self,value=None):
        return {"scope":"live_case_qa","provider":"deepseek","real_deepseek_attempts":len(self.calls),
                "mock_attempts_confirmed":0,"unresolved_slots":0,"batch_unresolved_slots":0,
                "session_remaining_calls":3-len(self.calls),"case_remaining_calls":3-len(self.calls),
                "batch_remaining_calls":6-len(self.calls),"cost_estimate_cny":"0.000000","unsettled_hold_cny":"0.000000",
                "budget_status":"shared_ledger","authorization":"approved_case_qa","block_reason":None}

    def reserve(self,value,request,packet):
        self.reservations.append(deepcopy(packet))
        return {"attempt_id":"a"*32,"state":"reserved","reserved_micro_cny":1,"transport_attempts":0,
                "cost_estimate_micro_cny":None,"hold_micro_cny":1}

    def generate(self,ticket,packet):
        self.calls.append(deepcopy(packet))
        view = packet["view"]["evidence"]
        value = self.answer or answer(view,judgment=[{"text":"需要结合正常使用背景核实，不能据此认定欺诈。","refs":["F1"]}])
        raw = json.dumps(value,ensure_ascii=False)
        return {"raw":raw,"error_code":None,"audit":{**ticket,"state":"settled","transport_attempts":1,
                  "cost_estimate_micro_cny":0,"hold_micro_cny":0}}


def answer(view,**changes):
    return {"version":"case-qa-answer-v1","case_alias":view["case_alias"],"evidence_version":view["evidence_version"],
            "fact_refs":[],"judgment":[],"hypotheses":[],"gaps":[],**changes}


def harness(tmp_path):
    snapshot = case()
    budget = LocalBudget()
    def resolve(transaction,batch):
        assert transaction == "1" and batch is None
        return deepcopy(snapshot)
    def current(alias,binding):
        assert alias == snapshot["case_alias"] and binding == snapshot["binding"]
        return deepcopy(snapshot)
    store = ShortConversationStore((tmp_path/"short.sqlite3").resolve())
    service = ShortService(store,resolve,current,budget=budget,id_aliases={"1":snapshot["case_alias"]})
    created = service.create({"transaction_id":"1","batch_id":None},"short-create-key")
    return service,budget,created


def follow(service,created,message_id="a"*32):
    current = service.get(created["conversation_id"])
    return service.follow(current["conversation_id"],{"message_id":message_id,
        "parent_turn_id":current["turns"][-1]["message_id"] if current["turns"] else None,
        "evidence_version":current["evidence_version"],"message":"为什么需要关注？"})


def packet_value(turns=None):
    return {"case":case(),"turns":deepcopy(turns or [])}


def history_turn(index,status="answered",text="已保存的未核实背景。"):
    return {"message_id":f"{index:032x}","message":"之前的问题。","result":{"status":status,"answer":text}}


def test_new_prompt_is_shorter_complete_and_has_only_soft_short_answer_target():
    assert len(ANSWER_RULE) < len(OLD_RULE)
    assert "默认选0至2条" in ANSWER_RULE and "只写1个相关段落" in ANSWER_RULE
    assert "用户明确要详情时可以展开" in ANSWER_RULE
    assert "同一段refs" in ANSWER_RULE and "共享邮箱域不能写成同一邮箱" in ANSWER_RULE
    assert EXAMPLE["fact_refs"] == [] and EXAMPLE["hypotheses"] == [] and EXAMPLE["gaps"] == []
    assert len(EXAMPLE["judgment"]) == 1 and EXAMPLE["judgment"][0]["refs"] == []
    assert set(EXAMPLE) == {"version","case_alias","evidence_version","fact_refs","judgment","hypotheses","gaps"}


def test_packet_has_exact_new_prompt_unchanged_evidence_and_true_byte_size():
    value = packet_value()
    original = deepcopy(value)
    packet,context,size = object.__new__(ShortService)._packet(value,{"message":"为什么需要关注？"})
    old_packet,_,old_size = object.__new__(QualityService)._packet(value,{"message":"为什么需要关注？"})
    assert packet["system_prompt"] == SYSTEM_PROMPT+CONTEXT_RULE+ANSWER_RULE
    assert packet["system_prompt"] != old_packet["system_prompt"] and size < old_size
    assert packet["view"]["evidence"] == value["case"]["evidence"] and value == original
    assert packet["view"] == old_packet["view"]
    assert size == len(canonical(packet).encode("utf-8")) <= MAX_CONTEXT_BYTES
    assert not context["included_turn_ids"] and not context["omitted_turn_ids"]


def test_context_only_last_three_successful_pairs_and_tracks_omissions():
    turns = [history_turn(i) for i in range(1,6)]
    turns.insert(3,history_turn(99,"invalid_response","未通过首答不得当成功历史。"))
    packet,context,size = object.__new__(ShortService)._packet(packet_value(turns),{"message":"继续解释。"})
    assert context["included_turn_ids"] == [f"{i:032x}" for i in (3,4,5)]
    assert context["omitted_turn_ids"] == [f"{i:032x}" for i in (1,2)]
    assert all(t["source"] == "unverified_dialogue" for t in packet["view"]["dialogue_background"])
    assert f"{99:032x}" not in context["included_turn_ids"] and size == len(canonical(packet).encode("utf-8"))


def test_larger_utf8_history_drops_oldest_without_cutting_evidence():
    value = packet_value([history_turn(i,text="甲"*5000) for i in range(1,4)])
    packet,context,size = object.__new__(ShortService)._packet(value,{"message":"继续。"})
    assert context["included_turn_ids"] == [f"{3:032x}"]
    assert context["omitted_turn_ids"] == [f"{i:032x}" for i in (1,2)]
    assert packet["view"]["evidence"] == value["case"]["evidence"]
    assert size == len(canonical(packet).encode("utf-8")) <= MAX_CONTEXT_BYTES


def test_context_overflow_does_not_silently_crop_evidence():
    value = packet_value()
    value["case"]["evidence"]["cards"][0]["text"] = "甲"*12000
    original = deepcopy(value)
    with pytest.raises(SessionError,match="context_too_large"):
        object.__new__(ShortService)._packet(value,{"message":"解释。"})
    assert value == original


def test_store_and_public_provenance_keep_mode_adapter_answer_and_prompt_separate(tmp_path):
    service,budget,created = harness(tmp_path)
    assert created["prompt_revision"] == PROMPT_REVISION
    assert created["adapter_revision"] == "case-qa-live-quality-v3" and created["answer_revision"] == "case-qa-quality-v1"
    response = follow(service,created)
    result = response["turn"]["result"]
    assert result["status"] == "answered" and result["source"] == "deepseek"
    assert response["prompt_revision"] == result["prompt_revision"] == PROMPT_REVISION
    assert response["mode"] == result["mode"] == "live_case_qa"
    stored = service.store.get(created["conversation_id"])
    assert stored["prompt_revision"] == stored["turns"][0]["result"]["prompt_revision"] == PROMPT_REVISION
    raw = service.raw(created["conversation_id"],response["turn"]["message_id"])
    assert raw["prompt_revision"] == PROMPT_REVISION and raw["source"] == "unreviewed_deepseek_output"
    assert service.evidence(created["conversation_id"])["prompt_revision"] == PROMPT_REVISION
    assert json.loads(b64decode(raw["raw_response"]["content_base64"])) == json.loads(json.dumps(budget.answer or answer(case()["evidence"],judgment=[{"text":"需要结合正常使用背景核实，不能据此认定欺诈。","refs":["F1"]}])))
    with sqlite3.connect(service.store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (ShortConversationStore.STORE_MARKER,)
    with pytest.raises(SessionError,match="session_store_invalid"):
        LiveQualityConversationStore(service.store.path)


def test_replay_and_reread_do_not_create_another_request(tmp_path):
    service,budget,created = harness(tmp_path)
    first = follow(service,created)
    request = {"message_id":"a"*32,"parent_turn_id":None,"evidence_version":created["evidence_version"],"message":"为什么需要关注？"}
    replay = service.follow(created["conversation_id"],request)
    service.get(created["conversation_id"])
    service.raw(created["conversation_id"],"a"*32)
    assert replay["replayed"] and len(budget.calls) == len(budget.reservations) == 1
    assert replay["turn"]["result"] == first["turn"]["result"]


def test_more_than_one_related_paragraph_is_not_a_new_hard_failure(tmp_path):
    service,budget,created = harness(tmp_path)
    view = case()["evidence"]
    budget.answer = answer(view,judgment=[{"text":"这个线索值得结合背景核实。","refs":["F1"]} for _ in range(3)],
                           hypotheses=[{"text":"可能有正常解释。","refs":[]}],gaps=[{"text":"需要核实使用目的。","refs":[]}])
    response = follow(service,created)
    assert response["turn"]["result"]["status"] == "answered"
    assert len(budget.calls) == 1 and not response["turn"]["result"]["diagnostics"]


@pytest.mark.parametrize("text",["不能说已确认欺诈团伙。","本轮未查询新的资料。","此前1小时有7笔交易，但单凭频次不足以下结论。"])
def test_negated_assertions_and_grounded_numbers_keep_old_verdict(text):
    view = case()["evidence"]
    raw = json.dumps(answer(view,judgment=[{"text":text,"refs":["F1"]}]),ensure_ascii=False)
    assert check_answer(raw,view)["status"] == original_check(raw,view)["status"] == "answered"


@pytest.mark.parametrize("text",["此前1小时有9笔交易。","已确认同一客户实施了盗刷。","欺诈概率为86%。"])
def test_no_new_prompt_weakens_old_numeric_or_assertion_review(text):
    view = case()["evidence"]
    raw = json.dumps(answer(view,judgment=[{"text":text,"refs":["F1"]}]),ensure_ascii=False)
    assert check_answer(raw,view)["status"] == original_check(raw,view)["status"] == "review_required"


@pytest.mark.parametrize("changes",[{"fact_refs":["T1"]},{"case_alias":"2"*32,"fact_refs":["F1"]},
                                  {"fact_refs":["F1","F1"]},{"judgment":{"text":"说明。","refs":[]}}])
def test_old_structural_rejection_is_unchanged(changes):
    view = case()["evidence"]
    raw = json.dumps(answer(view,**changes),ensure_ascii=False)
    assert check_answer(raw,view)["status"] == original_check(raw,view)["status"] == "invalid_response"


def test_original_v3_draft_stays_invalid_and_local_numeric_refs_still_require_review():
    root = Path(__file__).parents[2]
    base = root/"reports/case-qa-v1/20261117-live-quality-v3"
    path = base/"receipts/eec30afe16534fd89a310cf5f7d927d1.json"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = json.loads(path.read_text(encoding="utf-8"))
    raw = receipt["body"]["choices"][0]["message"]["content"]
    cfg = json.loads((base/"config.json").read_text(encoding="utf-8"))
    view = json.loads(Path(cfg["case_files"][0]["path"]).read_text(encoding="utf-8"))["evidence"]
    result = check_answer(raw,view)
    assert result["status"] == "invalid_response"
    assert result["diagnostics"][0]["code"] == "total_items_out_of_range" and result["diagnostics"][0]["actual"] == 22
    parsed = json.loads(raw)
    lookup = {card["ref"]:card for card in view["cards"]}
    for index in (2,3):
        item = parsed["judgment"][index]
        assert _review_signals(item["text"],item["refs"],lookup) == ["numeric_basis_requires_review"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
