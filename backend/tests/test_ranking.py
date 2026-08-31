"""
What the cluster term in `score_stories` is actually counting.

`score_stories` had no caller and no test at all until the news half of the
pipeline was wired in, so its w_cluster term — commented "how many
independent sources carried it" — had never been measured against a cluster
that was not made of independent sources.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.schemas import Item, SourceType, Story
from app.pipeline.ranking import score_stories

DATE = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)


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
        [solo, repeat], calendar_events=[]
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
        [solo, spread], calendar_events=[]
    )}
    assert scored["spread"] > scored["solo"]
