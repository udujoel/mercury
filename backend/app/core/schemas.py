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
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


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
    # The publisher's own lead image, as a URL — never the bytes.
    #
    # It stays on their CDN, which keeps R2 small, keeps the retention sweep
    # honest (nothing outlives the story it belongs to, because there is
    # nothing extra to delete), and means Mercury never redistributes an
    # image it has no licence to. Null for email: rule 5 discards the body
    # a newsletter's images live in.
    image_url: str | None = None
    author: str | None = None
    timestamp: datetime

    requires_response: bool = False
    priority: Priority = Priority.MEDIUM
    # Jev's consequence Score (0.0-3.0, a position on the level index), or
    # None when no judgment pass ran for this item. Carried so ranking can
    # order WITHIN a priority tier by how much missing the item costs,
    # rather than by arrival time — a gym mailer and a bank alert were
    # otherwise separated only by which arrived later (#55).
    consequence: float | None = None

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


# The two vocabularies that share `BriefingManifest.degraded_sources`.
#
# Named here, together, because they were once kept apart only by a docstring
# claiming they never overlap — and they did: `calendar` is both an ingestion
# source and `Segment.CALENDAR`, so `cli._only_refusals` filed a calendar
# fetch that failed on its own as a model refusal and never retried it.
# `test_cli_generate` asserts the two sets stay disjoint; the ladder in
# `script_chain.generate_script` refuses to record a segment outside its set.
#
# What ingestion records when a source fails (`generate._ingest_concurrently`).
INGESTION_SOURCE_LABELS: tuple[str, ...] = ("email", "calendar", "weather", "news")

# What the refusal ladder can give up when the model declines to write a
# segment. Calendar, weather and the greeting are never given up — the ladder
# raises instead — so they are not here.
REFUSABLE_SEGMENTS: frozenset[Segment] = frozenset(
    {Segment.WORLD, Segment.INBOX, Segment.FOLLOW_UPS}
)


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
    # Word timings, ABSOLUTE in the stitched file, when the TTS provider
    # supplied them. None for Google, and None for every manifest generated
    # before ElevenLabs — the client falls back to whole-line highlighting.
    words: list[WordSpan] | None = None


class WordSpan(BaseModel):
    """One word, and when it is spoken in the finished audio.

    Absolute, unlike `providers.WordTiming` which is relative to its own
    clip. `stitch` converts between the two at the one point that knows
    where a turn landed — and that is the same point that will know how much
    intro music sits in front of it (ROADMAP task 3), which is why the
    offset is applied there and nowhere else.
    """

    word: str
    start_ms: int
    end_ms: int


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
    # The id of the item or event this ref points at.
    #
    # Without it the client has only (title, source_name) to tell two refs
    # apart, and `_mentions` matches a calendar event BY TITLE — so two
    # events called "1:1" on the same day are indistinguishable there and
    # counted as one. Recurring meetings make that the common case, not the
    # exotic one.
    #
    # Nullable because every manifest already in R2 predates the field.
    item_id: str | None = None


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



class ResearchSourceRef(BaseModel):
    """One source a research finding drew on.

    Separate from `DigestSource`, which is a cluster member — something that
    arrived in the inbox or a feed. This is something Mercury went and found,
    and conflating them would let researched claims borrow the authority of
    material the user actually subscribed to.
    """

    title: str
    url: str


