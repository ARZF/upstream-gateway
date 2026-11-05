import logging
from time import perf_counter

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.core.logging import log_event
from app.db.database import Database
from app.db.models import RequestRecord
from app.schemas.request import ErrorDetails, ProcessRequest, ProcessResponse
from app.services.provider_service import ProviderService

router = APIRouter()


def get_service(request: Request) -> ProviderService:
    return request.app.state.provider_service


def get_database(request: Request) -> Database:
    return request.app.state.database


@router.get("/health", tags=["Health"])
async def health() -> dict[str, str]:
    """Liveness only; does not check dependencies."""
    return {"status": "ok"}


@router.get(
    "/ready", tags=["Health"], response_model=dict[str, str],
    responses={503: {
        "description": "Required dependency unavailable or not initialized",
    }},
)
async def ready(request: Request) -> JSONResponse:
    """Check completed startup, the local HTTP client, and database access."""
    is_ready = False
    if request.app.state.ready and not request.app.state.http_client.is_closed:
        try:
            await run_in_threadpool(get_database(request).check_connection)
        except SQLAlchemyError:
            log_event(
                "readiness_error", level=logging.WARNING,
                request_id=request.state.request_id,
            )
        else:
            is_ready = True
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={"status": "ready" if is_ready else "not_ready"},
    )


@router.post(
    "/api/v1/process", response_model=ProcessResponse,
    responses={
        413: {"model": ProcessResponse},
        500: {"model": ProcessResponse},
        502: {"model": ProcessResponse}, 503: {"model": ProcessResponse},
        504: {"model": ProcessResponse},
    },
)
async def process(
    body: ProcessRequest, request: Request,
    service: ProviderService = Depends(get_service),
    database: Database = Depends(get_database),
) -> JSONResponse:
    started = perf_counter()
    request_id = request.state.request_id
    pending = RequestRecord(
        request_id=request_id,
        request_payload=body.payload,
        processing_status="pending",
    )
    try:
        await run_in_threadpool(database.create_pending, pending)
    except SQLAlchemyError:
        log_event(
            "persistence_error", level=logging.ERROR, request_id=request_id
        )
        response = ProcessResponse(
            request_id=request_id, provider=None, status="failed",
            latency_ms=(perf_counter() - started) * 1000,
            error=ErrorDetails(
                code="persistence_unavailable",
                message=("The request could not be recorded; "
                         "the provider was not called"),
            ),
        )
        return JSONResponse(
            status_code=503, content=response.model_dump(mode="json")
        )

    # shortcut: cancelled or crashed requests leave a pending row, add a reaper or reconciliation job when these need automatic resolution.
    outcome = await service.process(body.payload, request_id)
    response = outcome.response
    provider_payload = response.result
    identifiers = (
        provider_payload if isinstance(provider_payload, dict) else {}
    )
    values = {
        "selected_provider": response.provider,
        "provider_response": provider_payload,
        "provider_transaction_id": (
            identifiers.get("id")
            if isinstance(identifiers.get("id"), str) else None
        ),
        "provider_request_id": (
            identifiers.get("request_id")
            if isinstance(identifiers.get("request_id"), str) else None
        ),
        "http_status_code": outcome.http_status,
        "upstream_http_status_code": outcome.upstream_status,
        "processing_status": response.status,
        "total_latency_ms": (perf_counter() - started) * 1000,
        "error_details": (
            response.error.model_dump() if response.error else None
        ),
        "attempt_count": outcome.attempt_count,
    }
    try:
        await run_in_threadpool(
            database.complete, request_id, values, outcome.attempts
        )
    except SQLAlchemyError:
        log_event(
            "persistence_error", level=logging.ERROR, request_id=request_id
        )
        response.status = "failed"
        response.error = ErrorDetails(
            code="persistence_error",
            message=("Metadata could not be stored; "
                     "the provider may have processed the request"),
        )
        response.latency_ms = (perf_counter() - started) * 1000
        return JSONResponse(
            status_code=500, content=response.model_dump(mode="json")
        )
    response.latency_ms = (perf_counter() - started) * 1000
    return JSONResponse(
        status_code=outcome.http_status,
        content=response.model_dump(mode="json"),
    )
