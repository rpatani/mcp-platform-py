from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_platform_core.config import CoreConfig, load_key_store
from mcp_platform_core.middleware import ANONYMOUS_FREE


def test_defaults_match_claude_md() -> None:
    config = CoreConfig(_env_file=None)  # type: ignore[call-arg]

    assert config.transport == "stdio"
    assert config.http_port == 8080
    assert config.http_path == "/mcp"
    assert config.metrics_backend == "prometheus"
    assert config.metrics_port == 9464
    assert config.upstream_timeout_s == 4.0
    assert config.upstream_retries == 2
    assert config.breaker_threshold == 5
    assert config.breaker_cooldown_s == 30.0


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    monkeypatch.setenv("MCP_HTTP_PORT", "9090")
    monkeypatch.setenv("MCP_UPSTREAM_RETRIES", "5")

    config = CoreConfig(_env_file=None)  # type: ignore[call-arg]

    assert config.transport == "http"
    assert config.http_port == 9090
    assert config.upstream_retries == 5


def test_metrics_enabled_defaults_to_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_METRICS_ENABLED", raising=False)

    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    assert CoreConfig(_env_file=None).metrics_enabled is False  # type: ignore[call-arg]

    monkeypatch.setenv("MCP_TRANSPORT", "http")
    assert CoreConfig(_env_file=None).metrics_enabled is True  # type: ignore[call-arg]


def test_metrics_enabled_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    monkeypatch.setenv("MCP_METRICS_ENABLED", "false")

    assert CoreConfig(_env_file=None).metrics_enabled is False  # type: ignore[call-arg]


async def test_load_key_store_none_is_all_anonymous() -> None:
    store = load_key_store(None)

    record = await store.resolve("whatever")
    assert record is ANONYMOUS_FREE


async def test_load_key_store_parses_file(tmp_path: Path) -> None:
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(
        json.dumps(
            {
                "abc123": {"owner": "acme", "tier": "premium", "rateLimitPerMinute": 120},
            }
        )
    )

    store = load_key_store(keys_file)

    record = await store.resolve("abc123")
    assert record.owner == "acme"
    assert record.tier == "premium"
    assert record.rate_limit_per_minute == 120


async def test_load_key_store_unknown_key_falls_back_to_anonymous(tmp_path: Path) -> None:
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(
        json.dumps({"abc123": {"owner": "acme", "tier": "free", "rateLimitPerMinute": 60}})
    )

    store = load_key_store(keys_file)

    record = await store.resolve("not-in-file")
    assert record is ANONYMOUS_FREE
