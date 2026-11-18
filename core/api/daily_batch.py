"""Server-clocked ingestion, closure and read access to day-end risk batches."""
from typing import Literal
import re
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from graphrisk.graph.coverage_data import NewTransaction
from graphrisk.graph.coverage_service import CoverageArtifactError
from graphrisk.graph.service import GraphTargetNotCoveredError

router = APIRouter(prefix='/api/v1/risk-batches', tags=['daily-risk'])

_ACTIONS = {
    'new event day must follow the bound historical cutoff day': ('event_in_bound_history', '该事件属于已绑定历史时段，请核对历史截止或使用新时段交易。'),
    'event time cannot follow the server arrival second': ('event_not_arrived', '交易事件时间晚于服务端到达时间，请核对时钟和相对时间起点。'),
    'transaction ID conflicts with a different received payload': ('transaction_id_conflict', '该交易编号已绑定另一份内容，请核对原记录；重试必须使用完全相同的数据。'),
    'daily batch is not closed': ('batch_not_closed', '服务端水位尚未到达当日日终，请等待数据到齐后再封批。'),
    'daily batch is not sealed': ('batch_not_sealed', '该日尚未封批，请先提交数据并封批后查看评分。'),
    'seal the earlier nonempty relative day first': ('earlier_batch_open', '请先封存更早的非空批次，再封存本日。'),
    'daily batch must follow bound history and model selection cutoff': ('batch_before_history_cutoff', '请使用历史基线和模型选择截止之后的新交易，避免重复或提前评分。'),
}


def _rejection(error, default):
    message = str(error)
    prefix = 'daily source fields require values or explicit null: '
    if message.startswith(prefix):
        fields = message[len(prefix):].strip('[]').replace("'", '').split(',')
        fields = [value.strip() for value in fields if re.fullmatch(r'[A-Za-z0-9_]+', value.strip())]
        return {'code': 'missing_raw_fields', 'message': '请补齐以下源字段，确实缺失的字段请显式填 null。', 'fields': fields}
    code, action = _ACTIONS.get(message, (default, '输入与当前批次或模型合同不一致，请核对源字段、交易编号和封批状态。'))
    return {'code': code, 'message': action}


@router.get('')
def batches(request: Request):
    service = getattr(request.app.state, 'daily_risk_service', None)
    if service is None or not hasattr(service, 'list_batches'):
        raise HTTPException(status_code=503, detail='daily_ingestion_not_configured')
    try:
        return {'batches': service.list_batches()}
    except (ValueError, OSError):
        raise HTTPException(status_code=409, detail='daily_batch_registry_unavailable') from None


@router.post('/transactions')
def ingest(body: NewTransaction, request: Request):
    if not re.fullmatch(r'[0-9]{1,18}', body.transaction_id):
        raise HTTPException(status_code=422, detail='invalid_transaction_id')
    service = getattr(request.app.state, 'daily_risk_service', None)
    if service is None or not hasattr(service, 'ingest'):
        raise HTTPException(status_code=503, detail='daily_ingestion_not_configured')
    try:
        result = service.ingest(body)
        status = 202 if result.get('status') == 'quarantined' else 200 if result.get('status') == 'duplicate' else 201
        return JSONResponse(status_code=status, content=result)
    except MemoryError:
        raise HTTPException(status_code=503, detail='daily_risk_resources_unavailable') from None
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail='daily_batch_storage_unavailable') from None
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=409, detail=_rejection(error, 'daily_ingestion_rejected')) from None


@router.get('/late-arrivals')
def late_arrivals(request: Request):
    service = getattr(request.app.state, 'daily_risk_service', None)
    if service is None or not hasattr(service, 'quarantined'):
        raise HTTPException(status_code=503, detail='daily_ingestion_not_configured')
    try:
        return {'late_arrivals': service.quarantined()}
    except (ValueError, OSError, sqlite3.Error):
        raise HTTPException(status_code=503, detail='daily_batch_storage_unavailable') from None


@router.post('/{relative_day}/seal')
def seal(relative_day: int, request: Request):
    service = getattr(request.app.state, 'daily_risk_service', None)
    if service is None or not hasattr(service, 'seal'):
        raise HTTPException(status_code=503, detail='daily_ingestion_not_configured')
    try:
        return service.seal(relative_day)
    except MemoryError:
        raise HTTPException(status_code=503, detail='daily_risk_resources_unavailable') from None
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail='daily_batch_storage_unavailable') from None
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=409, detail=_rejection(error, 'daily_batch_not_sealable')) from None


@router.get('/{relative_day}')
def daily_risks(relative_day: int, request: Request):
    service = getattr(request.app.state, 'daily_risk_service', None)
    if service is None:
        raise HTTPException(status_code=503, detail='daily_risk_not_configured')
    try:
        return service.get_day(relative_day)
    except MemoryError:
        raise HTTPException(status_code=503, detail='daily_risk_resources_unavailable') from None
    except (ValueError, OSError):
        raise HTTPException(status_code=409, detail='daily_risk_unavailable') from None


@router.get('/{relative_day}/transactions/{transaction_id}')
def transaction_risk(relative_day: int, transaction_id: str, request: Request,
                     model: Literal['daily', 'x3'] = 'daily'):
    service = getattr(request.app.state, 'daily_risk_service', None)
    if service is None:
        raise HTTPException(status_code=503, detail='daily_risk_not_configured')
    if not transaction_id.isascii() or not transaction_id.isdigit() or len(transaction_id) > 18:
        raise HTTPException(status_code=422, detail='invalid_transaction_id')
    try:
        return service.get_transaction(relative_day, transaction_id, model)
    except MemoryError:
        raise HTTPException(status_code=503, detail='daily_risk_resources_unavailable') from None
    except (ValueError, OSError, LookupError, CoverageArtifactError, GraphTargetNotCoveredError):
        raise HTTPException(status_code=409, detail='model_score_unavailable') from None
