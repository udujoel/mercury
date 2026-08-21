"""
Story clustering and cross-source deduplication.

This is the Particle mechanic and the highest-leverage code in the system. It
is what makes the briefing feel edited rather than concatenated.

The case that matters most: Stratechery, Benedict Evans and Platformer all
cover the same acquisition, and Reuters carries the announcement. That is ONE
item in your briefing with four sources attributed, not four items.

At personal scale (a few hundred items a day) you do not need a vector
database. The similarity matrix fits comfortably in memory.
"""

from __future__ import annotations

import logging

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from app.core.schemas import Item, SourceType, Story

log = logging.getLogger(__name__)

# Sources considered authoritative when picking a cluster's canonical item.
# Primary reporting beats commentary.
AUTHORITY_ORDER = [
    SourceType.EMAIL,      # your own inbox always wins — it is yours
    SourceType.RSS,
    SourceType.SLACK,
    SourceType.REDDIT,
    SourceType.SOCIAL,
]


def cluster_items(
    items: list[Item],
    *,
    threshold: float = 0.82,
) -> list[Story]:
    """
    Agglomerative clustering over item embeddings.

    `threshold` is cosine similarity, not distance. Higher means stricter —
    fewer, tighter clusters. Start at 0.82 and tune against your own data.
    Too low and unrelated stories merge, which is far worse than a duplicate.
    """
    embedded = [i for i in items if i.embedding]
    unembedded = [i for i in items if not i.embedding]

    if unembedded:
        log.warning("%d items had no embedding, passing through unclustered",
                    len(unembedded))

    if len(embedded) < 2:
        return [_singleton(i) for i in embedded + unembedded]

    matrix = np.array([i.embedding for i in embedded], dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)

    # AgglomerativeClustering wants a distance threshold, not similarity.
    distance_threshold = 1.0 - threshold

    labels = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        metric="cosine",
        linkage="average",
    ).fit_predict(matrix)

    grouped: dict[int, list[Item]] = {}
    for item, label in zip(embedded, labels, strict=True):
        item.cluster_id = f"c{label}"
        grouped.setdefault(int(label), []).append(item)

    stories = [
        Story(
            cluster_id=f"c{label}",
            canonical=_pick_canonical(members),
            members=members,
        )
        for label, members in grouped.items()
    ]
    stories.extend(_singleton(i) for i in unembedded)

    merged = sum(len(s.members) - 1 for s in stories)
    log.info("clustered %d items into %d stories (%d duplicates collapsed)",
             len(items), len(stories), merged)

    return stories


def _pick_canonical(members: list[Item]) -> Item:
    """
    The representative item for a cluster.

    Prefer higher-authority source types, then earliest timestamp (whoever
    reported it first), then the most complete body.
    """
    def rank(item: Item) -> tuple:
        try:
            authority = AUTHORITY_ORDER.index(item.source_type)
        except ValueError:
            authority = len(AUTHORITY_ORDER)
        return (authority, item.timestamp, -len(item.body))

    return sorted(members, key=rank)[0]


def _singleton(item: Item) -> Story:
    item.cluster_id = item.cluster_id or f"s{item.id}"
    return Story(cluster_id=item.cluster_id, canonical=item, members=[item])
