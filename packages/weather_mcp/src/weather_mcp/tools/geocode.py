"""geocode_place — resolve a place name to coordinates via Open-Meteo (keyless, free)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mcp_platform_core import ToolContext, ToolDefinition
from weather_mcp.lib import WeatherLib

_DAY_MS = 24 * 60 * 60 * 1000


class GeocodeInput(BaseModel):
    place: str = Field(description="Place name to look up, e.g. 'Berlin' or 'Paris, France'.")
    count: int = Field(default=1, ge=1, le=10, description="Max number of matches to return.")


def make_geocode_tool(lib: WeatherLib) -> ToolDefinition:
    async def handler(args: GeocodeInput, ctx: ToolContext) -> dict[str, Any]:
        return await ctx.resilient.call(
            "open-meteo-geocoding", lambda: lib.geocode(args.place, args.count)
        )

    return ToolDefinition(
        name="geocode_place",
        description=(
            "Resolve a place name (city, town, landmark) to latitude/longitude and country. "
            "Use this first to get coordinates for the weather tools."
        ),
        input_model=GeocodeInput,
        min_tier="free",
        cost_units=1,
        cache_ttl_ms=_DAY_MS,
        handler=handler,
    )
