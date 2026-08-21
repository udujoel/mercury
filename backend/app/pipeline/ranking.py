"""
Ranking and budget allocation.

The LLM cannot receive everything. This decides what makes the cut.
"""

from __future__ import annotations

import math
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
) -> list[Story]:
    """Score every story and return sorted descending."""
    s = get_settings()
    now = datetime.now(UTC)

    attendee_names = {
        a.lower()
        for ev in calendar_events
        for a in ev.attendees
    }
    followed = {e.lower() for e in (followed_entities or set())}

    for story in stories:
        item = story.canonical

        # Personal relevance — does this touch someone or something in my day
        haystack = f"{item.title} {item.body[:600]}".lower()
        personal = 0.0
        if any(name and name in haystack for name in attendee_names):
            personal = 1.0
        elif any(e and e in haystack for e in followed):
            personal = 0.8
        elif item.source_type.value in ("email", "slack", "calendar"):
            personal = 0.5

        # Urgency
        urgency = 1.0 if item.requires_response else 0.0

        # Topic match
        if topic_centroid is not None and item.embedding:
            v = np.array(item.embedding, dtype=np.float32)
            v /= np.linalg.norm(v) or 1.0
            interest = float(np.dot(v, topic_centroid))
        else:
            interest = 0.5

        # How many independent sources carried it
        cluster = math.log1p(story.cluster_size) / math.log(6)

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
    """Centroid of the user's selected interest topics, normalised."""
    if not topic_embeddings:
        return None
    m = np.array(topic_embeddings, dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    c = m.mean(axis=0)
    return c / (np.linalg.norm(c) or 1.0)
