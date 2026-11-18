"""Explain the frozen QA contract precisely; never repair or weaken its verdict."""
from __future__ import annotations

import json
import re

from extensions.case_qa_v1 import evidence_contract as original

LIMITS = {"refs_per_group":12, "items_per_part":6, "total_items_min":1,
          "total_items_max":12, "answer_chars":4000, "output_bytes":32768}
PARTS = original.PARTS
FORMAT_EXAMPLE = {"version":original.ANSWER_SCHEMA,
    "case_alias":"逐字复制当前evidence.case_alias，不能使用此占位文字",
    "evidence_version":"逐字复制当前evidence.evidence_version，不能使用此占位文字",
    "fact_refs":[], "judgment":[{"text":"示例，仅展示格式，不能作为证据", "refs":[]}],
    "hypotheses":[], "gaps":[]}


def build_answer_rule():
    return (
        "只输出一个JSON对象，不加Markdown围栏、说明前缀或额外字段，不允许重复JSON键。"
        f"七个字段完整保留：version/case_alias/evidence_version/fact_refs/judgment/hypotheses/gaps；version固定{original.ANSWER_SCHEMA}。"
        "case_alias和evidence_version逐字复制当前evidence中的同名值；不从历史对话或示例取得。"
        "fact_refs必须是字符串数组，只能引用当前evidence.cards中kind为data_fact或model_signal的ref；policy和tool_status不能放入fact_refs。"
        "judgment/hypotheses/gaps分别为模型解释、未核实假设、信息缺口或尚未执行的核实建议；三者始终是数组，单段也要用数组，未用项填[]。"
        "每个段落必须恰好是{text,refs}对象，text为非空字符串，refs为当前证据ref的字符串数组，也可为空数组。"
        f"fact_refs及每段refs各最多{LIMITS['refs_per_group']}条，组内不能重复；每个段落数组最多{LIMITS['items_per_part']}项。"
        f"fact_refs条数加上judgment/hypotheses/gaps的段落项数，总计必须为{LIMITS['total_items_min']}至{LIMITS['total_items_max']}项；不是对所有refs求和。"
        f"每段text以及程序最终拼接的答案最多{LIMITS['answer_chars']}字符，整个输出最多{LIMITS['output_bytes']} UTF-8字节。"
        "普通问题只选最相关的几条事实，可少选或选0条；按需填写段落，通常用短答，不要抄满事实清单。"
        "事实数值由程序显示，你解释其含义；不要重复内部字段码、引用码或冗长分数。需要数字时只能引用有据数值，不能把评分解释为已验证欺诈概率。"
        "共享匿名字段不是已核实身份；历史对话只是未核实背景，不能变成事实。引用标为项目演示的政策时明确其演示性质；真实政策按证据中的来源标注，不得一概称为演示。引用命中不表示本案条件已成立，也不等于处置授权。"
        "不声称本轮新增工具查询、已执行业务处置或已确认欺诈；核实建议明确尚未执行。"
        "下面仅演示完整JSON结构，正文和占位值不能用于实际回答；ref为空只为展示类型，不暗示本案存在任何事实关系："
        + json.dumps(FORMAT_EXAMPLE,ensure_ascii=False,separators=(",",":"))
    )


ANSWER_RULE = build_answer_rule()


class _Pairs(list):
    pass


def _type(value):
    return {dict:"object",list:"array",str:"string",int:"integer",float:"number",bool:"boolean",type(None):"null"}.get(type(value),type(value).__name__)


def _parse(raw, notes):
    def add(code,path,message,**details):
        notes.append({"code":code,"path":path,"message":message,**details})
    if type(raw) is not str:
        add("output_type_invalid","$","模型输出必须是JSON文本字符串。",expected="string",actual=_type(raw))
        return None
    try:
        size = len(raw.encode("utf-8"))
    except UnicodeError:
        add("output_encoding_invalid","$","模型输出包含无效UTF-8字符。")
        return None
    if size > LIMITS["output_bytes"]:
        add("output_bytes_exceeded","$",f"输出为{size}字节，最多{LIMITS['output_bytes']}字节。",expected=LIMITS["output_bytes"],actual=size)
        return None
    def invalid(_):
        raise ValueError
    try:
        value = json.loads(raw,object_pairs_hook=_Pairs,parse_constant=invalid)
    except (ValueError,RecursionError):
        add("json_invalid","$","输出不是完整、有效的严格JSON；未自动修复。")
        return None
    def convert(value,path):
        if type(value) is _Pairs:
            out = {}
            for key,item in value:
                child = path+"."+key
                converted = convert(item,child)
                if key in out:
                    add("json_duplicate_key",child,"JSON字段重复；未选择、删除或修复重复字段。",actual=key)
                else:
                    out[key] = converted
            return out
        if type(value) is list:
            return [convert(item,f"{path}[{index}]") for index,item in enumerate(value)]
        return value
    try:
        return convert(value,"$")
    except RecursionError:
        add("json_depth_invalid","$","JSON嵌套过深，未自动修复。")
        return None


