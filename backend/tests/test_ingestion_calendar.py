"""
Offline tests for app/ingestion/calendar.py, against the shared fixture at
backend/tests/fixtures/calendar_events.json (owned by another agent — do
not modify it).
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.ingestion import calendar

FIXTURE = Path(__file__).parent / "fixtures" / "calendar_events.json"
TZ = ZoneInfo("Europe/Lisbon")


async def test_fetch_events_offline_returns_events():
    events = await calendar.fetch_events(fixture=FIXTURE)
    assert events
    titles = [e.title for e in events]
    assert "Optional Design Review" not in titles, "declined event must be excluded"


async def test_fetch_events_are_sorted_chronologically():
    events = await calendar.fetch_events(fixture=FIXTURE)
    starts = [e.start for e in events]
    assert starts == sorted(starts)


async def test_cancelled_events_excluded():
    events = await calendar.fetch_events(fixture=FIXTURE)
    ids = [e.id for e in events]
    assert "evt_cancelled_001" not in ids


def test_parse_event_time_all_day():
    dt, is_all_day = calendar.parse_event_time({"date": "2026-08-22"}, TZ)
    assert is_all_day is True
    assert dt.year == 2026
    assert dt.month == 8
    assert dt.day == 22
    assert dt.tzinfo is not None


def test_parse_event_time_timed():
    dt, is_all_day = calendar.parse_event_time(
        {"dateTime": "2026-08-21T09:00:00+01:00"}, TZ
    )
    assert is_all_day is False
    assert dt.hour == 9


def test_user_declined_true_when_self_declined():
    attendees = [{"self": True, "responseStatus": "declined", "email": "me@x.com"}]
    assert calendar.user_declined(attendees) is True


def test_user_declined_false_when_self_accepted():
    attendees = [{"self": True, "responseStatus": "accepted", "email": "me@x.com"}]
    assert calendar.user_declined(attendees) is False


def test_extract_attendees_excludes_self():
    attendees = [
        {"self": True, "email": "me@x.com"},
        {"email": "bob@x.com", "displayName": "Bob"},
    ]
    result = calendar.extract_attendees(attendees)
    assert result == ["Bob"]


def test_needs_prep_true_for_three_plus_attendees():
    assert calendar.needs_prep("", attendee_count=3) is True


def test_needs_prep_true_for_agenda_keyword():
    assert calendar.needs_prep("Please review the agenda before we meet", 1) is True


def test_needs_prep_false_otherwise():
    assert calendar.needs_prep("Quick catch up", attendee_count=2) is False


def test_event_to_calendar_event_returns_none_for_cancelled():
    raw = {
        "id": "x",
        "status": "cancelled",
        "summary": "Cancelled thing",
        "start": {"dateTime": "2026-08-21T09:00:00+01:00"},
        "end": {"dateTime": "2026-08-21T10:00:00+01:00"},
    }
    assert calendar.event_to_calendar_event(raw, TZ) is None


def test_event_to_calendar_event_raises_on_missing_start_end():
    raw = {"id": "x", "summary": "Broken"}
    with pytest.raises(ValueError):
        calendar.event_to_calendar_event(raw, TZ)


async def test_malformed_event_is_skipped_not_raised(tmp_path):
    import json

    bad_fixture = tmp_path / "bad_calendar.json"
    bad_fixture.write_text(
        json.dumps(
            {
                "calendarList": {"items": [{"id": "cal1", "summary": "Cal"}]},
                "eventsByCalendar": {
                    "cal1": {"items": [{"id": "broken", "summary": "Broken"}]}
                },
            }
        )
    )
    events = await calendar.fetch_events(fixture=bad_fixture)
    assert events == []


async def test_fetch_events_live_without_fixture_raises_clear_error():
    """No live path can succeed on this machine (no credentials.json) —
    verify the failure names the missing credential/dependency rather than
    crashing with an opaque traceback."""
    with pytest.raises(Exception) as exc_info:
        await calendar.fetch_events(fixture=None)
    message = str(exc_info.value)
    assert "credentials" in message.lower() or "google" in message.lower()


# ──────────────────────────────────────────────────────────────
# All-day events: Google's end.date is EXCLUSIVE
# ──────────────────────────────────────────────────────────────


async def test_one_day_all_day_event_does_not_run_into_the_next_day():
    """Google reports an all-day event's `end.date` as the day AFTER it
    finishes. Stored verbatim, the fixture's one-day offsite on the 22nd
    came out as 2026-08-22T00:00 to 2026-08-23T00:00 — and
    `script_chain._format_calendar` hands exactly those two isoformat
    strings to the model, so the hosts described a one-day offsite as
    running into Sunday.
    """
    events = await calendar.fetch_events(fixture=FIXTURE)
    offsite = next(e for e in events if e.id == "evt_allday_001")

    assert offsite.all_day is True
    assert offsite.start.date().isoformat() == "2026-08-22"
    assert offsite.end.date().isoformat() == "2026-08-22"
    assert offsite.end > offsite.start


def test_multi_day_all_day_event_keeps_every_day_it_covers():
    """The correction is one day off the exclusive end, not a collapse onto
    the start date — a Thursday-to-Saturday conference still ends Saturday.
    """
    event = calendar.event_to_calendar_event(
        {
            "id": "conf",
            "summary": "Conference",
            "start": {"date": "2026-08-20"},
            "end": {"date": "2026-08-23"},
        },
        TZ,
    )
    assert event.start.date().isoformat() == "2026-08-20"
    assert event.end.date().isoformat() == "2026-08-22"


def test_timed_event_end_is_untouched():
    """Only `end.date` is exclusive. `end.dateTime` is the real end instant
    and must not have a second shaved off it."""
    event = calendar.event_to_calendar_event(
        {
            "id": "timed",
            "summary": "Standup",
            "start": {"dateTime": "2026-08-21T09:30:00+01:00"},
            "end": {"dateTime": "2026-08-21T09:45:00+01:00"},
        },
        TZ,
    )
    assert event.end.hour == 9
    assert event.end.minute == 45
    assert event.end.second == 0
