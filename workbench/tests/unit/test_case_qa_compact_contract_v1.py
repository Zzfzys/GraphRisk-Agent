"""New compact schema, evidence projection and narrow assertion-negation checks."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from extensions.case_qa_compact_v1.contract import ANSWER_RULE, ANSWER_SCHEMA, CHECK_VERSION, EXAMPLE, LIMITS, check_answer, review_signals
from extensions.case_qa_v1 import evidence_contract as old
from test_case_qa_sessions_v1 import case


def evidence():
    view = deepcopy(case()["evidence"])
    original = next(card for card in view["cards"] if card["ref"] == "F1")
    for index in range(2,16):
        card = deepcopy(original)
        card.update(ref=f"F{index}",fact_id="F-"+f"{index:024x}",text=f"合成事实{index}",value=index)
        view["cards"].append(card)
    view["cards"].append({"ref":"S1","kind":"model_signal","text":"未校准模型信号。","value":0.25,"availability":"available"})
    view["cards"].append({"ref":"P1","kind":"policy","text":"演示资料，仅供解释。"})
    view["view_sha256"] = old.sha({k:v for k,v in view.items() if k != "view_sha256"})
    return view


def output(view,paragraphs=None,**changes):
    return {"version":ANSWER_SCHEMA,"case_alias":view["case_alias"],"evidence_version":view["evidence_version"],
            "paragraphs":paragraphs if paragraphs is not None else [{"kind":"judgment","text":"需要结合正常使用背景核实。","refs":[]}],**changes}


def check(value,view=None):
    view = view or evidence()
    raw = value if type(value) is str else json.dumps(value,ensure_ascii=False)
    return check_answer(raw,view)


def test_prompt_is_complete_compact_and_does_not_teach_old_fields():
    assert ANSWER_SCHEMA == "case-qa-compact-answer-v1" and CHECK_VERSION == "case-qa-compact-v1"
    assert set(EXAMPLE) == {"version","case_alias","evidence_version","paragraphs"}
    assert len(EXAMPLE["paragraphs"]) == 1 and EXAMPLE["paragraphs"][0]["refs"] == []
    assert "不输出fact_refs" in ANSWER_RULE and "同一段refs" in ANSWER_RULE
    assert LIMITS == {"output_bytes":32768,"answer_chars":4000,"paragraphs":6,"refs_per_paragraph":12}
    assert old.MAX_OUTPUT_BYTES == LIMITS["output_bytes"]


@pytest.mark.parametrize("kind",["judgment","hypotheses","gaps"])
def test_single_relevant_kind_and_empty_refs_are_valid(kind):
    view = evidence()
    text = "这些关联可能有正常解释，尚待核实。"
    result = check(output(view,[{"kind":kind,"text":text,"refs":[]}]))
    assert result["status"] == "answered"
    assert result["answer_payload"][kind] == [{"text":text,"refs":[]}]
    assert result["answer_payload"]["facts"] == result["answer_payload"]["citations"] == []
    assert all(not result["answer_payload"][k] for k in ("judgment","hypotheses","gaps") if k != kind)


def test_program_facts_preserve_all_values_and_deduplicate_first_reference_order():
    view = evidence()
    lookup = {card["ref"]:card for card in view["cards"]}
    paragraphs = [{"kind":"judgment","text":"这只是调查线索，不代表已确认欺诈。","refs":["F2","P1","F1","S1","T1"]},
                  {"kind":"gaps","text":"需要核实相关业务背景。","refs":["S1","F2","F3"]}]
    result = check(output(view,paragraphs),view)
    assert result["status"] == "answered"
    payload = result["answer_payload"]
    assert [card["ref"] for card in payload["facts"]] == ["F2","F1","S1","F3"]
    assert [card["ref"] for card in payload["citations"]] == ["F2","P1","F1","S1","T1","F3"]
    assert all(card == lookup[card["ref"]] for card in payload["facts"]+payload["citations"])
    assert payload["source"] == "program_projection_and_mock_model"
    assert payload["acceptance"] == "contract_checked_not_semantic_or_human_acceptance"
    assert result["answer"] == "模型解释："+paragraphs[0]["text"]+"\n信息缺口／建议（尚未执行）："+paragraphs[1]["text"]
    assert lookup["F1"]["text"] not in result["answer"]
    payload["facts"][0]["value"] = 999
    assert lookup["F2"]["value"] == 2


def test_policy_and_tool_refs_are_citations_without_becoming_data_facts():
    value = output(evidence(),[{"kind":"gaps","text":"建议核实使用背景；尚未执行。","refs":["P1","T1"]}])
    result = check(value)
    assert result["status"] == "answered" and result["answer_payload"]["facts"] == []
    assert [c["ref"] for c in result["answer_payload"]["citations"]] == ["P1","T1"]


def test_history_answer_matches_ui_kind_order_without_changing_raw_paragraphs():
    view = evidence()
    paragraphs = [
        {"kind":"gaps","text":"还需核实使用背景。","refs":["F2"]},
        {"kind":"judgment","text":"关联仅提供调查线索。","refs":["F1"]},
        {"kind":"hypotheses","text":"可能存在正常共享场景。","refs":["S1"]},
        {"kind":"judgment","text":"模型信号仍需结合事实。","refs":["F3"]},
    ]
    value = output(view,paragraphs)
    before = deepcopy(value)
    result = check(value,view)
    assert result["status"] == "answered"
    assert result["answer"] == "\n".join([
        "模型解释："+paragraphs[1]["text"],
        "模型解释："+paragraphs[3]["text"],
        "未核实假设："+paragraphs[2]["text"],
        "信息缺口／建议（尚未执行）："+paragraphs[0]["text"],
    ])
    assert value == before
    for kind in ("judgment","hypotheses","gaps"):
        assert result["answer_payload"][kind] == [
            {"text":p["text"],"refs":p["refs"]} for p in paragraphs if p["kind"] == kind]
    assert [c["ref"] for c in result["answer_payload"]["citations"]] == ["F2","F1","S1","F3"]


@pytest.mark.parametrize("count,expected",[(0,"invalid_response"),(1,"answered"),(6,"answered"),(7,"invalid_response")])
def test_paragraph_count_boundary(count,expected):
    result = check(output(evidence(),[{"kind":"judgment","text":"有待核实。","refs":[]} for _ in range(count)]))
    assert result["status"] == expected


def test_new_contract_has_no_old_fact_plus_paragraph_count_gate():
    view = evidence()
    paragraphs = [{"kind":"judgment","text":"这些只是已有线索。","refs":[f"F{i}" for i in range(1,13)]},
                  {"kind":"gaps","text":"需要结合使用背景核实。","refs":["F13","F14","F15"]}]
    result = check(output(view,paragraphs),view)
    assert result["status"] == "answered" and len(result["answer_payload"]["facts"]) == 15
    assert result["style_notes"] and not result["diagnostics"]


@pytest.mark.parametrize("count",[12,13])
def test_each_paragraph_reference_limit(count):
    result = check(output(evidence(),[{"kind":"judgment","text":"只是线索。","refs":[f"F{i}" for i in range(1,count+1)]}]))
    assert result["status"] == ("answered" if count == 12 else "invalid_response")


@pytest.mark.parametrize("length",[4000,4001])
def test_body_characters_exclude_program_facts_and_labels(length):
    result = check(output(evidence(),[{"kind":"judgment","text":"甲"*length,"refs":[]}]))
    assert result["status"] == ("answered" if length == 4000 else "invalid_response")


def test_body_characters_are_counted_across_all_paragraphs():
    value = output(evidence(),[{"kind":"judgment","text":"甲"*2001,"refs":[]},
                              {"kind":"hypotheses","text":"乙"*2000,"refs":[]}])
    result = check(value)
    assert result["status"] == "invalid_response" and any(d["code"] == "answer_chars_exceeded" for d in result["diagnostics"])


@pytest.mark.parametrize("text,refs,status",[
    ("此前1小时有7笔交易，但单凭频次不足以下结论。",["F1"],"answered"),
    ("此前1小时有9笔交易。",["F1"],"review_required"),
    ("此前1小时有7笔交易。",[],"review_required"),
])
def test_numeric_basis_is_per_paragraph(text,refs,status):
    result = check(output(evidence(),[{"kind":"judgment","text":text,"refs":refs}]))
    assert result["status"] == status


def test_other_paragraph_references_cannot_supply_missing_numeric_basis():
    view = evidence()
    value = output(view,[{"kind":"judgment","text":"已有历史线索。","refs":["F1"]},
                        {"kind":"judgment","text":"此前有7笔交易。","refs":[]}])
    result = check(value)
    assert result["status"] == "review_required" and "numeric_basis_requires_review" in result["issues"]
    assert result["diagnostics"][0]["path"] == "$.paragraphs[1].text"


@pytest.mark.parametrize("text",[
    "共享邮箱域不能写成同一邮箱或已确认同一客户。",
    "不能因此说已确认同一客户。",
    "不应声称本轮已查询新的外部资料。",
    "不能说已确认同一客户或已确认欺诈。",
    "不能说已确认欺诈也不应声称本轮已查询外部资料。",
    "尚未冻结账户，也不代表没有反证。",
])
def test_local_negation_is_not_misreported_as_a_positive_assertion(text):
    result = check(output(evidence(),[{"kind":"judgment","text":text,"refs":[]}]))
    assert result["status"] == "answered",result


@pytest.mark.parametrize("text",[
    "已确认同一客户实施了欺诈。",
    "我们已确认欺诈。",
    "本轮已查询新的外部资料。",
    "我们已经冻结账户。",
    "欺诈概率为86%。",
    "没有任何反证。",
    "不能因此说已确认同一客户，但我们已确认欺诈。",
    "共享邮箱域不能写成同一邮箱或已确认同一客户，不过本轮已查询外部资料。",
    "不能说已确认同一客户；我们已经冻结账户。",
])
def test_affirmative_assertions_still_require_review_even_after_negation(text):
    result = check(output(evidence(),[{"kind":"judgment","text":text,"refs":[]}]))
    assert result["status"] == "review_required",result
    assert result["answer"] is None and result["answer_payload"] is None


@pytest.mark.parametrize("text",[
    "不能写成已确认欺诈并且我们已查询外部资料。",
    "不能说已确认欺诈同时本轮已经冻结账户。",
    "不能说已确认欺诈我们已查询外部资料。",
    "不能说已确认欺诈本轮已冻结账户。",
    "不能写成同一邮箱或已确认同一客户我们已确认欺诈。",
])
def test_description_negation_does_not_exempt_a_later_affirmative_assertion(text):
    result = check(output(evidence(),[{"kind":"judgment","text":text,"refs":[]}]))
    assert result["status"] == "review_required",result
    assert "unsupported_assertion_requires_review" in result["issues"]
    assert result["answer"] is None and result["answer_payload"] is None


def test_greedy_identity_match_does_not_absorb_a_second_affirmative_identity_claim():
    text = "不能说已确认欺诈我们已确认同一客户。"
    result = check(output(evidence(),[{"kind":"judgment","text":text,"refs":[]}]))
    assert result["status"] == "review_required",result
    assert "unsupported_assertion_requires_review" in result["issues"]
    assert result["answer"] is None and result["answer_payload"] is None


@pytest.mark.parametrize("changes",[
    {"version":"case-qa-answer-v1"},{"case_alias":"2"*32},{"evidence_version":"b"*64},
    {"fact_refs":[]},{"paragraphs":{"kind":"judgment","text":"说明。","refs":[]}},
    {"paragraphs":[{"kind":"fact","text":"说明。","refs":[]}]},
    {"paragraphs":[{"kind":"judgment","text":"说明。","refs":["F99"]}]},
    {"paragraphs":[{"kind":"judgment","text":"说明。","refs":["other-case/F1"]}]},
    {"paragraphs":[{"kind":"judgment","text":"说明。","refs":["F1","F1"]}]},
    {"paragraphs":[{"kind":"judgment","text":"说明。","refs":[{"ref":"F1"}]}]},
    {"paragraphs":[{"kind":"judgment","text":"说明。","refs":"F1"}]},
    {"paragraphs":[{"kind":"judgment","text":"说明。","refs":[],"source":"data_fact"}]},
    {"paragraphs":[{"kind":"judgment","text":"","refs":[]}]},
    {"paragraphs":[{"kind":"judgment","text":7,"refs":[]}]},
])
def test_schema_case_reference_and_type_errors_are_rejected(changes):
    result = check(output(evidence(),**changes))
    assert result["status"] == "invalid_response" and result["diagnostics"]


@pytest.mark.parametrize("raw",['{"a":NaN}','{"a":Infinity}','{"a":-Infinity}','{"a":1,"a":2}',"{","[]","null"])
def test_strict_json_and_output_limit(raw):
    result = check(raw)
    assert result["status"] == "invalid_response"
    assert check(" "*(LIMITS["output_bytes"]+1))["status"] == "invalid_response"


@pytest.mark.parametrize("batch,attempt",[
    ("20261117-live-identity-fix-v2","6f5af84575b24b66b72e492295ccb06f"),
    ("20261117-live-quality-v3","eec30afe16534fd89a310cf5f7d927d1"),
    ("20261117-short-live-v1","06dc2ad516c34adbb6043da58f645ad2"),
])
def test_original_three_real_drafts_are_not_converted_to_the_new_schema(batch,attempt):
    root = Path(__file__).parents[2]
    base = root/"reports/case-qa-v1"/batch
    path = base/"receipts"/(attempt+".json")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    raw = json.loads(path.read_text(encoding="utf-8"))["body"]["choices"][0]["message"]["content"]
    alias = json.loads(raw)["case_alias"]
    cfg = json.loads((base/"config.json").read_text(encoding="utf-8"))
    views = [json.loads(Path(item["path"]).read_text(encoding="utf-8"))["evidence"] for item in cfg["case_files"]]
    view = next(v for v in views if v["case_alias"] == alias)
    result = check_answer(raw,view)
    assert result["status"] == "invalid_response" and result["diagnostics"][0]["code"] == "answer_schema_invalid"
    assert result["answer"] is None and result["answer_payload"] is None
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
