"""Live quality adapter over the original request state machine and diagnostics.

The dedicated store keeps verdicts and raw bytes unchanged. Provenance touches
metadata only, in the same transaction as the inherited quality enrichment.
"""
from copy import deepcopy

from extensions.case_qa_live_v1.service import LiveCaseQAService, MODE, result_provenance
from extensions.case_qa_quality_v1.service import QualityConversationStore, QualityService, REVISION as ANSWER_REVISION
from extensions.case_qa_v1.sessions import SessionError

REVISION = "case-qa-live-quality-v3"


def _stamp(value):
    value["adapter_revision"] = REVISION
    value["answer_revision"] = ANSWER_REVISION
    return value


def _result(value):
    value = _stamp(result_provenance(value))
    payload = value.get("answer_payload")
    if type(payload) is dict:
        payload["source"] = {"program_projection_and_mock_model":"program_projection_and_deepseek"}.get(payload.get("source"),payload.get("source"))
    return value


def _document(value):
    value = _stamp(deepcopy(value))
    value["execution_mode"] = MODE
    for turn in value.get("turns",[]):
        turn["result"] = _result(turn["result"])
    return value


class LiveQualityConversationStore(QualityConversationStore):
    STORE_MARKER = 0x4C5133

    @staticmethod
    def _value(row):
        value = QualityConversationStore._value(row)
        if (value.get("execution_mode") != MODE or value.get("adapter_revision") != REVISION
                or value.get("answer_revision") != ANSWER_REVISION):
            raise SessionError("session_store_invalid")
        return value

    def create(self, key, fingerprint, value):
        return super().create(key, fingerprint, _document(value))

    def mutate(self, identifier, fn):
        def update(value):
            changed, returned = fn(value)
            # The inherited enrichment compares only status/issues/answer. These
            # bytes stay intact; metadata normalization cannot change its verdict.
            changed = _document(changed)
            if type(returned) is dict and "result" in returned:
                returned = deepcopy(returned)
                returned["result"] = _result(returned["result"])
            return changed, returned
        return super().mutate(identifier, update)


class LiveQualityService(LiveCaseQAService):
    def __init__(self, store, resolve, check, *, budget, id_aliases=None, **kwargs):
        if not isinstance(store, LiveQualityConversationStore):
            raise SessionError("session_store_invalid")
        super().__init__(store, resolve, check, budget=budget, id_aliases=id_aliases, **kwargs)

    def _packet(self, value, request):
        return QualityService._packet(self, value, request)

    @staticmethod
    def _public(value):
        value = _stamp(LiveCaseQAService._public(value))
        for turn in value["turns"]:
            turn["result"] = _result(turn["result"])
        return value

    def follow(self, identifier, request):
        value = _stamp(super().follow(identifier, request))
        value["turn"]["result"] = _result(value["turn"]["result"])
        return value

    def evidence(self, identifier):
        return _stamp(super().evidence(identifier))

    def raw(self, identifier, message_id):
        return _stamp(super().raw(identifier, message_id))
