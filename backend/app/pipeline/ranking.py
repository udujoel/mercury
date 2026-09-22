"""
Ranking and budget allocation.

The LLM cannot receive everything. This decides what makes the cut.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

import numpy as np

from app.core.config import get_settings
from app.core.schemas import CalendarEvent, Story


def score_stories(
    stories: list[Story],
    *,
    calendar_events: list[CalendarEvent],
    topic_centroid: np.ndarray | None = None,
    followed_entities: set[str] | None = None,
    judged_personal: dict[str, float] | None = None,
) -> list[Story]:
    """Score every story and return sorted descending.

    `judged_personal` maps `cluster_id` to P(this matters to this person
    today), from `app.pipeline.relevance` (ADR-017). Where a story has an
    entry, it REPLACES the lexical personal term — same weight, same
    [0, 1] range, nothing else about the formula moves. A story with no
    entry keeps the lexical score, so an absent or failed judgment leaves
    ranking exactly as it was (rule 7).
    """
    s = get_settings()
    now = datetime.now(UTC)

    attendee_names = {
        a.lower()
        for ev in calendar_events
        for a in ev.attendees
    }
    # Stripped, and short names dropped.
    #
    # Review of #58: matching was an unanchored substring worth 0.28 —
    # larger than the interest term's entire range. Following "X" (the
    # company) matched 10 of 25 stories in the clustering corpus, because
    # "extension" contains an x, and put a Hacker News dark-mode post
    # above a Russian missile strike. "AI" matched "said", "again",
    # "chair" and "Ukraine". An unstripped `[" "]` matched everything.
    #
    # Two characters cannot be matched safely even on a word boundary
    # ("AI" is a word in "AI" and in nothing useful else), so the floor is
    # three and the match is anchored.
    followed = {
        e.strip().lower() for e in (followed_entities or set())
        if len(e.strip()) >= 3
    }
    followed_re = (
        re.compile(
            "|".join(rf"\b{re.escape(e)}\b" for e in sorted(followed)),
            re.I,
        )
        if followed else None
    )

    judged = judged_personal or {}

    for story in stories:
        item = story.canonical

        # Personal relevance — does this touch someone or something in my day
        haystack = f"{item.title} {item.body[:600]}".lower()
        personal = 0.0
        # `is not None`, not `or`: a judged 0.0 is a real verdict — "this
        # is not about your day" — and must not fall through to the
        # lexical branches that would then score it 0.5 for being email.
        if (verdict := judged.get(story.cluster_id)) is not None:
            personal = verdict
        elif any(name and name in haystack for name in attendee_names):
            personal = 1.0
        # `source_name` is in the haystack deliberately: following a
        # PUBLICATION is the most obvious thing a person types into this
        # box, and before review it was the one thing that could not
        # match, because the haystack was title and body only.
        elif followed_re is not None and followed_re.search(
            f"{item.source_name} {haystack}"
        ):
            personal = 0.8
        elif item.source_type.value in ("email", "slack", "calendar"):
            personal = 0.5

        # Urgency
        urgency = 1.0 if item.requires_response else 0.0

        # Topic match, rescaled from cosine [-1, 1] to [0, 1].
        #
        # Not cosmetic. The fallback for an item with no usable embedding
        # is the constant 0.5, and a raw cosine is NOT on that scale:
        # review of #58 measured an unembeddable item ("!!!") taking the
        # TOP of the world segment once interests were set, above the
        # story the interests were about, because 0.5 beat every real
        # similarity. `dedup.build_stories` nulls embeddings per item, so
        # this is uneven rather than run-wide — the change meant to demote
        # junk promoted it.
        #
        # `(1 + cos) / 2` makes 0.5 mean "orthogonal", which is what the
        # fallback always meant: no information either way.
        if topic_centroid is not None and item.embedding:
            v = np.array(item.embedding, dtype=np.float32)
            v /= np.linalg.norm(v) or 1.0
            interest = (1.0 + float(np.dot(v, topic_centroid))) / 2.0
        else:
            interest = 0.5

        # How many independent sources carried it. Distinct source names,
        # not member count: a publication running a follow-up on its own
        # story scored as though the wider press had picked it up — the fake
        # popularity boost app/ingestion/rss.py describes for duplicate urls,
        # one level up and still reachable now that newsletters and feeds
        # cluster together and a publication's own newsletter can sit beside
        # its article.
        cluster = math.log1p(len(story.source_names)) / math.log(6)

        # Recency decay
        hours = max((now - _aware(item.timestamp)).total_seconds() / 3600, 0)
        recency = math.exp(-hours / 12)

        story.score = (
            s.w_personal * personal
            + s.w_urgency * urgency
            + s.w_interest * interest
            + s.w_cluster * min(cluster, 1.0)
            + s.w_recency * recency
        )

    return sorted(stories, key=lambda x: x.score, reverse=True)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def topic_centroid(topic_embeddings: list[list[float]]) -> np.ndarray | None:
    """Centroid of the user's selected interest topics, normalised.

    Rows that cannot be normalised are DROPPED rather than divided by
    zero. Review of #58: the final norm was guarded and the per-row one
    was not, so a single degenerate topic — "!!!", an emoji, any string
    the provider embeds to zeros — produced `0/0` = NaN, `mean` spread it
    across every dimension, and `score_stories` then set `score = nan` for
    every story. `sorted` on NaN is arbitrary, so the world segment
    silently fell back to whatever order clustering emitted. No exception,
    no log line: the worst possible failure for a ranking change.

    `dedup._is_usable_vector` is the same guard one module away.
    """
    if not topic_embeddings:
        return None
    m = np.array(topic_embeddings, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    usable = np.isfinite(norms).all(axis=1) & (norms > 0).all(axis=1)
    if not usable.any():
        return None
    m = m[usable] / norms[usable]
    c = m.mean(axis=0)
    if not np.isfinite(c).all():
        return None
    return c / (np.linalg.norm(c) or 1.0)
