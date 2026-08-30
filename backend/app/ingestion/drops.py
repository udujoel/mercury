"""
Making per-item normalisation failures visible.

CLAUDE.md rule 7 says one malformed message must never take down a run, and
both Gmail and Calendar honour that with a per-item `except: continue`. What
they did not have was any way to tell the two ends of that behaviour apart:
losing one odd message out of sixty, and losing all sixty, produced exactly
the same observable result — an empty-ish list, no ERROR, no `::warning::`
and a green tick in Actions. A totally broken parser looked like a quiet
morning, which is the failure mode Mercury can least afford, because a quiet
morning is a perfectly normal thing for an inbox to be.

So: every drop is logged with its id and its traceback, the total is
reported once, and a source that lost MOST of a non-empty input raises
instead. `app.pipeline.generate._ingest_concurrently` catches that, drops
the segment (rule 7 intact — the briefing still ships) and records it in
`_failed_personal_sources`, which is what `app/cli.py` already reads to
print the degraded-run warning. No parallel reporting channel.
"""

from __future__ import annotations

import logging

# Both bars have to be cleared before a source is called broken.
#
# The ratio alone is useless at low volume: one unparseable message on a
# two-message Sunday is 50% and means nothing at all. The absolute floor
# alone is useless at high volume: three drops out of two hundred is a
# healthy run. Together they say "most of what this source returned was
# unreadable, and it was enough of it to be a pattern rather than a fluke".
ESCALATION_RATIO = 0.5
ESCALATION_MIN_DROPPED = 3


class SourceNormalisationError(RuntimeError):
    """Most of a non-empty source payload could not be normalised.

    Deliberately a distinct type rather than a bare RuntimeError:
    `app/cli.py` reports on the TYPE of an ingestion failure (see
    `failed_personal_source_errors`) so that a known cause with a permanent
    fix reads differently from a transient one, and matching on message
    text breaks the first time somebody rewords a string.
    """


def log_drop(log: logging.Logger, label: str, item_id: object) -> None:
    """Record one item that failed to normalise. Call from the except block.

    `exc_info=True` is the point of this function. Without the traceback the
    operator learns that something was lost but not what broke, which is
    only marginally better than the silence this replaces.
    """
    log.warning(
        "%s: dropping item %r — it failed to normalise", label, item_id, exc_info=True
    )


def report_drops(log: logging.Logger, label: str, *, total: int, dropped: int) -> None:
    """Summarise a source's drops, and raise if it lost most of its input."""
    if not dropped:
        return

    summary = f"{label}: {dropped} of {total} items failed to normalise"

    if total and dropped >= ESCALATION_MIN_DROPPED and dropped / total >= (
        ESCALATION_RATIO
    ):
        log.error(
            "%s — treating this source as failed rather than reporting an "
            "empty result that reads like a quiet day",
            summary,
        )
        raise SourceNormalisationError(summary)

    log.warning("%s (the rest were kept)", summary)
