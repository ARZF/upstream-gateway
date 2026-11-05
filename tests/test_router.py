import asyncio
from time import monotonic
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.clients.provider_client import ProviderClient, ProviderError, parse_retry_after
from app.core.config import Settings
from app.services.provider_service import ProviderService
from app.services.smart_router import NoProviderAvailable, SmartRouter


@pytest.mark.asyncio
async def test_selection_uses_latency_and_error_rate():
    router = SmartRouter(Settings())
    a, b, c = router.providers.values()
    router.record(a, 100, success=True)
    router.record(a, 100, success=False)
    router.record(b, 200, success=True)
    router.record(c, 900, success=True)
    selected = await router.acquire(set())
    assert selected is b
    router.release(selected)
    b.unavailable_until = monotonic() + 10
    selected = await router.acquire(set())
    assert selected is c
    router.release(selected)
    c.rate_limited_until = monotonic() + 10
    selected = await router.acquire(set())
    assert selected is a
    router.release(selected)


@pytest.mark.asyncio
async def test_per_provider_capacity_and_exclusions():
    router = SmartRouter(Settings(provider_a_concurrency=1))
    a = await router.acquire(set())
    assert a.name == "a" and a.active_requests == 1
    b = await router.acquire(set())
    assert b.name == "b"
    with pytest.raises(NoProviderAvailable):
        await router.acquire({"b", "c"})
    router.release(a)
    router.release(b)
    a = await router.acquire({"b", "c"})
    assert a.active_requests == 1
    router.release(a)


@pytest.mark.asyncio
async def test_global_limit_has_no_wait_queue():
    router = SmartRouter(Settings(max_upstream_concurrency=1))
    provider = await router.acquire(set())
    with pytest.raises(NoProviderAvailable):
        await asyncio.wait_for(router.acquire(set()), 0.1)
    router.release(provider)
    assert not router.semaphore.locked()


def test_bounded_recent_statistics_and_recovery():
    router = SmartRouter(Settings(stats_window=2))
    a = router.providers["a"]
    error = ProviderError("connection_error", "mock", retryable=True)
    router.record(a, 1, success=False, error=error)
    assert not a.available
    router.record(a, 10, success=True)
    assert not a.available  # An in-flight success must not clear a recent failure.
    a.unavailable_until = monotonic() - 1
    router.record(a, 20, success=True)
    assert a.available
    assert a.successful_requests == 2 and a.failed_requests == 0
    assert a.average_latency_ms == 15


@pytest.mark.parametrize("has_success_sample", [False, True])
def test_client_4xx_does_not_change_provider_score(has_success_sample):
    router = SmartRouter(Settings())
    provider = router.providers["a"]
    if has_success_sample:
        router.record(provider, 100, success=True)
    samples = list(provider.samples)
    score = router.score(provider)

    for latency in [10, 200, 500]:
        router.record(provider, latency, success=False, error=ProviderError(
            "upstream_rejected", "mock", retryable=False, status_code=422,
        ))
        assert provider.failed_requests == 0
        assert list(provider.samples) == samples
        assert router.score(provider) == score
        assert provider.unavailable_until == provider.rate_limited_until == 0


@pytest.mark.parametrize("status", [429, 500])
def test_rate_limit_and_server_errors_still_count_as_failures(status):
    router = SmartRouter(Settings())
    provider = router.providers["a"]
    router.record(provider, 100, success=False, error=ProviderError(
        "rate_limited" if status == 429 else "upstream_server_error",
        "mock", retryable=True, status_code=status,
    ))
    assert provider.failed_requests == 1
    assert list(provider.samples) == [(False, 100)]
    if status == 429:
        assert provider.rate_limited
    else:
        assert not provider.available


def test_rate_limit_is_not_concurrency_or_shortened_by_later_responses():
    router = SmartRouter(Settings())
    a = router.providers["a"]
    for cooldown in [120, 5]:
        router.record(a, 1, success=False, error=ProviderError(
            "rate_limited", "mock", retryable=True,
            status_code=429, retry_after=cooldown,
        ))
    assert a.rate_limited_until > monotonic() + 119
    assert a.active_requests == 0 and not a.semaphore.locked()
    a.rate_limited_until = monotonic() - 1
    assert not a.rate_limited


@pytest.mark.asyncio
async def test_cancellation_releases_all_capacity():
    entered = asyncio.Event()

    async def handler(_):
        entered.set()
        await asyncio.Event().wait()

    router = SmartRouter(Settings(max_upstream_concurrency=1, provider_a_concurrency=1))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = ProviderService(
            router, ProviderClient(client, router.settings.max_response_bytes)
        )
        task = asyncio.create_task(service.process({}, str(uuid4())))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert router.providers["a"].active_requests == 0
    assert router.providers["a"].failed_requests == 1
    assert not router.semaphore.locked()
    assert not router.providers["a"].semaphore.locked()


@pytest.mark.parametrize("value,expected", [(None, None), ("bogus", None), ("-1", None), ("0", 0), ("120", 120)])
def test_retry_after_parsing(value, expected):
    assert parse_retry_after(value) == expected


def test_configuration_from_environment(monkeypatch):
    monkeypatch.setenv("MAX_ATTEMPTS", "3")
    monkeypatch.setenv("RETRY_AMBIGUOUS_FAILURES", "true")
    monkeypatch.setenv("DATABASE_POOL_SIZE", "7")
    assert Settings.from_env().max_attempts == 3
    assert Settings.from_env().retry_ambiguous_failures
    assert Settings.from_env().database_pool_size == 7


@pytest.mark.parametrize("values", [
    {"max_attempts": 4}, {"provider_a_concurrency": 0},
    {"connect_timeout_seconds": -1}, {"read_timeout_seconds": float("inf")},
    {"http_max_connections": 1, "http_max_keepalive_connections": 2},
    {"database_pool_size": 0}, {"database_max_overflow": -1},
    {"total_deadline_seconds": 0}, {"max_request_body_bytes": 0},
    {"max_response_bytes": 0},
    {"provider_a_url": "file:///unsafe"},
])
def test_invalid_configuration(values):
    with pytest.raises(ValidationError):
        Settings(**values)
