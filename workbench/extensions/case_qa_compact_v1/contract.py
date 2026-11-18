"""A new compact answer contract; frozen seven-field drafts are never converted."""
from copy import deepcopy
import json
import re

from extensions.case_qa_v1 import evidence_contract as original

VERSION = ANSWER_SCHEMA = "case-qa-compact-answer-v1"
CHECK_VERSION = ANSWER_REVISION = "case-qa-compact-v1"
LIMITS = {"output_bytes":32768,"answer_chars":4000,"paragraphs":6,"refs_per_paragraph":12}
KINDS = original.PARTS
LABELS = {"judgment":"模型解释","hypotheses":"未核实假设","gaps":"信息缺口／建议（尚未执行）"}
EXAMPLE = {"version":VERSION,"case_alias":"复制当前evidence.case_alias",
    "evidence_version":"复制当前evidence.evidence_version", "paragraphs":[
        {"kind":"judgment","text":"示例：只解释当前问题直接相关的一点；这不是本案结论。","refs":[]}]}
ANSWER_RULE = (
    "直接回答当前问题。普通短问通常只写1个最相关段落，细节仅在问题需要时展开。"
    "只输出四字段JSON：version/case_alias/evidence_version/paragraphs；"
    f"version固定{VERSION}，case_alias和evidence_version逐字复制当前evidence。"
    f"paragraphs为1至{LIMITS['paragraphs']}个{{kind,text,refs}}对象；kind只能是judgment（解释）、hypotheses（待核实假设）、gaps（缺口或尚未执行的建议）。"
    "text为非空短正文；refs为本案evidence.cards的引用字符串数组，可空且组内不重复。"
    f"每段refs最多{LIMITS['refs_per_paragraph']}条；所有text合计最多{LIMITS['answer_chars']}字符，输出最多{LIMITS['output_bytes']}UTF-8字节。"
    "不输出fact_refs或其他旧字段；程序从refs提取并展示原事实，正文解释意义，不重复事实清单和长分数。"
    "若正文需要数字，同一段refs必须支持这个数字和指标，其他段的引用不能代替。"
    "共享邮箱域不等于同一邮箱或已核实客户；不把模型信号写成欺诈事实或已验证概率，不声称本轮新工具查询或业务处置。"
    "政策按来源标注，演示资料不能当成真实业务授权。不要Markdown围栏、额外字段或重复JSON键。"
    "下面仅示范完整结构，正文与占位值不能用作本案回答："
    + json.dumps(EXAMPLE,ensure_ascii=False,separators=(",",":"))
)
# This one description needs a longer local prefix than the frozen negation rule.
# It grants no exemption to later tool claims or a longer compound assertion.
NEGATED_DESCRIPTION = re.compile(r"(?:不能|不得|不应|不可|不要|不允许)(?:因此|据此)?(?:写成|说成|表述为|描述为|称为|声称|说)同一邮箱或$")
CLAUSE_BOUNDARY = re.compile(r"[。！？；\n，,]|但是|然而|不过|可是|但|却|事实上|实际上")
# Stop at the first identity/fraud object, so a later confirmation stays visible.
ASSERTION_PATTERNS = (re.compile(r"(?:已确认|已证实|确定是|就是).{0,10}?(?:同一人|同一客户|欺诈团伙|盗刷|欺诈)"),
                      *original.RISK_PATTERNS[1:])


def review_signals(text,refs,lookup):
    """Per-paragraph numeric bases plus locally negated assertion detection."""
    issues = [code for code in original._review_signals(text,refs,lookup) if code == "numeric_basis_requires_review"]
    for clause in CLAUSE_BOUNDARY.split(text):
        for index,pattern in enumerate(ASSERTION_PATTERNS):
            negated_description_end = None
            for match in pattern.finditer(clause):
                prefix = clause[:match.start()]
                negation = original.NEGATION.search(prefix[-12:])
                # Even a short negation cannot cross an already completed claim:
                # "不能说已确认欺诈本轮已冻结..." contains an affirmative action.
                negated = negation is not None and not any(
                    prior.search(negation[0]) for prior in ASSERTION_PATTERNS)
                description = (index == 0 and match[0] == "已确认同一客户"
                               and NEGATED_DESCRIPTION.search(prefix) is not None)
                # An immediate "或" can join the same prohibited descriptions;
                # a new subject such as "我们" cannot inherit that negation.
                joined_description = (index == 0 and negated_description_end is not None
                                      and clause[negated_description_end:match.start()] == "或")
                is_negated = negated or description or joined_description
                if not is_negated:
                    issues.append("unsupported_assertion_requires_review")
                negated_description_end = match.end() if index == 0 and is_negated else None
    return sorted(set(issues))


_review_signals = review_signals


def _style(paragraphs,facts,evidence):
    text = "\n".join(p["text"] for p in paragraphs if type(p) is dict and type(p.get("text")) is str)
    notes = []
    if len(paragraphs)>3 or len(text)>800:
        notes.append({"code":"long_answer","message":"回答较长，可检查是否只保留与当前问题相关的解释。"})
    if len(facts)>3:
        notes.append({"code":"many_facts","message":"引用较多，可优先保留最相关的依据。"})
    if re.search(r"0\.\d{6,}",text):
        notes.append({"code":"long_score","message":"可用自然语言解释分数含义，原值由程序展示。"})
    codes = {c.get(key) for c in evidence["cards"] for key in ("missing_reason","metric","source_tool","temporal_scope")
             if type(c.get(key)) is str and "_" in c[key]}
    if re.search(r"\b[FTP]\d+\b|F-[0-9a-f]{24}",text) or any(code in text for code in codes):
        notes.append({"code":"internal_terms","message":"正文可用自然语言解释，内部字段和引用码按需查看。"})
    return notes


