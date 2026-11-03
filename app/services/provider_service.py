import logging
from dataclasses import dataclass
from time import perf_counter

from pydantic import JsonValue

from app.clients.provider_client import ProviderClient, ProviderError
from app.core.logging import log_event
from app.schemas.request import ErrorDetails, ProcessResponse
from app.services.smart_router import NoProviderAvailable, SmartRouter


@dataclass
class ProcessingResult:
    response: ProcessResponse
    http_status: int
    upstream_status: int | None
    attempt_count: int


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
        attempts = 0

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
            attempts += 1
            upstream_status = None
            log_event(
                "provider_selected", request_id=request_id, provider=selected
            )
            log_event(
                "upstream_start", request_id=request_id,
                provider=selected, attempt=attempts,
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
                last_error = ErrorDetails(code=exc.code, message=str(exc))
                if exc.status_code == 429:
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
                    provider, latency, success=attempt_success, error=error
                )
                self.router.release(provider)
                log_event(
                    "upstream_end", request_id=request_id, provider=selected,
                    status_code=upstream_status, latency_ms=round(latency, 3),
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
                or attempts >= self.router.settings.max_attempts
            ):
                break
            log_event(
                "fallback", request_id=request_id, failed_provider=selected,
                next_attempt=attempts + 1, reason=error.code,
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
            attempt_count=attempts,
        )
