"""
Heuristic email scoring and briefing selection.

Phase 1 is heuristic only — no embeddings, no clustering for email scoring
(ROADMAP explicitly forbids it this phase). `classify_priority` here is
lifted from `scripts/spike_02_gmail.py` / `app/ingestion/gmail.py` rather
than reinvented; scoring just orders what ingestion already classified.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.schemas import (
    LENGTH_BUDGETS,
    BriefingLength,
    CalendarEvent,
    Item,
    Priority,
)

# PRD F-05: at most this many emails are ever read individually in the
# briefing, regardless of inbox volume. The rest collapse into a single
# "N other emails" closing line via `aggregated_count`.
MAX_INDIVIDUAL_EMAILS = 8

_PRIORITY_RANK: dict[Priority, int] = {
    Priority.HIGH: 0,
    Priority.MEDIUM: 1,
    Priority.LOW: 2,
}


def score_emails(items: list[Item]) -> list[Item]:
    """Sets .priority and .requires_response (already set by ingestion, but
    this is the single place that owns final scoring) and returns items
    sorted high -> low priority, most recent first within a priority tier.

    Ingestion (`app/ingestion/gmail.py`) already runs `classify_priority`
    per message, so this function's real job is ordering: high priority
    first, then medium, then low; within a tier, most recent first so a
    fresher item of equal priority surfaces before a stale one.
    """
    return sorted(
        items,
        key=lambda item: (_PRIORITY_RANK[item.priority], -item.timestamp.timestamp()),
    )


def select_for_briefing(
    emails: list[Item],
    events: list[CalendarEvent],
    length: BriefingLength,
    *,
    date: datetime | None = None,
) -> dict:
    """Applies LENGTH_BUDGETS caps to emails and events for the given
    briefing length. Returns
    {"emails": [...], "events": [...], "background_events": [...],
     "aggregated_count": int}.

    The email cap is min(LENGTH_BUDGETS[length]["emails"],
    MAX_INDIVIDUAL_EMAILS) — the length budget can only make the cut
    tighter than the PRD F-05 ceiling, never looser.

    `date` is the briefing day. Ingestion fetches today plus 72 hours (PRD
    F-02) on purpose, and this is where that window is supposed to narrow
    to what is actually SPOKEN — see `_overlaps_day`. `date=None` keeps
    every event, which is what a caller with no briefing date should get.
    """
    budget = LENGTH_BUDGETS[length]
    scored = score_emails(emails)

    email_cap = min(budget["emails"], MAX_INDIVIDUAL_EMAILS)
    selected_emails = scored[:email_cap]
    aggregated_count = max(0, len(scored) - email_cap)

    if date is not None:
        events = [e for e in events if _overlaps_day(e, date)]

    # Through `_aware` for the same reason `_overlaps_day` is: one naive
    # event among aware ones raises TypeError here, and this is the one call
    # `generate_briefing` does not wrap, so the whole briefing dies rather
    # than losing a segment.
    sorted_events = sorted(events, key=lambda e: _aware(e.start))

    # Keep everything, rank it down: subscribed all-day noise stays in the
    # briefing but as one closing line rather than a chronological walk.
    foreground = [e for e in sorted_events if not e.is_background]
    background = [e for e in sorted_events if e.is_background]

    return {
        "emails": selected_emails,
        "events": foreground,
        "background_events": background,
        "aggregated_count": aggregated_count,
    }


def _overlaps_day(event: CalendarEvent, day: datetime) -> bool:
    """Does this event touch the briefing day at all?

    Overlap rather than "starts today", so a conference that began
    yesterday and runs through tomorrow is still part of your day.

    The filter exists because OUTLINE_PROMPT defines the calendar segment as
    "the day walked chronologically" and the cold open as an observation
    "about the day ahead", and both were being handed the full 72-hour fetch
    window. "Your day" described the next three.
    """
    day_start = _aware(day.replace(hour=0, minute=0, second=0, microsecond=0))
    day_end = day_start + timedelta(days=1)
    return _aware(event.start) < day_end and _aware(event.end) > day_start


def _aware(dt: datetime) -> datetime:
    """Naive timestamps are read as UTC so they can be compared at all.

    Not defensive padding: comparing a naive datetime to an aware one raises
    TypeError rather than returning False, and that exact mismatch had
    already silently disabled clustering elsewhere in this pipeline
    (see `app/pipeline/clustering.py::_as_utc`).
    """
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
