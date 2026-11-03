import json
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx


@dataclass
class UpstreamResult:
    status_code: int
    payload: Any


class ProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        ambiguous: bool = False,
        status_code: int | None = None,
        payload: Any = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.status_code = status_code
        self.payload = payload
        self.retry_after = retry_after


def parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    if value.strip().isascii() and value.strip().isdigit():
        try:
            return float(int(value.strip()))
        except (ValueError, OverflowError):
            return None
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        return max(0.0, (date - datetime.now(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def reject_nonfinite(value: str) -> None:
    raise ValueError(f"Invalid JSON number: {value}")


class ProviderClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def process(
        self, url: str, payload: dict[str, Any], request_id: str
    ) -> UpstreamResult:
        try:
            # shortcut: no body cap, add streaming limits for untrusted APIs.
            response = await self.client.post(
                url, json={"payload": payload},
                headers={"X-Request-ID": request_id},
            )
        except (
            httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout
        ) as exc:
            raise ProviderError(
                "connection_error", "Unable to connect to the provider",
                retryable=True,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "timeout", "The provider request timed out",
                retryable=True, ambiguous=True,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                "transport_error", "The provider connection failed",
                retryable=True, ambiguous=True,
            ) from exc

        try:
            data = json.loads(
                response.content, parse_constant=reject_nonfinite
            )
            json.dumps(data, allow_nan=False)
        except (ValueError, UnicodeDecodeError):
            data = None
            if 200 <= response.status_code < 300:
                raise ProviderError(
                    "invalid_response", "The provider returned invalid JSON",
                    retryable=False, status_code=response.status_code,
                )

        if response.status_code == 429:
            raise ProviderError(
                "rate_limited", "The provider is rate limited",
                retryable=True, status_code=429, payload=data,
                retry_after=parse_retry_after(
                    response.headers.get("Retry-After")
                ),
            )
        if response.status_code >= 500:
            raise ProviderError(
                "upstream_server_error",
                "The provider returned a server error",
                retryable=True, ambiguous=True,
                status_code=response.status_code, payload=data,
            )
        if not 200 <= response.status_code < 300:
            raise ProviderError(
                "upstream_rejected", "The provider rejected the request",
                retryable=False,
                status_code=response.status_code, payload=data,
            )
        return UpstreamResult(response.status_code, data)
