"""
Tests for app/ingestion/weather.py.

Offline path against backend/tests/fixtures/weather_openmeteo.json, plus
proof that any failure (bad host, malformed JSON, timeout) degrades to
`None` rather than raising — CLAUDE.md rule 7.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from app.core.schemas import Weather
from app.ingestion import weather

FIXTURE = Path(__file__).parent / "fixtures" / "weather_openmeteo.json"


async def test_fetch_weather_offline_returns_weather():
    result = await weather.fetch_weather(lat=40.64, lon=-8.65, fixture=FIXTURE)
    assert isinstance(result, Weather)
    assert result.temperature_c == 16
    assert result.condition == "mostly clear"
    assert result.high_c == 24
    assert result.low_c == 14
    assert result.location_name
    assert len(result.hourly) == 24


def test_wmo_code_mapping_known_codes():
    assert weather.wmo_to_condition(0) == "clear sky"
    assert weather.wmo_to_condition(61) == "slight rain"
    assert weather.wmo_to_condition(95) == "thunderstorm"


def test_wmo_code_mapping_unknown_code_is_safe():
    assert weather.wmo_to_condition(9999) == "unknown"


def test_wmo_code_mapping_none_is_safe():
    assert weather.wmo_to_condition(None) == "unknown"


async def test_fetch_weather_missing_fixture_returns_none():
    """A fixture path that doesn't exist must degrade to None, never raise
    into the caller — a generation run must be able to proceed without
    weather."""
    result = await weather.fetch_weather(
        lat=0, lon=0, fixture=Path("/nonexistent/weather.json")
    )
    assert result is None


async def test_fetch_weather_live_network_failure_returns_none(monkeypatch):
    async def _boom(lat, lon):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(weather, "_fetch_live", _boom)
    result = await weather.fetch_weather(lat=40.64, lon=-8.65, fixture=None)
    assert result is None


async def test_fetch_weather_malformed_fixture_returns_none(tmp_path):
    bad_fixture = tmp_path / "bad_weather.json"
    bad_fixture.write_text("{not valid json")
    result = await weather.fetch_weather(lat=0, lon=0, fixture=bad_fixture)
    assert result is None
