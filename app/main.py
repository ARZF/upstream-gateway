import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.api.routes import router
from app.clients.provider_client import ProviderClient
from app.core.config import Settings
from app.core.logging import configure_logging, log_event
from app.db.database import Database
from app.services.provider_service import ProviderService
from app.services.smart_router import SmartRouter


def create_app(
    settings: Settings | None = None,
    *, transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    config = settings if settings is not None else Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        database = Database(config.database_url)
        try:
            await run_in_threadpool(database.initialize)
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=config.connect_timeout_seconds,
                    read=config.read_timeout_seconds,
                    write=config.write_timeout_seconds,
                    pool=config.pool_timeout_seconds,
                ),
                limits=httpx.Limits(
                    max_connections=config.http_max_connections,
                    max_keepalive_connections=(
                        config.http_max_keepalive_connections
                    ),
                ),
                transport=transport, follow_redirects=False, trust_env=False,
            ) as client:
                smart_router = SmartRouter(config)
                application.state.database = database
                application.state.http_client = client
                application.state.smart_router = smart_router
                application.state.provider_service = ProviderService(
                    smart_router, ProviderClient(client)
                )
                yield
        finally:
            await run_in_threadpool(database.close)

    application = FastAPI(title="Upstream Gateway", lifespan=lifespan)
    application.include_router(router)

    @application.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        log_event(
            "validation_error", level=logging.WARNING,
            request_id=request.state.request_id,
        )
        # Raw invalid input may contain secrets or non-JSON floats.
        return JSONResponse(status_code=422, content={
            "detail": [
                {key: error[key] for key in ("loc", "msg", "type")}
                for error in exc.errors()
            ],
        })

    @application.middleware("http")
    async def request_logging(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = str(uuid4())
        request.state.request_id = request_id
        started = perf_counter()
        log_event(
            "incoming_request", request_id=request_id,
            method=request.method, path=request.url.path,
        )
        try:
            response = await call_next(request)
        except Exception as exc:
            log_event(
                "request_error", level=logging.ERROR, request_id=request_id,
                error_type=type(exc).__name__,
            )
            response = JSONResponse(status_code=500, content={
                "request_id": request_id,
                "error": {
                    "code": "internal_error",
                    "message": "Internal server error",
                },
            })
        response.headers["X-Request-ID"] = request_id
        log_event(
            "request_end", request_id=request_id,
            status_code=response.status_code,
            latency_ms=round((perf_counter() - started) * 1000, 3),
        )
        return response

    return application


app = create_app()
