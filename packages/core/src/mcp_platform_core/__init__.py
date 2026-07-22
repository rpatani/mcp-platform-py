"""Public API surface of mcp-platform-core — a versioned contract (SemVer, see CLAUDE.md §4)."""

from mcp_platform_core.middleware import (
    InMemoryKeyStore,
    InMemoryRateLimiter,
    InMemoryResponseCache,
    LoggingUsageSink,
    MiddlewareDeps,
    ToolExecutor,
    build_tool_executor,
)
from mcp_platform_core.observability.logger import create_logger
from mcp_platform_core.observability.metrics import (
    Metrics,
    NullMetrics,
    OtelMetrics,
    PrometheusMetrics,
    build_metrics,
)
from mcp_platform_core.registry import ToolRegistry
from mcp_platform_core.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    ResilientCaller,
    UpstreamTimeoutError,
)
from mcp_platform_core.types import (
    TIER_RANK,
    ApiKeyRecord,
    KeyStore,
    RateLimiter,
    RateLimitError,
    RateLimitResult,
    ResponseCache,
    Tier,
    TierError,
    ToolContext,
    ToolDefinition,
    UsageEvent,
    UsageSink,
)

__version__ = "0.1.0"

__all__ = [
    "TIER_RANK",
    "ApiKeyRecord",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
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
    "build_metrics",
    "build_tool_executor",
    "create_logger",
]
