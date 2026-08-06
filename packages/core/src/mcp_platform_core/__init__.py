"""Public API surface of mcp-platform-core — a versioned contract (SemVer, see CLAUDE.md §4)."""

from mcp_platform_core.config import CoreConfig, load_key_store
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
from mcp_platform_core.observability.redaction import REDACTED, fingerprint, redact, scrub_text
from mcp_platform_core.registry import ToolRegistry
from mcp_platform_core.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    ResilientCaller,
    UpstreamTimeoutError,
)
from mcp_platform_core.server import build_mcp_server
from mcp_platform_core.transports.http import run_http, run_stdio
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

__version__ = "0.2.0"

__all__ = [
    "REDACTED",
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
    "fingerprint",
    "load_key_store",
    "redact",
    "run_http",
    "run_stdio",
    "scrub_text",
]
