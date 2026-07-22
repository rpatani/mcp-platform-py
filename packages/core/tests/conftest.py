"""Shared test fixtures for the core suite.

A ``FakeClock`` is threaded into the rate limiter, cache, and circuit breaker
so tests advance time deterministically instead of sleeping (needed for exact
rolling-window / TTL / cooldown assertions).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
import structlog
from pydantic import BaseModel

from mcp_platform_core.middleware import (
    InMemoryKeyStore,
    InMemoryRateLimiter,
    InMemoryResponseCache,
    MiddlewareDeps,
)
from mcp_platform_core.observability.metrics import PrometheusMetrics
from mcp_platform_core.resilience import ResilientCaller
from mcp_platform_core.types import ApiKeyRecord, ToolContext, ToolDefinition, UsageEvent


@pytest.fixture(autouse=True)
def _isolate_structlog() -> object:
    """Reset structlog's global config around every test.

    ``create_logger`` binds structlog to the current stdout/stderr, which under
    pytest is a per-test capture buffer that gets closed afterwards. Without
    this reset, a later test logging through the leaked config hits
    "I/O operation on closed file".
    """
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class RecordingUsageSink:
    def __init__(self) -> None:
        self.events: list[UsageEvent] = []

    async def record(self, event: UsageEvent) -> None:
        self.events.append(event)


class EchoInput(BaseModel):
    value: int = 0


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def usage_sink() -> RecordingUsageSink:
    return RecordingUsageSink()


@pytest.fixture
def metrics() -> PrometheusMetrics:
    from prometheus_client import CollectorRegistry

    return PrometheusMetrics(registry=CollectorRegistry())


@pytest.fixture
def key_store() -> InMemoryKeyStore:
    return InMemoryKeyStore(
        {
            "free-key": ApiKeyRecord(
                api_key="free-key", owner="free-user", tier="free", rate_limit_per_minute=5
            ),
            "premium-key": ApiKeyRecord(
                api_key="premium-key",
                owner="premium-user",
                tier="premium",
                rate_limit_per_minute=100,
            ),
        }
    )


@pytest.fixture
def deps(
    clock: FakeClock,
    usage_sink: RecordingUsageSink,
    metrics: PrometheusMetrics,
    key_store: InMemoryKeyStore,
) -> MiddlewareDeps:
    return MiddlewareDeps(
        key_store=key_store,
        rate_limiter=InMemoryRateLimiter(clock=clock),
        cache=InMemoryResponseCache(clock=clock),
        usage_sink=usage_sink,
        metrics=metrics,
        logger=structlog.get_logger(),
        resilient=ResilientCaller(),
    )


def make_tool(
    *,
    name: str = "echo",
    handler: Callable[[EchoInput, ToolContext], Awaitable[object]] | None = None,
    min_tier: str = "free",
    cost_units: int = 1,
    cache_ttl_ms: int | None = None,
) -> ToolDefinition:
    async def default_handler(args: EchoInput, ctx: ToolContext) -> dict[str, int]:
        return {"echoed": args.value}

    return ToolDefinition(
        name=name,
        description="echo tool for tests",
        input_model=EchoInput,
        min_tier=min_tier,  # type: ignore[arg-type]
        cost_units=cost_units,
        cache_ttl_ms=cache_ttl_ms,
        handler=handler or default_handler,
    )
