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
    # Which `Story` this ref came from, so the digest can join a browsable
    # story back onto the turn the hosts discussed it in.
    #
    # Nullable and cheap (one short string per ref) rather than a separate
    # index, because the alternative was matching on (title, source_name).
    # Two items can share both — a feed that publishes "Morning Briefing"
    # every day, carried in a 24-hour window that spans two of them — and
    # those land in different clusters, so a title join silently deep-links
    # a story into the wrong story's turn. None for calendar refs, which
    # belong to no cluster.
    cluster_id: str | None = None


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


# ──────────────────────────────────────────────────────────────
# The digest artifact
#
# A SIBLING of the manifest, not an extension of it. The manifest is what the
# player downloads to start playing audio; the digest is the browse surface —
# every newsletter that arrived and every story that clustered, including the
# many the ranker never gave to the hosts. Folding one into the other would
# make every player launch download a screen the user may never open.
#
# The shapes live here, beside BriefingManifest, because they are persisted
# artifacts. `app/pipeline/digest.py` builds them and `app/synthesis/storage.py`
# writes them, and neither should have to import the other.
# ──────────────────────────────────────────────────────────────

# Bumped when the SHAPE changes incompatibly, so an older iOS build can
# refuse a digest it would otherwise render as an empty list — a screen that
# says nothing and looks like "no newsletters today". Present from day one
# because the version you wish you had is always the one before the change.
DIGEST_SCHEMA_VERSION = 1



class DigestSource(BaseModel):
    """One publication's coverage of a story.

    Exactly the fields `SourceRef` already persists. If a field here needs
    the body to fill it, the field is wrong — see the module docstring.
    """

    source_name: str
    title: str
    url: str | None = None
    published_at: datetime
    source_type: SourceType


class DigestStory(BaseModel):
    id: str
    headline: str
    # Nullable, and null is a normal answer. The alternative for an
    # unenriched story would be an extractive snippet of the source text,
    # which is the one thing rule 5 forbids — an extract is the raw body with
    # a smaller word count.
    summary: str | None = None
    why_it_matters: str | None = None
    topics: list[str] = Field(default_factory=list)
    # == len(sources). Denormalised so a list row can render "5 sources"
    # without the client walking the array for every row it scrolls past.
    source_count: int
    sources: list[DigestSource]

    covered_in_audio: bool = False
    # Indexes manifest.turns AFTER compaction. Null unless covered.
    turn_index: int | None = None
    segment: str | None = None


class DigestNewsletter(BaseModel):
    id: str
    source_name: str
    sender: str | None = None
    subject: str
    received_at: datetime
    # Always null today: `app/ingestion/gmail.py` builds every email Item with
    # `url=None`, because there is no public URL for a message in your inbox
    # and the "view in browser" link would have to be dug out of a body that
    # rule 5 discards. Kept in the shape so the client does not need a schema
    # bump the day a web view exists, and documented so nobody builds a
    # tappable row expecting one.
    url: str | None = None
    story_ids: list[str] = Field(default_factory=list)
    in_briefing: bool = False


class BriefingDigest(BaseModel):
    briefing_id: str
    generated_at: datetime
    schema_version: int = DIGEST_SCHEMA_VERSION
    # EVERY newsletter that arrived in the window, including the ones the
    # ranker dropped. "View all the newsletters that dropped" means all of
    # them; a list of only the ones that reached the audio is the manifest's
    # source list under a different name.
    newsletters: list[DigestNewsletter] = Field(default_factory=list)
    stories: list[DigestStory] = Field(default_factory=list)

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
