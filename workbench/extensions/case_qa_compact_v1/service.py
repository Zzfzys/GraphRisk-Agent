"""Compact first-verdict QA engine; frozen execution controls stay unchanged."""
from copy import deepcopy
from pathlib import Path
import re
import sqlite3

from extensions.case_qa_live_v1.service import LiveCaseQAService, MODE
from extensions.case_qa_v1.behavior import PreflightStop, SYSTEM_PROMPT, validate_question
from extensions.case_qa_v1.evidence_contract import raw_receipt
from extensions.case_qa_v1.sessions import (
    CaseQAService, SQLiteConversationStore, SessionError, canonical, digest,
    CONTEXT_RULE, MAX_CONTEXT_BYTES, MAX_HISTORY_TURNS, MAX_SESSION_MESSAGES,
    NOTICES, _identifier, _public_audit, _state_block,
)
from .contract import ANSWER_RULE, CHECK_VERSION, check_answer

REVISION = "case-qa-compact-live-v1"
ANSWER_REVISION = PROMPT_REVISION = "case-qa-compact-v1"
OFFLINE_MODE = "offline_simulation"


def _stamp(value):
    value["adapter_revision"] = REVISION
    value["answer_revision"] = ANSWER_REVISION
    value["prompt_revision"] = PROMPT_REVISION
    return value


def _result(value, mode):
    value = _stamp(deepcopy(value))
    value["mode"] = mode
    if mode == MODE:
        value["source"] = {"mock_model":"deepseek", "program_and_mock_model":"program_and_deepseek"}.get(value.get("source"), value.get("source"))
        payload = value.get("answer_payload")
        if type(payload) is dict:
            payload["source"] = {"program_projection_and_mock_model":"program_projection_and_deepseek"}.get(payload.get("source"), payload.get("source"))
    return value


def _document(value, mode):
    value = _stamp(deepcopy(value))
    value["execution_mode"] = mode
    for turn in value.get("turns", []):
        turn["result"] = _result(turn["result"], mode)
    return value


class CompactConversationStore(SQLiteConversationStore):
    """Independent marker; never open or enrich a previous contract's results."""
    STORE_MARKER = 0x435131

    def __init__(self, path):
        path = Path(path)
        if not path.is_absolute():
            raise SessionError("session_store_invalid")
        if path.exists():
            try:
                with sqlite3.connect(path.as_uri()+"?mode=ro", uri=True) as db:
                    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if (db.execute("PRAGMA user_version").fetchone()[0] != self.STORE_MARKER
                            or db.execute("PRAGMA application_id").fetchone()[0] != 1196507473
                            or tables != {"qa_conversations_v1"}):
                        raise SessionError("session_store_invalid")
            except sqlite3.Error:
                raise SessionError("session_store_invalid") from None
            self.path, self.execution_mode = path, None
            return
        super().__init__(path)
        with self.connect() as db:
            db.execute(f"PRAGMA user_version={self.STORE_MARKER}")
        self.execution_mode = None

    @staticmethod
    def _value(row):
        value = SQLiteConversationStore._value(row)
        if (value.get("adapter_revision") != REVISION or value.get("answer_revision") != ANSWER_REVISION
                or value.get("prompt_revision") != PROMPT_REVISION
                or value.get("execution_mode") not in {MODE, OFFLINE_MODE}):
            raise SessionError("session_store_invalid")
        return value

    def create(self, key, fingerprint, value):
        if self.execution_mode not in {MODE, OFFLINE_MODE}:
            raise SessionError("session_store_invalid")
        return super().create(key, fingerprint, _document(value, self.execution_mode))

    def mutate(self, identifier, fn):
        def update(value):
            changed, returned = fn(value)
            changed = _document(changed, self.execution_mode)
            if type(returned) is dict and "result" in returned:
                returned = deepcopy(returned)
                returned["result"] = _result(returned["result"], self.execution_mode)
            return changed, returned
        return super().mutate(identifier, update)


