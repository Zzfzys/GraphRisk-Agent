"""A sparse answer target on the unchanged QA verdict and execution contracts."""
from copy import deepcopy
import json

from extensions.case_qa_live_v3.service import LiveQualityService, LiveQualityConversationStore
from extensions.case_qa_quality_v1.contract import LIMITS
from extensions.case_qa_v1.behavior import SYSTEM_PROMPT
from extensions.case_qa_v1.sessions import CONTEXT_RULE, MAX_CONTEXT_BYTES, MAX_HISTORY_TURNS, SessionError, canonical

PROMPT_REVISION = "case-qa-short-v1"
EXAMPLE = {"version":"case-qa-answer-v1","case_alias":"复制evidence.case_alias",
    "evidence_version":"复制evidence.evidence_version","fact_refs":[],
    "judgment":[{"text":"示例：只解释当前问题直接相关的一点，其他内容暂不展开。此句仅示范格式，不是本案结论。","refs":[]}],
    "hypotheses":[],"gaps":[]}
ANSWER_RULE = (
    "先直接回答当前这一个问题。普通短问默认选0至2条最相关事实、只写1个相关段落；未被问到的段落填[]。"
    "为什么关注通常填judgment，正常解释填hypotheses，缺什么资料填gaps；不必每次同时填写三类。"
    "这是简洁目标，不是要求漏掉必要信息；用户明确要详情时可以展开。数值由程序显示，正文解释意义，不重复清单。"
    "若正文必须提具体数字，同一段refs须包含直接支持该数字及指标的事实卡；fact_refs或其他段引用不能代替。"
    "共享邮箱域不能写成同一邮箱或已确认同一客户。政策按来源说明，项目演示资料不能当成真实业务授权。"
    "仅输出完整七字段JSON：version/case_alias/evidence_version/fact_refs/judgment/hypotheses/gaps；"
    "version固定case-qa-answer-v1，case_alias及evidence_version逐字复制当前evidence。"
    "fact_refs为当前本案data_fact或model_signal的ref数组，不能放policy/tool_status；"
    "三段均为数组，段落项恰为{text,refs}，text非空，refs为当前本案ref的字符串数组，可空，组内不重复。"
    f"原硬上限不变：每组refs最多{LIMITS['refs_per_group']}；每类段落最多{LIMITS['items_per_part']}项；"
    f"fact_refs条数与三类段落项数合计{LIMITS['total_items_min']}至{LIMITS['total_items_max']}项；"
    f"每段及最终答案最多{LIMITS['answer_chars']}字符，输出最多{LIMITS['output_bytes']}UTF-8字节。"
    "不加额外字段、重复JSON键或Markdown围栏。下例仅展示稀疏完整结构，正文和占位值不能复制为本案答案："
    + json.dumps(EXAMPLE,ensure_ascii=False,separators=(",",":"))
)


def _stamp(value):
    value = deepcopy(value)
    value["prompt_revision"] = PROMPT_REVISION
    for turn in value.get("turns",[]):
        turn["result"]["prompt_revision"] = PROMPT_REVISION
    return value


class ShortConversationStore(LiveQualityConversationStore):
    STORE_MARKER = 0x535131

    @staticmethod
    def _value(row):
        value = LiveQualityConversationStore._value(row)
        if value.get("prompt_revision") != PROMPT_REVISION:
            raise SessionError("session_store_invalid")
        return value

    def create(self,key,fingerprint,value):
        return super().create(key,fingerprint,_stamp(value))

    def mutate(self,identifier,fn):
        def update(value):
            changed,returned = fn(value)
            changed = _stamp(changed)
            if type(returned) is dict and "result" in returned:
                returned = deepcopy(returned)
                returned["result"]["prompt_revision"] = PROMPT_REVISION
            return changed,returned
        return super().mutate(identifier,update)


class ShortService(LiveQualityService):
    def __init__(self,store,resolve,check,*,budget,**kwargs):
        if not isinstance(store,ShortConversationStore):
            raise SessionError("session_store_invalid")
        super().__init__(store,resolve,check,budget=budget,**kwargs)

    def _packet(self,value,request):
        completed = [t for t in value["turns"] if t["result"]["status"] == "answered"]
        selected = completed[-MAX_HISTORY_TURNS:]
        while True:
            included = [t["message_id"] for t in selected]
            omitted = [t["message_id"] for t in completed if t["message_id"] not in included]
            context = {"included_turn_ids":included,"omitted_turn_ids":omitted,
                       "history_limit":MAX_HISTORY_TURNS,"max_bytes":MAX_CONTEXT_BYTES,
                       "notice":f"本轮带入{len(included)}轮历史问答，{len(omitted)}轮较早历史未带入；未调用模型摘要。"}
            packet = {"purpose":"case_qa","system_prompt":SYSTEM_PROMPT+CONTEXT_RULE+ANSWER_RULE,
                      "view":{"question":request["message"],"evidence":deepcopy(value["case"]["evidence"]),
                              "dialogue_background":[{"turn_id":t["message_id"],"user":t["message"],
                                  "assistant":t["result"]["answer"],"source":"unverified_dialogue"} for t in selected],
                              "context":context}}
            size = len(canonical(packet).encode("utf-8"))
            if size <= MAX_CONTEXT_BYTES:
                return packet,context,size
            if not selected:
                raise SessionError("context_too_large")
            selected = selected[1:]

    @staticmethod
    def _public(value):
        return _stamp(LiveQualityService._public(value))

    def follow(self,identifier,request):
        value = _stamp(super().follow(identifier,request))
        value["turn"]["result"]["prompt_revision"] = PROMPT_REVISION
        return value

    def evidence(self,identifier):
        return _stamp(super().evidence(identifier))

    def raw(self,identifier,message_id):
        return _stamp(super().raw(identifier,message_id))
