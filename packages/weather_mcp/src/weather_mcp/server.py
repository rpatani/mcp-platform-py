"""weather-mcp entrypoint: wire the four tools + core deps + transport.

Run with ``uv run weather-mcp`` (see CLAUDE.md §9). Transport, keys, and metrics
are all driven by env via CoreConfig; the free tools need no secrets.
"""

from __future__ import annotations

import asyncio
import os

from mcp_platform_core import (
    CoreConfig,
    InMemoryRateLimiter,
    InMemoryResponseCache,
    LoggingUsageSink,
    MiddlewareDeps,
    ResilientCaller,
    ToolRegistry,
    build_mcp_server,
    build_metrics,
    create_logger,
    load_key_store,
    run_http,
    run_stdio,
)
from weather_mcp.lib import WeatherLib
from weather_mcp.tools.current import make_current_weather_tool, make_premium_weather_tool
from weather_mcp.tools.forecast import make_forecast_tool
from weather_mcp.tools.geocode import make_geocode_tool

SERVICE_NAME = "weather-mcp"
SERVICE_VERSION = "0.1.0"


def main() -> None:
    config = CoreConfig()
    log = create_logger(
        service=SERVICE_NAME,
        version=SERVICE_VERSION,
        transport=config.transport,
        level=config.log_level,
    )
    metrics = build_metrics(config.metrics_backend, enabled=config.metrics_enabled)
    key_store = load_key_store(config.keys_file)
    resilient = ResilientCaller(
        metrics=metrics,
        timeout_s=config.upstream_timeout_s,
        retries=config.upstream_retries,
        breaker_threshold=config.breaker_threshold,
        breaker_cooldown_s=config.breaker_cooldown_s,
    )
    deps = MiddlewareDeps(
        key_store=key_store,
        rate_limiter=InMemoryRateLimiter(),
        cache=InMemoryResponseCache(),
        usage_sink=LoggingUsageSink(log),
        metrics=metrics,
        logger=log,
        resilient=resilient,
    )

    # OPENWEATHERMAP_API_KEY is an app-level secret, read here — never in core.
    lib = WeatherLib(owm_api_key=os.environ.get("OPENWEATHERMAP_API_KEY"))

    registry = ToolRegistry()
    registry.register_all(
        [
            make_geocode_tool(lib),
            make_current_weather_tool(lib),
            make_forecast_tool(lib),
            make_premium_weather_tool(lib),
        ]
    )

    server = build_mcp_server(
        name=SERVICE_NAME, version=SERVICE_VERSION, registry=registry, deps=deps
    )

    async def _serve() -> None:
        try:
            if config.transport == "stdio":
                await run_stdio(server, api_key=config.api_key, log=log)
            else:
                await run_http(
                    server,
                    port=config.http_port,
                    mcp_path=config.http_path,
                    metrics=metrics,
                    metrics_port=config.metrics_port,
                    log=log,
                    log_level=config.log_level,
                )
        finally:
            await lib.aclose()

    asyncio.run(_serve())


if __name__ == "__main__":
    main()
