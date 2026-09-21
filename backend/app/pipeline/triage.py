"""
Semantic email triage — the include/exclude decision (ADR-014).

Replaces the judgment half of `classify_priority` with a Jev System One
call. The heuristic stays where it is and keeps running at ingestion time:
this module OVERWRITES its verdict when a judgment arrives and leaves it
alone when one does not, so the fallback path is "do nothing" rather than a
second code path that can rot unexercised (rule 7).

Rule 5 lives here. `email_state` is the only place an `Item` is turned into
something sent to TypeSafe, and it truncates the body to BODY_CHARS. No
other module builds that dict.
"""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.core.providers import EmailJudgment, JudgmentProvider
from app.core.schemas import Item, Priority

log = logging.getLogger(__name__)

# Enough for a triage judgment, not enough to be a copy of the mail. Jev is
# deciding whether the email matters, not summarising it — the summary is
# the script chain's job, and it works from the full body locally.
BODY_CHARS = 500


def email_state(item: Item) -> dict:
    """The state sent to the judgment provider for one email.

    A fixed, named set of fields rather than the whole `Item`: named JSON
    fields are what the model reads best, and an explicit allowlist means a
    field added to `Item` later cannot silently start leaving the machine.
    """
    raw = item.raw or {}
    return {
        "sender_name": item.source_name,
        "sender_address": item.author or "",
        "subject": item.title,
        "to": raw.get("to", []),
        "has_list_unsubscribe": bool(raw.get("is_newsletter")),
        "received": item.timestamp.isoformat(),
        "body_excerpt": item.body[:BODY_CHARS],
    }


def priority_for(judgment: EmailJudgment, settings=None) -> tuple[Priority, bool]:
    """Compose the three raw judgments into the pipeline's own vocabulary.

    Policy lives here, in code, rather than in the prompt — so a threshold
    can be retuned on a morning's real data without re-running inference or
    touching what the model was asked.
    """
    settings = settings or get_settings()
    if judgment.consequence >= settings.judgment_high_threshold:
        priority = Priority.HIGH
    elif judgment.consequence >= settings.judgment_medium_threshold:
        priority = Priority.MEDIUM
    else:
        priority = Priority.LOW
    requires_response = judgment.awaiting_reply > settings.judgment_awaiting_threshold
    return priority, requires_response


async def triage_emails(
    items: list[Item], provider: JudgmentProvider | None
) -> list[Item]:
    """Set priority/requires_response/consequence from a judgment pass.

    Mutates and returns `items`. `provider=None` returns them untouched,
    still carrying the heuristic's verdict from ingestion.

    Never raises. A provider that blows up entirely costs the briefing its
    semantic triage, not its email segment.
    """
    if provider is None or not items:
        return items

    settings = get_settings()
    try:
        judgments = await provider.triage_emails(
            [email_state(i) for i in items],
            timeout=settings.judgment_timeout_seconds,
        )
    except Exception as exc:
        log.warning(
            "email triage failed entirely (%s) — keeping the classify_priority "
            "heuristic for all %d email(s)",
            exc, len(items),
        )
        return items

    # A provider that returns the wrong number of judgments would zip them
    # onto the wrong emails. Refuse rather than misattribute: a briefing
    # built on the heuristic is wrong in a known way, one built on shifted
    # judgments is wrong in a way nobody can see.
    if len(judgments) != len(items):
        log.error(
            "email triage returned %d judgment(s) for %d email(s) — discarding "
            "the whole pass rather than risk misaligning them",
            len(judgments), len(items),
        )
        return items

    changed = unjudged = 0
    for item, judgment in zip(items, judgments, strict=True):
        if judgment is None:
            unjudged += 1
            continue
        priority, requires_response = priority_for(judgment, settings)
        if priority != item.priority:
            changed += 1
        item.priority = priority
        item.requires_response = requires_response
        item.consequence = judgment.consequence

    log.info(
        "email triage: %d judged, %d fell back to the heuristic, %d priority "
        "change(s) against the heuristic",
        len(items) - unjudged, unjudged, changed,
    )
    return items
