"""One resolver for the user's timezone, and every caller going through it.

Five places used to resolve USER_TIMEZONE themselves. They disagreed: two
logged a bad zone name, two fell back to UTC silently, and calendar
ingestion did not fall back at all, so one typo failed the calendar source
while the rest of the briefing quietly dated itself in UTC. Each of this
project's date bugs came from a layer deciding "today" on its own.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.core.config import get_settings
from app.core.tz import resolve_tz, user_tz

APP_DIR = Path(__file__).resolve().parents[1] / "app"
CALENDAR_FIXTURE = Path(__file__).parent / "fixtures" / "calendar_events.json"
BAD_ZONE = "Europe/Lisbn"


def _utc_days() -> set[str]:
    # Both sides of a call, so a run that straddles UTC midnight still passes.
    return {datetime.now(UTC).strftime("%Y-%m-%d")}


def test_a_real_zone_resolves_to_itself():
    assert resolve_tz("Europe/Lisbon") == ZoneInfo("Europe/Lisbon")


@pytest.mark.parametrize(
    "name",
    [
        BAD_ZONE,  # ZoneInfoNotFoundError
        "",  # ValueError, which is why the resolver cannot catch only the former
        "Europe/",  # ValueError
        "../etc/passwd",  # ValueError
    ],
)
def test_a_bad_zone_falls_back_to_utc_and_says_so(name, caplog):
    with caplog.at_level("ERROR", logger="app.core.tz"):
        tz = resolve_tz(name)

    assert tz == UTC
    assert [r.name for r in caplog.records] == ["app.core.tz"]
    assert repr(name) in caplog.records[0].getMessage()


def test_the_fallback_needs_no_zone_database(monkeypatch, caplog):
    """With no system zone database and no `tzdata` package (a slim
    container, say), every `ZoneInfo` call fails, including
    `ZoneInfo("UTC")`. The fallback must not be one of them, or the module
    fails at import and takes the app down instead of degrading."""
    import importlib
    import zoneinfo

    import app.core.tz as tz_module

    def _no_database(key):
        raise zoneinfo.ZoneInfoNotFoundError(f"No time zone found with key {key}")

    # A scoped context, not monkeypatch.undo(): undo() would also reverse
    # the conftest autouse patches that scrub credentials.
    try:
        with monkeypatch.context() as m:
            m.setattr(zoneinfo, "ZoneInfo", _no_database)
            reloaded = importlib.reload(tz_module)
            with caplog.at_level("ERROR", logger="app.core.tz"):
                tz = reloaded.resolve_tz("Europe/Lisbon")
    finally:
        importlib.reload(tz_module)

    assert tz.utcoffset(None) == timedelta(0)
    assert caplog.records


def test_user_tz_reads_settings(monkeypatch):
    monkeypatch.setenv("USER_TIMEZONE", "America/Los_Angeles")
    get_settings.cache_clear()

    assert user_tz() == ZoneInfo("America/Los_Angeles")


# --- every caller goes through the resolver --------------------------------
#
# Proven by where the error is logged from: a site that still resolves the
# zone itself either logs under its own logger, or not at all.


@pytest.fixture
def bad_zone(monkeypatch):
    monkeypatch.setenv("USER_TIMEZONE", BAD_ZONE)
    get_settings.cache_clear()
    return BAD_ZONE


def _core_errors(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "app.core.tz" and r.levelname == "ERROR"
    ]


def test_cli_today_goes_through_the_resolver(bad_zone, caplog):
    from app.cli import _today_briefing_id

    before = _utc_days()
    day = _today_briefing_id(bad_zone)

    assert day in before | _utc_days()
    assert _core_errors(caplog)


def test_scheduler_target_id_goes_through_the_resolver(bad_zone, caplog):
    from app.scheduler.cron import _target_briefing_id

    before = _utc_days()
    day = _target_briefing_id(bad_zone)

    assert day in before | _utc_days()
    assert _core_errors(caplog)


def test_api_today_goes_through_the_resolver(bad_zone, caplog):
    from app.api.briefing import _today_briefing_id

    before = _utc_days()
    day = _today_briefing_id()

    assert day in before | _utc_days()
    assert _core_errors(caplog)


def test_pipeline_goes_through_the_resolver(bad_zone, caplog):
    from app.pipeline.generate import _parse_briefing_date

    greeted = _parse_briefing_date(
        "2026-03-30", now=datetime(2026, 3, 29, 23, 30, tzinfo=UTC)
    )

    assert greeted.tzinfo == UTC
    assert _core_errors(caplog)


async def test_calendar_degrades_to_utc_instead_of_failing_the_source(bad_zone, caplog):
    """A typo used to raise ZoneInfoNotFoundError out of `fetch_events`, so
    the calendar segment vanished while every other date in the briefing
    fell back to UTC. Now it falls back with them, and stays in the
    briefing (CLAUDE.md rule 7)."""
    from app.ingestion import calendar

    events = await calendar.fetch_events(fixture=CALENDAR_FIXTURE)

    assert events
    # The zone object itself, not the offset: Lisbon is UTC+0 all winter, so
    # an offset check would pass for the wrong reason half the year.
    assert {e.start.tzinfo for e in events} == {UTC}
    assert _core_errors(caplog)


def test_nothing_outside_the_resolver_constructs_a_zoneinfo():
    """A guard, not a convention. The copies this file replaces each began
    as a reasonable two-line `ZoneInfo(settings.user_timezone)`. A new one
    fails here and points at `app.core.tz`."""
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        if path == APP_DIR / "core" / "tz.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name | ast.Attribute)
                and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
                == "ZoneInfo"
            ):
                offenders.append(f"{path.relative_to(APP_DIR)}:{node.lineno}")

    assert not offenders, (
        "resolve the user's zone with app.core.tz.user_tz() / resolve_tz() "
        f"instead of constructing ZoneInfo directly: {offenders}"
    )
