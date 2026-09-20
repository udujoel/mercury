"""ROADMAP task 30 — the ranker was never connected to the reader.

`score_stories` has accepted `topic_centroid` and `followed_entities`
since Phase 2, and no caller ever passed either. So `interest` was the
constant 0.5 and its 0.20 weight cancelled; with 153 of 160 stories
single-outlet the cluster term was constant too. The world segment was
`0.12 x cluster + 0.08 x recency` — a recency sort with extra steps.

On 2026-09-13 the two stories that reached the audio were the two most
recently published of 209, and "Ask HN: how can I browse HN in dark
mode?" beat a Russian strike on a Ukrainian train by six minutes.

Third of the four "built and never called" bugs in this project's
history, after `n_stories=0` and `music_set`, and the reason
`app/pipeline/census.py` exists. So these tests assert on what
`score_stories` is ACTUALLY HANDED by `generate_briefing` — not on a
hand-built call, which is how the parameter stayed dead through two
phases of green suites.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import numpy as np

from app.core.schemas import BriefingLength, Item, SourceType
from app.pipeline.generate import _build_world_stories, _topic_centroid
from app.providers.fake_embeddings import FakeEmbeddingProvider

NOW = datetime(2026, 9, 20, 6, tzinfo=UTC)


def _article(name: str, title: str, body: str, minutes_old: int) -> Item:
    return Item(
        id=f"a-{name}", source_type=SourceType.RSS, source_name=name,
        title=title, body=body, url=f"https://{name}.test/x",
        timestamp=NOW.replace(minute=59 - minutes_old), raw={},
    )


# The 2026-09-13 shape: a trivial recent item against real news that is
# slightly older.
DARK_MODE = _article(
    "hn", "Ask HN: how can I browse HN in dark mode?",
    "I would like a dark theme for Hacker News. What browser extension "
    "do people use for this?", minutes_old=0,
)
STRIKE = _article(
    "reuters", "Russian strike hits Ukrainian train station",
    "A Russian missile struck a railway station in eastern Ukraine, "
    "killing several people and halting evacuation trains.", minutes_old=6,
)


async def _world(interests=None, entities=None):
    world, _, _ = await _build_world_stories(
        emails=[], articles=[DARK_MODE, STRIKE], events=[],
        embeddings=FakeEmbeddingProvider(), length=BriefingLength.STANDARD,
        interests=interests, followed_entities=entities,
    )
    return [story.canonical.source_name for story in world]


async def test_the_centroid_reaches_score_stories_from_the_real_path(
    monkeypatch
):
    """The wiring, asserted where it was missing.

    `score_stories` is patched to record what it receives, so this fails
    the moment somebody stops forwarding the argument — which is the
    failure that survived two phases.
    """
    seen: dict = {}
    from app.pipeline import generate as gen

    real = gen.score_stories

    def _spy(stories, **kwargs):
        seen.update(kwargs)
        return real(stories, **kwargs)

    monkeypatch.setattr(gen, "score_stories", _spy)
    await _world(interests=["war in Ukraine"], entities=["Reuters"])

    assert seen["topic_centroid"] is not None, (
        "generate_briefing computed no centroid, so the 0.20 interest "
        "weight goes on cancelling and the segment stays a recency sort"
    )
    assert seen["followed_entities"] == {"Reuters"}


async def test_interests_change_which_story_leads():
    """The order MOVES. Deliberately not a claim about direction.

    Review of #58: `FakeEmbeddingProvider` hashes each token to a +-1
    sign, so with non-overlapping vocabulary the cosine is small-magnitude
    hash noise of arbitrary sign. Under it, `["front-end web
    development"]` also puts reuters first — so asserting direction from
    one phrasing here measures token overlap and luck. What this test
    honestly proves is the wiring claim: the centroid reaches the ranker
    and changes the result. `test_the_interest_term_points_the_right_way`
    does direction, against recency pulling the other way.
    """
    without = await _world()
    with_war = await _world(interests=[
        "war in Ukraine", "Russian missile strikes", "European security",
    ])

    assert without != with_war, (
        "the interest term did not move the order at all — the parameter "
        "is connected but inert, which is the bug wearing a disguise"
    )
    assert with_war[0] == "reuters"


async def test_no_interests_leaves_the_ranking_exactly_as_it_was():
    """Empty is the default and must be a no-op, not a behaviour change
    nobody asked for."""
    from app.pipeline.ranking import score_stories

    assert await _world() == await _world(interests=[])
    # And the ranker really does fall back to its constant.
    assert score_stories([], calendar_events=[], topic_centroid=None) == []


async def test_an_empty_setting_says_so_every_morning(caplog):
    """A silent no-op is exactly how this parameter went unnoticed for two
    phases. The run log has to state that the term is inactive."""
    with caplog.at_level(logging.INFO):
        await _topic_centroid([], FakeEmbeddingProvider())

    assert "no interest topics configured" in caplog.text


async def test_configured_interests_are_named_in_the_log(caplog):
    with caplog.at_level(logging.INFO):
        centroid = await _topic_centroid(
            ["war in Ukraine", "semiconductors"], FakeEmbeddingProvider()
        )

    assert centroid is not None
    assert "war in Ukraine" in caplog.text


async def test_a_failing_embeddings_provider_costs_the_term_not_the_run(
    caplog
):
    """Rule 7. Clustering is already allowed to fail gracefully; a ranking
    refinement is a far weaker reason to lose a briefing."""
    class _Broken:
        async def embed(self, texts):
            raise RuntimeError("embeddings are down")

    with caplog.at_level(logging.WARNING):
        assert await _topic_centroid(["anything"], _Broken()) is None
    assert "could not embed interest topics" in caplog.text


async def test_the_centroid_is_a_unit_vector():
    """`score_stories` takes a plain dot product against it, so a
    non-normalised centroid silently rescales the whole interest term."""
    centroid = await _topic_centroid(
        ["war in Ukraine", "semiconductors"], FakeEmbeddingProvider()
    )
    assert np.isclose(np.linalg.norm(centroid), 1.0)


async def test_generate_briefing_itself_forwards_the_interests(
    tmp_path, monkeypatch
):
    """The link that was actually broken, and the one the tests above miss.

    Everything above calls `_build_world_stories` directly. Mutation
    testing showed that deleting `interests=interests` from
    `generate_briefing`'s call to it left all of them green — which is
    PRECISELY the shape of the bug this task fixes, reproduced inside its
    own test file. The parameter was never wrong; the caller never passed
    it. So this enters at the top, where the omission lived.
    """
    from app.core.schemas import BriefingLength as BL
    from app.pipeline import generate as gen
    from app.pipeline.generate import generate_briefing
    from app.providers.fake import FakeLLMProvider
    from app.providers.fake_tts import FakeTTSProvider
    from app.synthesis.storage import LocalFilesystemStorage

    seen: dict = {}
    real = gen.score_stories

    def _spy(stories, **kwargs):
        seen.update(kwargs)
        return real(stories, **kwargs)

    monkeypatch.setattr(gen, "score_stories", _spy)

    async def _async(value):
        return value

    await generate_briefing(
        briefing_id="2026-09-20", length=BL.STANDARD,
        llm=FakeLLMProvider(), tts=FakeTTSProvider(),
        embeddings=FakeEmbeddingProvider(),
        storage=LocalFilesystemStorage(base_dir=tmp_path),
        fetch_emails=lambda **_: _async([]),
        fetch_events=lambda **_: _async([]),
        fetch_weather=lambda **_: _async(None),
        fetch_articles=lambda **_: _async([DARK_MODE, STRIKE]),
        research_provider=None,
        interests=["war in Ukraine"],
        followed_entities=["Reuters"],
    )

    assert seen.get("topic_centroid") is not None, (
        "generate_briefing did not forward `interests`; the ranker is "
        "back to a recency sort and every test above still passes"
    )
    assert seen.get("followed_entities") == {"Reuters"}


async def test_the_scheduled_job_forwards_the_settings(monkeypatch):
    """One level ABOVE `generate_briefing`, where the settings hop lives.

    The test above was written to fix exactly this mistake and then made
    it again: review of #58 deleted the `interests=` block from
    `cron.run_daily_briefing_job` and all 861 tests stayed green, because
    every test entered at `generate_briefing` or below.

    `generate_briefing` is not the top of the production path.
    `run_daily_briefing_job` is, and the settings -> pipeline hop is only
    visible from there. Third time this session the same shape has come
    back, so this one enters at the genuine top.
    """
    import contextlib

    from app.core.schemas import UserSettings
    from app.providers.fake import FakeLLMProvider
    from app.providers.fake_tts import FakeTTSProvider
    from app.scheduler import cron

    seen: dict = {}

    async def _spy(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("far enough — the arguments are what matter")

    monkeypatch.setattr(cron, "generate_briefing", _spy)

    class _Storage:
        def load_settings(self):
            return UserSettings(
                interests=["war in Ukraine"], followed_entities=["Reuters"],
            )

        def load_manifest(self, briefing_id):
            return None      # nothing generated yet, so the job proceeds

    with contextlib.suppress(Exception):
        await cron.run_daily_briefing_job(
            storage=_Storage(), llm=FakeLLMProvider(), tts=FakeTTSProvider(),
            embeddings=FakeEmbeddingProvider(),
        )

    assert seen.get("interests") == ["war in Ukraine"], (
        "the scheduled job dropped `interests` between the settings and "
        "the pipeline; every test below this level still passes"
    )
    assert seen.get("followed_entities") == ["Reuters"]


# ──────────────────────────────────────────────────────────────
# The scoring maths, after review of #58.
# ──────────────────────────────────────────────────────────────


JUNK = _article("junk", "!!!", "!!!", minutes_old=3)


async def test_an_unembeddable_item_does_not_lead_the_world_segment():
    """The change meant to demote junk was promoting it.

    `interest` fell back to the constant 0.5 for an item with no usable
    embedding, and a raw cosine is not on that scale — so the one story
    the pipeline could not embed outscored the story the interests were
    actually about. `dedup` nulls embeddings PER ITEM, so this is uneven
    rather than run-wide.
    """
    world, _, _ = await _build_world_stories(
        emails=[], articles=[DARK_MODE, STRIKE, JUNK], events=[],
        embeddings=FakeEmbeddingProvider(), length=BriefingLength.STANDARD,
        interests=["war in Ukraine", "Russian missile strikes"],
    )
    leaders = [s.canonical.source_name for s in world]
    assert leaders[0] != "junk", (
        f"the unembeddable item leads the segment: {leaders}"
    )


async def test_a_degenerate_interest_topic_does_not_nan_every_score():
    """One bad topic used to poison the whole run, silently.

    The per-row norm was unguarded, so `0/0` gave NaN, `mean` spread it
    everywhere, and `score_stories` set `score = nan` for every story.
    `sorted` on NaN is arbitrary, so the segment fell back to clustering
    order with no exception and no log line.
    """
    centroid = await _topic_centroid(
        ["war in Ukraine", "!!!"], FakeEmbeddingProvider()
    )
    assert centroid is not None
    assert np.isfinite(centroid).all(), "a degenerate topic poisoned it"

    world, _, _ = await _build_world_stories(
        emails=[], articles=[DARK_MODE, STRIKE], events=[],
        embeddings=FakeEmbeddingProvider(), length=BriefingLength.STANDARD,
        interests=["war in Ukraine", "!!!"],
    )
    assert all(np.isfinite(s.score) for s in world)


async def test_every_topic_degenerate_is_reported_not_silently_inert(caplog):
    with caplog.at_level(logging.WARNING):
        assert await _topic_centroid(["!!!"], FakeEmbeddingProvider()) is None
    assert "no usable centroid" in caplog.text


async def test_a_whitespace_only_setting_says_the_term_is_off(caplog):
    """The log line this PR leans on as its safety net was able to state
    the opposite of the truth: `[" "]` filtered to nothing, the term was
    inert, and the morning log said "ranked against 1 interest topic"."""
    with caplog.at_level(logging.INFO):
        assert await _topic_centroid(["   "], FakeEmbeddingProvider()) is None
    assert "no interest topics configured" in caplog.text
    assert "ranked against" not in caplog.text


async def test_a_raising_provider_costs_the_term_not_the_briefing():
    """Rule 7, and the docstring said "never raises" while it did —
    `topic_centroid(vectors)` sat outside the try, so ragged vectors
    killed the whole briefing from inside a ranking refinement."""
    class _Ragged:
        async def embed(self, texts):
            return [[0.1, 0.2], [0.1, 0.2, 0.3]]

    assert await _topic_centroid(["a", "b"], _Ragged()) is None


# ──────────────────────────────────────────────────────────────
# followed_entities — an unanchored substring re-created the bug.
# ──────────────────────────────────────────────────────────────


async def test_following_a_short_name_does_not_match_inside_words():
    """Following "X" matched 10 of 25 stories in the clustering corpus,
    because "extension" contains an x, and put the dark-mode post above a
    missile strike at 3.2x its score."""
    world, _, _ = await _build_world_stories(
        emails=[], articles=[DARK_MODE, STRIKE], events=[],
        embeddings=FakeEmbeddingProvider(), length=BriefingLength.STANDARD,
        interests=["war in Ukraine"], followed_entities=["X"],
    )
    assert [s.canonical.source_name for s in world][0] == "reuters"


async def test_an_all_whitespace_entity_does_not_match_everything():
    world, _, _ = await _build_world_stories(
        emails=[], articles=[DARK_MODE, STRIKE], events=[],
        embeddings=FakeEmbeddingProvider(), length=BriefingLength.STANDARD,
        followed_entities=[" "],
    )
    assert len({s.score for s in world}) > 1, (
        "every story got the same +0.28 personal boost"
    )


async def test_following_a_publication_by_name_works():
    """The most obvious thing a person types into this box, and before
    review it was the one thing that could not match: the haystack was
    title and body only, never `source_name`."""
    world, _, _ = await _build_world_stories(
        emails=[], articles=[DARK_MODE, STRIKE], events=[],
        embeddings=FakeEmbeddingProvider(), length=BriefingLength.STANDARD,
        followed_entities=["reuters"],
    )
    assert [s.canonical.source_name for s in world][0] == "reuters"


async def test_a_trailing_space_does_not_stop_a_match():
    world, _, _ = await _build_world_stories(
        emails=[], articles=[DARK_MODE, STRIKE], events=[],
        embeddings=FakeEmbeddingProvider(), length=BriefingLength.STANDARD,
        followed_entities=["Reuters "],
    )
    assert [s.canonical.source_name for s in world][0] == "reuters"


async def test_the_interest_term_points_the_right_way():
    """Direction, with recency pulling AGAINST it.

    The dark-mode post is made the OLDER item here, so recency favours
    the strike and any apparent win for dark mode has to come from the
    interest term. Then the same two stories are ranked under two
    opposite interest profiles and must swap.

    Review of #58 was right that the earlier assertion did not establish
    this: `assert with_war[0] == "reuters"` held under interests that
    mean the opposite. A single-direction check against a recency sort
    that already favours the same answer proves nothing.
    """
    old_dark_mode = _article(
        "hn", DARK_MODE.title, DARK_MODE.body, minutes_old=30,
    )
    fresh_strike = _article(
        "reuters", STRIKE.title, STRIKE.body, minutes_old=0,
    )

    async def _rank(interests):
        world, _, _ = await _build_world_stories(
            emails=[], articles=[old_dark_mode, fresh_strike], events=[],
            embeddings=FakeEmbeddingProvider(),
            length=BriefingLength.STANDARD, interests=interests,
        )
        return [s.canonical.source_name for s in world]

    assert await _rank(None) == ["reuters", "hn"], (
        "precondition: with no interests, recency puts the strike first"
    )
    assert await _rank(["dark theme", "browser extensions"]) == ["hn", "reuters"], (
        "interests about the HN post did not overcome recency, so the "
        "term is either inert or pointing the wrong way"
    )
    war = await _rank(["Russian missile strikes", "war in Ukraine"])
    assert war[0] == "reuters"


async def test_an_entity_does_not_match_inside_a_longer_word():
    """The word boundary, pinned on its own.

    The length floor and the anchor were covering for each other: "X" is
    dropped by the floor, so removing the anchor changed nothing, and
    "X" is anchored, so removing the floor changed nothing. Each guard
    needs a case the other cannot rescue. This one is long enough to pass
    the floor and only matches unanchored.
    """
    about_metadata = _article(
        "verge", "A new metadata standard for photos",
        "Photographers are arguing about metadata again this week.",
        minutes_old=30,
    )
    world, _, _ = await _build_world_stories(
        emails=[], articles=[about_metadata, STRIKE], events=[],
        embeddings=FakeEmbeddingProvider(), length=BriefingLength.STANDARD,
        followed_entities=["Meta"],
    )
    assert [s.canonical.source_name for s in world][0] == "reuters", (
        "following Meta matched 'metadata' and promoted an unrelated story"
    )


async def test_a_one_letter_entity_does_not_boost_ordinary_prose():
    """The length floor, pinned on its own.

    A single letter clears every word boundary in ordinary English —
    "I" is a word in most sentences — so anchoring cannot save it and the
    floor is the only thing that does.

    The HN post is aged so that recency favours the strike; otherwise it
    leads anyway and the assertion says nothing about entity matching.
    The first cut of this test got that wrong and failed for the right
    answer's reason.
    """
    aged_dark_mode = _article(
        "hn", DARK_MODE.title, DARK_MODE.body, minutes_old=30,
    )
    baseline, _, _ = await _build_world_stories(
        emails=[], articles=[aged_dark_mode, STRIKE], events=[],
        embeddings=FakeEmbeddingProvider(), length=BriefingLength.STANDARD,
    )
    assert [s.canonical.source_name for s in baseline][0] == "reuters", (
        "precondition: without any entity, recency puts the strike first"
    )

    world, _, _ = await _build_world_stories(
        emails=[], articles=[aged_dark_mode, STRIKE], events=[],
        embeddings=FakeEmbeddingProvider(), length=BriefingLength.STANDARD,
        followed_entities=["I"],
    )
    assert "I" in aged_dark_mode.body, "precondition: a standalone I"
    assert [s.canonical.source_name for s in world][0] == "reuters", (
        "a one-letter entity boosted a story for containing the word 'I'"
    )
