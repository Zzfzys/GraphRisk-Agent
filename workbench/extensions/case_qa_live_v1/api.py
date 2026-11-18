"""Independent live QA API; request authorization is enforced by the budget."""
import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from extensions.case_qa_v1.behavior import ERRORS
from extensions.case_qa_v1.sessions import NOTICES, SessionError

router = APIRouter(prefix="/api/v1/case-qa/conversations", tags=["case-qa-live"])


def error_response(error):
    status = (404 if error.code in {"conversation_not_found", "answer_not_found"} else
              410 if error.code == "conversation_expired" else
              422 if error.code in {"invalid_request", "invalid_question", "empty_question", "question_too_long"} else
              503 if error.code in {"provider_unconfigured", "session_store_invalid", "checkpoint_recovery_required", "budget_unavailable"} else 409)
    return JSONResponse(status_code=status, content={"source": "system", "mode": "live_case_qa",
        "error_code": error.code, "message": NOTICES.get(error.code, ERRORS.get(error.code, "发送前检查未通过，本轮未发送。")),
        "new_model_attempts": 0 if error.code != "checkpoint_recovery_required" else None})


def invoke(request, fn):
    try:
        service = getattr(request.app.state, "case_qa_service", None)
        if service is None:
            raise SessionError("provider_unconfigured")
        return fn(service)
    except SessionError as error:
        return error_response(error)


async def post(request, action):
    # Parse here so FastAPI validation cannot echo rejected private input.
    raw = bytearray()
    try:
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 32768:
                raise ValueError
            raw.extend(chunk)
        body = json.loads(raw.decode("utf-8"))
        if type(body) is not dict:
            raise ValueError
    except (ValueError, UnicodeError, RecursionError):
        return error_response(SessionError("invalid_request"))
    return await asyncio.to_thread(invoke, request, lambda service: action(service, body))


@router.post("", status_code=201)
async def create(request: Request):
    return await post(request, lambda service, body: service.create(body, request.headers.get("Idempotency-Key")))


@router.get("/{conversation_id}")
def get(conversation_id: str, request: Request):
    return invoke(request, lambda service: service.get(conversation_id))


@router.post("/{conversation_id}/turns")
async def follow(conversation_id: str, request: Request):
    return await post(request, lambda service, body: service.follow(conversation_id, body))


@router.get("/{conversation_id}/turns/{message_id}/raw")
def raw(conversation_id: str, message_id: str, request: Request):
    return invoke(request, lambda service: service.raw(conversation_id, message_id))


@router.get("/created/{creation_key}")
def created(creation_key: str, request: Request):
    return invoke(request, lambda service: service.created(creation_key))


@router.get("/{conversation_id}/evidence")
def evidence(conversation_id: str, request: Request):
    return invoke(request, lambda service: service.evidence(conversation_id))


@router.put("/{conversation_id}/control")
async def control(conversation_id: str, request: Request):
    return await post(request, lambda service, body: service.control(conversation_id, body))
