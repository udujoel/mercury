"""
Cross-source dedup: embed every item and wire the result into the existing
`cluster_items` scaffold.

ADR-003 / PRD F-08 — the case that matters: Stratechery, Benedict Evans and
Platformer all cover one acquisition, and Reuters carries the announcement.
That is ONE story with four sources attributed, not four items. Just as
important and easy to miss: an email newsletter and a news article about the
same event must also merge — that cross-source case is the entire reason
every source normalises into `Item` first, rather than newsletters and RSS
articles staying in separate shapes.

Rule 7 (graceful degradation): if embedding fails entirely, this must not
take the whole run down. Log it and fall back to one singleton Story per
item, unclustered, so the briefing still ships.
"""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.core.providers import EmbeddingProvider
from app.core.schemas import Item, Story
from app.pipeline.clustering import _singleton, cluster_items

log = logging.getLogger(__name__)


async def build_stories(
    items: list[Item],
    *,
    embeddings: EmbeddingProvider,
    threshold: float | None = None,
) -> list[Story]:
    """Embed every item, populate `Item.embedding`, and cluster into stories.

    Order of `items` passed to `embeddings.embed` is preserved so embeddings
    map back onto the right item — the EmbeddingProvider protocol promises
    order preservation, and this is the one place that promise gets relied
    on to attribute the right vector to the right item.

    Degrades per rule 7: if embedding raises, this logs and returns one
    singleton Story per item so the briefing still ships, minus clustering.
    """
    if not items:
        return []

    resolved_threshold = (
        threshold
        if threshold is not None
        else get_settings().cluster_similarity_threshold
    )

    try:
        texts = [item.embed_text for item in items]
        vectors = await embeddings.embed(texts)
    except Exception:
        log.exception(
            "embedding failed for %d items; shipping unclustered singletons",
            len(items),
        )
        return [_singleton(item) for item in items]

    if len(vectors) != len(items):
        log.error(
            "embedding provider returned %d vectors for %d items; "
            "refusing to guess an order mapping, shipping unclustered "
            "singletons",
            len(vectors), len(items),
        )
        return [_singleton(item) for item in items]

    for item, vector in zip(items, vectors, strict=True):
        item.embedding = vector

    return cluster_items(items, threshold=resolved_threshold)
