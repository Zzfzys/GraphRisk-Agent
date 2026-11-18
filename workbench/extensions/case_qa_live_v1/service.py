"""Production provenance wrapper over the frozen, tested QA session engine."""
from copy import deepcopy
import re
from extensions.case_qa_v1.behavior import validate_question

from extensions.case_qa_v1.sessions import CaseQAService,SQLiteConversationStore,SessionError

MODE="live_case_qa"


def result_provenance(result):
    result=deepcopy(result)
    result["mode"]=MODE
    result["source"]={"mock_model":"deepseek","program_and_mock_model":"program_and_deepseek"}.get(result.get("source"),result.get("source"))
    return result


def live_document(value):
    value=deepcopy(value)
    value["execution_mode"]=MODE
    for turn in value.get("turns",[]):
        turn["result"]=result_provenance(turn["result"])
    return value


class LiveConversationStore(SQLiteConversationStore):
    """Dedicated database; refuse accidentally reading old mock conversations."""
    @staticmethod
    def _value(row):
        value=SQLiteConversationStore._value(row)
        if value.get("execution_mode")!=MODE:
            raise SessionError("session_store_invalid")
        return value

    def create(self,key,fingerprint,value):
        return super().create(key,fingerprint,live_document(value))

    def mutate(self,identifier,fn):
        def update(value):
            changed,result=fn(value)
            changed=live_document(changed)
            # The parent engine returns a saved turn on finish/replay; the
            # actual persisted result is normalized before its content hash.
            if type(result) is dict and "result" in result:
                result=deepcopy(result)
                result["result"]=result_provenance(result["result"])
            return changed,result
        return super().mutate(identifier,update)


class LiveCaseQAService(CaseQAService):
    def __init__(self,store,resolve,check,*,budget,id_aliases=None,**kwargs):
        if budget is None:
            raise SessionError("provider_unconfigured")
        super().__init__(store,resolve,check,budget=budget,**kwargs)
        self.id_aliases=dict(id_aliases or {})

    @staticmethod
    def _public(value):
        result=CaseQAService._public(value)
        result["mode"]=MODE
        for turn in result["turns"]:
            turn["result"]=result_provenance(turn["result"])
        return result

    def get(self,identifier):
        result=super().get(identifier)
        if result["usage"].get("budget_status") in {"unavailable","authorization_required"}:
            # Only known local attempt records; unavailable ledger fields stay
            # null. Never describe a real response as a mock or zero-cost call.
            result["usage"].update({"provider":"deepseek","scope":"live_case_qa",
                "mock_attempts_confirmed":0,
                "unresolved_slots":sum(t["result"].get("model_attempts") is None or t["result"]["status"]=="response_unknown"
                    for t in result["turns"]),
                "batch_unresolved_slots":None,
                "real_deepseek_attempts":sum(t["result"]["model_attempts"] for t in result["turns"]
                    if type(t["result"].get("model_attempts")) is int)})
        return result

    def follow(self,identifier,request):
        if type(request) is dict and type(request.get("message")) is str:
            error=validate_question({key:request.get(key) for key in ("message","evidence_version")})
            if error:
                raise SessionError(error)
            request=deepcopy(request)
            for transaction,alias in self.id_aliases.items():
                request["message"]=re.sub(r"(?<![0-9])"+re.escape(transaction)+r"(?![0-9])",alias,request["message"])
        result=super().follow(identifier,request)
        result["mode"]=MODE
        result["turn"]["result"]=result_provenance(result["turn"]["result"])
        return result

    def evidence(self,identifier):
        result=super().evidence(identifier)
        result["mode"]=MODE
        return result

    def raw(self,identifier,message_id):
        result=super().raw(identifier,message_id)
        result.update(mode=MODE,source="unreviewed_deepseek_output")
        return result
