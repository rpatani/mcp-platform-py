from __future__ import annotations

import asyncio

import httpx
import pytest
from prometheus_client import CollectorRegistry
from prometheus_client.parser import text_string_to_metric_families

from mcp_platform_core.observability.metrics import PrometheusMetrics
from mcp_platform_core.resilience import (
    CircuitOpenError,
    CircuitState,
    ResilientCaller,
    UpstreamTimeoutError,
)

from .conftest import FakeClock


def _metric_value(metrics: PrometheusMetrics, name: str, labels: dict[str, str]) -> float:
    body = metrics.expose()[0].decode()
    for family in text_string_to_metric_families(body):
        for sample in family.samples:
            if sample.name == name and all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
    return 0.0


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://upstream.test")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(f"{code}", request=request, response=response)


def _caller(**kwargs: object) -> ResilientCaller:
    kwargs.setdefault("backoff_base_s", 0.0)  # instant retries in tests
    return ResilientCaller(**kwargs)  # type: ignore[arg-type]


async def test_success_passes_through() -> None:
    caller = _caller()

    async def ok() -> str:
        return "value"

    assert await caller.call("up", ok) == "value"


async def test_retry_then_succeed() -> None:
    metrics = PrometheusMetrics(registry=CollectorRegistry())
    caller = _caller(metrics=metrics, retries=2)
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("boom")
        return "ok"

    result = await caller.for_tool("get_forecast").call("open-meteo", flaky)

    assert result == "ok"
    assert calls["n"] == 3
    assert _metric_value(metrics, "mcp_upstream_retries_total", {"tool": "get_forecast"}) == 2.0


async def test_retries_exhausted_reraises() -> None:
    caller = _caller(retries=2)

    async def always_fail() -> str:
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        await caller.call("up", always_fail)


async def test_4xx_never_retried() -> None:
    caller = _caller(retries=3)
    calls = {"n": 0}

    async def client_error() -> str:
        calls["n"] += 1
        raise _status_error(404)

    with pytest.raises(httpx.HTTPStatusError):
        await caller.call("up", client_error)
    assert calls["n"] == 1  # no retries on a 4xx


@pytest.mark.parametrize("code", [429, 500, 503])
async def test_429_and_5xx_retried(code: int) -> None:
    caller = _caller(retries=1)
    calls = {"n": 0}

    async def transient() -> str:
        calls["n"] += 1
        raise _status_error(code)

    with pytest.raises(httpx.HTTPStatusError):
        await caller.call("up", transient)
    assert calls["n"] == 2  # original + 1 retry


async def test_timeout_maps_to_upstream_timeout_and_is_retried() -> None:
    caller = _caller(retries=1, timeout_s=0.01)
    calls = {"n": 0}

    async def too_slow() -> str:
        calls["n"] += 1
        await asyncio.sleep(1.0)
        return "never"

    with pytest.raises(UpstreamTimeoutError):
        await caller.call("up", too_slow)
    assert calls["n"] == 2  # timeout is transient -> retried once


async def test_breaker_opens_after_n_logical_failures() -> None:
    clock = FakeClock()
    metrics = PrometheusMetrics(registry=CollectorRegistry())
    caller = _caller(metrics=metrics, retries=0, breaker_threshold=3, clock=clock)

    async def fail() -> str:
        raise httpx.ConnectError("down")

    for _ in range(3):
        with pytest.raises(httpx.ConnectError):
            await caller.call("up", fail)

    # circuit now open: the next call fails fast without invoking fn
    with pytest.raises(CircuitOpenError):
        await caller.call("up", fail)
    circuit = _metric_value(metrics, "mcp_circuit_state", {"upstream": "up"})
    assert circuit == float(CircuitState.OPEN)


async def test_breaker_stays_open_until_cooldown() -> None:
    clock = FakeClock()
    caller = _caller(retries=0, breaker_threshold=1, breaker_cooldown_s=30.0, clock=clock)

    async def fail() -> str:
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        await caller.call("up", fail)
    with pytest.raises(CircuitOpenError):
        await caller.call("up", fail)

    clock.advance(29)
    with pytest.raises(CircuitOpenError):
        await caller.call("up", fail)  # still within cooldown


async def test_half_open_probe_succeeds_closes() -> None:
    clock = FakeClock()
    metrics = PrometheusMetrics(registry=CollectorRegistry())
    caller = _caller(
        metrics=metrics, retries=0, breaker_threshold=1, breaker_cooldown_s=30.0, clock=clock
    )
    state = {"fail": True}

    async def fn() -> str:
        if state["fail"]:
            raise httpx.ConnectError("down")
        return "recovered"

    with pytest.raises(httpx.ConnectError):
        await caller.call("up", fn)  # opens circuit

    clock.advance(31)  # cooldown elapsed
    state["fail"] = False
    assert await caller.call("up", fn) == "recovered"  # probe succeeds -> closed
    assert _metric_value(metrics, "mcp_circuit_state", {"upstream": "up"}) == float(
        CircuitState.CLOSED
    )


async def test_half_open_probe_fails_reopens_with_fresh_cooldown() -> None:
    clock = FakeClock()
    caller = _caller(retries=0, breaker_threshold=1, breaker_cooldown_s=30.0, clock=clock)

    async def fail() -> str:
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        await caller.call("up", fail)  # open
    clock.advance(31)
    with pytest.raises(httpx.ConnectError):
        await caller.call("up", fail)  # probe fails -> reopen, fresh cooldown

    clock.advance(10)  # only 10s into the fresh 30s cooldown
    with pytest.raises(CircuitOpenError):
        await caller.call("up", fail)


async def test_only_one_concurrent_probe_admitted() -> None:
    clock = FakeClock()
    caller = _caller(retries=0, breaker_threshold=1, breaker_cooldown_s=30.0, clock=clock)
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()
    attempts = {"n": 0}

    async def fail() -> str:
        raise httpx.ConnectError("down")

    async def slow_probe() -> str:
        attempts["n"] += 1
        probe_started.set()
        await release_probe.wait()
        return "ok"

    with pytest.raises(httpx.ConnectError):
        await caller.call("up", fail)  # open
    clock.advance(31)  # cooldown elapsed -> next call becomes the probe

    probe_task = asyncio.create_task(caller.call("up", slow_probe))
    await probe_started.wait()  # probe is mid-flight, holding half-open

    # a second concurrent caller must be rejected, not admitted as a 2nd probe
    with pytest.raises(CircuitOpenError):
        await caller.call("up", slow_probe)

    release_probe.set()
    assert await probe_task == "ok"
    assert attempts["n"] == 1  # the rejected caller never ran fn


async def test_per_upstream_breakers_are_independent() -> None:
    clock = FakeClock()
    caller = _caller(retries=0, breaker_threshold=1, clock=clock)

    async def fail() -> str:
        raise httpx.ConnectError("down")

    async def ok() -> str:
        return "fine"

    with pytest.raises(httpx.ConnectError):
        await caller.call("upstream-a", fail)  # opens A only
    with pytest.raises(CircuitOpenError):
        await caller.call("upstream-a", fail)

    # upstream-b is unaffected
    assert await caller.call("upstream-b", ok) == "fine"


async def test_missing_config_error_not_retried() -> None:
    caller = _caller(retries=3)
    calls = {"n": 0}

    class MissingApiKeyError(Exception):
        pass

    async def needs_key() -> str:
        calls["n"] += 1
        raise MissingApiKeyError("no key configured")

    with pytest.raises(MissingApiKeyError):
        await caller.call("up", needs_key)
    assert calls["n"] == 1  # non-transient -> no retries
