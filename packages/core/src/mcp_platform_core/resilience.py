"""Resilience layer (DESIGN.md §9): per-attempt timeout, bounded retry, circuit breaker.

Handlers stay pure API clients — they declare resilience with one call:

    data = await ctx.resilient.call("open-meteo", lambda: client.get(url), timeout_s=4.0, retries=2)

All retry/timeout/breaker bookkeeping lives here, never in a handler.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import IntEnum
from typing import Protocol, TypeVar

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from mcp_platform_core.observability.metrics import Metrics, NullMetrics

T = TypeVar("T")


class CircuitState(IntEnum):
    """Values match the ``mcp_circuit_state`` gauge directly (no lookup table)."""

    CLOSED = 0
    OPEN = 1
    HALF_OPEN = 2


class CircuitOpenError(Exception):
    def __init__(self, upstream: str) -> None:
        super().__init__(f"circuit open for upstream {upstream!r}")
        self.upstream = upstream


class UpstreamTimeoutError(Exception):
    def __init__(self, upstream: str) -> None:
        super().__init__(f"upstream {upstream!r} timed out")
        self.upstream = upstream


class _Clock(Protocol):
    def now(self) -> float: ...


class _MonotonicClock:
    def now(self) -> float:
        return time.monotonic()


def _is_transient(exc: BaseException) -> bool:
    """Retry only transient upstream failures — never 4xx client errors or config errors."""
    if isinstance(exc, UpstreamTimeoutError):
        return True
    if isinstance(exc, httpx.TransportError):  # timeouts, connect/read errors, network
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


class CircuitBreaker:
    """Per-upstream failure-counting breaker with a single-probe half-open state.

    ``consecutive_failures`` counts *logical* calls that failed after exhausting
    their own retry budget — not individual low-level attempts. Transition
    methods are synchronous (no ``await``), so they are atomic w.r.t. the event
    loop and need no lock: only one coroutine can be mid-transition at a time.
    """

    def __init__(
        self,
        upstream: str,
        *,
        threshold: int,
        cooldown_s: float,
        clock: _Clock,
        metrics: Metrics,
    ) -> None:
        self.upstream = upstream
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._metrics = metrics
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.opened_at: float | None = None
        self._probe_in_flight = False
        self._metrics.set_circuit_state(upstream, int(self.state))

    def _transition(self, state: CircuitState) -> None:
        self.state = state
        self._metrics.set_circuit_state(self.upstream, int(state))

    def before_call(self) -> None:
        """Admit or reject a call. Raises CircuitOpenError when the circuit is open."""
        if self.state is CircuitState.CLOSED:
            return
        if self.state is CircuitState.OPEN:
            opened_at = self.opened_at if self.opened_at is not None else self._clock.now()
            if self._clock.now() - opened_at >= self.cooldown_s:
                self._probe_in_flight = True
                self._transition(CircuitState.HALF_OPEN)
                return  # this caller is the single probe
            raise CircuitOpenError(self.upstream)
        # HALF_OPEN: only one probe may be in flight at a time
        if self._probe_in_flight:
            raise CircuitOpenError(self.upstream)
        self._probe_in_flight = True

    def on_success(self) -> None:
        self.consecutive_failures = 0
        if self.state is not CircuitState.CLOSED:
            self._probe_in_flight = False
            self.opened_at = None
            self._transition(CircuitState.CLOSED)

    def on_failure(self) -> None:
        self.consecutive_failures += 1
        if self.state is CircuitState.HALF_OPEN:
            self._probe_in_flight = False
            self.opened_at = self._clock.now()  # fresh cooldown on a failed probe
            self._transition(CircuitState.OPEN)
        elif self.state is CircuitState.CLOSED and self.consecutive_failures >= self.threshold:
            self.opened_at = self._clock.now()
            self._transition(CircuitState.OPEN)


class ResilientCaller:
    """Injected into every ToolContext; wraps upstream calls with timeout+retry+breaker.

    Breakers are keyed per upstream string and shared across the per-tool bound
    callers produced by :meth:`for_tool`, so circuit state is per-upstream (a
    failing "open-meteo" breaker never affects "openweathermap").
    """

    def __init__(
        self,
        *,
        metrics: Metrics | None = None,
        timeout_s: float = 4.0,
        retries: int = 2,
        breaker_threshold: int = 5,
        breaker_cooldown_s: float = 30.0,
        backoff_base_s: float = 0.1,
        clock: _Clock | None = None,
        _tool: str | None = None,
        _breakers: dict[str, CircuitBreaker] | None = None,
    ) -> None:
        self._metrics = metrics or NullMetrics()
        self._default_timeout_s = timeout_s
        self._default_retries = retries
        self._breaker_threshold = breaker_threshold
        self._breaker_cooldown_s = breaker_cooldown_s
        self._backoff_base_s = backoff_base_s
        self._clock = clock or _MonotonicClock()
        self._tool = _tool
        self._breakers = _breakers if _breakers is not None else {}

    def for_tool(self, tool: str) -> ResilientCaller:
        """Return a caller that labels retry metrics with ``tool`` and shares breakers/config."""
        return ResilientCaller(
            metrics=self._metrics,
            timeout_s=self._default_timeout_s,
            retries=self._default_retries,
            breaker_threshold=self._breaker_threshold,
            breaker_cooldown_s=self._breaker_cooldown_s,
            backoff_base_s=self._backoff_base_s,
            clock=self._clock,
            _tool=tool,
            _breakers=self._breakers,
        )

    def _breaker_for(self, upstream: str) -> CircuitBreaker:
        breaker = self._breakers.get(upstream)
        if breaker is None:
            breaker = CircuitBreaker(
                upstream,
                threshold=self._breaker_threshold,
                cooldown_s=self._breaker_cooldown_s,
                clock=self._clock,
                metrics=self._metrics,
            )
            self._breakers[upstream] = breaker
        return breaker

    async def call(
        self,
        upstream: str,
        fn: Callable[[], Awaitable[T]],
        *,
        timeout_s: float | None = None,
        retries: int | None = None,
    ) -> T:
        timeout = timeout_s if timeout_s is not None else self._default_timeout_s
        attempts = (retries if retries is not None else self._default_retries) + 1
        breaker = self._breaker_for(upstream)
        breaker.before_call()  # raises CircuitOpenError if not admitted (no failure recorded)

        try:
            result = await self._run_with_retries(upstream, fn, timeout, attempts)
        except Exception:
            breaker.on_failure()
            raise
        breaker.on_success()
        return result

    async def _run_with_retries(
        self,
        upstream: str,
        fn: Callable[[], Awaitable[T]],
        attempt_timeout_s: float,
        attempts: int,
    ) -> T:
        tool_label = self._tool or upstream

        def _on_retry(retry_state: RetryCallState) -> None:
            self._metrics.record_retry(tool_label)

        retryer: AsyncRetrying = AsyncRetrying(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential_jitter(
                initial=self._backoff_base_s, max=5.0, jitter=self._backoff_base_s
            ),
            retry=retry_if_exception(_is_transient),
            before_sleep=_on_retry,
            reraise=True,
        )
        result: T = await retryer(self._attempt, upstream, fn, attempt_timeout_s)
        return result

    async def _attempt(
        self, upstream: str, fn: Callable[[], Awaitable[T]], attempt_timeout_s: float
    ) -> T:
        try:
            return await asyncio.wait_for(fn(), timeout=attempt_timeout_s)
        except TimeoutError as exc:  # asyncio.TimeoutError is an alias in 3.11+
            raise UpstreamTimeoutError(upstream) from exc
