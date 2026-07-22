"""CoreConfig (pydantic-settings) + key-store loader.

CoreConfig stays domain-agnostic: it holds only the platform env surface from
CLAUDE.md §8. App-specific secrets (e.g. OPENWEATHERMAP_API_KEY) are read by the
app, never by core — "never put app-specific logic in core" (CLAUDE.md §3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from mcp_platform_core.middleware import InMemoryKeyStore
from mcp_platform_core.types import ApiKeyRecord, Tier

Transport = Literal["stdio", "http"]
MetricsBackend = Literal["prometheus", "otel"]


class CoreConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore", env_file=None)

    transport: Transport = Field(default="stdio", alias="MCP_TRANSPORT")
    http_port: int = Field(default=8080, alias="MCP_HTTP_PORT")
    http_path: str = Field(default="/mcp", alias="MCP_HTTP_PATH")
    keys_file: Path | None = Field(default=None, alias="MCP_KEYS_FILE")
    api_key: str | None = Field(default=None, alias="MCP_API_KEY")
    log_level: str = Field(default="info", alias="LOG_LEVEL")

    metrics_enabled_override: bool | None = Field(default=None, alias="MCP_METRICS_ENABLED")
    metrics_backend: MetricsBackend = Field(default="prometheus", alias="MCP_METRICS_BACKEND")
    metrics_port: int = Field(default=9464, alias="MCP_METRICS_PORT")

    upstream_timeout_s: float = Field(default=4.0, alias="MCP_UPSTREAM_TIMEOUT_S")
    upstream_retries: int = Field(default=2, alias="MCP_UPSTREAM_RETRIES")
    breaker_threshold: int = Field(default=5, alias="MCP_BREAKER_THRESHOLD")
    breaker_cooldown_s: float = Field(default=30.0, alias="MCP_BREAKER_COOLDOWN_S")

    @property
    def metrics_enabled(self) -> bool:
        """Explicit override wins; otherwise metrics are on for HTTP, off for stdio."""
        if self.metrics_enabled_override is not None:
            return self.metrics_enabled_override
        return self.transport == "http"


def load_key_store(path: Path | None) -> InMemoryKeyStore:
    """Parse a keys JSON file (apiKey -> {owner, tier, rateLimitPerMinute}).

    ``path=None`` yields an empty store — every request is anonymous/free, which
    is exactly how the keyless demo runs with zero configuration. Unknown keys
    fall through to the anonymous free tier at resolve time (not an error).
    """
    if path is None:
        return InMemoryKeyStore()

    raw = json.loads(Path(path).read_text())
    records: dict[str, ApiKeyRecord] = {}
    for api_key, entry in raw.items():
        tier: Tier = entry["tier"]
        records[api_key] = ApiKeyRecord(
            api_key=api_key,
            owner=entry["owner"],
            tier=tier,
            rate_limit_per_minute=int(entry["rateLimitPerMinute"]),
        )
    return InMemoryKeyStore(records)
