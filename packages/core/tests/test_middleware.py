from __future__ import annotations

import pytest
from prometheus_client.parser import text_string_to_metric_families

from mcp_platform_core.middleware import MiddlewareDeps, build_cache_key, build_tool_executor
from mcp_platform_core.observability.metrics import PrometheusMetrics
from mcp_platform_core.types import RateLimitError, TierError, ToolContext

from .conftest import EchoInput, FakeClock, RecordingUsageSink, make_tool


def _metric_value(metrics: PrometheusMetrics, name: str, labels: dict[str, str]) -> float:
    body = metrics.expose()[0].decode()
    for family in text_string_to_metric_families(body):
        for sample in family.samples:
            if sample.name == name and all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
    return 0.0


async def test_tier_accept_runs_handler(deps: MiddlewareDeps) -> None:
    tool = make_tool(min_tier="free")
    execute = build_tool_executor(tool, deps)

    result = await execute({"value": 7}, "free-key")

    assert result == {"echoed": 7}


async def test_tier_reject_below_min_tier(
    deps: MiddlewareDeps, usage_sink: RecordingUsageSink
) -> None:
    tool = make_tool(name="premium_tool", min_tier="premium")
    execute = build_tool_executor(tool, deps)

    with pytest.raises(TierError):
        await execute({"value": 1}, "free-key")

    assert usage_sink.events[-1].success is False
    assert usage_sink.events[-1].error_type == "TierError"
    assert usage_sink.events[-1].cost_units == 0


async def test_unknown_key_falls_back_to_anonymous_free(deps: MiddlewareDeps) -> None:
    tool = make_tool(min_tier="free")
    execute = build_tool_executor(tool, deps)

    result = await execute({"value": 3}, "nonexistent-key")

    assert result == {"echoed": 3}


async def test_none_key_is_anonymous_and_rejected_from_premium(deps: MiddlewareDeps) -> None:
    tool = make_tool(name="premium_tool", min_tier="premium")
    execute = build_tool_executor(tool, deps)

    with pytest.raises(TierError):
        await execute({"value": 1}, None)


async def test_cache_hit_skips_handler(deps: MiddlewareDeps) -> None:
    calls = {"n": 0}

    async def counting_handler(args: EchoInput, ctx: ToolContext) -> dict[str, int]:
        calls["n"] += 1
        return {"echoed": args.value}

    tool = make_tool(handler=counting_handler, cache_ttl_ms=60_000)
    execute = build_tool_executor(tool, deps)

    await execute({"value": 5}, "free-key")
    await execute({"value": 5}, "free-key")

    assert calls["n"] == 1


async def test_cache_hit_billed_zero_cost_and_skips_rate_limiter(
    deps: MiddlewareDeps, usage_sink: RecordingUsageSink
) -> None:
    # rate limit is 5/min for free-key; a cache hit must not consume the window.
    tool = make_tool(cost_units=3, cache_ttl_ms=60_000)
    execute = build_tool_executor(tool, deps)

    await execute({"value": 1}, "free-key")  # miss, populates cache, cost 3
    for _ in range(10):
        await execute({"value": 1}, "free-key")  # all hits, zero cost, no rate-limit consumption

    hit_events = [e for e in usage_sink.events if e.cache_hit]
    assert len(hit_events) == 10
    assert all(e.cost_units == 0 and e.success for e in hit_events)


async def test_cache_miss_then_populate(deps: MiddlewareDeps, clock: FakeClock) -> None:
    calls = {"n": 0}

    async def counting_handler(args: EchoInput, ctx: ToolContext) -> dict[str, int]:
        calls["n"] += 1
        return {"echoed": args.value}

    tool = make_tool(handler=counting_handler, cache_ttl_ms=5_000)
    execute = build_tool_executor(tool, deps)

    await execute({"value": 1}, "free-key")  # miss
    await execute({"value": 1}, "free-key")  # hit
    clock.advance(6)  # TTL (5s) expired
    await execute({"value": 1}, "free-key")  # miss again

    assert calls["n"] == 2


async def test_no_cache_ttl_never_caches(deps: MiddlewareDeps) -> None:
    calls = {"n": 0}

    async def counting_handler(args: EchoInput, ctx: ToolContext) -> dict[str, int]:
        calls["n"] += 1
        return {"echoed": args.value}

    tool = make_tool(handler=counting_handler, cache_ttl_ms=None)
    execute = build_tool_executor(tool, deps)

    await execute({"value": 1}, "premium-key")
    await execute({"value": 1}, "premium-key")

    assert calls["n"] == 2


async def test_rate_limit_allows_then_rejects(
    deps: MiddlewareDeps, usage_sink: RecordingUsageSink
) -> None:
    tool = make_tool()  # no cache, so every call hits the limiter
    execute = build_tool_executor(tool, deps)

    for _ in range(5):  # free-key limit is 5/min
        await execute({"value": 1}, "free-key")

    with pytest.raises(RateLimitError):
        await execute({"value": 1}, "free-key")

    assert usage_sink.events[-1].error_type == "RateLimitError"


