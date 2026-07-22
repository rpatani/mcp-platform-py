"""Embeddable weather client — standalone, no dependency on mcp_platform_core.

Usable directly from any Python app or notebook. The MCP tool handlers wrap
these calls in ``ctx.resilient.call(...)``; the resilience/tier/cache concerns
live in the platform, never here, so this stays a pure API client.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OWM_ONECALL_URL = "https://api.openweathermap.org/data/3.0/onecall"


class WeatherLibError(Exception):
    """Base error for the weather client."""


class MissingApiKeyError(WeatherLibError):
    """Raised when a premium call is made without an API key. Never retried."""


class WeatherLib:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        owm_api_key: str | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._owns_client = client is None
        self._owm_api_key = owm_api_key

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> WeatherLib:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def geocode(self, place: str, count: int = 1) -> dict[str, Any]:
        response = await self._client.get(GEOCODING_URL, params={"name": place, "count": count})
        response.raise_for_status()
        results = response.json().get("results") or []
        return {
            "query": place,
            "matches": [
                {
                    "name": r.get("name"),
                    "latitude": r.get("latitude"),
                    "longitude": r.get("longitude"),
                    "country": r.get("country"),
                    "admin1": r.get("admin1"),
                    "timezone": r.get("timezone"),
                }
                for r in results
            ],
        }

    async def current_weather(self, lat: float, lon: float) -> dict[str, Any]:
        response = await self._client.get(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,wind_speed_10m,weather_code",
            },
        )
        response.raise_for_status()
        current = response.json().get("current", {})
        return {
            "latitude": lat,
            "longitude": lon,
            "time": current.get("time"),
            "temperature_c": current.get("temperature_2m"),
            "wind_speed_kmh": current.get("wind_speed_10m"),
            "weather_code": current.get("weather_code"),
        }

    async def forecast(self, lat: float, lon: float, days: int = 3) -> dict[str, Any]:
        response = await self._client.get(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "forecast_days": days,
            },
        )
        response.raise_for_status()
        daily = response.json().get("daily", {})
        dates = daily.get("time", [])
        highs = daily.get("temperature_2m_max", [])
        lows = daily.get("temperature_2m_min", [])
        codes = daily.get("weather_code", [])
        return {
            "latitude": lat,
            "longitude": lon,
            "days": [
                {
                    "date": dates[i],
                    "temp_max_c": highs[i] if i < len(highs) else None,
                    "temp_min_c": lows[i] if i < len(lows) else None,
                    "weather_code": codes[i] if i < len(codes) else None,
                }
                for i in range(len(dates))
            ],
        }

    async def premium_onecall(self, lat: float, lon: float) -> dict[str, Any]:
        if not self._owm_api_key:
            raise MissingApiKeyError(
                "get_weather_premium requires OPENWEATHERMAP_API_KEY to be set"
            )
        response = await self._client.get(
            OWM_ONECALL_URL,
            params={
                "lat": lat,
                "lon": lon,
                "appid": self._owm_api_key,
                "units": "metric",
                "exclude": "minutely,hourly,alerts",
            },
        )
        response.raise_for_status()
        data = response.json()
        current = data.get("current", {})
        weather = (current.get("weather") or [{}])[0]
        return {
            "provider": "openweathermap",
            "latitude": lat,
            "longitude": lon,
            "temperature_c": current.get("temp"),
            "feels_like_c": current.get("feels_like"),
            "humidity_pct": current.get("humidity"),
            "wind_speed_ms": current.get("wind_speed"),
            "conditions": weather.get("description"),
        }