def diagnose_answer(raw,evidence):
    """Return all independent structural faults without altering the raw answer."""
    original.validate_view(evidence)
    notes = []
    def add(code,path,message,**details):
        notes.append({"code":code,"path":path,"message":message,**details})
    value = _parse(raw,notes)
    if value is None and notes:
        return notes
    if type(value) is not dict:
        add("answer_type_invalid","$","答案顶层必须是JSON对象。",expected="object",actual=_type(value))
        return notes
    required = {"version","case_alias","evidence_version","fact_refs",*PARTS}
    for key in sorted(required-value.keys()):
        add("field_missing","$."+key,"缺少必填字段。",expected=key)
    for key in sorted(value.keys()-required):
        add("field_unexpected","$."+key,"存在合同之外的字段。",actual=key)
    for key,wanted,code in (("version",original.ANSWER_SCHEMA,"answer_version_mismatch"),
                            ("case_alias",evidence["case_alias"],"case_mismatch"),
                            ("evidence_version",evidence["evidence_version"],"evidence_version_mismatch")):
        if key in value and value[key] != wanted:
            add(code,"$."+key,"字段未匹配当前版本或本案证据。",expected=wanted,actual=value[key])
    lookup = {c["ref"]:c for c in evidence["cards"]}
    def refs(items,path,facts=False):
        if type(items) is not list:
            add("refs_type_invalid",path,"引用字段必须是字符串数组。",expected="array",actual=_type(items))
            return
        if len(items) > LIMITS["refs_per_group"]:
            add("refs_count_exceeded",path,f"该组有{len(items)}条引用，最多{LIMITS['refs_per_group']}条。",expected=LIMITS["refs_per_group"],actual=len(items))
        seen = set()
        for index,ref in enumerate(items):
            here = f"{path}[{index}]"
            if type(ref) is not str:
                add("ref_type_invalid",here,"单条引用必须是字符串。",expected="string",actual=_type(ref))
                continue
            if ref in seen:
                add("reference_duplicate",here,"同组引用重复。",actual=ref)
            seen.add(ref)
            if ref not in lookup:
                add("reference_not_found",here,"引用不在当前本案证据中。",actual=ref)
            elif facts and lookup[ref]["kind"] not in {"data_fact","model_signal"}:
                add("fact_source_kind_invalid",here,f"{ref}属于{lookup[ref]['kind']}，不能作为fact_refs中的数据事实。",actual=ref,kind=lookup[ref]["kind"],expected="data_fact or model_signal")
    if "fact_refs" in value:
        refs(value["fact_refs"],"$.fact_refs",facts=True)
    total = len(value["fact_refs"]) if type(value.get("fact_refs")) is list else 0
    invalid_objects = []
    texts = []
    for part in PARTS:
        if part not in value:
            continue
        items = value[part]
        if type(items) is not list:
            add("part_type_invalid","$."+part,"该字段必须是段落数组，单个对象也不合格；未自动包成数组。",expected="array",actual=_type(items))
            if type(items) is not dict:
                continue
            # Diagnostic traversal counts this observed object once; it remains invalid.
            invalid_objects.append(part)
            candidates = [("$."+part,items)]
        else:
            if len(items) > LIMITS["items_per_part"]:
                add("part_count_exceeded","$."+part,f"该段有{len(items)}项，最多{LIMITS['items_per_part']}项。",expected=LIMITS["items_per_part"],actual=len(items))
            candidates = [(f"$.{part}[{index}]",item) for index,item in enumerate(items)]
        total += len(candidates)
        for path,item in candidates:
            if type(item) is not dict:
                add("item_type_invalid",path,"段落项必须是{text,refs}对象。",expected="object",actual=_type(item))
                continue
            for key in sorted({"text","refs"}-item.keys()):
                add("field_missing",path+"."+key,"段落项缺少字段。",expected=key)
            for key in sorted(item.keys()-{"text","refs"}):
                add("field_unexpected",path+"."+key,"段落项存在额外字段。",actual=key)
            text = item.get("text")
            if type(text) is not str:
                if "text" in item:
                    add("text_type_invalid",path+".text","段落正文必须是字符串。",expected="string",actual=_type(text))
            elif not text.strip():
                add("text_empty",path+".text","段落正文不能为空。")
            else:
                texts.append((part,text))
                try:
                    text.encode("utf-8")
                except UnicodeError:
                    add("text_encoding_invalid",path+".text","段落正文包含无效UTF-8字符。")
                if len(text) > LIMITS["answer_chars"]:
                    add("text_chars_exceeded",path+".text",f"该段正文有{len(text)}字符，最多{LIMITS['answer_chars']}字符。",expected=LIMITS["answer_chars"],actual=len(text))
            if "refs" in item:
                refs(item["refs"],path+".refs")
    if not LIMITS["total_items_min"] <= total <= LIMITS["total_items_max"]:
        qualifier = "（含类型不合格的对象各按1项统计，未转换）" if invalid_objects else ""
        add("total_items_out_of_range","$",f"fact_refs与三段项数合计{total}，应为{LIMITS['total_items_min']}至{LIMITS['total_items_max']}项{qualifier}。",
            expected=[LIMITS["total_items_min"],LIMITS["total_items_max"]],actual=total)
    labels = {"data_fact":"数据事实", "model_signal":"模型信号", "judgment":"模型解释",
              "hypotheses":"未核实假设", "gaps":"信息缺口／建议（尚未执行）"}
    fact_lines = []
    if type(value.get("fact_refs")) is list:
        fact_lines = [labels[lookup[ref]["kind"]]+"："+lookup[ref]["text"] for ref in value["fact_refs"]
                      if type(ref) is str and ref in lookup and lookup[ref]["kind"] in {"data_fact","model_signal"}]
    rendered = "\n".join(fact_lines+[labels[part]+"："+text for part,text in texts])
    if len(rendered) > LIMITS["answer_chars"]:
        add("rendered_answer_too_long","$rendered_answer",f"程序呈现后共{len(rendered)}字符，最多{LIMITS['answer_chars']}字符。",expected=LIMITS["answer_chars"],actual=len(rendered))
    return notes


