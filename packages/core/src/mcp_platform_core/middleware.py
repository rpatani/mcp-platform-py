"""build_tool_executor: the fixed middleware chain (CLAUDE.md §5), plus in-memory deps.

    resolve account
      -> tier check              (reject < min_tier)
      -> cache lookup            (hit => zero-cost usage event, SKIP rate limiter)
      -> rate-limit check        (per api_key, rolling 60s window)
      -> run handler             (through ctx.resilient)
      -> populate cache          (only if cache_ttl_ms set and success)
      -> emit metrics + structured log + usage event

This order must never be reordered — it is a versioned behavioral contract,
not just an implementation detail.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from mcp_platform_core.observability.metrics import Metrics
from mcp_platform_core.resilience import ResilientCaller
from mcp_platform_core.types import (
    TIER_RANK,
    ApiKeyRecord,
    KeyStore,
    RateLimiter,
    RateLimitError,
    RateLimitResult,
    ResponseCache,
    TierError,
    ToolContext,
    ToolDefinition,
    UsageEvent,
    UsageSink,
)

ToolExecutor = Callable[[dict[str, Any], "str | None"], Awaitable[Any]]

ANONYMOUS_FREE = ApiKeyRecord(api_key="", owner="anonymous", tier="free", rate_limit_per_minute=60)


@dataclass
class MiddlewareDeps:
    """Cross-cutting dependencies the executor threads through every tool call."""

    key_store: KeyStore
    rate_limiter: RateLimiter
    cache: ResponseCache
    usage_sink: UsageSink
    metrics: Metrics
    logger: structlog.BoundLogger
    resilient: ResilientCaller


def build_cache_key(tool_name: str, raw_args: dict[str, Any]) -> str:
    """Global cache key per tool+args — deliberately not scoped by api_key/tier.

    Upstream responses don't depend on caller identity, so this maximizes
    cache value; billing still happens per request regardless of hit.
    """
    canonical = json.dumps(raw_args, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{tool_name}:{canonical}".encode()).hexdigest()


def build_tool_executor(tool: ToolDefinition, deps: MiddlewareDeps) -> ToolExecutor:
    async def execute(raw_args: dict[str, Any], api_key: str | None) -> Any:
        request_id = str(uuid.uuid4())
        start = time.monotonic()
        account = await deps.key_store.resolve(api_key)
        log = deps.logger.bind(
            request_id=request_id, tool=tool.name, owner=account.owner, tier=account.tier
        )

        async def _record_outcome(
            *,
            status: str,
            cost_units: int,
            success: bool,
            cache_hit: bool,
            error_type: str | None,
        ) -> None:
            elapsed_s = time.monotonic() - start
            deps.metrics.record_tool_call(tool.name, account.tier, status, elapsed_s)
            await deps.usage_sink.record(
                UsageEvent(
                    request_id=request_id,
                    tool=tool.name,
                    owner=account.owner,
                    tier=account.tier,
                    cost_units=cost_units,
                    success=success,
                    cache_hit=cache_hit,
                    duration_ms=elapsed_s * 1000,
                    error_type=error_type,
                )
            )

        if TIER_RANK[account.tier] < TIER_RANK[tool.min_tier]:
            await _record_outcome(
                status="error", cost_units=0, success=False, cache_hit=False, error_type="TierError"
            )
            log.warning("tool_call_rejected", reason="tier", required_tier=tool.min_tier)
            raise TierError(tool.min_tier, account.tier)

        cache_key: str | None = None
        if tool.cache_ttl_ms is not None:
            cache_key = build_cache_key(tool.name, raw_args)
            cached = await deps.cache.get(cache_key)
            if cached is not None:
                deps.metrics.record_cache_event(tool.name, "hit")
                await _record_outcome(
                    status="success", cost_units=0, success=True, cache_hit=True, error_type=None
                )
                log.info("tool_call_cache_hit")
                return cached
            deps.metrics.record_cache_event(tool.name, "miss")

        rate_result = await deps.rate_limiter.check_and_increment(
            api_key or "anonymous", account.rate_limit_per_minute
        )
        if not rate_result.allowed:
            await _record_outcome(
                status="error",
                cost_units=0,
                success=False,
                cache_hit=False,
                error_type="RateLimitError",
            )
            log.warning("tool_call_rejected", reason="rate_limit")
            raise RateLimitError(rate_result.retry_after_s)

        parsed = tool.input_model.model_validate(raw_args)
        ctx = ToolContext(
            request_id=request_id,
            account=account,
            api_key=api_key,
            resilient=deps.resilient.for_tool(tool.name),
            log=log,
        )

        try:
            result = await tool.handler(parsed, ctx)
        except Exception as exc:
            await _record_outcome(
                status="error",
                cost_units=tool.cost_units,
                success=False,
                cache_hit=False,
                error_type=type(exc).__name__,
            )
            log.error("tool_call_failed", error_type=type(exc).__name__)
            raise

        if cache_key is not None and tool.cache_ttl_ms is not None:
            await deps.cache.set(cache_key, result, tool.cache_ttl_ms)

        await _record_outcome(
            status="success",
            cost_units=tool.cost_units,
            success=True,
            cache_hit=False,
            error_type=None,
        )
        log.info("tool_call_succeeded")
        return result

    return execute


class _Clock(Protocol):
    def now(self) -> float: ...


class _MonotonicClock:
    def now(self) -> float:
        return time.monotonic()


class InMemoryKeyStore:
    """Read-only after construction; unknown/None keys fall through to anonymous free tier."""

    def __init__(self, records: dict[str, ApiKeyRecord] | None = None) -> None:
        self._records = dict(records or {})

    async def resolve(self, api_key: str | None) -> ApiKeyRecord:
        if api_key is None:
            return ANONYMOUS_FREE
        return self._records.get(api_key, ANONYMOUS_FREE)


@dataclass
class InMemoryRateLimiter:
    """Sliding-window-log rate limiter, keyed per api_key. Single-process only."""

    clock: _Clock = field(default_factory=_MonotonicClock)
    _windows: dict[str, deque[float]] = field(default_factory=dict, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def check_and_increment(self, key: str, limit_per_minute: int) -> RateLimitResult:
        async with self._lock:
            now = self.clock.now()
            window_start = now - 60.0
            timestamps = self._windows.setdefault(key, deque())
            while timestamps and timestamps[0] <= window_start:
                timestamps.popleft()
            if len(timestamps) >= limit_per_minute:
                retry_after_s = max(60.0 - (now - timestamps[0]), 0.0)
                return RateLimitResult(allowed=False, retry_after_s=retry_after_s)
            timestamps.append(now)
            return RateLimitResult(allowed=True, retry_after_s=None)


@dataclass
class InMemoryResponseCache:
    """TTL cache keyed by the caller-supplied cache key. Single-process only."""

    clock: _Clock = field(default_factory=_MonotonicClock)
    _store: dict[str, tuple[float, Any]] = field(default_factory=dict, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if self.clock.now() >= expires_at:
                del self._store[key]
                return None
            return value

    async def set(self, key: str, value: Any, ttl_ms: int) -> None:
        async with self._lock:
            self._store[key] = (self.clock.now() + ttl_ms / 1000.0, value)


class LoggingUsageSink:
    """Default UsageSink — logs each usage event as a structured line (billing pipeline: future)."""

    def __init__(self, logger: structlog.BoundLogger | None = None) -> None:
        self._logger = logger or structlog.get_logger()

    async def record(self, event: UsageEvent) -> None:
        self._logger.info(
            "usage_event",
            request_id=event.request_id,
            tool=event.tool,
            owner=event.owner,
            tier=event.tier,
            cost_units=event.cost_units,
            success=event.success,
            cache_hit=event.cache_hit,
            duration_ms=event.duration_ms,
            error_type=event.error_type,
        )
