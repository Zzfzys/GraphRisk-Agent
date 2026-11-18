"""Conversation API. No raw text is logged, echoed in errors or stored."""
import re
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from graphrisk.serving.conversations import CreateConversation, FollowUp, ConversationError

router = APIRouter(prefix='/api/v1/conversations', tags=['conversations'])

MESSAGES = {
    'daily_narrative_configuration_invalid': '日终报告配置不符合已绑定的 v12 单次首稿合同，请核对服务端配置后再生成。',
    'batch_selection_mismatch': '该交易与所选封批版本不一致，请刷新批次名单并重新建立会话。',
    'daily_batch_case_not_registered': '该交易尚未出现在可核验的封批结果中，请先完成封批。',
    'clarification_required': '请明确选择：调查风险、解释依据与反证、政策前提、继续核查。混合指令或改变权限的要求不会执行。',
    'transaction_required': '请提供交易编号，再开始调查。',
    'case_switch_requires_new_conversation': '请为另一笔交易新建会话。',
    'turn_in_progress_recovery_required': '本轮尚未完成。请先检查原调用和检查点，重复提交不会重新调用模型。',
    'conversation_service_unconfigured': '自主调查会话服务尚未配置。',
    'conversation_expired': '会话已过期，请新建会话并重新绑定证据。',
}


def invoke(request, fn):
    try:
        service = getattr(request.app.state, 'conversation_service', None)
        if service is None:
            raise ConversationError('conversation_service_unconfigured')
        return fn(service)
    except ConversationError as error:
        code = str(error)
        request.state.error_code = code
        status = (503 if code == 'conversation_service_unconfigured' else
                  404 if code == 'conversation_not_found' else
                  410 if code == 'conversation_expired' else
                  422 if code in ('clarification_required','transaction_required','invalid_idempotency_key','invalid_conversation_id') else
                  429 if code.endswith('_limit') else 409)
        return JSONResponse(status_code=status, content={'error_code': code,
            'message': MESSAGES.get(code, '请求与当前会话、证据版本或预算不一致；请读取会话状态后再操作。')})


def key(value):
    if value is None or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', value):
        raise ConversationError('invalid_idempotency_key')
    return value


def identifier(value):
    if not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ConversationError('invalid_conversation_id')
    return value


@router.post('', status_code=201)
def create(body: CreateConversation, request: Request, idempotency_key: str | None = Header(default=None)):
    return invoke(request, lambda service: service.create(body, key(idempotency_key)))


@router.get('/{conversation_id}')
def get(conversation_id: str, request: Request):
    return invoke(request, lambda service: service.get(identifier(conversation_id)))


@router.post('/{conversation_id}/turns')
def follow(conversation_id: str, body: FollowUp, request: Request, idempotency_key: str | None = Header(default=None)):
    return invoke(request, lambda service: service.follow(identifier(conversation_id), body, key(idempotency_key)))
