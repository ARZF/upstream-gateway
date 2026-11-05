import asyncio
import logging
from dataclasses import dataclass
from time import perf_counter

from pydantic import JsonValue

from app.clients.provider_client import ProviderClient, ProviderError
from app.core.logging import log_event
from app.schemas.request import ErrorDetails, ProcessResponse
from app.services.smart_router import NoProviderAvailable, SmartRouter


@dataclass
class AttemptRecord:
    attempt_number: int
    provider: str
    upstream_status_code: int | None
    latency_ms: float
    success: bool
    error_code: str | None


@dataclass
class ProcessingResult:
    response: ProcessResponse
    http_status: int
    upstream_status: int | None
    attempt_count: int
    attempts: list[AttemptRecord]


class ProviderService:
    def __init__(self, router: SmartRouter, client: ProviderClient) -> None:
        self.router = router
        self.client = client

    async def process(
        self, payload: dict[str, JsonValue], request_id: str
    ) -> ProcessingResult:
        started = perf_counter()
        excluded: set[str] = set()
        selected = None
        last_error = None
        upstream_status = None
        result_payload = None
        http_status = 503
        success = False
        attempt_count = 0
        attempt_records: list[AttemptRecord] = []

        deadline = asyncio.timeout(self.router.settings.total_deadline_seconds)
        try:
            async with deadline:
                for _ in range(self.router.settings.max_attempts):
                    try:
                        provider = await self.router.acquire(excluded)
                    except NoProviderAvailable as exc:
                        http_status = 503
                        if last_error is None:
                            last_error = ErrorDetails(
                                code="unavailable", message=str(exc)
                            )
                        break

                    selected = provider.name
                    excluded.add(selected)
                    attempt_count += 1
                    upstream_status = None
                    log_event(
                        "provider_selected", request_id=request_id,
                        provider=selected,
                    )
                    log_event(
                        "upstream_start", request_id=request_id,
                        provider=selected, attempt=attempt_count,
                    )
                    attempt_started = perf_counter()
                    error = None
                    attempt_success = False
                    try:
                        result = await self.client.process(
                            provider.url, payload, request_id
                        )
                        upstream_status = result.status_code
                        result_payload = result.payload
                        http_status = 200
                        success = attempt_success = True
                        last_error = None
                    except ProviderError as exc:
                        error = exc
                        upstream_status = exc.status_code
                        result_payload = exc.payload
                        last_error = ErrorDetails(
                            code=exc.code, message=str(exc)
                        )
                        if exc.code == "response_too_large":
                            http_status = 502
                        elif exc.status_code == 429:
                            http_status = 503
                        elif (
                            exc.status_code is not None
                            and 400 <= exc.status_code < 500
                        ):
                            http_status = exc.status_code
                        elif exc.code == "timeout":
                            http_status = 504
                        else:
                            http_status = 502
                        log_event(
                            "upstream_error", level=logging.WARNING,
                            request_id=request_id, provider=selected,
                            code=exc.code, status_code=exc.status_code,
                        )
                    finally:
                        latency = (perf_counter() - attempt_started) * 1000
                        self.router.record(
                            provider, latency, success=attempt_success,
                            error=error,
                        )
                        self.router.release(provider)
                        attempt_records.append(AttemptRecord(
                            attempt_number=attempt_count,
                            provider=selected,
                            upstream_status_code=upstream_status,
                            latency_ms=latency,
                            success=attempt_success,
                            error_code=(
                                error.code if error is not None
                                else None if attempt_success else "interrupted"
                            ),
                        ))
                        log_event(
                            "upstream_end", request_id=request_id,
                            provider=selected, status_code=upstream_status,
                            latency_ms=round(latency, 3),
                            success=attempt_success,
                        )

                    if success:
                        break
                    if (
                        error is None or not error.retryable
                        or (
                            error.ambiguous
                            and not self.router.settings.retry_ambiguous_failures
                        )
                        or attempt_count >= self.router.settings.max_attempts
                    ):
                        break
                    log_event(
                        "fallback", request_id=request_id,
                        failed_provider=selected,
                        next_attempt=attempt_count + 1,
                        reason=error.code,
                    )
        except TimeoutError:
            if not deadline.expired():
                raise
            http_status = 504
            success = False
            result_payload = None
            last_error = ErrorDetails(
                code="deadline_exceeded",
                message="The request exceeded the total processing deadline",
            )

        return ProcessingResult(
            response=ProcessResponse(
                request_id=request_id, provider=selected,
                status="succeeded" if success else "failed",
                result=result_payload,
                latency_ms=(perf_counter() - started) * 1000,
                error=last_error,
            ),
            http_status=http_status, upstream_status=upstream_status,
            attempt_count=attempt_count, attempts=attempt_records,
        )
