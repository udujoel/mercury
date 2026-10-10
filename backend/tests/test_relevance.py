"""
Personal relevance, asked rather than matched (ADR-017).

The lexical term this replaces went wrong twice (review of #58), and both
times the failure was invisible in the score. So these tests pin the
composition, not just the judgment.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime

from app.core.schemas import CalendarEvent, Item, SourceType, Story
from app.pipeline.ranking import score_stories
from app.pipeline.relevance import EXCERPT_CHARS, judge_personal_relevance
from app.providers.fake_judgment import FakeClaimJudge

NOW = datetime(2026, 9, 22, 7, 0, tzinfo=UTC)
IRRELEVANT = "__IRRELEVANT__"


def _slug(t):
    return hashlib.sha256(t.encode()).hexdigest()[:12]


def story(title, *, source="Reuters", body="body", kind=SourceType.RSS, cid=None):
    it = Item(
        source_type=kind, source_name=source, title=title, body=body,
        url=f"https://example.test/{_slug(title)}", timestamp=NOW,
    )
    it.cluster_id = cid or f"c-{_slug(title)}"
    return Story(cluster_id=it.cluster_id, canonical=it, members=[it])


def event(title="Standup", attendees=("priya@aveiro.test",)):
    return CalendarEvent(
        id="e1", title=title, start=NOW, end=NOW, attendees=list(attendees),
    )


# ── fallback ──────────────────────────────────────────────────────────


async def test_no_provider_means_no_opinion():
    got = await judge_personal_relevance(
        [story("A")], events=[event()], followed_entities=["TypeSafe"],
        interests=None, provider=None,
    )
    assert got == {}


async def test_a_provider_that_raises_means_no_opinion():
    class Exploding:
        async def judge_claims(self, state, claims, *, timeout=20.0):
            raise RuntimeError("down")

    got = await judge_personal_relevance(
        [story("A")], events=[event()], followed_entities=["TypeSafe"],
        interests=None, provider=Exploding(),
    )
    assert got == {}


async def test_nothing_to_be_relevant_to_makes_no_call():
    """With no attendees, follows or interests there is no context to
    reason from, and a judgment would be an opinion about the news in
    general rather than about this person."""
    judge = FakeClaimJudge()
    got = await judge_personal_relevance(
        [story("A")], events=[], followed_entities=[], interests=[],
        provider=judge,
    )
    assert got == {} and judge.claim_calls == []


async def test_an_empty_story_list_makes_no_call():
    judge = FakeClaimJudge()
    assert await judge_personal_relevance(
        [], events=[event()], followed_entities=["X"], interests=None,
        provider=judge,
    ) == {}
    assert judge.claim_calls == []


# ── composition into the score ────────────────────────────────────────


def test_a_judgment_replaces_the_lexical_personal_term():
    s = story("Anything at all")
    score_stories([s], calendar_events=[], judged_personal={id(s): 1.0}, now=NOW)
    high = s.score
    score_stories([s], calendar_events=[], judged_personal={id(s): 0.0}, now=NOW)
    assert high > s.score


def test_a_judged_zero_does_not_fall_through_to_the_email_default():
    """The subtle one.

    An email scores 0.5 on the lexical term just for being email. Written
    as `personal = judged.get(cid) or lexical`, a judged 0.0 — a real
    verdict of "this is not about your day" — would be falsy and fall
    through to that 0.5, so the judgment could only ever RAISE a score.
    """
    mail = story("A newsletter", kind=SourceType.EMAIL)
    score_stories(
        [mail], calendar_events=[], judged_personal={id(mail): 0.0}, now=NOW
    )
    judged_zero = mail.score
    score_stories([mail], calendar_events=[], judged_personal=None, now=NOW)
    assert judged_zero < mail.score, "a judged 0.0 fell through to the 0.5 default"


def test_an_unjudged_story_keeps_its_lexical_score():
    """Exact, because the clock is pinned.

    This was `pytest.approx` in #66, because `score_stories` read
    `datetime.now(UTC)` itself and `recency` decays continuously: two calls
    microseconds apart differed in the tenth decimal, and an `==` (as
    written in #63) passed only when both landed in the same tick. With
    `now=` passed, both calls age the story against the same instant, so
    any difference at all is the judgment leaking into an unjudged story.
    """
    mail = story("A newsletter", kind=SourceType.EMAIL)
    score_stories([mail], calendar_events=[], judged_personal={-1: 1.0}, now=NOW)
    with_map = mail.score
    score_stories([mail], calendar_events=[], judged_personal=None, now=NOW)
    assert with_map == mail.score


def test_ranking_is_byte_identical_without_a_judgment_map():
    """Rule 7: an absent judgment must leave ranking exactly as it was."""
    stories = [story(f"S{i}", kind=SourceType.EMAIL if i % 2 else SourceType.RSS)
               for i in range(6)]
    a = [s.cluster_id for s in score_stories(
        list(stories), calendar_events=[], now=NOW)]
    b = [s.cluster_id for s in score_stories(
        list(stories), calendar_events=[], judged_personal={}, now=NOW)]
    assert a == b


# ── request shape ─────────────────────────────────────────────────────


async def test_one_request_carries_the_person_and_every_story():
    judge = FakeClaimJudge()
    stories = [story(f"S{i}") for i in range(5)]
    await judge_personal_relevance(
        stories, events=[event()], followed_entities=["TypeSafe"],
        interests=["clustering"], provider=judge,
    )
    assert len(judge.claim_calls) == 1
    state, claims = judge.claim_calls[0]
    assert len(claims) == 5
    assert state["person"]["follows"] == ["TypeSafe"]
    assert state["person"]["interests"] == ["clustering"]
    assert "priya@aveiro.test" in state["person"]["meeting_today"]


async def test_the_story_payload_is_an_allowlist_and_truncated():
    judge = FakeClaimJudge()
    s = story("A", body="x" * 5000)
    s.canonical.raw = {"oauth_refresh_token": "must-not-leave"}
    await judge_personal_relevance(
        [s], events=[event()], followed_entities=["TypeSafe"], interests=None,
        provider=judge,
    )
    state, _ = judge.claim_calls[0]
    sent = state["stories"][0]
    assert set(sent) == {"source", "title", "excerpt"}
    assert len(sent["excerpt"]) == EXCERPT_CHARS
    assert "must-not-leave" not in str(state)


async def test_a_cap_on_the_pass_is_said_out_loud(caplog, monkeypatch):
    """A silent cap reads as 'we judged everything'."""
    from app.pipeline import relevance

    monkeypatch.setattr(relevance, "MAX_JUDGED", 2)
    stories = [story(f"S{i}") for i in range(5)]
    with caplog.at_level(logging.WARNING, logger="app.pipeline.relevance"):
        got = await judge_personal_relevance(
            stories, events=[event()], followed_entities=["TypeSafe"],
            interests=None, provider=FakeClaimJudge(),
        )
    assert len(got) == 2
    assert any("over the 2 this pass budgets for" in r.getMessage()
               for r in caplog.records)


# ── cluster_id is not a key (review of PR #63) ────────────────────────


async def test_two_stories_with_a_colliding_cluster_id_get_their_own_verdict():
    """Regression: the map was keyed on `cluster_id`.

    `clustering._singleton` sets `cluster_id = f"s{item.id}"`, and
    `Item.id` is a content hash — every Gmail item is built with
    `url=None`, so two messages from one sender with one subject at one
    timestamp share it. Keyed on cluster_id the two collapsed to ONE map
    entry and the second verdict overwrote the first: measured, a story
    judged 0.95 took a colliding story's 0.03 and both ranked as though
    neither mattered.

    This is the same defect review found in `cluster_verify` on #62, in a
    different module, three commits later.
    """
    def mail(body):
        return Item(
            source_type=SourceType.EMAIL, source_name="Morning Brew",
            title="Your daily brief", body=body, url=None, timestamp=NOW,
        )

    a, b = mail("Aveiro Labs raised a Series A."), mail("Arsenal beat Spurs.")
    assert a.id == b.id, "fixture must actually collide"
    for it in (a, b):
        it.cluster_id = f"s{it.id}"
    sa = Story(cluster_id=a.cluster_id, canonical=a, members=[a])
    sb = Story(cluster_id=b.cluster_id, canonical=b, members=[b])
    assert sa.cluster_id == sb.cluster_id

    class _PerStory:
        async def judge_claims(self, state, claims, *, timeout=20.0):
            return {
                c.key: (0.95 if "Aveiro" in state["stories"][i]["excerpt"] else 0.03)
                for i, c in enumerate(claims)
            }

    judged = await judge_personal_relevance(
        [sa, sb], events=[event()], followed_entities=["TypeSafe"],
        interests=None, provider=_PerStory(),
    )
    assert len(judged) == 2, "two stories collapsed into one verdict"

    ranked = score_stories(
        [sa, sb], calendar_events=[], judged_personal=judged, now=NOW
    )
    assert ranked[0] is sa, "the relevant story took the other's verdict"
    assert ranked[0].score > ranked[1].score
