"""
Heuristic email scoring and briefing selection.

Phase 1 is heuristic only — no embeddings, no clustering for email scoring
(ROADMAP explicitly forbids it this phase). `classify_priority` here is
lifted from `scripts/spike_02_gmail.py` / `app/ingestion/gmail.py` rather
than reinvented; scoring just orders what ingestion already classified.
"""

from __future__ import annotations

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
) -> dict:
    """Applies LENGTH_BUDGETS caps to emails and events for the given
    briefing length. Returns
    {"emails": [...], "events": [...], "aggregated_count": int}.

    The email cap is min(LENGTH_BUDGETS[length]["emails"],
    MAX_INDIVIDUAL_EMAILS) — the length budget can only make the cut
    tighter than the PRD F-05 ceiling, never looser.
    """
    budget = LENGTH_BUDGETS[length]
    scored = score_emails(emails)

    email_cap = min(budget["emails"], MAX_INDIVIDUAL_EMAILS)
    selected_emails = scored[:email_cap]
    aggregated_count = max(0, len(scored) - email_cap)

    sorted_events = sorted(events, key=lambda e: e.start)

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
