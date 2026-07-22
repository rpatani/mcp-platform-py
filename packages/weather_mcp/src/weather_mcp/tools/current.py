"""Current-conditions tools: get_current_weather (free) and get_weather_premium (premium)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mcp_platform_core import ToolContext, ToolDefinition
from weather_mcp.lib import WeatherLib

_FIVE_MIN_MS = 5 * 60 * 1000


class CurrentWeatherInput(BaseModel):
    latitude: float = Field(ge=-90, le=90, description="Latitude in decimal degrees.")
    longitude: float = Field(ge=-180, le=180, description="Longitude in decimal degrees.")


class PremiumWeatherInput(BaseModel):
    latitude: float = Field(ge=-90, le=90, description="Latitude in decimal degrees.")
    longitude: float = Field(ge=-180, le=180, description="Longitude in decimal degrees.")


def make_current_weather_tool(lib: WeatherLib) -> ToolDefinition:
    async def handler(args: CurrentWeatherInput, ctx: ToolContext) -> dict[str, Any]:
        return await ctx.resilient.call(
            "open-meteo", lambda: lib.current_weather(args.latitude, args.longitude)
        )

    return ToolDefinition(
        name="get_current_weather",
        description=(
            "Get current temperature, wind speed and weather code for a latitude/longitude "
            "via Open-Meteo. Free and keyless."
        ),
        input_model=CurrentWeatherInput,
        min_tier="free",
        cost_units=1,
        cache_ttl_ms=_FIVE_MIN_MS,
        handler=handler,
    )


def make_premium_weather_tool(lib: WeatherLib) -> ToolDefinition:
    async def handler(args: PremiumWeatherInput, ctx: ToolContext) -> dict[str, Any]:
        return await ctx.resilient.call(
            "openweathermap", lambda: lib.premium_onecall(args.latitude, args.longitude)
        )

    return ToolDefinition(
        name="get_weather_premium",
        description=(
            "Get enriched current conditions (feels-like, humidity, textual description) from "
            "OpenWeatherMap One Call. Premium tier; requires OPENWEATHERMAP_API_KEY."
        ),
        input_model=PremiumWeatherInput,
        min_tier="premium",
        cost_units=3,
        cache_ttl_ms=_FIVE_MIN_MS,
        handler=handler,
    )
