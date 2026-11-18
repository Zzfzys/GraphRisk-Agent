"""Offline quality candidate over the frozen session state machine.

No live provider is constructed here. Diagnostics enrich a new session's first
saved result without changing its verdict, answer, raw bytes or request count.
"""
from base64 import b64decode
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import sqlite3
import time

from extensions.case_qa_v1.behavior import SYSTEM_PROMPT
from extensions.case_qa_v1.sessions import (
    CaseQAService, SQLiteConversationStore, SessionError, canonical,
    CONTEXT_RULE, MAX_CONTEXT_BYTES, MAX_HISTORY_TURNS,
)
from .contract import ANSWER_RULE, check_answer

REVISION = "case-qa-quality-v1"


class QualityConversationStore(SQLiteConversationStore):
    STORE_MARKER = 0x514131

    def __init__(self, path):
        path = Path(path)
        if not path.is_absolute():
            raise SessionError("session_store_invalid")
        if path.exists():
            try:
                with sqlite3.connect(path.as_uri()+"?mode=ro", uri=True) as db:
                    if db.execute("PRAGMA user_version").fetchone()[0] != self.STORE_MARKER:
                        raise SessionError("session_store_invalid")
            except sqlite3.Error:
                raise SessionError("session_store_invalid") from None
        super().__init__(path)
        with self.connect() as db:
            db.execute(f"PRAGMA user_version={self.STORE_MARKER}")

    @staticmethod
    def _value(row):
        value = SQLiteConversationStore._value(row)
        if value.get("quality_revision") != REVISION:
            raise SessionError("session_store_invalid")
        return value

    def create(self, key, fingerprint, value):
        value = deepcopy(value)
        value["quality_revision"] = REVISION
        return super().create(key, fingerprint, value)

    def mutate(self, identifier, fn):
        def update(value):
            was_in_flight = value["state"] == "in_flight"
            changed, returned = fn(value)
            # Preserve/claim/replay transitions are untouched. Only enrich the
            # first finish, in this same transaction, before it becomes readable.
            if (was_in_flight and changed["state"] != "in_flight"
                    and type(returned) is dict and "message_id" in returned and "result" in returned):
                saved = next(t for t in changed["turns"] if t["message_id"] == returned["message_id"])
                result = saved["result"]
                receipt = result.get("raw_response", {})
                if receipt.get("complete") and "content_base64" in receipt:
                    try:
                        data = b64decode(receipt["content_base64"], validate=True)
                        if hashlib.sha256(data).hexdigest() != receipt["sha256"] or len(data) != receipt["byte_length"]:
                            raise ValueError("raw_receipt_changed")
                        text = data.decode("utf-8", errors="surrogatepass")
                        raw = text if receipt["format"] == "text" else json.loads(text)
                        checked = check_answer(raw, changed["case"]["evidence"])
                        # Provider/usage/identity/checker failures must never be
                        # reclassified as a content-check result.
                        if all(checked.get(k) == result.get(k) for k in ("status", "issues", "answer")):
                            for key in ("diagnostics", "style_notes", "notice"):
                                if key in checked:
                                    result[key] = deepcopy(checked[key])
                            result["check_version"] = REVISION
                    except Exception:
                        # Diagnostics are advisory; keep the original verdict,
                        # receipt and completed result on any enrichment failure.
                        result["diagnostics_unavailable"] = True
                returned = deepcopy(saved)
            return changed, returned
        return super().mutate(identifier, update)


class QualityService(CaseQAService):
    def __init__(self, store, resolve, check, *, mock_provider=None,
                 preflight=lambda: None, clock=time.time, budget=None):
        if not isinstance(store, QualityConversationStore):
            raise SessionError("session_store_invalid")
        if budget is not None:
            from .budget import QualityBudget
            if type(budget) is not QualityBudget:
                raise SessionError("provider_unconfigured")
        super().__init__(store, resolve, check, mock_provider=mock_provider,
                         preflight=preflight, clock=clock, budget=budget)

    def _packet(self, value, request):
        completed = [t for t in value["turns"] if t["result"]["status"] == "answered"]
        selected = completed[-MAX_HISTORY_TURNS:]
        while True:
            included = [t["message_id"] for t in selected]
            omitted = [t["message_id"] for t in completed if t["message_id"] not in included]
            context = {"included_turn_ids": included, "omitted_turn_ids": omitted,
                       "history_limit": MAX_HISTORY_TURNS, "max_bytes": MAX_CONTEXT_BYTES,
                       "notice": f"本轮带入{len(included)}轮历史问答，{len(omitted)}轮较早历史未带入；未调用模型摘要。"}
            packet = {"purpose": "case_qa", "system_prompt": SYSTEM_PROMPT + CONTEXT_RULE + ANSWER_RULE,
                      "view": {"question": request["message"], "evidence": deepcopy(value["case"]["evidence"]),
                               "dialogue_background": [{"turn_id": t["message_id"], "user": t["message"],
                                    "assistant": t["result"]["answer"], "source": "unverified_dialogue"} for t in selected],
                               "context": context}}
            size = len(canonical(packet).encode("utf-8"))
            if size <= MAX_CONTEXT_BYTES:
                return packet, context, size
            if not selected:
                raise SessionError("context_too_large")
            selected = selected[1:]
