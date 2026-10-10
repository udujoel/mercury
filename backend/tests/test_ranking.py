"""
What the cluster term in `score_stories` is actually counting.

`score_stories` had no caller and no test at all until the news half of the
pipeline was wired in, so its w_cluster term — commented "how many
independent sources carried it" — had never been measured against a cluster
that was not made of independent sources.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.schemas import CalendarEvent, Item, SourceType, Story
from app.pipeline.ranking import score_stories

DATE = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)
# Every fixture is stamped at DATE, so recency is the same for all of them
# at any clock. Passed explicitly anyway: the ranker must not read the wall
# clock inside a test (`tests/conftest.py`).
CLOCK = DATE.replace(hour=7)


def _article(n: int, source_name: str) -> Item:
    return Item(
        source_type=SourceType.RSS,
        source_name=source_name,
        title=f"Northwind buys Halcyon, take {n}",
        body="Northwind confirmed the acquisition this morning.",
        url=f"https://example.com/story-{n}",
        timestamp=DATE,
    )


def _story(cluster_id: str, members: list[Item]) -> Story:
    # Same canonical for every story here, so personal relevance, urgency and
    # recency are identical and the only term left moving is w_cluster.
    return Story(cluster_id=cluster_id, canonical=members[0], members=members)


def test_one_publication_covering_itself_is_not_a_popularity_signal():
    """`cluster_size` counts members. A publication running a follow-up on
    its own story therefore scored as though the wider press had picked it
    up — the same fake boost the RSS ingestion comment describes for
    duplicate urls, one level up and still reachable.
    """
    verge = [_article(n, "The Verge") for n in range(3)]
    solo = _story("solo", [verge[0]])
    repeat = _story("repeat", verge)

    scored = {s.cluster_id: s.score for s in score_stories(
        [solo, repeat], calendar_events=[], now=CLOCK
    )}
    assert scored["repeat"] == scored["solo"]


def test_three_publications_still_outrank_one():
    """The discipline must not cost the signal it protects: several outlets
    on one story is what ADR-003 exists to surface."""
    solo = _story("solo", [_article(0, "The Verge")])
    spread = _story("spread", [
        _article(0, "The Verge"), _article(1, "Reuters"), _article(2, "BBC News")
    ])

    scored = {s.cluster_id: s.score for s in score_stories(
        [solo, spread], calendar_events=[], now=CLOCK
    )}
    assert scored["spread"] > scored["solo"]


# ──────────────────────────────────────────────────────────────
# Which calendar window the personal term sees
# ──────────────────────────────────────────────────────────────


def _event(event_id: str, attendee: str) -> CalendarEvent:
    return CalendarEvent(
        id=event_id, title="Meeting", start=DATE, end=DATE, attendees=[attendee]
    )


def test_an_attendee_from_later_in_the_window_still_lifts_a_story():
    """Deliberate, and pinned here because it looks like an oversight.

    `select_for_briefing` narrows events to the briefing day; ranking is
    handed the full PRD F-02 fetch window on purpose. The narrowing exists
    because the calendar segment is "the day walked chronologically" and the
    cold open is an observation "about the day ahead" — both make a claim
    about TODAY. The world segment makes no such claim, and a story about
    someone you are seeing on Thursday is the prep the 72-hour window is for.
    """
    story = _story("chen", [_article(0, "Reuters")])
    story.canonical.title = "Sam Chen's firm acquired"

    # Floats captured immediately: score_stories writes Story.score in place,
    # so holding the returned list and reading .score afterwards compares one
    # object with itself.
    with_window = score_stories(
        [story], calendar_events=[_event("thu", "Sam Chen")], now=CLOCK
    )[0].score
    without = score_stories([story], calendar_events=[], now=CLOCK)[0].score
    assert with_window > without


def test_a_wider_window_does_not_cost_a_today_story_its_score():
    """The control for the claim that a future appointment can displace a
    story relevant to today. It cannot: the today story's score does not
    move. What the extra attendees lift is a story that would otherwise have
    NO personal connection at all, which is the personal term working.
    """
    today_story = _story("today", [_article(0, "Reuters")])
    today_story.canonical.title = "Priya Raman named CFO"

    today_only = _event("today", "Priya Raman")
    later = _event("thu", "Sam Chen")

    narrow = score_stories(
        [today_story], calendar_events=[today_only], now=CLOCK
    )[0].score
    wide = score_stories(
        [today_story], calendar_events=[today_only, later], now=CLOCK
    )[0].score
    assert narrow == wide


async def test_ranking_is_handed_the_whole_fetch_window_not_just_today(monkeypatch):
    """The wiring decision itself, pinned where it is made.

    The two tests above measure `score_stories`; this one measures what
    `generate_briefing` hands it. Narrowing the events passed here to the
    briefing day is a one-word change that no other test would catch, and it
    would silently delete the prep value of the 72-hour window.
    """
    from app.core.schemas import BriefingLength
    from app.pipeline import generate as gen

    seen: dict = {}

    def _capture(stories, *, calendar_events, **kwargs):
        seen["events"] = calendar_events
        return stories

    monkeypatch.setattr(gen, "score_stories", _capture)
    await gen._build_world_stories(
        emails=[],
        articles=[_article(0, "Reuters")],
        events=[_event("today", "Priya Raman"), _event("thu", "Sam Chen")],
        embeddings=None,
        length=BriefingLength.STANDARD,
        now=CLOCK,
    )
    assert len(seen["events"]) == 2, seen


# ──────────────────────────────────────────────────────────────
# Which clock recency is measured from
# ──────────────────────────────────────────────────────────────


def test_recency_is_measured_from_the_clock_it_is_handed():
    """An hour older against the same `now` scores lower; the same story
    against the same `now` scores the same, however many days later the
    suite runs."""
    fresh = _story("fresh", [_article(0, "Reuters")])
    stale = _story("stale", [_article(1, "Reuters")])
    stale.canonical.timestamp = DATE.replace(hour=5)

    ranked = score_stories([stale, fresh], calendar_events=[], now=CLOCK)
    assert [s.cluster_id for s in ranked] == ["fresh", "stale"]


def test_no_clock_falls_back_to_the_wall_clock(monkeypatch):
    """The production default for any caller other than `generate_briefing`.

    conftest makes `_wall_clock` raise for every test, so this swaps in a
    fixed one to show that `now=None` really does go through it, and that
    the result matches passing the same instant explicitly.
    """
    from app.pipeline import ranking

    monkeypatch.setattr(ranking, "_wall_clock", lambda: CLOCK)
    story = _story("s", [_article(0, "Reuters")])

    implicit = score_stories([story], calendar_events=[])[0].score
    explicit = score_stories([story], calendar_events=[], now=CLOCK)[0].score
    assert implicit == explicit
