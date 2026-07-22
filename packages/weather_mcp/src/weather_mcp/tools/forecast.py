"""get_forecast — multi-day daily forecast via Open-Meteo (keyless, free)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mcp_platform_core import ToolContext, ToolDefinition
from weather_mcp.lib import WeatherLib

_ONE_HOUR_MS = 60 * 60 * 1000


class ForecastInput(BaseModel):
    latitude: float = Field(ge=-90, le=90, description="Latitude in decimal degrees.")
    longitude: float = Field(ge=-180, le=180, description="Longitude in decimal degrees.")
    days: int = Field(default=3, ge=1, le=16, description="Number of forecast days (1-16).")


def make_forecast_tool(lib: WeatherLib) -> ToolDefinition:
    async def handler(args: ForecastInput, ctx: ToolContext) -> dict[str, Any]:
        return await ctx.resilient.call(
            "open-meteo", lambda: lib.forecast(args.latitude, args.longitude, args.days)
        )

    return ToolDefinition(
        name="get_forecast",
        description=(
            "Get a daily forecast (max/min temperature and weather code) for a latitude/longitude "
            "over the next N days via Open-Meteo. Free and keyless."
        ),
        input_model=ForecastInput,
        min_tier="free",
        cost_units=1,
        cache_ttl_ms=_ONE_HOUR_MS,
        handler=handler,
    )
