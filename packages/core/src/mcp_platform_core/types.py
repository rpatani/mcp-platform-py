"""Core data contracts shared by every layer: registry, middleware, resilience, transports.

Everything here is re-exported from ``mcp_platform_core`` and is part of the
versioned public API (CLAUDE.md §4) — do not break a signature without a
major version bump.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    import structlog

    from mcp_platform_core.resilience import ResilientCaller

Tier = Literal["free", "premium", "enterprise"]
TIER_RANK: dict[Tier, int] = {"free": 0, "premium": 1, "enterprise": 2}


@dataclass(frozen=True)
class ApiKeyRecord:
    api_key: str
    owner: str
    tier: Tier
    rate_limit_per_minute: int


@dataclass(frozen=True)
class UsageEvent:
    request_id: str
    tool: str
    owner: str
    tier: Tier
    cost_units: int
    success: bool
    cache_hit: bool
    duration_ms: float
    error_type: str | None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class KeyStore(Protocol):
    async def resolve(self, api_key: str | None) -> ApiKeyRecord: ...


class UsageSink(Protocol):
    async def record(self, event: UsageEvent) -> None: ...


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_s: float | None


class RateLimiter(Protocol):
    async def check_and_increment(self, key: str, limit_per_minute: int) -> RateLimitResult: ...


class ResponseCache(Protocol):
    async def get(self, key: str) -> Any | None: ...

    async def set(self, key: str, value: Any, ttl_ms: int) -> None: ...


@dataclass
class ToolContext:
    request_id: str
    account: ApiKeyRecord
    api_key: str | None
    resilient: ResilientCaller
    log: structlog.BoundLogger


class ToolDefinition(BaseModel):
    """Transport-agnostic tool contract — the Pydantic analogue of the TS zod-shape handler."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    input_model: type[BaseModel]
    min_tier: Tier = "free"
    cost_units: int = 1
    cache_ttl_ms: int | None = None
    handler: Callable[[BaseModel, ToolContext], Awaitable[Any]]


class TierError(Exception):
    def __init__(self, required: Tier, actual: Tier) -> None:
        super().__init__(f"tool requires tier {required!r}, account has tier {actual!r}")
        self.required = required
        self.actual = actual


class RateLimitError(Exception):
    def __init__(self, retry_after_s: float | None = None) -> None:
        super().__init__("rate limit exceeded")
        self.retry_after_s = retry_after_s