class CompactService(LiveCaseQAService):
    def __init__(self, store, resolve, check, *, budget=None, id_aliases=None, **kwargs):
        if not isinstance(store, CompactConversationStore):
            raise SessionError("session_store_invalid")
        self.mode = MODE if budget is not None else OFFLINE_MODE
        with store.connect() as db:
            for row in db.execute("SELECT content,content_sha,state,case_alias FROM qa_conversations_v1"):
                if store._value(row)["execution_mode"] != self.mode:
                    raise SessionError("session_store_invalid")
        store.execution_mode = self.mode
        # The live wrapper requires a budget; explicit offline fakes use the
        # same frozen base initialization without claiming live provenance.
        CaseQAService.__init__(self, store, resolve, check, budget=budget, **kwargs)
        self.id_aliases = dict(id_aliases or {})

    def _public(self, value):
        value = _document(CaseQAService._public(value), self.mode)
        value["mode"] = self.mode
        return value

    def get(self, identifier):
        return LiveCaseQAService.get(self, identifier) if self.mode == MODE else CaseQAService.get(self, identifier)

    def evidence(self, identifier):
        value = _stamp(CaseQAService.evidence(self, identifier))
        value["mode"] = self.mode
        return value

    def raw(self, identifier, message_id):
        value = _stamp(CaseQAService.raw(self, identifier, message_id))
        value.update(mode=self.mode, source="unreviewed_deepseek_output" if self.mode == MODE else "unreviewed_mock_output")
        return value

    def _packet(self, value, request):
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

    def follow(self, identifier, request):
        if type(request) is dict and type(request.get("message")) is str:
            error = validate_question({key:request.get(key) for key in ("message", "evidence_version")})
            if error:
                raise SessionError(error)
            request = deepcopy(request)
            for transaction, alias in self.id_aliases.items():
                request["message"] = re.sub(r"(?<![0-9])"+re.escape(transaction)+r"(?![0-9])", alias, request["message"])
        value = _stamp(self._follow(identifier, request))
        value["mode"] = self.mode
        value["turn"]["result"] = _result(value["turn"]["result"], self.mode)
        return value

    # Static copy of the frozen engine's claim/preserve/finish path: only the
    # first verdict call, check revision and provenance mode differ below.
    def _follow(self, identifier, request):
        _identifier(identifier, r"[0-9a-f]{32}")
        if type(request) is not dict or set(request) != {"message_id", "parent_turn_id", "evidence_version", "message"}:
            raise SessionError("invalid_request")
        _identifier(request["message_id"], r"[0-9a-f]{32}")
        if request["parent_turn_id"] is not None:
            _identifier(request["parent_turn_id"], r"[0-9a-f]{32}")
        error = validate_question({k: request[k] for k in ("message", "evidence_version")})
        if error:
            raise SessionError(error)
        request = deepcopy(request)
        budget_claimed = []
        def claim(value):
            sha = digest([identifier, value["case"], request])
            previous = next((t for t in value["turns"] if t["message_id"] == request["message_id"]), None)
            if previous:
                if previous["request_sha"] != sha:
                    raise SessionError("idempotency_conflict")
                return value, (False, previous)
            self._current(value)
            if not value.get("qa_enabled", True):
                raise SessionError("qa_disabled")
            if value["state"] == "retention_incomplete":
                raise SessionError("raw_retention_incomplete")
            if value["state"] == "response_unknown":
                raise SessionError("response_unknown_paused")
            if value["state"] == "in_flight":
                raise SessionError("turn_in_progress")
            if request["evidence_version"] != value["evidence_version"]:
                raise SessionError("evidence_changed")
            if request["parent_turn_id"] != (value["turns"][-1]["message_id"] if value["turns"] else None):
                raise SessionError("parent_turn_changed")
            if len(value["turns"]) >= MAX_SESSION_MESSAGES:
                raise SessionError("session_message_limit")
            if self.budget is None and not callable(self.mock_provider):
                raise SessionError("provider_unconfigured")
            packet, context, size = self._packet(value, request)
            try:
                self.preflight()
            except PreflightStop as error:
                raise SessionError(error.code) from None
            except Exception:
                raise SessionError("preflight_failed") from None
            turn = {**request, "request_sha": sha, "context": context, "context_bytes": size,
                    "result": {"status": "in_flight", "source": "system", "answer": None,
                               "model_attempts": None, "attempt_slots_claimed": 1,
                               "notice": NOTICES["turn_in_progress"]}}
            ticket = None
            if self.budget is not None:
                # The conversation write lock serializes this precheck with all
                # local claims, so old unknown slots cannot waste a new hold.
                reason = _state_block(self.store.states())
                if reason:
                    raise SessionError(reason)
                try:
                    ticket = self.budget.reserve(value, request, packet)
                except Exception as error:
                    code = str(error)
                    raise SessionError(code if code in NOTICES else "budget_unavailable") from None
                budget_claimed.append(ticket)
                turn["result"]["budget_audit"] = _public_audit(ticket)
            value["turns"].append(turn)
            value["state"] = "in_flight"
            return value, (True, (turn, packet, ticket))
        try:
            claimed, payload = self.store.mutate(identifier, claim)
        except Exception:
            if budget_claimed:
                # Two SQLite databases cannot commit atomically. Keep the charged
                # reservation on any checkpoint failure; it blocks future sends.
                raise SessionError("checkpoint_recovery_required") from None
            raise
        if not claimed:
            return {"mode": self.mode, "replayed": True, "turn": self._public_turn(payload)}
        turn, packet, ticket = payload
        budget_audit = _public_audit(ticket) if ticket is not None else None
        budget_error = None
        attempts = 1
        # The budget owns one authorized transport request and its receipt.
        try:
            if self.budget is None:
                raw = self.mock_provider(deepcopy(packet))
            else:
                reply = self.budget.generate(ticket, deepcopy(packet))
                raw, budget_error = reply["raw"], reply["error_code"]
                budget_audit = _public_audit(reply["audit"])
                attempts = budget_audit.get("transport_attempts")
        except Exception:
            raw, budget_error = None, "provider_response_unknown"
            if self.budget is not None:
                attempts = None
        if raw is None and budget_error:
            result = {"status":"response_unknown", "answer":None, "answer_payload":None, "source":"system",
                      "issues":[budget_error or "provider_response_unknown"],
                      "notice":"请求结果尚未确认，保留占位和费用预约；不会自动重发。"}
        else:
            receipt = raw_receipt(raw)
            def preserve(value):
                saved = next(t for t in value["turns"] if t["message_id"] == turn["message_id"])
                saved["result"]["raw_response"] = receipt
                return value, None
            try:
                self.store.mutate(identifier, preserve)
            except Exception:
                raise SessionError("checkpoint_recovery_required") from None
            if budget_error:
                unknown = budget_audit and budget_audit.get("state") in {"reserved","unknown"}
                result = {"status":"response_unknown" if unknown else "review_required",
                          "answer":None,"answer_payload":None,"source":"system","issues":[budget_error],
                          "notice":"原始响应已保存，但使用量、模型身份或输出限制未通过核验；请查看原记录，不会自动重试。"}
            else:
                try:
                    result = check_answer(raw, packet["view"]["evidence"])
                except Exception:
                    result = {"status":"review_required", "answer":None, "answer_payload":None,
                              "issues":["checker_failed"], "notice":"回答检查未完成，原文已单独保存，请人工核验；未重试。"}
            if not receipt["complete"]:
                result = {"status":"review_required", "answer":None, "answer_payload":None,
                          "issues":["raw_retention_incomplete"], "notice":NOTICES["raw_retention_incomplete"]}
            result["raw_response"] = receipt
            result["source"] = (("program_and_mock_model" if result["answer_payload"]["facts"] else "mock_model")
                                if result["status"] == "answered" else "system")
        result.update({"mode":self.mode, "operation":"case_qa", "check_version":CHECK_VERSION,
                       "model_attempts":attempts, "attempt_slots_claimed":1, "tool_requests":0, "business_action_executed":False})
        if budget_audit is not None:
            result["budget_audit"] = budget_audit
        def finish(value):
            saved = next(t for t in value["turns"] if t["message_id"] == turn["message_id"])
            saved["result"] = result
            value["state"] = ("retention_incomplete" if "raw_retention_incomplete" in result["issues"] else
                              "response_unknown" if result["status"] == "response_unknown" else "ready")
            return value, saved
        try:
            saved = self.store.mutate(identifier, finish)
        except Exception:
            raise SessionError("checkpoint_recovery_required") from None
        return {"mode": self.mode, "replayed": False, "turn": self._public_turn(saved)}