async def test_rate_limit_window_resets_after_60s(
    deps: MiddlewareDeps, clock: FakeClock
) -> None:
    tool = make_tool()
    execute = build_tool_executor(tool, deps)

    for _ in range(5):
        await execute({"value": 1}, "free-key")
    with pytest.raises(RateLimitError):
        await execute({"value": 1}, "free-key")

    clock.advance(61)
    result = await execute({"value": 1}, "free-key")  # window rolled over
    assert result == {"echoed": 1}


async def test_success_emits_metrics_and_usage(
    deps: MiddlewareDeps, metrics: PrometheusMetrics, usage_sink: RecordingUsageSink
) -> None:
    tool = make_tool(cost_units=2)
    execute = build_tool_executor(tool, deps)

    await execute({"value": 9}, "free-key")

    assert usage_sink.events[-1].success is True
    assert usage_sink.events[-1].cost_units == 2
    assert (
        _metric_value(
            metrics, "mcp_tool_calls_total", {"tool": "echo", "tier": "free", "status": "success"}
        )
        == 1.0
    )


async def test_handler_exception_reraises_and_records_error(
    deps: MiddlewareDeps, metrics: PrometheusMetrics, usage_sink: RecordingUsageSink
) -> None:
    async def boom(args: EchoInput, ctx: ToolContext) -> dict[str, int]:
        raise ValueError("upstream exploded")

    tool = make_tool(handler=boom)
    execute = build_tool_executor(tool, deps)

    with pytest.raises(ValueError, match="upstream exploded"):
        await execute({"value": 1}, "free-key")

    assert usage_sink.events[-1].success is False
    assert usage_sink.events[-1].error_type == "ValueError"
    assert (
        _metric_value(
            metrics, "mcp_tool_calls_total", {"tool": "echo", "tier": "free", "status": "error"}
        )
        == 1.0
    )


async def test_failed_handler_result_not_cached(deps: MiddlewareDeps) -> None:
    calls = {"n": 0}

    async def flaky(args: EchoInput, ctx: ToolContext) -> dict[str, int]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first call fails")
        return {"echoed": args.value}

    tool = make_tool(handler=flaky, cache_ttl_ms=60_000)
    execute = build_tool_executor(tool, deps)

    with pytest.raises(RuntimeError):
        await execute({"value": 1}, "premium-key")
    # second call must re-run the handler (nothing was cached on failure)
    result = await execute({"value": 1}, "premium-key")
    assert result == {"echoed": 1}
    assert calls["n"] == 2


async def test_input_validated_before_handler(deps: MiddlewareDeps) -> None:
    ran = {"handler": False}

    async def handler(args: EchoInput, ctx: ToolContext) -> dict[str, int]:
        ran["handler"] = True
        return {"echoed": args.value}

    tool = make_tool(handler=handler)
    execute = build_tool_executor(tool, deps)

    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        await execute({"value": "not-an-int"}, "free-key")

    assert ran["handler"] is False


async def test_context_carries_request_id_account_and_logger(deps: MiddlewareDeps) -> None:
    seen: dict[str, object] = {}

    async def capture(args: EchoInput, ctx: ToolContext) -> dict[str, int]:
        seen["request_id"] = ctx.request_id
        seen["owner"] = ctx.account.owner
        seen["api_key"] = ctx.api_key
        seen["has_log"] = ctx.log is not None
        seen["has_resilient"] = ctx.resilient is not None
        return {"echoed": args.value}

    tool = make_tool(handler=capture)
    execute = build_tool_executor(tool, deps)

    await execute({"value": 1}, "premium-key")

    assert isinstance(seen["request_id"], str) and seen["request_id"]
    assert seen["owner"] == "premium-user"
    assert seen["api_key"] == "premium-key"
    assert seen["has_log"] is True
    assert seen["has_resilient"] is True


async def test_cache_key_shared_across_api_keys_for_same_args(deps: MiddlewareDeps) -> None:
    calls = {"n": 0}

    async def counting_handler(args: EchoInput, ctx: ToolContext) -> dict[str, int]:
        calls["n"] += 1
        return {"echoed": args.value}

    tool = make_tool(handler=counting_handler, cache_ttl_ms=60_000)
    execute = build_tool_executor(tool, deps)

    await execute({"value": 42}, "premium-key")  # miss, caches globally
    await execute({"value": 42}, "free-key")  # different caller, same args -> hit

    assert calls["n"] == 1


def test_build_cache_key_is_arg_order_independent() -> None:
    a = build_cache_key("t", {"x": 1, "y": 2})
    b = build_cache_key("t", {"y": 2, "x": 1})
    assert a == b
