"""Versioned, evidence-limited HTTP surface for Stage 06 investigations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from graphrisk.serving.request_trace import trace_event, trace_run
from graphrisk.graph.coverage_data import NewTransaction
from graphrisk.investigation.contracts import TransactionToolRequest

from graphrisk.serving.investigation_service import ReadinessStatus, ServiceError, Stage06Service
from graphrisk.serving.investigation_views import (
    ApiError,
    ApprovalSubmitRequest,
    ApprovalView,
    InvestigationCreateRequest,
    InvestigationView,
    project_approval,
    project_investigation,
)


router = APIRouter(prefix="/api/v1", tags=["investigations"])

_STATUS_ERRORS = {
    "invalid_input": (422, "invalid_request", "The investigation request is invalid."),
    "not_found": (404, "transaction_not_found", "The transaction was not found."),
    "invalid_plan": (502, "invalid_plan", "The investigation plan was rejected."),
    "retrieval_error": (503, "policy_retrieval_unavailable", "Policy retrieval is unavailable."),
    "generation_error": (503, "generation_unavailable", "Report generation is unavailable."),
    "upstream_error": (503, "investigation_unavailable", "An investigation dependency is unavailable."),
    "deadline_exceeded": (504, "deadline_exceeded", "The investigation exceeded its time limit."),
    "budget_exhausted": (429, "budget_exhausted", "The investigation budget is exhausted."),
}

_SERVICE_ERRORS = {
    "dataset_source_mismatch": (422, "The requested dataset source is not configured."),
    "run_capacity_exceeded": (429, "The local investigation capacity is full."),
    "run_expired": (410, "The investigation record has expired."),
    "run_instance_mismatch": (410, "The investigation belongs to a previous service instance."),
    "run_not_found": (404, "The investigation record was not found."),
    "approval_not_available": (409, "This investigation has no approvable report."),
    "approval_binding_mismatch": (409, "The approval does not match this investigation."),
    "approval_run_mismatch": (409, "The approval run ID does not match the URL."),
    "approval_conflict": (409, "A different decision has already finalized this approval."),
    "approval_expired": (410, "The approval window has expired."),
    "report_hash_mismatch": (409, "The frozen report no longer matches its approval."),
    "service_stopped": (503, "The investigation service is stopped."),
    "service_unconfigured": (503, "The investigation service is not configured."),
    "invalid_request": (422, "The investigation request is invalid."),
    "idempotency_conflict": (409, "The idempotency key belongs to a different request."),
    "persistence_unavailable": (503, "Persistent investigation state is unavailable."),
}

_ERROR_RESPONSES = {
    404: {"model": ApiError, "description": "Transaction or run not found"},
    409: {"model": ApiError, "description": "Approval unavailable, binding mismatch, or finalized conflict"},
    410: {"model": ApiError, "description": "Run expired or created by a previous instance"},
    422: {"model": ApiError, "description": "Invalid request or data source"},
    429: {"model": ApiError, "description": "Capacity or budget stop"},
    500: {"model": ApiError, "description": "Internal evidence binding failure"},
    502: {"model": ApiError, "description": "Invalid bounded plan"},
    503: {"model": ApiError, "description": "Dependency unavailable"},
    504: {"model": ApiError, "description": "Total deadline exceeded"},
}


def _service(request: Request) -> Stage06Service:
    service = getattr(request.app.state, "stage06_service", None)
    if service is None:
        raise ServiceError("service_unconfigured")
    return service


def _error(http_status: int, code: str, message: str, *, run_id: str | None = None,
           status: str | None = None, request: Request | None = None) -> JSONResponse:
    if request is not None:
        request.state.error_code = code
    body = ApiError(error_code=code, message=message, run_id=run_id, status=status)
    return JSONResponse(status_code=http_status, content=body.model_dump(mode="json"))


def _service_error(error: ServiceError, request: Request | None = None) -> JSONResponse:
    if request is not None:
        request.state.error_code = error.code
    http_status, message = _SERVICE_ERRORS.get(
        error.code, (500, "The investigation failed an internal evidence check."))
    return _error(http_status, error.code, message)


def _public_record(record, *, created: bool, request: Request):
    request.state.run_id = record.run_id
    request.state.model_version = (record.versions.generation_model_revision or record.versions.generation_model_id)
    request.state.index_version = record.versions.policy_index_version
    if record.status in _STATUS_ERRORS:
        http_status, code, message = _STATUS_ERRORS[record.status]
        request.state.error_code = code
        return _error(http_status, code, message, run_id=record.run_id, status=record.status)
    try:
        view = project_investigation(record)
    except (ValueError, TypeError):
        request.state.error_code = "evidence_projection_failed"
        return _error(500, "evidence_projection_failed", "The report failed an evidence check.")
    return JSONResponse(status_code=201 if created else 200, content=view.model_dump(mode="json"))


@router.get("/health", summary="Process liveness")
def health(request: Request) -> dict[str, object]:
    return {"status": "ok", "investigation_service_enabled": hasattr(request.app.state, "stage06_service")}


@router.get("/ready", response_model=ReadinessStatus,
            responses={503: {"model": ReadinessStatus, "description": "A required dependency is unavailable"}},
            summary="Investigation dependency readiness")
def ready(request: Request):
    service = getattr(request.app.state, "stage06_service", None)
    if service is None:
        result = ReadinessStatus(status="not_ready", components={
            name: False for name in ("data", "risk_model", "graph", "policy_index", "ollama")})
    else:
        try:
            result = service.readiness()
        except ServiceError:
            result = ReadinessStatus(status="not_ready", components={
                name: False for name in ("data", "risk_model", "graph", "policy_index", "ollama")})
    return JSONResponse(status_code=200 if result.status == "ready" else 503,
                        content=result.model_dump(mode="json"))


def _coverage_service(service):
    investigation=getattr(getattr(service,'runner',None),'investigation',None)
    graph=getattr(investigation,'graph_service',None)
    if graph is None or not hasattr(graph,'coverage') or not hasattr(graph,'prospective'):
        return None
    return graph


@router.get('/coverage/{transaction_id}', summary='Inspect independent historical scoring coverage without generation')
def historical_coverage(transaction_id: str, http_request: Request, service: Stage06Service=Depends(_service)):
    from pydantic import ValidationError
    try:
        key=TransactionToolRequest(transaction_id=transaction_id,dataset_source='ieee_cis').transaction_id
    except ValidationError:
        return _error(422,'invalid_request','Provide a valid transaction ID.',request=http_request)
    graph=_coverage_service(service)
    if graph is None:return _error(503,'coverage_profile_unconfigured','Select the explicit historical coverage profile.',request=http_request)
    result=graph.coverage(key)
    return JSONResponse(status_code=503 if result['status']=='artifact_unavailable' else 200,content=result)


@router.post('/predictions/prospective', summary='Read-only frozen CPU inference for one new event; no Agent or approval')
def prospective_prediction(event: NewTransaction, http_request: Request, service: Stage06Service=Depends(_service)):
    graph=_coverage_service(service)
    if graph is None:return _error(503,'coverage_profile_unconfigured','Select the explicit historical coverage profile.',request=http_request)
    try:
        return graph.prospective(event.model_dump())
    except BlockingIOError:
        return _error(429,'inference_busy','One new-event prediction is already running.',request=http_request)
    except MemoryError:
        return _error(503,'inference_resource_stop','The inference resource floor was reached; retry after releasing memory.',request=http_request)
    except TimeoutError:
        return _error(504,'inference_timeout','The new-event prediction exceeded its deadline.',request=http_request)
    except ValueError:
        return _error(422,'invalid_new_event','The event fields, time, or frozen feature contract are invalid.',request=http_request)
    except Exception:
        return _error(503,'inference_unavailable','Frozen model inference is unavailable; no prediction was issued.',request=http_request)



@router.post("/investigations", response_model=InvestigationView, status_code=201,
             responses={201: {"model": InvestigationView,
                              "description": "Created: verified, partial_evidence, or insufficient_evidence"},
                        **_ERROR_RESPONSES},
             summary="Create a bounded fraud investigation")
def create_investigation(request: InvestigationCreateRequest, http_request: Request,
                         service: Stage06Service = Depends(_service),
                         idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    try:
        graph=_coverage_service(service)
        if graph is not None:
            coverage=graph.coverage(request.transaction_id)
            if coverage['status']=='out_of_coverage':
                return _error(409,'prediction_out_of_coverage','This transaction has no registered independent score; inspect its coverage first.',request=http_request)
            if coverage['status']=='artifact_unavailable':
                return _error(503,'coverage_artifact_unavailable','Frozen coverage artifacts are unavailable.',request=http_request)
        if idempotency_key is not None and getattr(service, "storage_mode", "memory") != "postgres":
            raise ServiceError("invalid_request")
        kwargs = {"idempotency_key": idempotency_key} if idempotency_key is not None else {}
        record = service.run(request, request_id=http_request.state.request_id, **kwargs)
        trace_run(record, http_request.state.request_id)
    except ServiceError as error:
        return _service_error(error, http_request)
    except Exception:
        http_request.state.error_code = "investigation_internal_error"
        return _error(500, "investigation_internal_error", "The investigation could not be completed.")
    return _public_record(record, created=not record.idempotent_replay, request=http_request)


@router.get("/investigations/{run_id}", response_model=InvestigationView,
            responses={200: {"model": InvestigationView,
                             "description": "Read an existing verified, partial, or insufficient run"},
                       **_ERROR_RESPONSES},
            summary="Read an existing investigation without rerunning it")
def get_investigation(run_id: str, http_request: Request,
                      service: Stage06Service = Depends(_service)):
    http_request.state.run_id = run_id
    try:
        record = service.get(run_id, request_id=http_request.state.request_id)
    except ServiceError as error:
        return _service_error(error, http_request)
    except Exception:
        http_request.state.error_code = "investigation_internal_error"
        return _error(500, "investigation_internal_error", "The investigation could not be read.")
    return _public_record(record, created=False, request=http_request)


@router.get("/investigations/{run_id}/approval", response_model=ApprovalView,
            responses=_ERROR_RESPONSES, summary="Read the current human approval state")
def get_approval(run_id: str, http_request: Request,
                 service: Stage06Service = Depends(_service)):
    http_request.state.run_id = run_id
    try:
        record = service.get(run_id, request_id=http_request.state.request_id)
        approval = project_approval(record)
        http_request.state.model_version = (record.versions.generation_model_revision or record.versions.generation_model_id)
        http_request.state.index_version = record.versions.policy_index_version
        if approval is None:
            raise ServiceError("approval_not_available")
        return approval
    except ServiceError as error:
        return _service_error(error, http_request)
    except Exception:
        http_request.state.error_code = "approval_internal_error"
        return _error(500, "approval_internal_error", "The approval could not be read.")


@router.post("/investigations/{run_id}/approval", response_model=ApprovalView,
             responses=_ERROR_RESPONSES, summary="Record a human approval or rejection; reviewer identity is not authenticated")
def decide_approval(run_id: str, request: ApprovalSubmitRequest, http_request: Request,
                    service: Stage06Service = Depends(_service)):
    http_request.state.run_id = run_id
    try:
        if request.run_id != run_id:
            raise ServiceError("approval_run_mismatch")
        record = service.decide(run_id, request, request_id=http_request.state.request_id)
        approval = project_approval(record)
        if approval is None:
            raise ServiceError("approval_checkpoint_failed")
        http_request.state.model_version = (record.versions.generation_model_revision or record.versions.generation_model_id)
        http_request.state.index_version = record.versions.policy_index_version
        trace_event(request_id=http_request.state.request_id, phase="approval",
                    tool="submit_decision", run_id=run_id, status=approval.status,
                    model_version=(record.versions.generation_model_revision or record.versions.generation_model_id),
                    index_version=record.versions.policy_index_version)
        return approval
    except ServiceError as error:
        trace_event(request_id=http_request.state.request_id, phase="approval",
                    tool="submit_decision", run_id=run_id, status="error",
                    error_code=error.code)
        return _service_error(error, http_request)
    except Exception:
        http_request.state.error_code = "approval_internal_error"
        return _error(500, "approval_internal_error", "The approval could not be recorded.")
