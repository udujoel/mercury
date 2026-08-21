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
