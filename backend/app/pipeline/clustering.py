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
from datetime import UTC, datetime

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from app.core.schemas import Item, SourceType, Story

log = logging.getLogger(__name__)

# Sources considered authoritative when picking a cluster's canonical item.
# Primary reporting beats commentary.
AUTHORITY_ORDER = [
    SourceType.RSS,
    # Below RSS, not above it. This list used to open with EMAIL, justified
    # as "your own inbox always wins — it is yours", which is a good rule for
    # a colleague's mail and the wrong one for the only clusters that ever
    # have a choice to make. A cross-source cluster is ADR-003's worked
    # example: Reuters carries the announcement and Stratechery, Benedict
    # Evans and Platformer write ABOUT it. Those newsletters are not "yours"
    # in any sense that matters — they are commentary that happens to arrive
    # by email — and putting them first contradicted this module's own
    # docstring in the one case it was written for.
    #
    # It also cost the story its link. `app/ingestion/gmail.py` builds every
    # email `Item` with `url=None` (there is no public URL for a message in
    # your inbox), so an email canonical hands the manifest a `SourceRef`
    # with no URL and the show-notes entry for the day's biggest story is
    # not tappable — PRD F-19.
    SourceType.EMAIL,
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
        return (authority, _as_utc(item.timestamp), -len(item.body))

    return sorted(members, key=rank)[0]


def _as_utc(dt: datetime) -> datetime:
    """Read a naive timestamp as UTC so it can be ordered against an aware one.

    `rank` puts the timestamp inside a tuple, so the moment two members tie
    on authority `sorted` compares the datetimes directly — and comparing a
    naive datetime to an aware one raises TypeError rather than returning
    False. One such pair took the ENTIRE clustering pass down: build_stories
    catches the exception and degrades to unclustered singletons, so every
    story in the briefing silently stopped merging and the only trace was a
    log line.

    It needs no exotic input. `app/ingestion/gmail.py`'s `_parse_date` falls
    back to `email.utils.parsedate_to_datetime`, which returns a NAIVE
    datetime for the RFC 2822 "unknown timezone" offset `-0000` — common in
    bulk newsletter mail. Two newsletters in one cluster, one of them dated
    that way, is enough.

    UTC is the right reading rather than a convenience: every other
    timestamp in the pipeline is UTC (`ranking._aware` already assumes the
    same), and the alternative — sorting naive items into their own bucket —
    would make "whoever reported it first" mean something different
    depending on how the sender's mail server writes dates.
    """
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _singleton(item: Item) -> Story:
    item.cluster_id = item.cluster_id or f"s{item.id}"
    return Story(cluster_id=item.cluster_id, canonical=item, members=[item])
