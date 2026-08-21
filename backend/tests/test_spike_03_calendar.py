"""
Offline tests for scripts/spike_03_calendar.py — runs entirely against the
fixture at backend/tests/fixtures/calendar_events.json, no live API calls.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import pytest  # noqa: E402
import spike_03_calendar as spike  # noqa: E402

TZ = ZoneInfo("Europe/Lisbon")
WORK_CAL = "work_cal_id@group.calendar.google.com"


@pytest.fixture
def calendars_and_events():
    return spike.load_fixture()


def _find(events_by_cal, cal_id, event_id):
    return next(e for e in events_by_cal[cal_id] if e["id"] == event_id)


# ──────────────────────────────────────────────────────────────
# All-day events
# ──────────────────────────────────────────────────────────────


def test_all_day_event_parsed_as_midnight_local(calendars_and_events):
    _calendars, events_by_cal = calendars_and_events
    raw = _find(events_by_cal, "udujoel@gmail.com", "evt_allday_001")
    event = spike.event_to_calendar_event(raw, TZ)
    assert event is not None
    assert event.start.hour == 0
    assert event.start.minute == 0
    assert event.start.tzinfo is not None


def test_parse_event_time_distinguishes_date_vs_datetime():
    all_day, is_all_day = spike.parse_event_time({"date": "2026-08-22"}, TZ)
    assert is_all_day is True
    assert all_day.hour == 0

    timed, is_all_day2 = spike.parse_event_time(
        {"dateTime": "2026-08-21T09:30:00+01:00"}, TZ
    )
    assert is_all_day2 is False
    assert timed.hour == 9


# ──────────────────────────────────────────────────────────────
# Declined / cancelled exclusion
# ──────────────────────────────────────────────────────────────


def test_declined_event_is_excluded(calendars_and_events):
    _calendars, events_by_cal = calendars_and_events
    raw = _find(events_by_cal, "udujoel@gmail.com", "evt_declined_001")
    assert spike.event_to_calendar_event(raw, TZ) is None


def test_cancelled_event_is_excluded(calendars_and_events):
    _calendars, events_by_cal = calendars_and_events
    raw = _find(events_by_cal, "udujoel@gmail.com", "evt_cancelled_001")
    assert spike.event_to_calendar_event(raw, TZ) is None


def test_user_declined_helper_checks_self_attendee():
    attendees = [
        {"email": "udujoel@gmail.com", "self": True, "responseStatus": "declined"},
        {"email": "other@x.com", "responseStatus": "accepted"},
    ]
    assert spike.user_declined(attendees) is True


def test_user_declined_false_when_self_accepted():
    attendees = [
        {"email": "udujoel@gmail.com", "self": True, "responseStatus": "accepted"},
    ]
    assert spike.user_declined(attendees) is False


# ──────────────────────────────────────────────────────────────
# Recurring event expansion (singleEvents=True upstream)
# ──────────────────────────────────────────────────────────────


def test_recurring_instance_carries_recurring_event_id(calendars_and_events):
    _calendars, events_by_cal = calendars_and_events
    raw = _find(events_by_cal, WORK_CAL, "evt_recurring_20260821")
    assert raw["recurringEventId"] == "evt_recurring_series"
    event = spike.event_to_calendar_event(raw, TZ)
    assert event is not None
    assert event.title == "Daily Standup"


# ──────────────────────────────────────────────────────────────
# Timezone conversion
# ──────────────────────────────────────────────────────────────


def test_cross_timezone_event_converted_to_user_tz(calendars_and_events):
    _calendars, events_by_cal = calendars_and_events
    raw = _find(events_by_cal, WORK_CAL, "evt_crosstz_001")
    event = spike.event_to_calendar_event(raw, TZ)
    assert event is not None
    # 09:00 America/New_York (-04:00, EDT) == 14:00 Europe/Lisbon (+01:00, WEST)
    assert event.start.hour == 14
    assert str(event.start.tzinfo) != "America/New_York"


# ──────────────────────────────────────────────────────────────
# Conference boilerplate stripping / join URL extraction
# ──────────────────────────────────────────────────────────────


def test_join_url_extracted_from_conference_data(calendars_and_events):
    _calendars, events_by_cal = calendars_and_events
    raw = _find(events_by_cal, WORK_CAL, "evt_crosstz_001")
    url = spike.extract_join_url(raw["description"], raw.get("conferenceData"))
    assert url == "https://meet.google.com/abc-defg-hij"


def test_boilerplate_stripped_but_join_url_kept_in_description(calendars_and_events):
    _calendars, events_by_cal = calendars_and_events
    raw = _find(events_by_cal, WORK_CAL, "evt_crosstz_001")
    event = spike.event_to_calendar_event(raw, TZ)
    assert event is not None
    assert "Or dial" not in event.description
    assert "meet.google.com" in event.description


# ──────────────────────────────────────────────────────────────
# Attendees
# ──────────────────────────────────────────────────────────────


def test_extract_attendees_excludes_self():
    attendees = [
        {"email": "udujoel@gmail.com", "self": True},
        {"email": "dave.park@acmecorp.com", "displayName": "Dave Park"},
    ]
    names = spike.extract_attendees(attendees)
    assert names == ["Dave Park"]


# ──────────────────────────────────────────────────────────────
# needs_prep heuristic
# ──────────────────────────────────────────────────────────────


def test_needs_prep_true_for_big_meeting_with_agenda_doc(calendars_and_events):
    _calendars, events_by_cal = calendars_and_events
    raw = _find(events_by_cal, WORK_CAL, "evt_bigmeeting_001")
    event = spike.event_to_calendar_event(raw, TZ)
    assert event is not None
    assert event.needs_prep is True
    assert len(event.attendees) == 4  # 5 total minus self


def test_needs_prep_false_for_small_meeting_with_no_agenda(calendars_and_events):
    _calendars, events_by_cal = calendars_and_events
    raw = _find(events_by_cal, WORK_CAL, "evt_recurring_20260821")
    event = spike.event_to_calendar_event(raw, TZ)
    assert event is not None
    assert event.needs_prep is False


def test_needs_prep_keyword_signal():
    text = "Please review the attached notes before we meet"
    assert spike.needs_prep(text, 1) is True
    assert spike.needs_prep("Quick catch up, nothing formal", 1) is False


# ──────────────────────────────────────────────────────────────
# Full fixture run
# ──────────────────────────────────────────────────────────────


def test_full_fixture_run_excludes_declined_and_cancelled(calendars_and_events):
    calendars, events_by_cal = calendars_and_events
    all_events = []
    for cal in calendars:
        for raw in events_by_cal.get(cal["id"], []):
            event = spike.event_to_calendar_event(raw, TZ)
            if event is not None:
                all_events.append(event)
    ids = {e.id for e in all_events}
    assert "evt_declined_001" not in ids
    assert "evt_cancelled_001" not in ids
    assert len(all_events) == 5


def test_events_sorted_chronologically_across_calendars(calendars_and_events):
    calendars, events_by_cal = calendars_and_events
    all_events = []
    for cal in calendars:
        for raw in events_by_cal.get(cal["id"], []):
            event = spike.event_to_calendar_event(raw, TZ)
            if event is not None:
                all_events.append(event)
    ordered = sorted(all_events, key=lambda e: e.start)
    starts = [e.start for e in ordered]
    assert starts == sorted(starts)


def test_now_is_timezone_aware():
    now = datetime.now(TZ)
    assert now.tzinfo is not None
