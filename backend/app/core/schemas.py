"""
The common schema every source normalises into.

This is the most important file in the backend. Gmail, Calendar, RSS, Reddit
and Slack all produce `Item` objects. That is what allows a newsletter and a
news article about the same event to deduplicate against each other.

Do not add source-specific fields here. Put them in `raw` if you need them.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    EMAIL = "email"
    CALENDAR = "calendar"
    RSS = "rss"
    REDDIT = "reddit"
    SLACK = "slack"
    SOCIAL = "social"
    WEATHER = "weather"


class Priority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Item(BaseModel):
    """A single piece of content from any source, normalised."""

    id: str = ""
    source_type: SourceType
    source_name: str                      # "Stratechery" | "Reuters" | "r/devops"
    title: str
    body: str = ""                        # cleaned plain text
    url: str | None = None
    author: str | None = None
    timestamp: datetime

    requires_response: bool = False
    priority: Priority = Priority.MEDIUM

    embedding: list[float] | None = None
    cluster_id: str | None = None
    entities: list[str] = Field(default_factory=list)

    raw: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.id:
            seed = f"{self.source_name}:{self.url or self.title}:{self.timestamp}"
            self.id = hashlib.sha256(seed.encode()).hexdigest()[:16]

    @property
    def embed_text(self) -> str:
        """Text used for embedding. Title carries most of the signal."""
        return f"{self.title}\n\n{self.body[:500]}"


class Story(BaseModel):
    """A cluster of Items covering the same event."""

    cluster_id: str
    canonical: Item                       # the representative item
    members: list[Item]
    score: float = 0.0

    @property
    def cluster_size(self) -> int:
        return len(self.members)

    @property
    def source_names(self) -> list[str]:
        return sorted({m.source_name for m in self.members})


class CalendarEvent(BaseModel):
    """Calendar events stay separate. They are not clustered with news."""

    id: str
    title: str
    start: datetime
    end: datetime
    location: str | None = None
    description: str | None = None
    attendees: list[str] = Field(default_factory=list)
    needs_prep: bool = False

    all_day: bool = False
    calendar_id: str | None = None
    # False for subscribed calendars the user only reads — holidays, week
    # numbers, contact birthdays, religious calendars. A real Google account
    # carries dozens of these; see `is_background`.
    owned: bool = True

    @property
    def is_background(self) -> bool:
        """Ambient context rather than something on the user's schedule.

        A live account here had 73 calendars and 10 of 14 events in the
        window were all-day entries from subscribed calendars ("Week 35 of
        2026", "10 Elul 5786", "Day of Remembrance for Victims of Communism
        and Nazism"). Walking those chronologically buries the two real
        appointments. They are not deleted — the PRD asks for all calendars —
        they are demoted to a single closing line.

        Birthdays need no special case: on a real account they live on the
        user's OWN calendar (calendar_id == their address), so `owned` already
        keeps them in the foreground, while "The Prophet's Birthday" sits on
        en.islamic#holiday and is correctly demoted. A title regex for
        "birthday" was tried first and promoted exactly that holiday — a
        heuristic compensating for a signal that was already available.
        """
        return self.all_day and not self.owned


class Weather(BaseModel):
    temperature_c: float
    condition: str
    high_c: float
    low_c: float
    hourly: list[dict[str, Any]] = Field(default_factory=list)
    location_name: str


# ──────────────────────────────────────────────────────────────
# Script and output types
# ──────────────────────────────────────────────────────────────


class Segment(str, Enum):
    COLD_OPEN = "cold_open"
    WEATHER = "weather"
    CALENDAR = "calendar"
    INBOX = "inbox"
    WORLD = "world"
    FOLLOW_UPS = "follow_ups"
    SIGN_OFF = "sign_off"


SEGMENT_LABELS: dict[Segment, str] = {
    Segment.COLD_OPEN: "Good morning",
    Segment.WEATHER: "Weather",
    Segment.CALENDAR: "Your day",
    Segment.INBOX: "Your inbox",
    Segment.WORLD: "Your world",
    Segment.FOLLOW_UPS: "Follow-ups",
    Segment.SIGN_OFF: "Sign-off",
}


class Turn(BaseModel):
    """One speaker turn in the generated script."""

    speaker: str                          # "A" | "B"
    segment: Segment
    text: str
    start_ms: int | None = None           # filled in after synthesis
    end_ms: int | None = None


class SourceRef(BaseModel):
    turn_index: int
    title: str
    url: str | None
    source_name: str
    source_type: SourceType
    cluster_size: int = 1


class BriefingManifest(BaseModel):
    """
    The single artifact the iOS client consumes.

    Drives transcript sync, chapter tabs, seeking, show notes and the context
    passed to the LLM during a voice interrupt. Get this right and every client
    feature is straightforward.
    """

    briefing_id: str                      # "2026-08-21"
    generated_at: datetime
    duration_ms: int
    audio_url: str
    segments: list[dict[str, Any]]        # [{id, label, start_ms}]
    turns: list[Turn]
    sources: list[SourceRef]

    # Which personal sources were missing when this was generated, e.g.
    # ["email", "calendar"]. Empty on a healthy run.
    #
    # Persisted because generation is idempotent and the existence check is
    # what makes the daily retry a no-op. Without it a briefing that shipped
    # with no inbox and no calendar — most of the point of Mercury — became
    # the one thing the retry could never replace, so a token that came back
    # to life at 06:00 healed nothing until the next day. Observed for three
    # consecutive days: the first run exits 3 and goes red, the next run
    # reports success because the degraded briefing is already there.
    degraded_sources: list[str] = Field(default_factory=list)

    # How many times this briefing has already been regenerated *because* it
    # shipped degraded. Zero on a healthy run, and reset to zero the moment
    # one succeeds.
    #
    # Without it the retry is unbounded, and the failure it retries — a dead
    # OAuth token — stays dead until a human intervenes. Eight checkpoints
    # fire on a broken day, so every one of them would run a full
    # generation: 8x the Gemini and TTS spend, daily, for a briefing that
    # cannot improve until someone re-mints a token. Google TTS's free tier
    # is a million characters a month and a briefing is roughly six
    # thousand, so that is not a rounding error.
    degraded_attempts: int = 0


class BriefingLength(str, Enum):
    EXPRESS = "express"
    STANDARD = "standard"
    FULL = "full"


# Natural conversational speech is roughly 150 words per minute.
# Budget by word count, not tokens, because words map to audio duration.
LENGTH_BUDGETS: dict[BriefingLength, dict[str, int]] = {
    BriefingLength.EXPRESS: {"words": 450, "emails": 3, "stories": 2},
    BriefingLength.STANDARD: {"words": 1050, "emails": 6, "stories": 4},
    BriefingLength.FULL: {"words": 2250, "emails": 10, "stories": 7},
}
