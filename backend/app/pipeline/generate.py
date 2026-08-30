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
from app.core.providers import LLMProvider, TTSProvider
from app.core.schemas import BriefingLength, BriefingManifest, Item
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
    from app.pipeline.script_chain import generate_script as _real_generate_script
except ImportError:
    _real_generate_script = None


async def generate_briefing(
    *,
    llm: LLMProvider,
    tts: TTSProvider,
    briefing_id: str | None = None,
    length: BriefingLength = BriefingLength.STANDARD,
    fetch_emails: Any = _real_fetch_emails,
    fetch_events: Any = _real_fetch_events,
    fetch_weather: Any = _real_fetch_weather,
    score_emails: Any = _real_score_emails,
    select_for_briefing: Any = _real_select_for_briefing,
    generate_script: Any = _real_generate_script,
    storage: BriefingStorage | None = None,
    gmail_fixture: Path | None = None,
    calendar_fixture: Path | None = None,
    weather_fixture: Path | None = None,
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

    emails, events, weather = await _ingest_concurrently(
        fetch_emails=fetch_emails,
        fetch_events=fetch_events,
        fetch_weather=fetch_weather,
        gmail_fixture=gmail_fixture,
        calendar_fixture=calendar_fixture,
        weather_fixture=weather_fixture,
        max_messages=max_messages,
        hours_ahead=hours_ahead,
        user_lat=settings.user_lat,
        user_lon=settings.user_lon,
    )

    scored_emails = _score_emails(score_emails, emails, events)

    if select_for_briefing is None:
        raise RuntimeError(
            "select_for_briefing is not available (app.pipeline.scoring not "
            "found and no fake was injected). Pass select_for_briefing= "
            "explicitly, or wait for app/pipeline/scoring.py to land."
        )
    selection = select_for_briefing(scored_emails, events, length)

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
    )

    storage.save(briefing_id, audio_bytes, audio_format, manifest)
    return manifest


async def _ingest_concurrently(
    *,
    fetch_emails: Any,
    fetch_events: Any,
    fetch_weather: Any,
    gmail_fixture: Path | None,
    calendar_fixture: Path | None,
    weather_fixture: Path | None,
    max_messages: int,
    hours_ahead: int,
    user_lat: float,
    user_lon: float,
) -> tuple[list[Item], list[Any], Any]:
    """Fetch all three sources concurrently. Any source that fails (missing
    module, network error, malformed fixture, anything) is logged and
    replaced with an empty/None default — CLAUDE.md rule 7."""

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

    labels = ("email", "calendar", "weather")
    defaults: tuple[Any, Any, Any] = ([], [], None)
    results = await asyncio.gather(
        _emails(), _events(), _weather(), return_exceptions=True
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
    personal_failed = {
        label: result
        for label, result in zip(labels[:2], results[:2], strict=True)
        if isinstance(result, BaseException)
    }
    _record_failed_sources(personal_failed)
    if len(personal_failed) == 2:
        log.error(
            "BOTH personal sources failed (%s) — this briefing has no calendar "
            "and no inbox in it, which is most of the point. Shipping it "
            "anyway per rule 7, but this is not a healthy run.",
            ", ".join(personal_failed),
        )

    return tuple(out)  # type: ignore[return-value]


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
