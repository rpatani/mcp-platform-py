"""Backward-compatibility guard for the public API (CLAUDE.md §4).

External app repos pin core by git tag, so anything removed or narrowed here
breaks them at import or call time. These tests fail loudly on an accidental
break; a *deliberate* break requires a major bump and editing the frozen lists
below.
"""

from __future__ import annotations

import inspect

import mcp_platform_core
from mcp_platform_core import create_logger, run_http, run_stdio

# The exact export surface shipped in mcp-platform-core-v0.1.0.
V0_1_0_EXPORTS = frozenset(
    {
        "TIER_RANK",
        "ApiKeyRecord",
        "CircuitBreaker",
        "CircuitOpenError",
        "CircuitState",
        "CoreConfig",
        "InMemoryKeyStore",
        "InMemoryRateLimiter",
        "InMemoryResponseCache",
        "KeyStore",
        "LoggingUsageSink",
        "Metrics",
        "MiddlewareDeps",
        "NullMetrics",
        "OtelMetrics",
        "PrometheusMetrics",
        "RateLimitError",
        "RateLimitResult",
        "RateLimiter",
        "ResilientCaller",
        "ResponseCache",
        "Tier",
        "TierError",
        "ToolContext",
        "ToolDefinition",
        "ToolExecutor",
        "ToolRegistry",
        "UpstreamTimeoutError",
        "UsageEvent",
        "UsageSink",
        "__version__",
        "build_mcp_server",
        "build_metrics",
        "build_tool_executor",
        "create_logger",
        "load_key_store",
        "run_http",
        "run_stdio",
    }
)


def test_no_v0_1_0_export_was_removed() -> None:
    missing = V0_1_0_EXPORTS - set(mcp_platform_core.__all__)
    assert not missing, f"public API regression — removed: {sorted(missing)}"


def test_every_export_actually_resolves() -> None:
    unresolved = [
        name for name in mcp_platform_core.__all__ if not hasattr(mcp_platform_core, name)
    ]
    assert not unresolved, f"declared in __all__ but not importable: {unresolved}"


def test_run_http_accepts_the_v0_1_0_call_shape() -> None:
    """An app written against v0.1.0 passes no log_level; it must still bind."""
    params = inspect.signature(run_http).parameters
    for name in ("host", "port", "mcp_path", "metrics", "metrics_port", "log"):
        assert name in params, f"run_http lost parameter {name!r}"
    # Anything added since must be optional, or old call sites raise TypeError.
    assert params["log_level"].default is not inspect.Parameter.empty


def test_run_stdio_and_create_logger_signatures_are_unchanged() -> None:
    stdio_params = inspect.signature(run_stdio).parameters
    for name in ("api_key", "log"):
        assert name in stdio_params

    logger_params = inspect.signature(create_logger).parameters
    for name in ("service", "version", "transport", "level"):
        assert name in logger_params
    # level stayed optional — v0.1.0 callers may omit it.
    assert logger_params["level"].default == "info"
