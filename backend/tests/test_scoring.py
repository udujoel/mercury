"""
Tests for app/pipeline/scoring.py — priority ordering, the PRD F-05 8-email
cap, and LENGTH_BUDGETS application.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.schemas import (
    BriefingLength,
    CalendarEvent,
    Item,
    Priority,
    SourceType,
)
from app.pipeline.scoring import (
    MAX_INDIVIDUAL_EMAILS,
    score_emails,
    select_for_briefing,
)

NOW = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)


def make_item(i: int, priority: Priority, minutes_ago: int = 0) -> Item:
    return Item(
        source_type=SourceType.EMAIL,
        source_name=f"Sender {i}",
        title=f"Subject {i}",
        body="body",
        timestamp=NOW - timedelta(minutes=minutes_ago),
        priority=priority,
    )


def make_event(i: int, start_offset_hours: int) -> CalendarEvent:
    start = NOW + timedelta(hours=start_offset_hours)
    return CalendarEvent(
        id=f"evt{i}",
        title=f"Event {i}",
        start=start,
        end=start + timedelta(hours=1),
    )


def test_score_emails_sorts_high_before_medium_before_low():
    items = [
        make_item(1, Priority.LOW),
        make_item(2, Priority.HIGH),
        make_item(3, Priority.MEDIUM),
    ]
    result = score_emails(items)
    expected = [Priority.HIGH, Priority.MEDIUM, Priority.LOW]
    assert [i.priority for i in result] == expected


def test_score_emails_most_recent_first_within_tier():
    items = [
        make_item(1, Priority.HIGH, minutes_ago=60),
        make_item(2, Priority.HIGH, minutes_ago=5),
    ]
    result = score_emails(items)
    assert result[0].source_name == "Sender 2"
    assert result[1].source_name == "Sender 1"


def test_select_for_briefing_caps_at_8_emails_regardless_of_length():
    # FULL budget allows 10 emails, but PRD F-05 caps individual reads at 8.
    items = [make_item(i, Priority.MEDIUM, minutes_ago=i) for i in range(20)]
    result = select_for_briefing(items, [], BriefingLength.FULL)
    assert len(result["emails"]) == MAX_INDIVIDUAL_EMAILS
    assert result["aggregated_count"] == 20 - MAX_INDIVIDUAL_EMAILS


def test_select_for_briefing_express_uses_tighter_budget_cap():
    items = [make_item(i, Priority.MEDIUM, minutes_ago=i) for i in range(20)]
    result = select_for_briefing(items, [], BriefingLength.EXPRESS)
    assert len(result["emails"]) == 3  # EXPRESS budget caps at 3, below the 8 ceiling
    assert result["aggregated_count"] == 17


def test_select_for_briefing_no_aggregation_when_under_cap():
    items = [make_item(i, Priority.MEDIUM) for i in range(2)]
    result = select_for_briefing(items, [], BriefingLength.STANDARD)
    assert len(result["emails"]) == 2
    assert result["aggregated_count"] == 0


def test_select_for_briefing_sorts_events_chronologically():
    events = [make_event(1, 5), make_event(2, 1), make_event(3, 3)]
    result = select_for_briefing([], events, BriefingLength.STANDARD)
    starts = [e.start for e in result["events"]]
    assert starts == sorted(starts)


def test_select_for_briefing_selects_highest_priority_emails_first():
    items = [
        make_item(1, Priority.LOW),
        make_item(2, Priority.HIGH),
        make_item(3, Priority.MEDIUM),
    ]
    result = select_for_briefing(items, [], BriefingLength.EXPRESS)  # cap=3
    assert len(result["emails"]) == 3
    assert result["emails"][0].priority == Priority.HIGH


# ── calendar ranking (subscribed-calendar noise) ──────────────────────


def _cal_event(title: str, *, all_day: bool, owned: bool) -> CalendarEvent:
    return CalendarEvent(
        id=title,
        title=title,
        start=datetime(2026, 8, 22, 9, 0, tzinfo=UTC),
        end=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
        all_day=all_day,
        owned=owned,
    )


def test_timed_event_on_a_subscribed_calendar_stays_foreground():
    """Only all-day entries are ambient. A real timed appointment on a shared
    calendar is still something the listener has to show up for."""
    ev = _cal_event("Dentistry Consultation", all_day=False, owned=False)
    assert ev.is_background is False


def test_all_day_event_on_an_owned_calendar_stays_foreground():
    """Birthdays live on the user's own calendar on a real account, which is
    why they need no special-casing."""
    ev = _cal_event("Akpet Nyambi's birthday", all_day=True, owned=True)
    assert ev.is_background is False


def test_all_day_event_on_a_subscribed_calendar_is_background():
    for title in ("Week 35 of 2026", "10 Elul 5786", "The Prophet's Birthday"):
        ev = _cal_event(title, all_day=True, owned=False)
        assert ev.is_background is True, title


def test_select_for_briefing_splits_foreground_from_background():
    """Ranked down, never dropped — the PRD asks for all calendars, so the
    ambient entries must still reach the script chain."""
    events = [
        _cal_event("Dentistry Consultation", all_day=False, owned=True),
        _cal_event("Week 35 of 2026", all_day=True, owned=False),
        _cal_event("Akpet's birthday", all_day=True, owned=True),
    ]
    out = select_for_briefing([], events, BriefingLength.STANDARD)

    assert [e.title for e in out["events"]] == [
        "Dentistry Consultation",
        "Akpet's birthday",
    ]
    assert [e.title for e in out["background_events"]] == ["Week 35 of 2026"]
    # Nothing is lost.
    assert len(out["events"]) + len(out["background_events"]) == len(events)
