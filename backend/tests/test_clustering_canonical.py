"""
Tests for `_pick_canonical` — which member of a cluster represents it.

The canonical item is not cosmetic. Its title is what the hosts say, its URL
is the tappable show-notes entry (PRD F-19), and its body is what the
grounding gate checks world claims against. Getting it wrong degrades the
segment ADR-003 exists to produce.

Two things are covered here:

  1. A cluster whose members carry a mix of naive and timezone-aware
     timestamps must not raise. `rank()` returns a tuple with the timestamp
     in it, so `sorted` compares two datetimes directly the moment two
     members tie on authority — and comparing naive to aware is a
     TypeError, not a False.
  2. Primary reporting is canonical over commentary, which is what the
     module docstring has always claimed and what AUTHORITY_ORDER did not
     do.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.schemas import Item, SourceType
from app.pipeline.clustering import _pick_canonical

AWARE = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
NAIVE = datetime(2026, 8, 20, 8, 0)


def _item(
    source_type: SourceType,
    source_name: str,
    timestamp: datetime,
    *,
    body: str = "TechCorp is acquiring DataFlow.",
    url: str | None = "https://example.com/a",
) -> Item:
    return Item(
        source_type=source_type,
        source_name=source_name,
        title="TechCorp acquires DataFlow",
        body=body,
        url=url,
        timestamp=timestamp,
    )


# ──────────────────────────────────────────────────────────────
# Mixed naive/aware timestamps
# ──────────────────────────────────────────────────────────────


def test_mixed_naive_and_aware_timestamps_do_not_raise():
    """Two same-authority members, one naive timestamp, one aware.

    Reachable from Gmail alone: `_parse_date` falls back to
    `email.utils.parsedate_to_datetime`, which returns a NAIVE datetime for
    the RFC 2822 "unknown timezone" offset `-0000`. Before the fix this
    raised TypeError inside `sorted`, and `build_stories` caught it and
    shipped every item as an unclustered singleton — clustering silently
    off for the whole run, with only a log line to say so.
    """
    members = [
        _item(SourceType.EMAIL, "Stratechery", NAIVE),
        _item(SourceType.EMAIL, "Platformer", AWARE),
    ]
    canonical = _pick_canonical(members)
    # Naive timestamps are read as UTC, so 08:00 naive is earlier than
    # 09:00 aware and "whoever reported it first" picks it.
    assert canonical.source_name == "Stratechery"


def test_mixed_naive_and_aware_ordering_is_stable_either_way():
    """The same two members in the opposite input order pick the same
    canonical. A fix that merely stopped the raise — by catching it, say —
    would leave the choice dependent on list order."""
    a = _item(SourceType.RSS, "Reuters", NAIVE)
    b = _item(SourceType.RSS, "Benedict Evans", AWARE)
    assert _pick_canonical([a, b]).source_name == "Reuters"
    assert _pick_canonical([b, a]).source_name == "Reuters"


def test_all_naive_cluster_still_picks_earliest():
    members = [
        _item(SourceType.RSS, "Later", datetime(2026, 8, 20, 12, 0)),
        _item(SourceType.RSS, "Earlier", datetime(2026, 8, 20, 6, 0)),
    ]
    assert _pick_canonical(members).source_name == "Earlier"


# ──────────────────────────────────────────────────────────────
# Authority order
# ──────────────────────────────────────────────────────────────


def test_primary_reporting_beats_a_newsletter_covering_it():
    """ADR-003's worked example: Reuters carries the announcement,
    Stratechery writes about it. Reuters is the canonical item."""
    members = [
        _item(SourceType.EMAIL, "Stratechery", AWARE, url=None),
        _item(SourceType.RSS, "Reuters", AWARE),
    ]
    canonical = _pick_canonical(members)
    assert canonical.source_name == "Reuters"
    assert canonical.source_type is SourceType.RSS


def test_canonical_keeps_a_url_when_any_member_has_one():
    """The concrete cost of an email canonical: Gmail `Item`s are built with
    no `url` (see app/ingestion/gmail.py), so a newsletter winning the
    canonical slot strips the story's show-notes link entirely."""
    members = [
        _item(
            SourceType.EMAIL,
            "Stratechery",
            datetime(2026, 8, 20, 6, 0, tzinfo=UTC),
            url=None,
        ),
        _item(SourceType.RSS, "Reuters", AWARE, url="https://reuters.com/x"),
    ]
    assert _pick_canonical(members).url == "https://reuters.com/x"


def test_email_still_beats_slack_and_social():
    """Only the RSS/EMAIL pair was inverted. An email is still a better
    representative than a Slack message or a social post."""
    members = [
        _item(SourceType.SOCIAL, "Bluesky", AWARE),
        _item(SourceType.SLACK, "#general", AWARE),
        _item(SourceType.EMAIL, "Priya Shah", AWARE),
    ]
    assert _pick_canonical(members).source_type is SourceType.EMAIL
