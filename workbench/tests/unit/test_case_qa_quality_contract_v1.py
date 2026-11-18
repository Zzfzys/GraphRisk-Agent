"""Offline prompt/diagnosis agreement; legacy answer decisions stay authoritative."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from extensions.case_qa_quality_v1.contract import ANSWER_RULE, FORMAT_EXAMPLE, LIMITS, build_answer_rule, check_answer, diagnose_answer
from extensions.case_qa_v1 import evidence_contract as original
from test_case_qa_sessions_v1 import case


def evidence():
    value = deepcopy(case()["evidence"])
    fact = next(c for c in value["cards"] if c["ref"] == "F1")
    value["cards"] = [next(c for c in value["cards"] if c["ref"] == "T1")]
    for index in range(1,21):
        item = deepcopy(fact)
        item.update(ref=f"F{index}",fact_id="F-"+f"{index:024x}",text="此前一小时有7笔交易。")
        value["cards"].append(item)
    value["cards"].append({"ref":"P1","kind":"policy","text":"项目演示资料；不能当成处置授权。"})
    value["view_sha256"] = original.sha({k:v for k,v in value.items() if k != "view_sha256"})
    return value


def output(view,**changes):
    return {"version":"case-qa-answer-v1","case_alias":view["case_alias"],"evidence_version":view["evidence_version"],
            "fact_refs":[],"judgment":[],"hypotheses":[],"gaps":[],**changes}


def check(value,view=None):
    view = view or evidence()
    raw = value if type(value) is str else json.dumps(value,ensure_ascii=False)
    legacy = original.check_answer(raw,view)
    result = check_answer(raw,view)
    for key in ("status","answer","answer_payload","issues"):
        assert result[key] == legacy[key]
    return result


def diagnostic(result,code,path=None):
    return [d for d in result["diagnostics"] if d["code"] == code and (path is None or d["path"] == path)]


def test_prompt_has_complete_same_schema_and_limits_without_case_claims():
    assert ANSWER_RULE == build_answer_rule()
    assert set(FORMAT_EXAMPLE) == {"version","case_alias","evidence_version","fact_refs","judgment","hypotheses","gaps"}
    assert FORMAT_EXAMPLE["fact_refs"] == [] and FORMAT_EXAMPLE["judgment"][0]["refs"] == []
    assert all(type(FORMAT_EXAMPLE[key]) is list for key in original.PARTS)
    assert "示例，仅展示格式，不能作为证据" in ANSWER_RULE and "逐字复制当前evidence" in ANSWER_RULE
    assert "引用标为项目演示的政策时明确其演示性质" in ANSWER_RULE and "可少选或选0条" in ANSWER_RULE
    assert "真实政策按证据中的来源标注" in ANSWER_RULE and "引用命中不表示本案条件已成立" in ANSWER_RULE
    assert all(str(n) in ANSWER_RULE for n in LIMITS.values())
    view = evidence()
    example = deepcopy(FORMAT_EXAMPLE)
    example.update(case_alias=view["case_alias"],evidence_version=view["evidence_version"])
    assert check(example)["status"] == "answered"


@pytest.mark.parametrize("group",["fact_refs","item_refs"])
@pytest.mark.parametrize("count",[12,13])
def test_reference_group_count_boundary(group,count):
    view = evidence()
    refs = [f"F{i}" for i in range(1,count+1)]
    value = output(view,**({"fact_refs":refs} if group == "fact_refs" else {"judgment":[{"text":"需要结合正常使用情况核实。","refs":refs}]}))
    result = check(value)
    assert result["status"] == ("answered" if count == 12 else "invalid_response")
    assert bool(diagnostic(result,"refs_count_exceeded")) == (count == 13)


@pytest.mark.parametrize("count",[6,7])
def test_each_part_count_boundary(count):
    view = evidence()
    value = output(view,judgment=[{"text":"有待核实。","refs":[]} for _ in range(count)])
    result = check(value)
    assert result["status"] == ("answered" if count == 6 else "invalid_response")
    assert bool(diagnostic(result,"part_count_exceeded","$.judgment")) == (count == 7)


@pytest.mark.parametrize("count",[0,1,12,13])
def test_total_item_boundary(count):
    view = evidence()
    fact_count = min(count,6)
    rest = count-fact_count
    value = output(view,fact_refs=[f"F{i}" for i in range(1,fact_count+1)],
        judgment=[{"text":"需要核实。","refs":[]} for _ in range(min(rest,6))],
        hypotheses=[{"text":"可能有正常解释。","refs":[]} for _ in range(max(0,rest-6))])
    result = check(value)
    assert result["status"] == ("answered" if count in {1,12} else "invalid_response")
    assert bool(diagnostic(result,"total_items_out_of_range")) == (count in {0,13})


@pytest.mark.parametrize("changes,code,path",[
    ({"fact_refs":["P1"]},"fact_source_kind_invalid","$.fact_refs[0]"),
    ({"fact_refs":["T1"]},"fact_source_kind_invalid","$.fact_refs[0]"),
    ({"fact_refs":["F1","F1"]},"reference_duplicate","$.fact_refs[1]"),
    ({"fact_refs":["other-case/F1"]},"reference_not_found","$.fact_refs[0]"),
    ({"fact_refs":[23]},"ref_type_invalid","$.fact_refs[0]"),
    ({"fact_refs":"F1"},"refs_type_invalid","$.fact_refs"),
    ({"judgment":{"text":"解释。","refs":["F1"]}},"part_type_invalid","$.judgment"),
    ({"judgment":["解释。"]},"item_type_invalid","$.judgment[0]"),
    ({"judgment":[{"text":"解释。","refs":"F1"}]},"refs_type_invalid","$.judgment[0].refs"),
    ({"judgment":[{"text":"","refs":[]}]},"text_empty","$.judgment[0].text"),
    ({"judgment":[{"text":2,"refs":[]}]},"text_type_invalid","$.judgment[0].text"),
    ({"judgment":[{"text":"解释。"}]},"field_missing","$.judgment[0].refs"),
    ({"case_alias":"2"*32,"fact_refs":["F1"]},"case_mismatch","$.case_alias"),
    ({"evidence_version":"b"*64,"fact_refs":["F1"]},"evidence_version_mismatch","$.evidence_version"),
    ({"version":"other","fact_refs":["F1"]},"answer_version_mismatch","$.version"),
])
def test_types_refs_and_case_diagnostics_preserve_old_rejection(changes,code,path):
    result = check(output(evidence(),**changes))
    assert result["status"] == "invalid_response" and diagnostic(result,code,path)
    assert result["answer"] is None and "原文保持不变" in result["notice"]


@pytest.mark.parametrize("raw,code",[
    ("{","json_invalid"),("null","answer_type_invalid"),("[]","answer_type_invalid"),
    ('{"x":NaN}',"json_invalid"),('{"version":"a","version":"b"}',"json_duplicate_key"),
])
def test_json_errors_are_diagnosed_not_repaired(raw,code):
    result = check(raw)
    assert result["status"] == "invalid_response" and diagnostic(result,code)


def test_nested_duplicate_fields_and_independent_errors_are_all_reported():
    view = evidence()
    raw = json.dumps(output(view,judgment=[{"text":"解释。","refs":["F1","F1","missing"]}]),ensure_ascii=False)
    raw = raw.replace('"text": "解释。"','"text": "解释。", "text": "另一段"')
    result = check(raw)
    assert diagnostic(result,"json_duplicate_key","$.judgment[0].text")
    assert diagnostic(result,"reference_duplicate","$.judgment[0].refs[1]")
    assert diagnostic(result,"reference_not_found","$.judgment[0].refs[2]")


@pytest.mark.parametrize("size",[3995,3996,4001])
def test_rendered_and_segment_character_limits(size):
    value = output(evidence(),judgment=[{"text":"甲"*size,"refs":[]}])
    result = check(value)
    assert result["status"] == ("answered" if size == 3995 else "invalid_response")
    assert bool(diagnostic(result,"rendered_answer_too_long")) == (size > 3995)
    assert bool(diagnostic(result,"text_chars_exceeded")) == (size > LIMITS["answer_chars"])


def test_output_bytes_and_unicode_are_explicit():
    result = check(" "*(LIMITS["output_bytes"]+1))
    assert diagnostic(result,"output_bytes_exceeded")[0]["actual"] == 32769
    view = evidence()
    raw = json.dumps(output(view,judgment=[{"text":"\ud800","refs":[]}]))
    result = check(raw)
    assert result["status"] == "invalid_response" and diagnostic(result,"text_encoding_invalid")
    direct = diagnose_answer({"not":"json text"},view)
    assert direct[0]["code"] == "output_type_invalid"


@pytest.mark.parametrize("text",[
    "不能说已确认欺诈团伙。", "尚未冻结账户，也不代表没有反证。", "本轮未查询新的资料。",
    "此前1小时有7笔交易，但单凭频次不足以下结论。", "可能是正常共用设备，尚待核实。",
])
def test_negation_and_grounded_numbers_keep_their_original_meaning(text):
    value = output(evidence(),judgment=[{"text":text,"refs":["F1"]}])
    result = check(value)
    assert result["status"] == "answered" and text in result["answer"] and not result["diagnostics"]


@pytest.mark.parametrize("text",["此前1小时有9笔交易。","已确认同一客户实施了盗刷。","欺诈概率为86%。"])
def test_existing_semantic_review_is_not_weakened(text):
    result = check(output(evidence(),judgment=[{"text":text,"refs":["F1"]}]))
    assert result["status"] == "review_required" and not result["diagnostics"]


def test_style_is_advice_not_an_extra_gate():
    view = evidence()
    result = check(output(view,fact_refs=["F1","F2","F3","F4"],judgment=[{"text":"甲"*801,"refs":[]}]))
    assert result["status"] == "answered"
    assert {n["code"] for n in result["style_notes"]} == {"many_facts","long_answer"}
    assert result["notice"] is None


def test_real_first_draft_replay_preserves_failure_and_explains_all_five_faults():
    root = Path(__file__).parents[2]
    base = root/"reports/case-qa-v1/20261117-live-identity-fix-v2"
    path = base/"receipts/6f5af84575b24b66b72e492295ccb06f.json"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = json.loads(path.read_text(encoding="utf-8"))
    raw = receipt["body"]["choices"][0]["message"]["content"]
    prior_case = root/"reports/case-qa-v1/20261117-live-trial-v1/cases/case-1.json"
    view = json.loads(prior_case.read_text(encoding="utf-8"))["evidence"]
    result = check(raw,view)
    assert result["status"] == "invalid_response" and result["issues"] == ["answer_schema_or_reference_invalid"]
    assert diagnostic(result,"refs_count_exceeded","$.fact_refs")[0]["actual"] == 21
    policy = diagnostic(result,"fact_source_kind_invalid","$.fact_refs[20]")[0]
    assert policy["actual"] == "P1" and policy["kind"] == "policy"
    assert diagnostic(result,"part_type_invalid","$.judgment")[0]["actual"] == "object"
    assert diagnostic(result,"refs_count_exceeded","$.judgment.refs")[0]["actual"] == 16
    assert diagnostic(result,"total_items_out_of_range")[0]["actual"] == 27
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("code",["no_history_statistic","not_provided_by_legacy_contract"])
def test_actual_missing_reason_codes_get_soft_style_advice_only(code):
    view = evidence()
    fact = next(c for c in view["cards"] if c["ref"] == "F1")
    fact.update(availability="unavailable",value=None,missing_reason=code)
    view["view_sha256"] = original.sha({k:v for k,v in view.items() if k != "view_sha256"})
    value = output(view,judgment=[{"text":"当前标注为"+code+"，不能据此认定安全。","refs":["F1"]}])
    result = check(value,view)
    assert result["status"] == "answered" and not result["diagnostics"]
    assert "internal_terms" in {note["code"] for note in result["style_notes"]}
    assert code in result["answer"]
