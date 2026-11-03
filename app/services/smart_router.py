import asyncio
from collections import deque
from dataclasses import dataclass, field
from time import monotonic

from app.clients.provider_client import ProviderError
from app.core.config import Settings


class NoProviderAvailable(Exception):
    pass


@dataclass
class ProviderState:
    name: str
    url: str
    semaphore: asyncio.Semaphore
    samples: deque[tuple[bool, float]] = field(default_factory=deque)
    active_requests: int = 0
    unavailable_until: float = 0.0
    rate_limited_until: float = 0.0

    @property
    def successful_requests(self) -> int:
        return sum(success for success, _ in self.samples)

    @property
    def failed_requests(self) -> int:
        return len(self.samples) - self.successful_requests

    @property
    def average_latency_ms(self) -> float | None:
        if not self.samples:
            return None
        return sum(latency for _, latency in self.samples) / len(self.samples)

    @property
    def available(self) -> bool:
        return monotonic() >= self.unavailable_until

    @property
    def rate_limited(self) -> bool:
        return monotonic() < self.rate_limited_until


class SmartRouter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # shortcut: local limits, use shared state for multiple workers.
        self.providers = {
            name: ProviderState(
                name, url, asyncio.Semaphore(limit),
                samples=deque(maxlen=settings.stats_window),
            )
            for name, (url, limit) in settings.providers.items()
        }
        self.semaphore = asyncio.Semaphore(settings.max_upstream_concurrency)

    def score(self, provider: ProviderState) -> float:
        latency = provider.average_latency_ms
        error_rate = provider.failed_requests / max(1, len(provider.samples))
        return (
            latency
            if latency is not None else self.settings.initial_latency_ms
        ) + error_rate * self.settings.error_penalty_ms

    async def acquire(self, excluded: set[str]) -> ProviderState:
        if self.semaphore.locked():
            raise NoProviderAvailable("Global upstream capacity is exhausted")
        candidates = [
            provider for provider in self.providers.values()
            if provider.name not in excluded
            and provider.available
            and not provider.rate_limited
            and not provider.semaphore.locked()
        ]
        if not candidates:
            raise NoProviderAvailable("No provider is currently available")
        selected = min(candidates, key=self.score)
        # Unlocked acquire does not yield; admission is atomic on this loop.
        await self.semaphore.acquire()
        await selected.semaphore.acquire()
        selected.active_requests += 1
        return selected

    def release(self, provider: ProviderState) -> None:
        provider.active_requests -= 1
        provider.semaphore.release()
        self.semaphore.release()

    def record(
        self, provider: ProviderState, latency_ms: float,
        *, success: bool, error: ProviderError | None = None,
    ) -> None:
        provider.samples.append((success, latency_ms))
        now = monotonic()
        if not success and error is not None:
            if error.status_code == 429:
                cooldown = error.retry_after
                if cooldown is None:
                    cooldown = self.settings.rate_limit_cooldown_seconds
                provider.rate_limited_until = max(
                    provider.rate_limited_until, now + cooldown
                )
            elif error.retryable or error.code == "invalid_response":
                provider.unavailable_until = max(
                    provider.unavailable_until,
                    now + self.settings.failure_cooldown_seconds,
                )
