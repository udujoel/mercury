"""
Integration test for embeddings + clustering against a realistic corpus
(tests/fixtures/clustering_corpus.json), driven entirely by
FakeEmbeddingProvider — no live calls, ever, per conftest.py.

This is the actual test of whether clustering works (ADR-003 / PRD F-08):
- one event covered by 4 outlets with differently worded headlines,
  including one EMAIL newsletter (Stratechery) and three RSS pieces
  (Reuters, Benedict Evans, Platformer), must collapse to ONE story with
  all 4 source_names attributed.
- two RSS items that share a lot of surface vocabulary (SpaceX/Starship vs
  Rocket Lab/Neutron test flights) but describe genuinely distinct events
  must NOT merge.
- the remaining singletons must stay singletons (no spurious merges).

Also provides `sweep_thresholds`, the offline harness the brief asks for: it
reports cluster counts (and the two probe outcomes above) across a range of
thresholds so the number can be tuned against evidence.

Threshold note: FakeEmbeddingProvider is a sparse, entity-weighted
hash-of-vocabulary embedding (see app/providers/fake_embeddings.py), not a
dense semantic embedding, so its cosine similarities live on a different
scale than a real model's. `settings.cluster_similarity_threshold` (0.82) is
tuned for the LIVE Gemini provider (see docs/phase2/clustering.md for that
run). The tests here use a fake-provider-specific threshold, verified below
to hold correctly across a wide band (0.55-0.68), not just at one brittle
point.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.core.schemas import Item, Priority, SourceType, Story
from app.pipeline.clustering import cluster_items
from app.providers.fake_embeddings import FakeEmbeddingProvider

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "clustering_corpus.json"

# Verified in isolation (see docs/phase2/clustering.md) to correctly collapse
# the 4-outlet event into one story and keep the two superficially-similar
# rocket launches apart, with margin on both sides — not a single lucky
# value. See the threshold sweep test below for the evidence.
FAKE_PROVIDER_TEST_THRESHOLD = 0.60


def load_corpus_items() -> list[Item]:
    data = json.loads(FIXTURE_PATH.read_text())["items"]
    items = []
    for raw in data:
        items.append(
            Item(
                source_type=SourceType(raw["source_type"]),
                source_name=raw["source_name"],
                title=raw["title"],
                body=raw["body"],
                url=raw.get("url"),
                author=raw.get("author"),
                timestamp=datetime.fromisoformat(raw["timestamp"]),
                requires_response=raw.get("requires_response", False),
                priority=Priority(raw.get("priority", "medium")),
            )
        )
    return items


async def embed_corpus(items: list[Item]) -> list[Item]:
    provider = FakeEmbeddingProvider()
    vectors = await provider.embed([i.embed_text for i in items])
    for item, vector in zip(items, vectors, strict=True):
        item.embedding = vector
    return items


def story_containing(stories: list[Story], title_substring: str) -> Story:
    for story in stories:
        if any(title_substring in m.title for m in story.members):
            return story
    raise AssertionError(f"no story contains a member titled like {title_substring!r}")


def sweep_thresholds(items: list[Item], thresholds: list[float]) -> list[dict]:
    """Offline threshold-tuning harness.

    For each threshold, cluster the (already-embedded) corpus and report the
    cluster count plus the two behaviours that actually matter: did the
    4-outlet event collapse to one story, and did the two similar-but-
    distinct rocket launches stay apart.
    """
    report = []
    for threshold in thresholds:
        # cluster_items mutates item.cluster_id in place; clone per run so
        # sweeping thresholds doesn't contaminate the next iteration.
        round_items = [i.model_copy(deep=True) for i in items]
        stories = cluster_items(round_items, threshold=threshold)
        event_story = story_containing(stories, "TechCorp to acquire")
        rocket_a = story_containing(stories, "SpaceX launches")
        rocket_b = story_containing(stories, "Rocket Lab launches")
        report.append(
            {
                "threshold": threshold,
                "n_stories": len(stories),
                "event_cluster_sources": sorted(
                    {m.source_name for m in event_story.members}
                ),
                "event_cluster_size": event_story.cluster_size,
                "rockets_merged": rocket_a is rocket_b,
            }
        )
    return report


# ──────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────


async def test_four_outlet_event_collapses_to_one_story():
    items = await embed_corpus(load_corpus_items())
    stories = cluster_items(items, threshold=FAKE_PROVIDER_TEST_THRESHOLD)

    event_story = story_containing(stories, "TechCorp to acquire")
    assert event_story.cluster_size == 4
    assert event_story.source_names == [
        "Benedict Evans",
        "Platformer",
        "Reuters",
        "Stratechery",
    ]


async def test_event_cluster_includes_cross_source_email_and_rss():
    """The specific case ADR-003 calls out: an EMAIL newsletter (Stratechery)
    and RSS news items (Reuters, Benedict Evans, Platformer) about the same
    event must merge into the same story, not stay apart because they came
    from different source types."""
    items = await embed_corpus(load_corpus_items())
    stories = cluster_items(items, threshold=FAKE_PROVIDER_TEST_THRESHOLD)

    event_story = story_containing(stories, "TechCorp to acquire")
    source_types = {m.source_type for m in event_story.members}
    assert SourceType.EMAIL in source_types
    assert SourceType.RSS in source_types


async def test_similar_but_distinct_rocket_launches_do_not_merge():
    items = await embed_corpus(load_corpus_items())
    stories = cluster_items(items, threshold=FAKE_PROVIDER_TEST_THRESHOLD)

    spacex_story = story_containing(stories, "SpaceX launches")
    rocket_lab_story = story_containing(stories, "Rocket Lab launches")
    assert spacex_story is not rocket_lab_story
    assert spacex_story.cluster_size == 1
    assert rocket_lab_story.cluster_size == 1


async def test_unrelated_singletons_stay_singletons():
    items = await embed_corpus(load_corpus_items())
    stories = cluster_items(items, threshold=FAKE_PROVIDER_TEST_THRESHOLD)

    for title_substring in (
        "restaurants to open in Lisbon",
        "Fossil discovery",
        "static site generator",
        "Federal Reserve holds",
    ):
        story = story_containing(stories, title_substring)
        assert story.cluster_size == 1, (
            f"{title_substring!r} unexpectedly merged with "
            f"{[m.title for m in story.members]}"
        )


async def test_threshold_sweep_shows_a_stable_correct_band():
    """The offline tuning harness required by the brief: report cluster
    counts and the two probe outcomes across a range of thresholds. Asserts
    there's a genuine band of correct thresholds, not one brittle point."""
    items = await embed_corpus(load_corpus_items())
    thresholds = [0.50, 0.55, 0.58, 0.60, 0.62, 0.65, 0.68, 0.70, 0.75]
    report = sweep_thresholds(items, thresholds)

    correct = [
        r for r in report if r["event_cluster_size"] == 4 and not r["rockets_merged"]
    ]
    # 0.55 through 0.68 all land correctly in the standalone sweep this
    # threshold was chosen from (see docs/phase2/clustering.md) — assert a
    # meaningful band survives, not just the single chosen value.
    assert len(correct) >= 4, report
    assert any(r["threshold"] == FAKE_PROVIDER_TEST_THRESHOLD for r in correct), report

    # Too low: everything (including the two distinct rocket launches)
    # merges into a soup. Too high: the actual event cluster splits apart.
    too_low = next(r for r in report if r["threshold"] == 0.50)
    assert too_low["rockets_merged"] is True
    too_high = next(r for r in report if r["threshold"] == 0.75)
    assert too_high["event_cluster_size"] < 4
