"""
Tests for app/pipeline/dedup.py — build_stories: the wiring between an
EmbeddingProvider and the existing cluster_items scaffold, plus graceful
degradation (CLAUDE.md rule 7) when embedding fails outright.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.schemas import Item, SourceType
from app.pipeline.dedup import build_stories
from app.providers.fake_embeddings import FakeEmbeddingProvider
from tests.test_clustering_integration import (
    FAKE_PROVIDER_TEST_THRESHOLD,
    load_corpus_items,
)

NOW = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)


def make_item(
    source_type: SourceType, source_name: str, title: str, body: str = ""
) -> Item:
    return Item(
        source_type=source_type,
        source_name=source_name,
        title=title,
        body=body,
        timestamp=NOW,
    )


class RaisingEmbeddingProvider:
    """Simulates a total embedding outage — e.g. the API key is bad or the
    network is down mid-run."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("simulated embedding outage")


class WrongCountEmbeddingProvider:
    """Simulates a provider that silently drops or adds a result — the
    exact failure mode that would otherwise attribute the wrong vector to
    the wrong item with no error anywhere."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 1.0]] * (len(texts) - 1)


async def test_build_stories_empty_input_returns_empty():
    assert await build_stories([], embeddings=FakeEmbeddingProvider()) == []


async def test_build_stories_populates_item_embeddings():
    items = [
        make_item(SourceType.RSS, "A", "Some headline", "Some body text here"),
        make_item(SourceType.RSS, "B", "A different headline", "Different body"),
    ]
    await build_stories(items, embeddings=FakeEmbeddingProvider())
    assert all(i.embedding is not None for i in items)


async def test_build_stories_degrades_to_singletons_on_embedding_failure():
    """Rule 7: any source can fail and the briefing still ships. If
    embedding fails entirely, build_stories must not raise — it returns one
    singleton Story per item so the run still produces a briefing, just
    unclustered."""
    items = [
        make_item(SourceType.RSS, "A", "Headline one"),
        make_item(SourceType.EMAIL, "B", "Headline two"),
        make_item(SourceType.RSS, "C", "Headline three"),
    ]
    stories = await build_stories(items, embeddings=RaisingEmbeddingProvider())

    assert len(stories) == 3
    assert all(s.cluster_size == 1 for s in stories)
    titles = {s.canonical.title for s in stories}
    assert titles == {"Headline one", "Headline two", "Headline three"}


async def test_build_stories_degrades_to_singletons_on_length_mismatch():
    """A provider that drops results silently is exactly the "no error
    anywhere" failure mode the order-preservation contract warns about —
    build_stories must refuse to guess and degrade instead of mis-attributing
    a vector to the wrong item."""
    items = [
        make_item(SourceType.RSS, "A", "Headline one"),
        make_item(SourceType.RSS, "B", "Headline two"),
    ]
    stories = await build_stories(items, embeddings=WrongCountEmbeddingProvider())

    assert len(stories) == 2
    assert all(s.cluster_size == 1 for s in stories)


async def test_build_stories_threshold_override_takes_precedence():
    """Passing threshold=None should fall back to settings; passing an
    explicit value should be used as-is regardless of settings."""
    items = [
        make_item(SourceType.RSS, "A", "TechCorp buys DataFlow for billions"),
        make_item(SourceType.RSS, "B", "TechCorp acquires DataFlow, sources say"),
    ]
    # A very low explicit threshold should merge these two clearly related
    # headlines into one story.
    merged = await build_stories(
        [i.model_copy(deep=True) for i in items],
        embeddings=FakeEmbeddingProvider(),
        threshold=0.1,
    )
    assert len(merged) == 1
    assert merged[0].cluster_size == 2

    # An unreasonably high explicit threshold should keep them apart even
    # though they are near-duplicates.
    split = await build_stories(
        [i.model_copy(deep=True) for i in items],
        embeddings=FakeEmbeddingProvider(),
        threshold=0.999,
    )
    assert len(split) == 2


async def test_build_stories_end_to_end_on_corpus_matches_cluster_items():
    """build_stories on the full corpus should produce the same shape of
    result already verified directly against cluster_items in
    test_clustering_integration.py: the 4-outlet event collapses to one
    story, and the two similar-but-distinct rocket launches stay apart."""
    items = load_corpus_items()
    stories = await build_stories(
        items,
        embeddings=FakeEmbeddingProvider(),
        threshold=FAKE_PROVIDER_TEST_THRESHOLD,
    )

    event_story = next(
        s for s in stories if any("TechCorp to acquire" in m.title for m in s.members)
    )
    assert event_story.cluster_size == 4
    assert event_story.source_names == [
        "Benedict Evans",
        "Platformer",
        "Reuters",
        "Stratechery",
    ]

    spacex_story = next(
        s for s in stories if any("SpaceX launches" in m.title for m in s.members)
    )
    rocket_lab_story = next(
        s for s in stories if any("Rocket Lab launches" in m.title for m in s.members)
    )
    assert spacex_story is not rocket_lab_story


@pytest.mark.parametrize("threshold", [None])
async def test_build_stories_default_threshold_uses_settings(threshold, monkeypatch):
    """threshold=None should read settings.cluster_similarity_threshold, not
    silently apply some other default."""
    from app.core.config import get_settings

    monkeypatch.setenv("CLUSTER_SIMILARITY_THRESHOLD", "0.999")
    get_settings.cache_clear()
    try:
        items = [
            make_item(SourceType.RSS, "A", "TechCorp buys DataFlow for billions"),
            make_item(SourceType.RSS, "B", "TechCorp acquires DataFlow, sources say"),
        ]
        stories = await build_stories(
            items, embeddings=FakeEmbeddingProvider(), threshold=threshold
        )
        # With an (artificially) near-impossible threshold from settings,
        # even near-duplicate headlines should not merge.
        assert len(stories) == 2
    finally:
        get_settings.cache_clear()
