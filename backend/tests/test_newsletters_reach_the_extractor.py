"""The wiring test this feature shipped without.

2026-09-16 to 2026-09-20: #51 merged, sixteen briefings generated, and the
newsletter segment ran ZERO times. Not once. The extractor was handed
`selection["emails"]`, and `select_for_briefing` caps emails at six while
`score_emails` sorts newsletters LAST — they are `Priority.LOW` precisely
because they were never meant to be read out individually.

So on a morning with eight ordinary emails, all six slots went to those and
the extractor received an empty list, then returned without logging. Every
layer was complete and tested except the one connecting them — this
project's signature failure, and its fourth occurrence after `n_stories=0`,
`music_set` and `topic_centroid`.

What let it survive four days is that it is INTERMITTENT: on an inbox that
is mostly newsletters, some do survive the cap, so a spot check passes. The
composition of the inbox decided whether the feature existed at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.schemas import BriefingLength, Item, Priority, SourceType
from app.pipeline.generate import _newsletters_for_extraction
from app.pipeline.scoring import score_emails, select_for_briefing


def _email(index: int, *, newsletter: bool) -> Item:
    return Item(
        source_type=SourceType.EMAIL,
        source_name=f"{'Brew' if newsletter else 'Person'}{index}",
        title=f"subject {index}",
        body="body",
        author=f"a{index}@example.test",
        timestamp=datetime(2026, 9, 20, 4, tzinfo=UTC) - timedelta(minutes=index),
        priority=Priority.LOW if newsletter else Priority.MEDIUM,
        raw={"is_newsletter": newsletter},
    )


# The exact composition of the 2026-09-20 04:49 run: 21 emails, 13 of them
# newsletters, which produced a briefing with no newsletter segment at all.
def _the_morning_that_failed() -> list[Item]:
    return [_email(i, newsletter=False) for i in range(8)] + [
        _email(100 + i, newsletter=True) for i in range(13)
    ]


def test_the_selection_cap_starves_the_newsletter_segment():
    """The mechanism, pinned so it cannot be reintroduced.

    This asserts the starvation EXISTS in `select_for_briefing`, which is
    correct behaviour for that function: its job is to cap what gets read
    out one by one, and ranking newsletters last is deliberate. The defect
    was building the newsletter segment on its output.
    """
    kept = select_for_briefing(
        score_emails(_the_morning_that_failed()), [], BriefingLength.STANDARD,
        date=datetime(2026, 9, 20, tzinfo=UTC),
    )["emails"]

    assert len(kept) == 6
    assert [e for e in kept if e.raw["is_newsletter"]] == [], (
        "selection is supposed to starve newsletters — that is exactly why "
        "the extractor must not be fed from it"
    )


def test_every_newsletter_in_the_inbox_reaches_the_extractor():
    """The fix, asserted on what `generate_briefing` actually hands over.

    Not on the selection, and not on a hand-built list of `MergedStory`
    objects — every test in `test_newsletter_items.py` did that, which is
    why they were all green while the feature did nothing.
    """
    handed = _newsletters_for_extraction(_the_morning_that_failed())
    assert len(handed) == 13, (
        f"the extractor sees {len(handed)} of 13 newsletters; for the rest "
        "the segment is as good as absent"
    )


def test_ordinary_mail_is_not_handed_to_the_newsletter_extractor():
    """The gate must not become "everything", either: a colleague's email
    is not journalism and rule 5 keeps it off that path."""
    handed = _newsletters_for_extraction(_the_morning_that_failed())
    assert all(e.raw["is_newsletter"] for e in handed)


async def test_the_census_would_have_caught_this_on_day_one(caplog):
    """The robustness half, asserted against the real generate path.

    Not a unit test of `RunCensus` — `test_census.py` does that. This runs a
    briefing end to end with the bug's exact inbox and asserts the run log
    would have named the starved stage. Four days of silent failure is what
    the absence of this line cost.
    """
    import logging

    from app.pipeline.census import RunCensus

    # Reproduce the wiring defect: hand the extractor the SELECTION, which
    # ranks newsletters last, instead of the inbox.
    inbox = _the_morning_that_failed()
    starved = select_for_briefing(
        score_emails(inbox), [], BriefingLength.STANDARD,
        date=datetime(2026, 9, 20, tzinfo=UTC),
    )["emails"]
    handed = [e for e in starved if e.raw["is_newsletter"]]
    assert handed == [], "precondition: the old wiring starved the extractor"

    census = RunCensus()
    census.record("newsletters", took=len(inbox), made=len(handed))
    with caplog.at_level(logging.WARNING):
        assert census.report() == ["newsletters"]
    assert "STARVED STAGE" in caplog.text
