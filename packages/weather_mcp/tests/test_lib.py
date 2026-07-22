"""Light tests for the standalone WeatherLib (respx-mocked, no live network)."""

from __future__ import annotations

import httpx
import pytest
import respx

from weather_mcp.lib import (
    FORECAST_URL,
    GEOCODING_URL,
    OWM_ONECALL_URL,
    MissingApiKeyError,
    WeatherLib,
)


@respx.mock
async def test_geocode_normalizes_matches() -> None:
    respx.get(GEOCODING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "name": "Berlin",
                        "latitude": 52.52,
                        "longitude": 13.41,
                        "country": "Germany",
                        "admin1": "State of Berlin",
                        "timezone": "Europe/Berlin",
                    }
                ]
            },
        )
    )

    async with WeatherLib() as lib:
        result = await lib.geocode("Berlin")

    assert result["query"] == "Berlin"
    assert result["matches"][0]["latitude"] == 52.52
    assert result["matches"][0]["country"] == "Germany"


@respx.mock
async def test_geocode_empty_results() -> None:
    respx.get(GEOCODING_URL).mock(return_value=httpx.Response(200, json={}))

    async with WeatherLib() as lib:
        result = await lib.geocode("Nowhereville")

    assert result["matches"] == []


@respx.mock
async def test_current_weather_shape() -> None:
    respx.get(FORECAST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "current": {
                    "time": "2026-07-22T16:00",
                    "temperature_2m": 18.2,
                    "wind_speed_10m": 13.5,
                    "weather_code": 80,
                }
            },
        )
    )

    async with WeatherLib() as lib:
        result = await lib.current_weather(52.52, 13.41)

    assert result["temperature_c"] == 18.2
    assert result["wind_speed_kmh"] == 13.5
    assert result["weather_code"] == 80


@respx.mock
async def test_forecast_zips_daily_arrays() -> None:
    respx.get(FORECAST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "daily": {
                    "time": ["2026-07-22", "2026-07-23"],
                    "temperature_2m_max": [19.4, 21.0],
                    "temperature_2m_min": [13.1, 14.2],
                    "weather_code": [80, 95],
                }
            },
        )
    )

    async with WeatherLib() as lib:
        result = await lib.forecast(52.52, 13.41, days=2)

    assert len(result["days"]) == 2
    assert result["days"][0] == {
        "date": "2026-07-22",
        "temp_max_c": 19.4,
        "temp_min_c": 13.1,
        "weather_code": 80,
    }


async def test_premium_without_key_raises_missing_key() -> None:
    async with WeatherLib() as lib:
        with pytest.raises(MissingApiKeyError):
            await lib.premium_onecall(52.52, 13.41)


@respx.mock
async def test_premium_with_key_calls_owm() -> None:
    route = respx.get(OWM_ONECALL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "current": {
                    "temp": 18.0,
                    "feels_like": 17.0,
                    "humidity": 55,
                    "wind_speed": 3.2,
                    "weather": [{"description": "broken clouds"}],
                }
            },
        )
    )

    async with WeatherLib(owm_api_key="secret") as lib:
        result = await lib.premium_onecall(52.52, 13.41)

    assert route.called
    assert result["provider"] == "openweathermap"
    assert result["conditions"] == "broken clouds"
    assert result["humidity_pct"] == 55


@respx.mock
async def test_http_error_propagates() -> None:
    respx.get(FORECAST_URL).mock(return_value=httpx.Response(500))

    async with WeatherLib() as lib:
        with pytest.raises(httpx.HTTPStatusError):
            await lib.current_weather(0.0, 0.0)
