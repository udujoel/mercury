"""
Weather ingestion — Open-Meteo, keyless.

Open-Meteo needs no API key, so the LIVE path genuinely works today with no
credentials configured anywhere (see docs/spikes and CLAUDE.md environment
notes). This is the one ingestion source with no OAuth dependency.

CLAUDE.md rule 7 — graceful degradation: a weather failure (network error,
timeout, malformed response, anything) returns `None` rather than raising.
The briefing continues without the weather segment. Never let a downed
weather API take down the whole generation run.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from app.core.schemas import Weather

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_S = 10.0

# WMO Weather interpretation codes (WW), per Open-Meteo docs, mapped to
# plain language for the script generation prompts.
WMO_CODES: dict[int, str] = {
    0: "clear sky",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


def wmo_to_condition(code: int | None) -> str:
    if code is None:
        return "unknown"
    return WMO_CODES.get(code, "unknown")


def _response_to_weather(data: dict[str, Any]) -> Weather:
    current = data.get("current", {})
    daily = data.get("daily", {})
    hourly = data.get("hourly", {})

    temperature_c = round(current.get("temperature_2m", 0.0))
    condition = wmo_to_condition(current.get("weather_code"))

    daily_max = daily.get("temperature_2m_max") or [temperature_c]
    daily_min = daily.get("temperature_2m_min") or [temperature_c]
    high_c = round(daily_max[0])
    low_c = round(daily_min[0])

    hourly_times = hourly.get("time", [])
    hourly_temps = hourly.get("temperature_2m", [])
    hourly_codes = hourly.get("weather_code", [])
    hourly_out = [
        {
            "time": t,
            "temperature_c": round(temp),
            "condition": wmo_to_condition(code),
        }
        for t, temp, code in zip(hourly_times, hourly_temps, hourly_codes, strict=False)
    ]

    location_name = data.get("location_name") or "your location"

    return Weather(
        temperature_c=temperature_c,
        condition=condition,
        high_c=high_c,
        low_c=low_c,
        hourly=hourly_out,
        location_name=location_name,
    )


def _load_fixture(fixture: Path) -> dict[str, Any]:
    return json.loads(fixture.read_text())


async def _fetch_live(lat: float, lon: float) -> dict[str, Any]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code",
        "hourly": "temperature_2m,weather_code",
        "timezone": "auto",
        "forecast_days": 1,
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        resp = await client.get(OPEN_METEO_URL, params=params)
        resp.raise_for_status()
        return resp.json()


async def fetch_weather(
    *, lat: float, lon: float, fixture: Path | None = None
) -> Weather | None:
    """Fetch current + today's weather from Open-Meteo.

    fixture=<path> reads that JSON file instead of hitting the network.
    fixture=None hits the live, keyless Open-Meteo API.

    Per CLAUDE.md rule 7, any failure (network, timeout, malformed
    response) is caught and logged; this function returns None rather than
    raising so a weather outage never fails the whole briefing.
    """
    try:
        if fixture is not None:
            data = _load_fixture(fixture)
        else:
            data = await _fetch_live(lat, lon)
        return _response_to_weather(data)
    except Exception:
        logger.warning(
            "weather fetch failed; briefing will ship without it", exc_info=True
        )
        return None
