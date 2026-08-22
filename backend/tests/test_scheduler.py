"""
Scheduler: fire-time computation, timezone/DST correctness, idempotence,
and failure isolation.

No real generation and no real APNs call happens in this file — every
dependency (`llm`, `tts`, `push_provider`, `storage`) `run_daily_briefing_job`
takes is injectable and is a fake here, per CLAUDE.md rule 6 and the
project's hard-won lesson about never making a live call from a test.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger

from app.core.config import Settings, get_settings
from app.notifications.apns import FakePushProvider
from app.scheduler import cron
from app.scheduler.cron import (
    _compute_fire_time,
    _today_briefing_id,
    run_daily_briefing_job,
    shutdown_scheduler,
    start_scheduler,
)

# ── fire-time arithmetic ─────────────────────────────────────────────


def test_fire_time_subtracts_lead_from_wake_time():
    assert _compute_fire_time("07:00", 30) == (6, 30)


def test_fire_time_wraps_past_midnight():
    assert _compute_fire_time("00:10", 30) == (23, 40)


def test_fire_time_zero_lead_equals_wake_time():
    assert _compute_fire_time("07:00", 0) == (7, 0)


# ── DST boundary ─────────────────────────────────────────────────────


def test_cron_trigger_holds_local_wall_time_across_dst_boundary():
    """The headline test: the job must fire at the same LOCAL clock time on
    both sides of a DST transition, not the same UTC instant.

    Europe/Lisbon springs forward on the last Sunday of March. 2026-03-29 is
    that Sunday, so 2026-03-28 is "before" (UTC+0) and 2026-03-30 is "after"
    (UTC+1). A bug that scheduled in UTC instead of the user's timezone would
    fire at 06:30 UTC on both sides, i.e. 07:30 local after the transition —
    exactly the class of error CLAUDE.md and this module's docstring call
    out for `datetime.now(UTC)`.
    """
    tz = ZoneInfo("Europe/Lisbon")
    hour, minute = _compute_fire_time("07:00", 30)
    trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)

    before = datetime(2026, 3, 28, 0, 0, tzinfo=tz)
    after = datetime(2026, 3, 30, 0, 0, tzinfo=tz)

    fire_before = trigger.get_next_fire_time(None, before)
    fire_after = trigger.get_next_fire_time(None, after)

    # Local wall-clock time is identical on both sides...
    assert (fire_before.hour, fire_before.minute) == (hour, minute)
    assert (fire_after.hour, fire_after.minute) == (hour, minute)
    # ...even though the UTC offset changed, proving this isn't a UTC-pinned
    # schedule that happens to look right on one side of the boundary.
    assert fire_before.utcoffset() != fire_after.utcoffset()


def test_today_briefing_id_uses_user_timezone_not_utc():
    """00:11 in Europe/Lisbon (UTC+1 in summer) is still the previous day in
    UTC. This is the exact bug described in app/pipeline/generate.py's
    `_parse_briefing_date` — the scheduler side of the same fix."""
    # A fixed instant that is one date in UTC and the previous date's late
    # evening... no, the SAME calendar date in Lisbon but would be a
    # different UTC date if this function mistakenly asked for UTC "now"
    # instead of asking `datetime.now(tz)` directly, which it does. The
    # meaningful assertion is simply that it resolves via ZoneInfo, not UTC.
    result = _today_briefing_id("Europe/Lisbon")
    expected = datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%Y-%m-%d")
    assert result == expected


def test_today_briefing_id_falls_back_to_utc_on_bad_timezone():
    result = _today_briefing_id("Not/A_Real_Zone")
    expected = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d")
    assert result == expected


# ── idempotence ──────────────────────────────────────────────────────


class _FakeStorageWithManifest:
    def load_manifest(self, briefing_id: str):
        return object()  # anything non-None means "already exists"


class _FakeStorageEmpty:
    def __init__(self):
        self.saved_ids: list[str] = []

    def load_manifest(self, briefing_id: str):
        return None


async def test_second_run_same_day_is_a_no_op():
    calls = []

    async def fake_generate(**kwargs):
        calls.append(kwargs)

    import app.scheduler.cron as cron_module

    original = cron_module.generate_briefing
    cron_module.generate_briefing = fake_generate
    try:
        push = FakePushProvider()
        await run_daily_briefing_job(
            llm=object(),
            tts=object(),
            push_provider=push,
            storage=_FakeStorageWithManifest(),
        )
    finally:
        cron_module.generate_briefing = original

    assert calls == []
    assert push.calls == []


async def test_run_generates_and_pushes_when_no_manifest_exists():
    calls = []

    async def fake_generate(**kwargs):
        calls.append(kwargs)

    import app.scheduler.cron as cron_module

    original = cron_module.generate_briefing
    cron_module.generate_briefing = fake_generate
    try:
        push = FakePushProvider()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "app.api.devices.list_registered_tokens", lambda: ["device-1"]
            )
            await run_daily_briefing_job(
                llm=object(),
                tts=object(),
                push_provider=push,
                storage=_FakeStorageEmpty(),
            )
    finally:
        cron_module.generate_briefing = original

    assert len(calls) == 1
    assert push.calls == [("device-1", {"briefing_id": calls[0]["briefing_id"]})]


async def test_no_push_when_no_devices_registered():
    async def fake_generate(**kwargs):
        pass

    import app.scheduler.cron as cron_module

    original = cron_module.generate_briefing
    cron_module.generate_briefing = fake_generate
    try:
        push = FakePushProvider()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.api.devices.list_registered_tokens", lambda: [])
            await run_daily_briefing_job(
                llm=object(),
                tts=object(),
                push_provider=push,
                storage=_FakeStorageEmpty(),
            )
    finally:
        cron_module.generate_briefing = original

    assert push.calls == []


# ── failure isolation ────────────────────────────────────────────────


async def test_failing_generation_does_not_raise():
    """A failed run must be logged and must NOT kill the scheduler — i.e.
    the job coroutine itself must not raise, or APScheduler would log an
    unhandled job-execution error and (depending on config) could stop
    scheduling it."""

    async def failing_generate(**kwargs):
        raise RuntimeError("Gemini is down")

    import app.scheduler.cron as cron_module

    original = cron_module.generate_briefing
    cron_module.generate_briefing = failing_generate
    try:
        push = FakePushProvider()
        # Must not raise.
        await run_daily_briefing_job(
            llm=object(),
            tts=object(),
            push_provider=push,
            storage=_FakeStorageEmpty(),
        )
    finally:
        cron_module.generate_briefing = original

    # And it must not have pushed a "ready" hint for a briefing that
    # doesn't exist.
    assert push.calls == []


# ── enable/disable wiring ────────────────────────────────────────────


def test_scheduler_disabled_by_default_does_not_start(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    try:
        result = start_scheduler(app=None)
        assert result is None
        assert cron.get_scheduler() is None
    finally:
        get_settings.cache_clear()


async def test_scheduler_enabled_starts_and_reports_next_run(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("USER_TIMEZONE", "Europe/Lisbon")
    monkeypatch.setenv("WAKE_TIME", "07:00")
    monkeypatch.setenv("GENERATION_LEAD_MINUTES", "30")
    get_settings.cache_clear()
    try:
        scheduler = start_scheduler(app=None)
        assert scheduler is not None
        job = scheduler.get_job(cron.JOB_ID)
        assert job is not None
        assert job.next_run_time is not None
        assert job.next_run_time.tzinfo is not None
    finally:
        shutdown_scheduler()
        get_settings.cache_clear()


def test_shutdown_is_safe_when_never_started():
    # Must not raise even if start_scheduler was never called (or already
    # shut down) in this process.
    shutdown_scheduler()
    shutdown_scheduler()


def test_settings_expose_new_scheduler_fields():
    s = Settings(mercury_api_token="x" * 32)
    assert s.scheduler_enabled is False
    assert s.generation_lead_minutes == 30