def _style_notes(raw,evidence):
    parsed = _parse(raw,[])
    if type(parsed) is not dict:
        return []
    notes = []
    def add(code,message):
        notes.append({"code":code,"message":message})
    refs = parsed.get("fact_refs")
    if type(refs) is list and len(refs)>3:
        add("many_facts","普通问题通常只需最相关的几条事实，可精简事实清单。")
    texts = [item["text"] for part in PARTS if type(parsed.get(part)) is list for item in parsed[part]
             if type(item) is dict and type(item.get("text")) is str]
    text = "\n".join(texts)
    if len(text)>800:
        add("long_answer","回答较长，可优先保留直接回答问题的解释。")
    if re.search(r"0\.\d{6,}",text):
        add("long_score","避免复述冗长分数，数值由程序显示，正文解释含义即可。")
    internal_codes = {card.get(key) for card in evidence["cards"]
                      for key in ("missing_reason","metric","source_tool","temporal_scope")
                      if type(card.get(key)) is str and "_" in card[key]}
    if (re.search(r"\b[FTP]\d+\b|F-[0-9a-f]{24}|get_(?:transaction|risk_score|related_entities)|primary_model_score|relative_day_end",text)
            or any(code in text for code in internal_codes)):
        add("internal_terms","正文可用自然语言解释，引用码、缺失原因代码和内部字段放在按需查看的依据中。")
    return notes


def check_answer(raw,evidence):
    result = original.check_answer(raw,evidence)
    result["diagnostics"] = diagnose_answer(raw,evidence)
    result["style_notes"] = _style_notes(raw,evidence)
    if result["diagnostics"]:
        first = result["diagnostics"][0]
        result["notice"] = "回答结构或引用未通过："+first["message"]+"其他问题可展开查看；原文保持不变，未自动修复或重试。"
    return result
