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


def _on_utc_day(call):
    """`call()`, and whether its result is the UTC day.

    The day is sampled on both sides of the call, so a run that straddles
    UTC midnight still passes instead of flaking once a day.
    """
    before = datetime.now(UTC).strftime("%Y-%m-%d")
    day = call()
    after = datetime.now(UTC).strftime("%Y-%m-%d")
    return day in {before, after}


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

    assert _on_utc_day(lambda: _today_briefing_id(bad_zone))
    assert _core_errors(caplog)


def test_scheduler_target_id_goes_through_the_resolver(bad_zone, caplog):
    from app.scheduler.cron import _target_briefing_id

    assert _on_utc_day(lambda: _target_briefing_id(bad_zone))
    assert _core_errors(caplog)


def test_api_today_goes_through_the_resolver(bad_zone, caplog):
    from app.api.briefing import _today_briefing_id

    assert _on_utc_day(lambda: _today_briefing_id())
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


async def test_the_scheduler_still_starts_on_a_bad_zone(bad_zone, monkeypatch, caplog):
    """The trigger now gets `datetime.UTC`, not `ZoneInfo("UTC")`, when the
    zone is bad. A typo must cost the user their local wake time, not the
    scheduler: this is app startup, and nothing would run at all."""
    from app.scheduler import cron

    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    get_settings.cache_clear()
    try:
        scheduler = cron.start_scheduler(app=None)
        assert scheduler is not None
        job = scheduler.get_job(cron.JOB_ID)
        assert job is not None and job.next_run_time is not None
        assert job.trigger.timezone.utcoffset(None) == timedelta(0)
    finally:
        cron.shutdown_scheduler()
    assert _core_errors(caplog)


def test_the_watchdog_reads_the_hour_through_the_resolver():
    """The watchdog's morning window was the one resolver outside Python:
    `TZ="$USER_TIMEZONE" date`, which glibc silently reads as UTC when the
    zone is misspelt, with no log anywhere."""
    run = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "briefing-watchdog.yml"
    ).read_text()

    assert 'TZ="${USER_TIMEZONE' not in run
    assert "from app.core.tz import user_tz" in run


# --- the guard -------------------------------------------------------------

# Files allowed to construct a zone directly, each with the reason. A zone
# that is NOT the user's (an event's own `timeZone`, say) belongs here rather
# than in `resolve_tz`, whose fallback would report it as a USER_TIMEZONE
# problem and send whoever reads the log to the wrong setting.
ALLOWED: dict[str, str] = {
    "core/tz.py": "the resolver itself",
}


def _zone_constructions(source: str) -> list[int]:
    """Lines in `source` that build a zone from a name: `ZoneInfo(...)` under
    any alias, `zoneinfo.ZoneInfo(...)`, and `dateutil`'s `gettz(...)`."""
    tree = ast.parse(source)
    names = {"ZoneInfo", "gettz"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "zoneinfo",
            "dateutil.tz",
        }:
            names |= {a.asname for a in node.names if a.asname}
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id in names)
            or (isinstance(node.func, ast.Attribute) and node.func.attr in names)
        )
    ]


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("from zoneinfo import ZoneInfo\nZoneInfo('UTC')", [2]),
        ("import zoneinfo\nzoneinfo.ZoneInfo('UTC')", [2]),
        ("from zoneinfo import ZoneInfo as Zone\nZone('UTC')", [2]),
        ("from dateutil.tz import gettz\ngettz('UTC')", [2]),
        ("from dateutil import tz\ntz.gettz('UTC')", [2]),
        # A type annotation is not a construction.
        ("from zoneinfo import ZoneInfo\ndef f(tz: ZoneInfo): ...", []),
    ],
)
def test_the_guard_sees_every_way_to_build_a_zone(source, flagged):
    assert _zone_constructions(source) == flagged


def test_nothing_outside_the_resolver_constructs_a_zone():
    """A guard, not a convention. The copies this file replaces each began
    as a reasonable two-line `ZoneInfo(settings.user_timezone)`. A new one
    fails here and points at `app.core.tz`."""
    offenders = [
        f"{rel}:{line}"
        for path in sorted(APP_DIR.rglob("*.py"))
        if (rel := path.relative_to(APP_DIR).as_posix()) not in ALLOWED
        for line in _zone_constructions(path.read_text())
    ]

    assert not offenders, (
        "resolve the user's zone with app.core.tz.user_tz() / resolve_tz() "
        "instead of constructing one directly. If it is not the user's zone, "
        f"add the file to ALLOWED with the reason: {offenders}"
    )
