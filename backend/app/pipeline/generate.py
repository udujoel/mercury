"""
The Phase 1 pipeline orchestrator.

    ingest (concurrent) -> score/select -> script -> TTS+stitch -> manifest -> store

CLAUDE.md rule 7 — graceful degradation — is the thing this module is most
responsible for getting right: Gmail, Calendar and Weather are fetched
concurrently with `asyncio.gather(..., return_exceptions=True)`, and any
single source failing is logged and dropped rather than aborting the run.
A briefing missing its weather segment still ships.

CLAUDE.md rule 5 — raw email bodies are ephemeral — is enforced here too:
once the script chain has turned emails into a script, every `Item.body`
this run touched is cleared before the manifest is stored. Only the script,
audio and source references persist.

Dependency injection note: `app.ingestion.calendar`, `app.ingestion.weather`,
`app.pipeline.scoring` and `app.pipeline.script_chain` are owned by another
agent working in parallel and may not exist yet. Every one of them is an
injectable parameter here, defaulting to the real implementation when it can
be imported and to `None` otherwise, so this module (and its tests) work
today with fakes and pick up the real thing automatically once it lands —
no reimplementation, no stub files on disk.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.core.providers import EmbeddingProvider, LLMProvider, TTSProvider
from app.core.schemas import (
    LENGTH_BUDGETS,
    BriefingLength,
    BriefingManifest,
    Item,
    SourceType,
    Story,
)
from app.pipeline.dedup import build_stories
from app.pipeline.ranking import score_stories
from app.synthesis.stitch import synthesize_briefing
from app.synthesis.storage import BriefingStorage, build_storage

log = logging.getLogger(__name__)

try:
    from app.ingestion.gmail import fetch_emails as _real_fetch_emails
except ImportError:  # pragma: no cover - present in this repo already
    _real_fetch_emails = None

try:
    from app.ingestion.calendar import fetch_events as _real_fetch_events
except ImportError:
    _real_fetch_events = None

try:
    from app.ingestion.weather import fetch_weather as _real_fetch_weather
except ImportError:
    _real_fetch_weather = None

try:
    from app.pipeline.scoring import (
        score_emails as _real_score_emails,
    )
    from app.pipeline.scoring import (
        select_for_briefing as _real_select_for_briefing,
    )
except ImportError:
    _real_score_emails = None
    _real_select_for_briefing = None

try:
    from app.ingestion.rss import fetch_articles as _real_fetch_articles
except ImportError:
    _real_fetch_articles = None

try:
    from app.pipeline.script_chain import generate_script as _real_generate_script
except ImportError:
    _real_generate_script = None


async def generate_briefing(
    *,
    llm: LLMProvider,
    tts: TTSProvider,
    briefing_id: str | None = None,
    length: BriefingLength = BriefingLength.STANDARD,
    embeddings: EmbeddingProvider | None = None,
    previous_degraded_attempts: int = 0,
    fetch_emails: Any = _real_fetch_emails,
    fetch_events: Any = _real_fetch_events,
    fetch_weather: Any = _real_fetch_weather,
    fetch_articles: Any = _real_fetch_articles,
    score_emails: Any = _real_score_emails,
    select_for_briefing: Any = _real_select_for_briefing,
    generate_script: Any = _real_generate_script,
    storage: BriefingStorage | None = None,
    gmail_fixture: Path | None = None,
    calendar_fixture: Path | None = None,
    weather_fixture: Path | None = None,
    rss_fixture: Path | None = None,
    max_messages: int = 60,
    # PRD F-02 and the ROADMAP both specify today plus 72 hours. What is
    # SPOKEN is decided by select_for_briefing; this is only the fetch
    # window, so it should match the spec rather than pre-truncating it.
    hours_ahead: int = 72,
) -> BriefingManifest:
    """Run the full generation pipeline and return the stored manifest.

    `*_fixture=None` means "use the live source"; passing a fixture Path
    runs that source offline. Any source that raises — live or offline —
    is logged and dropped; the briefing still ships without that segment.
    """
    settings = get_settings()
    # The default has to agree with STORAGE_BACKEND too. Every caller in the
    # deployed path injects storage explicitly, so this only bites the one
    # that forgets — which is exactly when writing to local disk instead of R2
    # would go unnoticed.
    storage = storage or build_storage()
    briefing_id = briefing_id or datetime.now(UTC).strftime("%Y-%m-%d")
    briefing_date = _parse_briefing_date(briefing_id)

    (emails, events, weather, articles), degraded = await _ingest_concurrently(
        fetch_emails=fetch_emails,
        fetch_events=fetch_events,
        fetch_weather=fetch_weather,
        fetch_articles=fetch_articles,
        gmail_fixture=gmail_fixture,
        calendar_fixture=calendar_fixture,
        weather_fixture=weather_fixture,
        rss_fixture=rss_fixture,
        max_messages=max_messages,
        hours_ahead=hours_ahead,
        user_lat=settings.user_lat,
        user_lon=settings.user_lon,
    )

    # Newsletters and articles cluster TOGETHER, in one call, which is the
    # whole reason every source normalises into `Item` before anything else
    # happens (CLAUDE.md, "The pipeline"). Clustering the news in isolation
    # would dedupe Reuters against the BBC and leave the three newsletters
    # that covered the same acquisition to be read out separately —
    # precisely the "concatenated, not edited" failure ADR-003 exists to
    # prevent.
    world_stories, inbox_emails = await _build_world_stories(
        emails=emails,
        articles=articles,
        events=events,
        embeddings=embeddings,
        length=length,
    )

    scored_emails = _score_emails(score_emails, inbox_emails, events)

    if select_for_briefing is None:
        raise RuntimeError(
            "select_for_briefing is not available (app.pipeline.scoring not "
            "found and no fake was injected). Pass select_for_briefing= "
            "explicitly, or wait for app/pipeline/scoring.py to land."
        )
    # date=, so "your day" means the briefing day. Ingestion deliberately
    # fetches today plus 72 hours (PRD F-02) and this is the only place that
    # narrows it to what gets spoken; without it the calendar segment walked
    # the next three days under the heading "Your day".
    selection = select_for_briefing(
        scored_emails, events, length, date=briefing_date
    )

    if generate_script is None:
        raise RuntimeError(
            "generate_script is not available (app.pipeline.script_chain "
            "not found and no fake was injected). Pass generate_script= "
            "explicitly, or wait for app/pipeline/script_chain.py to land."
        )
    turns, sources = await generate_script(
        llm=llm,
        emails=selection["emails"],
        events=selection["events"],
        background_events=selection.get("background_events", []),
        # PRD F-05 caps how many emails are read out individually; this is
        # how many were left over. It was computed and dropped on the floor,
        # so most of the inbox disappeared without so much as a "plus eleven
        # others, nothing urgent".
        aggregated_email_count=selection.get("aggregated_count", 0),
        stories=world_stories,
        # Phase 2: outline -> draft -> critique -> revise. The chain keeps
        # the draft if revision scores worse on the quality gates, which a
        # live run showed is not hypothetical (19 issues -> 20).
        passes=4,
        weather=weather,
        length=length,
        date=briefing_date,
    )

    # CLAUDE.md rule 5: raw email bodies are ephemeral, held only for the
    # duration of this run. The script has been written; nothing downstream
    # needs the body text, so drop it from every Item this run touched
    # before anything is persisted.
    _scrub_email_bodies(emails)
    _scrub_email_bodies(scored_emails)
    _scrub_email_bodies(selection.get("emails", []))

    audio_bytes, manifest, audio_format = await synthesize_briefing(
        turns,
        tts=tts,
        briefing_id=briefing_id,
        audio_url_for=lambda _fmt: storage.audio_path_for(briefing_id),
        sources=sources,
        # THIS run's failures, carried down from ingestion rather than
        # re-read from the module global at the end — see
        # _ingest_concurrently for why the global cannot answer honestly.
        degraded_sources=degraded,
        # How many times this briefing has already been regenerated because
        # it shipped degraded. Bounds the retry — see MAX_DEGRADED_RETRIES.
        degraded_attempts=(previous_degraded_attempts + 1) if degraded else 0,
    )

    # to_thread, because BriefingStorage is synchronous and R2Storage is boto3.
    # This is ~3MB up on a blocking socket, and it runs inside the API process
    # while the client polls /status — so calling it directly froze the server
    # for the whole upload.
    await asyncio.to_thread(
        storage.save, briefing_id, audio_bytes, audio_format, manifest
    )
    return manifest


async def _ingest_concurrently(
    *,
    fetch_emails: Any,
    fetch_events: Any,
    fetch_weather: Any,
    fetch_articles: Any,
    gmail_fixture: Path | None,
    calendar_fixture: Path | None,
    weather_fixture: Path | None,
    rss_fixture: Path | None,
    max_messages: int,
    hours_ahead: int,
    user_lat: float,
    user_lon: float,
) -> tuple[tuple[list[Item], list[Any], Any, list[Item]], list[str]]:
    """Fetch all four sources concurrently.

    Returns the four payloads AND the labels of the personal sources that
    failed in THIS invocation. The second half exists because the manifest
    needs to record it and a module global cannot answer the question
    honestly: `generate_briefing` runs minutes of LLM and TTS work between
    ingestion and synthesis, and both `app/scheduler/cron.py` and
    `POST /briefing/generate` drive it on the same event loop without a
    mutual gate. A second run starting in that window overwrites the global,
    and the manifest is then stamped with the wrong run's failures — a
    degraded briefing marked healthy, which is precisely the masking this
    was written to remove.

    Any source that fails (missing module, network error, malformed
    fixture, anything) is logged and replaced with an empty/None default —
    CLAUDE.md rule 7."""

    async def _emails() -> list[Item]:
        if fetch_emails is None:
            raise RuntimeError(
                "fetch_emails not available (app.ingestion.gmail.fetch_emails)"
            )
        return await fetch_emails(fixture=gmail_fixture, max_messages=max_messages)

    async def _events() -> list[Any]:
        if fetch_events is None:
            raise RuntimeError(
                "fetch_events not available (app.ingestion.calendar.fetch_events)"
            )
        return await fetch_events(fixture=calendar_fixture, hours_ahead=hours_ahead)

    async def _weather() -> Any:
        if fetch_weather is None:
            raise RuntimeError(
                "fetch_weather not available (app.ingestion.weather.fetch_weather)"
            )
        return await fetch_weather(
            lat=user_lat, lon=user_lon, fixture=weather_fixture
        )

    async def _articles() -> list[Item]:
        if fetch_articles is None:
            raise RuntimeError(
                "fetch_articles not available (app.ingestion.rss.fetch_articles)"
            )
        return await fetch_articles(fixture=rss_fixture)

    labels = ("email", "calendar", "weather", "news")
    defaults: tuple[Any, Any, Any, Any] = ([], [], None, [])
    results = await asyncio.gather(
        _emails(), _events(), _weather(), _articles(), return_exceptions=True
    )

    out: list[Any] = []
    for label, result, default in zip(labels, results, defaults, strict=True):
        if isinstance(result, BaseException):
            log.error(
                "ingestion source %r failed, dropping that segment "
                "(briefing still ships without it): %s", label, result,
            )
            out.append(default)
        else:
            out.append(result)

    # Rule 7 says any source can fail and the briefing still ships. It does
    # not say that should be quiet when the source that failed IS the product.
    # A real Actions run dropped BOTH email and calendar, shipped a
    # weather-and-news briefing, and exited 0 — a green tick over a briefing
    # missing the entire personal half of what Mercury is for.
    #
    # News is deliberately NOT in this set. A failed feed costs the world
    # segment, which rule 7 covers and the outline prompt already has an
    # empty-segment rule for; it does not mean the briefing lost the
    # personal content it exists to deliver, and folding it in here would
    # make the degraded-run warning fire on mornings that were fine.
    personal_failed = {
        label: result
        for label, result in zip(labels[:2], results[:2], strict=True)
        if isinstance(result, BaseException)
    }
    # Still recorded globally, because app/cli.py reads it after the fact to
    # decide the process exit code and there is exactly one run per process
    # there. But it is ALSO returned, and the manifest uses the returned
    # value — see below.
    _record_failed_sources(personal_failed)
    if len(personal_failed) == 2:
        log.error(
            "BOTH personal sources failed (%s) — this briefing has no calendar "
            "and no inbox in it, which is most of the point. Shipping it "
            "anyway per rule 7, but this is not a healthy run.",
            ", ".join(personal_failed),
        )

    return tuple(out), sorted(personal_failed)  # type: ignore[return-value]


def _is_newsletter(item: Item) -> bool:
    """An email the user subscribed to, per `gmail.detect_newsletter`.

    The gate the subtraction below serves is about NEWSLETTERS: three of them
    covering one acquisition is one story, and dropping the duplicates costs
    the listener nothing because the content is the story. An ordinary email
    is not interchangeable with the news it happens to mention — a world
    story renders the canonical ARTICLE's title and body, so an absorbed
    personal message survives only as a name in a source list, read out by
    nobody. Two colleagues writing about one project is not duplication
    either; they both wrote to the user.
    """
    return item.source_type is SourceType.EMAIL and bool(
        item.raw.get("is_newsletter")
    )


async def _build_world_stories(
    *,
    emails: list[Item],
    articles: list[Item],
    events: list[Any],
    embeddings: EmbeddingProvider | None,
    length: BriefingLength,
) -> tuple[list[Story], list[Item]]:
    """Cluster the day's news and decide what the world segment gets.

    Returns (stories for the world segment, emails still owed to the inbox).

    Everything goes into ONE `build_stories` call — newsletters and articles
    together. That is the point of the shared `Item` schema, and clustering
    news on its own would leave three newsletters covering one acquisition
    to be read out one after another.

    A story reaches the world segment when at least one RSS item is in it.
    That rule needs no newsletter classifier and gets the split right in both
    directions: a newsletter a publication also covered becomes one world
    story with every source attributed, while a newsletter nobody else
    carried — and every ordinary email — stays in the inbox where it belongs.
    Without it, a personal email that happened to cluster with an article
    would be promoted into "your world".

    Keyed on object identity, not `Item.id`: that is a content hash of
    source_name + url-or-title + timestamp, so two messages from one sender
    with one subject at one timestamp collide however different their bodies,
    and absorbing the first silently removed the second.

    Emails absorbed into a SELECTED story are then removed from the inbox
    list, which is the Phase 2 gate stated exactly: the same story does not
    appear twice however many newsletters covered it. Absorbed is computed
    after the cap, so a newsletter whose story missed the cut is still read
    out as email rather than vanishing from the briefing entirely.
    """
    clusterable = emails + articles
    if not clusterable:
        return [], emails

    stories = await build_stories(clusterable, embeddings=embeddings)
    # The WHOLE fetch window, not the briefing day. `select_for_briefing`
    # narrows events to today because the calendar segment is "the day walked
    # chronologically" and the cold open is an observation "about the day
    # ahead" — both make a claim about today. Ranking makes none: an attendee
    # is a personal-relevance signal, and a story about someone you are seeing
    # on Thursday is exactly the prep PRD F-02's 72 hours exist for. Narrowing
    # it here does not protect a today story — that story's score does not
    # move either way — it only stops a Thursday one outranking news you have
    # no connection to at all.
    stories = score_stories(stories, calendar_events=events)

    world = [
        story
        for story in stories
        if any(m.source_type is SourceType.RSS for m in story.members)
    ]
    world = world[: LENGTH_BUDGETS[length]["stories"]]

    absorbed = {
        id(member)
        for story in world
        for member in story.members
        if _is_newsletter(member)
    }

    # A cluster the world segment did not take is still a cluster.
    #
    # Promotion requires an RSS member, so three newsletters covering one
    # acquisition that no wired feed happened to carry used to arrive in the
    # inbox as three separate emails and be read out one after another. The
    # gate in CLAUDE.md is "the same story never appears twice REGARDLESS OF
    # HOW MANY NEWSLETTERS COVERED IT", and the no-feed case is precisely the
    # one that sentence is about — PRD F-08's narrower "newsletter and news
    # feed" wording was satisfied while the gate itself was not. Mercury's
    # news arrives largely as newsletters, so this is not an edge case.
    #
    # Collapsed rather than promoted. The world segment is for what the wider
    # press carried; a story only your own newsletters covered is inbox
    # material that happened to arrive three times.
    promoted = {story.cluster_id for story in world}
    superseded: set[str] = set()
    for story in stories:
        if story.cluster_id in promoted:
            continue
        members = [m for m in story.members if _is_newsletter(m)]
        if len(members) < 2:
            continue
        keeper = next(
            (m for m in members if m is story.canonical), members[0]
        )
        superseded.update(id(m) for m in members if m is not keeper)
        # Not silent. Dropping the duplicates without saying so would trade
        # one wrong behaviour for another — the hosts would read a story that
        # three publications thought worth writing about as though one did.
        # Naming the others is the multi-source signal the digest is built on,
        # and it is the honest version of "the same story never appears twice".
        keeper.raw["also_covered_by"] = sorted(
            m.source_name for m in members if m is not keeper
        )

    inbox_emails = [
        em for em in emails
        if id(em) not in absorbed and id(em) not in superseded
    ]

    log.info(
        "news: %d articles + %d emails clustered into %d stories; %d reach "
        "the world segment, absorbing %d email(s); %d further email(s) "
        "collapsed into a newsletter-only cluster",
        len(articles), len(emails), len(stories), len(world), len(absorbed),
        len(superseded),
    )
    return world, inbox_emails


# Set by the ingestion gather so callers (app.cli) can report a degraded run
# without re-deriving it. Inferring from the manifest does not work: the script
# chain REQUIRES all seven canonical segments, so "calendar" and "inbox" are
# present even when there was no calendar and no inbox data behind them.
_failed_personal_sources: dict[str, BaseException] = {}


def _record_failed_sources(failed: dict[str, BaseException]) -> None:
    _failed_personal_sources.clear()
    _failed_personal_sources.update(failed)


def failed_personal_sources() -> list[str]:
    """Which of email/calendar failed during the most recent run."""
    return list(_failed_personal_sources)


def failed_personal_source_errors() -> dict[str, BaseException]:
    """...and why, as the exception objects rather than their messages.

    The objects, deliberately. `app.cli` has to tell an expired OAuth token —
    a known cause with a permanent fix, and the failure this deployment is
    most likely to hit — apart from a transient API error, and the honest way
    to do that is the exception TYPE. Matching on message text would break the
    first time somebody rewords a string.
    """
    return dict(_failed_personal_sources)


def _score_emails(
    score_emails: Any, emails: list[Item], events: list[Any]
) -> list[Item]:
    if score_emails is None:
        return emails
    sig = inspect.signature(score_emails)
    if len(sig.parameters) >= 2:
        try:
            return score_emails(emails, events)
        except TypeError:
            pass
    return score_emails(emails)


def _scrub_email_bodies(items: list[Item]) -> None:
    for item in items:
        item.body = ""


def _parse_briefing_date(briefing_id: str) -> datetime:
    """`briefing_id` is normally a "YYYY-MM-DD" string (see the manifest's
    docstring in app/core/schemas.py), but `generate_script`'s `date`
    parameter is typed as a real `datetime`. Parse it back out; fall back
    to now() for a caller-supplied id that isn't date-shaped."""
    # The date must be resolved in the USER's timezone, never UTC. Generated
    # at 00:11 in Europe/Lisbon (UTC+1), datetime.now(UTC) still reads as the
    # previous day — so the cold open announced "Friday, August 21st" while
    # the calendar segment, built from real events, talked about the 22nd.
    # The hosts contradicted each other inside one briefing.
    try:
        tz = ZoneInfo(get_settings().user_timezone)
    except Exception:
        tz = UTC
    try:
        return datetime.strptime(briefing_id, "%Y-%m-%d").replace(tzinfo=tz)
    except ValueError:
        return datetime.now(tz)
