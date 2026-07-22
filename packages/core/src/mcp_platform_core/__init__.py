"""Public API surface of mcp-platform-core — a versioned contract (SemVer, see CLAUDE.md §4)."""

from mcp_platform_core.registry import ToolRegistry
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
    "KeyStore",
    "RateLimitError",
    "RateLimitResult",
    "RateLimiter",
    "ResponseCache",
    "Tier",
    "TierError",
    "ToolContext",
    "ToolDefinition",
    "ToolRegistry",
    "UsageEvent",
    "UsageSink",
    "__version__",
]