def _invalid(diagnostics):
    return {"status":"invalid_response","answer":None,"answer_payload":None,"issues":["compact_answer_invalid"],
            "diagnostics":diagnostics,"style_notes":[],
            "notice":"回答结构或本案引用未通过："+diagnostics[0]["message"]+"原始输出保持不变，未自动转换、修复或重试。"}


def check_answer(raw,evidence):
    original.validate_view(evidence)
    diagnostics = []
    def add(code,path,message,**details):
        diagnostics.append({"code":code,"path":path,"message":message,**details})
    try:
        value = original._load(raw)
    except (TypeError,ValueError,RecursionError,ArithmeticError):
        add("json_invalid","$","输出须为不含重复键、非有限数值的严格JSON文本，且不超过字节上限。")
        return _invalid(diagnostics)
    required = {"version","case_alias","evidence_version","paragraphs"}
    if type(value) is not dict or set(value) != required:
        add("answer_schema_invalid","$","答案必须恰好包含version/case_alias/evidence_version/paragraphs四个字段；旧七字段不会自动转换。")
        return _invalid(diagnostics)
    for key,wanted in (("version",VERSION),("case_alias",evidence["case_alias"]),("evidence_version",evidence["evidence_version"])):
        if value[key] != wanted:
            add("answer_case_or_version_mismatch","$."+key,"版本或本案证据标识不匹配。",expected=wanted)
    paragraphs = value["paragraphs"]
    if type(paragraphs) is not list:
        add("paragraphs_type_invalid","$.paragraphs","paragraphs必须是数组，单个对象也不合格。")
        return _invalid(diagnostics)
    if not 1 <= len(paragraphs) <= LIMITS["paragraphs"]:
        add("paragraph_count_invalid","$.paragraphs",f"段落数应为1至{LIMITS['paragraphs']}。",actual=len(paragraphs))
    lookup = {card["ref"]:card for card in evidence["cards"]}
    ordered_refs, seen_refs, text_chars = [], set(), 0
    for index,paragraph in enumerate(paragraphs):
        path = f"$.paragraphs[{index}]"
        if type(paragraph) is not dict or set(paragraph) != {"kind","text","refs"}:
            add("paragraph_schema_invalid",path,"段落项必须恰好包含kind/text/refs。")
            continue
        if paragraph["kind"] not in KINDS:
            add("paragraph_kind_invalid",path+".kind","kind只能为judgment/hypotheses/gaps。")
        text = paragraph["text"]
        if type(text) is not str or not text.strip():
            add("text_invalid",path+".text","正文必须是非空字符串。")
        else:
            text_chars += len(text)
            try:
                text.encode("utf-8")
            except UnicodeError:
                add("text_encoding_invalid",path+".text","正文包含无效UTF-8字符。")
        refs = paragraph["refs"]
        if type(refs) is not list:
            add("refs_type_invalid",path+".refs","refs必须是字符串数组。")
            continue
        if len(refs)>LIMITS["refs_per_paragraph"]:
            add("refs_count_exceeded",path+".refs",f"每段最多{LIMITS['refs_per_paragraph']}条引用。",actual=len(refs))
        local = set()
        for position,ref in enumerate(refs):
            here = f"{path}.refs[{position}]"
            if type(ref) is not str:
                add("ref_type_invalid",here,"单条引用必须是字符串。")
            elif ref in local:
                add("reference_duplicate",here,"本段引用重复。",actual=ref)
            elif ref not in lookup:
                add("reference_not_found",here,"引用不在当前本案证据中。",actual=ref)
            else:
                if ref not in seen_refs:
                    seen_refs.add(ref)
                    ordered_refs.append(ref)
            if type(ref) is str:
                local.add(ref)
    if text_chars>LIMITS["answer_chars"]:
        add("answer_chars_exceeded","$.paragraphs",f"正文合计最多{LIMITS['answer_chars']}字符。",actual=text_chars)
    if diagnostics:
        return _invalid(diagnostics)
    facts = [deepcopy(lookup[ref]) for ref in ordered_refs if lookup[ref]["kind"] in {"data_fact","model_signal"}]
    styles = _style(paragraphs,facts,evidence)
    issues = set()
    for index,paragraph in enumerate(paragraphs):
        try:
            found = review_signals(paragraph["text"],paragraph["refs"],lookup)
        except (TypeError,ValueError,ArithmeticError):
            found = ["numeric_basis_requires_review"]
        for code in found:
            issues.add(code)
            add(code,f"$.paragraphs[{index}].text","本段数字依据或肯定断言仍需复核。")
    if issues:
        return {"status":"review_required","answer":None,"answer_payload":None,"issues":sorted(issues),
                "diagnostics":diagnostics,"style_notes":styles,
                "notice":"回答含需复核的数字或断言，原始输出保持不变，未自动修复或重试。"}
    payload = {"facts":facts,**{kind:[] for kind in KINDS},"citations":[deepcopy(lookup[ref]) for ref in ordered_refs],
               "source":"program_projection_and_mock_model","acceptance":"contract_checked_not_semantic_or_human_acceptance"}
    for paragraph in paragraphs:
        payload[paragraph["kind"]].append({"text":paragraph["text"],"refs":deepcopy(paragraph["refs"])})
    return {"status":"answered","answer":"\n".join(LABELS[kind]+"："+p["text"] for kind in KINDS for p in paragraphs if p["kind"] == kind),
            "answer_payload":payload,"issues":[],"diagnostics":[],"style_notes":styles,"notice":None}