class Research(BaseModel):
    """What Mercury found out about a story's subject, beyond the sources
    that carried it.

    Task 18, and the point of the feature in the owner's words: "make sure
    the story is rich and informed, not just what's in the email."

    Rule 3 is enforced by construction rather than by hope: a `Research` with
    no sources is discarded before it reaches here, so anything the client
    renders is attributable. A researched paragraph without a citation looks
    MORE authoritative on a page than in audio, which is why this is the one
    place the rule is worth enforcing twice.

    Rule 5 shapes what goes in: research runs from an extracted SUBJECT, a
    short noun phrase, and never from an email body. Nothing stored here can
    be traced back to the contents of a message.
    """

    summary: str
    sources: list[ResearchSourceRef] = Field(default_factory=list)


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
    # The summary again, as points. Written as its own output rather than by
    # splitting the paragraph on the client, which would be guessing at
    # sentence boundaries in text full of abbreviations and figures.
    # Empty for an unenriched story, same as `summary` being null.
    points: list[str] = Field(default_factory=list)
    why_it_matters: str | None = None
    # The lead image of whichever source has one, preferring the canonical.
    # Null is ordinary: newsletters have none at all.
    image_url: str | None = None
    # The specific thing this story is about — "Houthi forces advancing on
    # Red Sea shipping lanes" — shared verbatim across stories about the
    # same thing, which is what lets them group. Null when a story has no
    # specific subject, which a weekly roundup genuinely does not.
    #
    # Optional on the wire: every digest already in R2 was written before
    # this existed, and the archive can open any of them.
    subject: str | None = None
    # CATEGORIES — `ai`, `finance` — for filtering a long list. Several
    # unrelated stories sharing one is expected and fine. Not a subject:
    # see `subject` above and docs/LOG.md, 2026-09-12.
    topics: list[str] = Field(default_factory=list)
    # What Mercury found out about `subject` beyond the sources that carried
    # the story. Null when research did not run, found nothing, or returned
    # an answer it could not attribute — all of which are ordinary.
    research: Research | None = None
    # Distinct PUBLICATIONS, which is not `len(sources)` and used to be.
    #
    # `sources` is per-article attribution: one entry per cluster member,
    # each with its own title and url, so the BBC covering a story twice
    # gives two openable rows. `source_count` answers a different question —
    # how many outlets corroborated this — and the BBC twice is one outlet.
    # Deduplicating `sources` to match would throw away a link the reader
    # can open, so the two deliberately differ.
    #
    # It was `len(members)`, which made a survey company that emailed four
    # times the highest-scoring story in the 2026-09-11 digest. Denormalised
    # so a row can render "5 sources" without walking the array.
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


SETTINGS_SCHEMA_VERSION = 1

# The music sets under `assets/music/`. "none" is a real choice, not an
# absence: a user who wants no music should be able to say so rather than
# having it mean "use the default".
MUSIC_SETS = ("paper", "broadsheet", "first-light", "none")

# The storage key. Deliberately not `YYYY-MM-DD.`-prefixed: `_BRIEFING_KEY`
# only matches that shape, so retention cannot mistake the settings for a
# briefing and sweep them after thirty days.
SETTINGS_KEY = "settings.json"


