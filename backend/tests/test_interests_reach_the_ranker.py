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
    """The point of the whole task. If the order cannot move, the wiring
    is decoration."""
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