class UserSettings(BaseModel):
    """What the app can change about how a briefing is made.

    The whole point of this artifact is that it is read by the JOB, not just
    stored by the phone. Most of what belongs on a settings screen — the feed
    list, the voices, the length — is a server-side constant read by a cron
    the phone cannot see, so a setting the app writes and `generate_briefing`
    never reads is worse than no setting at all: it is a control that appears
    to work.

    Every field is optional with a null default meaning "use the
    environment". That is what lets the artifact be absent entirely, which is
    the state every deployment starts in and the one this must never break.
    """

    schema_version: int = SETTINGS_SCHEMA_VERSION
    updated_at: datetime | None = None

    # The briefing
    briefing_length: str | None = None

    # Segments to leave out. ROADMAP task 15.
    #
    # This field was here once before and was REMOVED, because
    # `_has_valid_segment_coverage_and_order` required every canonical
    # segment to be present — so it was a toggle that did nothing, the exact
    # failure this artifact exists to prevent. It is back only now that the
    # pipeline honours it: `resolve_segments` narrows the prompt's segment
    # definitions, both response schemas, the draft's repair check and the
    # revision's validator, all from this one list.
    #
    # `cold_open` and `sign_off` cannot be dropped whatever is written here
    # (`STRUCTURAL_SEGMENTS`): a briefing with no greeting starts
    # mid-sentence and one with no close just stops. Unknown names are
    # ignored rather than rejected — the phone writing this artifact may be
    # older or newer than the job reading it, and failing a whole morning
    # over an unrecognised segment name is a worse outcome than ignoring it.
    disabled_segments: list[str] | None = None

    # Voice
    tts_provider: str | None = None
    # Which music set the briefing is topped and tailed with. One of the
    # directories under `assets/music/`, or "none" for silence. Null means
    # the server's default.
    #
    # Stored even though the stitcher does not read it yet (ROADMAP task 3):
    # the assets exist, the picker exists, and a setting that is saved and
    # honoured later is different from one that is offered and never
    # honoured — the artifact carries no field the pipeline will not
    # eventually read, and this one has a task number.
    music_set: str | None = None
    voice_a: str | None = None
    voice_b: str | None = None

    # Sources. Empty means "the curated defaults" rather than "no feeds",
    # because an empty list is what an app that has never written this
    # produces, and a briefing with no news is not a plausible thing to ask
    # for by accident.
    feeds: list[str] = Field(default_factory=list)
    muted_senders: list[str] = Field(default_factory=list)

    # What the listener actually cares about, in their own words —
    # "semiconductor export controls", "Nigerian fintech", "Formula One".
    #
    # ROADMAP task 30. `score_stories` has accepted a `topic_centroid`
    # since Phase 2 and no caller ever passed one, because there was
    # nowhere for a person to say what they were interested in. So
    # `interest` was the constant 0.5, its 0.20 weight cancelled, and with
    # 153 of 160 stories single-outlet the cluster term was constant too:
    # the world segment was `0.12 x cluster + 0.08 x recency`, a recency
    # sort. On 2026-09-13 the two stories that reached the audio were the
    # two most recently published of 209, and "Ask HN: how can I browse HN
    # in dark mode?" beat a Russian strike on a Ukrainian train by six
    # minutes.
    #
    # Empty means UNRANKED-BY-INTEREST, not "no interests" — the centroid
    # is simply not computed and the weight goes on cancelling, exactly as
    # before. That is the safe default, and the run log says so out loud
    # every morning so this cannot go back to being silently dead.
    interests: list[str] = Field(default_factory=list)
    # Named people, companies and projects. A cruder, higher-precision
    # signal than the centroid: an exact substring match worth 0.8 on the
    # personal-relevance term, where the centroid is a fuzzy 0-1 on a
    # different term entirely.
    followed_entities: list[str] = Field(default_factory=list)

    @field_validator("feeds")
    @classmethod
    def _feeds_must_be_http_urls(cls, value: list[str]) -> list[str]:
        """Refuse anything that is not an http(s) URL, at write time.

        `net_guard.safe_get` already blocks the SSRF primitive at FETCH time
        — it resolves each host and rejects loopback, link-local and private
        addresses, so `http://169.254.169.254/` never reaches the network
        however it got into the list. This is not that defence; it is the
        second one.

        What this adds is WHERE the failure surfaces. Without it, a typo or
        a `file:///` paste is accepted silently and then fails inside the
        morning job, where nobody sees it and the briefing simply arrives
        thinner. Rejecting on save puts the error in front of the person who
        can fix it.

        Deliberately does NOT resolve DNS: a save must not block on the
        network, and a feed that is unreachable today may be fine tomorrow.
        Reachability is the fetcher's question. Shape is this one's.
        """
        cleaned: list[str] = []
        for raw in value:
            url = raw.strip()
            if not url:
                continue
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(
                    f"feed {url!r} is not an http(s) URL. Feeds are fetched "
                    "over the network, so they need a scheme and a host."
                )
            cleaned.append(url)
        return cleaned

    def length_or(self, fallback: BriefingLength) -> BriefingLength:
        """The configured length, or the fallback when unset or unknown.

        Unknown rather than invalid: this artifact is written by a client
        that may be newer or older than the backend reading it, so a value
        this build does not recognise is an ordinary event and must not
        fail the only run of the day.
        """
        if not self.briefing_length:
            return fallback
        try:
            return BriefingLength(self.briefing_length)
        except ValueError:
            return fallback


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
