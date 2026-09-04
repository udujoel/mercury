"""
Briefing API.

    POST /briefing/generate          run the pipeline, return the manifest
    GET  /briefing/{briefing_id}     the stored manifest
    GET  /briefing/{briefing_id}/audio   the audio file, correct media type
    GET  /briefing/{briefing_id}/digest  the browse surface, if there is one

Generation is long-running (minutes, live; seconds, offline with fakes), so
the POST endpoint simply awaits the run and returns the finished manifest —
there's no job queue in the Phase 1 MVP.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.api import jobs
from app.core.auth import require_api_token
from app.core.config import get_settings
from app.core.providers import EmbeddingProvider, build_embedding_provider
from app.core.schemas import BriefingDigest, BriefingLength, BriefingManifest
from app.pipeline.generate import generate_briefing
from app.providers.fake import FakeLLMProvider
from app.providers.fake_tts import FakeTTSProvider
from app.synthesis.storage import build_storage, existing_manifest

log = logging.getLogger(__name__)

# Every route here returns the user's inbox and calendar as speech. The
# dependency fails closed: with MERCURY_API_TOKEN unset the routes refuse to
# serve rather than defaulting to open. See app/core/auth.py.
router = APIRouter(dependencies=[Depends(require_api_token)])

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def _today_briefing_id() -> str:
    """Today in the USER's timezone — matches how the scheduler names
    briefings, so a manual tap and the cron agree on the same id."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(get_settings().user_timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).strftime("%Y-%m-%d")


def _tts_configured(s) -> bool:
    """Whether the CONFIGURED TTS provider has what it needs.

    Asking for a Google service-account file unconditionally was wrong the
    moment a second provider existed: an ElevenLabs deployment with no
    Google key failed this check, so the scheduler quietly chose
    `FakeTTSProvider` and the daily job produced FIXTURE TONES that a
    manifest and a push notification presented as the morning briefing.
    A fake that presents as success is the failure mode this whole function
    was written to avoid, reintroduced by a new provider.

    Google is still required when Google is the provider, and also when
    ElevenLabs is configured WITHOUT it — not because the run needs it, but
    because there is then nothing behind ElevenLabs and an outage takes the
    briefing with it (rule 7). That is a warning rather than a refusal: a
    briefing in one voice with no safety net still beats fixture tones.
    """
    import logging

    from app.core.providers import ELEVENLABS_NAMES

    google_ready = _google_tts_credentials_present(s)
    choice = (s.tts_provider or "google").strip().lower()

    if choice in ELEVENLABS_NAMES:
        if not (s.elevenlabs_api_key and s.elevenlabs_voice_a and s.elevenlabs_voice_b):
            return False
        if not google_ready:
            logging.getLogger(__name__).warning(
                "TTS_PROVIDER=elevenlabs with no Google credentials — there is "
                "no fallback provider, so an ElevenLabs outage mid-run will "
                "cost the whole briefing rather than just the voice."
            )
        return True

    return google_ready


def _google_tts_credentials_present(s) -> bool:
    tts_creds = Path(s.google_tts_credentials)
    if not tts_creds.is_absolute():
        tts_creds = (Path(__file__).resolve().parents[2] / tts_creds).resolve()
    return tts_creds.is_file()


def _credentials_configured() -> bool:
    """Whether a live run can actually produce a real briefing.

    Gemini and TTS alone are NOT enough. Gmail and Calendar ingestion go
    through the separate Google *user* OAuth token, and without it a "live"
    run either drops both primary sources — shipping a weather-only briefing
    that looks like the real thing — or, when only the client file exists,
    tries to start an interactive consent flow from inside an API request and
    hangs there. Both failure modes are worse than falling back to fixtures,
    because they present as success.
    """
    s = get_settings()
    if not s.gemini_api_key:
        return False

    if not _tts_configured(s):
        return False

    # A cached user token, not just the OAuth client: the client file alone is
    # what triggers the interactive flow.
    return _google_user_token_present()


def _google_user_token_present() -> bool:
    repo_root = Path(__file__).resolve().parents[3]
    token_path = Path(
        os.environ.get("GOOGLE_TOKEN_FILE", repo_root / "token.json")
    )
    return token_path.is_file()


class GenerateRequest(BaseModel):
    length: BriefingLength = BriefingLength.STANDARD
    # Default to a real briefing when the machine can produce one, and to
    # fixtures when it cannot. Hardcoding offline=True meant that once
    # credentials existed, tapping the greeting card still played a canned
    # briefing about people who do not exist — which looks like success and
    # is the least useful possible failure. A client can still force either
    # mode explicitly.
    offline: bool | None = None
    briefing_id: str | None = None

    @property
    def resolved_offline(self) -> bool:
        if self.offline is not None:
            return self.offline
        return not _credentials_configured()


class GenerateResponse(BaseModel):
    """202 response. The manifest is NOT here — fetch it from
    GET /briefing/{id} once the status says ready."""

    status: str
    briefing_id: str


class StatusResponse(BaseModel):
    briefing_id: str
    status: str  # generating | ready | failed | unknown
    elapsed_s: float | None = None
    error: str | None = None


def _embeddings_for(offline: bool) -> EmbeddingProvider | None:
    """The embedding provider this run should use.

    None when offline. Offline exists to be free and deterministic — a
    fixture for every source — and constructing the live provider there sent
    fixture content to Gemini on a run the caller had explicitly asked to be
    fake: real quota, and a retry loop that can stall the job the phone is
    polling. The cost is that an offline briefing does not dedupe, which is
    the same degradation a keyless machine already gets.
    """
    if offline:
        return None
    return build_embedding_provider()


def _build_providers(offline: bool):
    if offline:
        return FakeLLMProvider(), FakeTTSProvider()

    # Imported lazily: these raise a clear, actionable RuntimeError at
    # construction time if the required credentials aren't configured, and
    # importing them at module load would make the whole router fail to
    # mount on a machine with no credentials at all.
    from app.core.providers import build_tts_provider
    from app.providers.gemini import GeminiProvider

    return GeminiProvider(), build_tts_provider()


@router.post("/generate", response_model=GenerateResponse, status_code=202)
async def generate(
    request: GenerateRequest | None = None,
) -> GenerateResponse:
    """Start generation and return IMMEDIATELY. Poll GET /briefing/{id}/status.

    This used to run the whole pipeline inside the request and return the
    finished manifest. That works on localhost and fails through any proxy: a
    full live run takes minutes and Cloudflare's tunnel enforces a hard
    120-second read timeout, so the phone got `Error 524
    origin_response_timeout` while the server carried on generating a briefing
    nobody was waiting for.

    ADR-001 already says the phone should never wait on generation. Holding the
    connection open was a Phase 1 shortcut; polling removes the timeout class
    of failure rather than raising a limit until it breaks somewhere else.
    """
    # Every field on GenerateRequest has a default, so "no body" is a
    # meaningful request: a standard-length briefing, live if this machine has
    # credentials and fixture-backed if it does not.
    request = request or GenerateRequest()
    offline = request.resolved_offline
    try:
        llm, tts = _build_providers(offline)
    except RuntimeError as exc:
        # Missing credentials for a live run — a client error, not a server
        # bug, and the message already names the exact fix.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    gmail_fixture = FIXTURES_DIR / "gmail_messages.json" if offline else None
    calendar_fixture = FIXTURES_DIR / "calendar_events.json" if offline else None
    # Gmail and Calendar fail closed without credentials, so leaving their
    # fixtures off in offline mode would at worst produce an empty briefing.
    # RSS and weather are keyless: fixture=None means "go and fetch the real
    # internet". Offline mode was therefore fetching live feeds and live
    # weather — the one mode that exists to be deterministic, depending on
    # what the BBC published that morning, inside a job the phone is polling.
    rss_fixture = FIXTURES_DIR / "rss_feeds.json" if offline else None
    weather_fixture = FIXTURES_DIR / "weather_openmeteo.json" if offline else None

    briefing_id = request.briefing_id or _today_briefing_id()
    storage = build_storage()

    # Already generated: hand it back without spending money regenerating it.
    # existing_manifest treats an unparseable manifest as absent, so a corrupt
    # one does not block its own replacement.
    existing = await asyncio.to_thread(existing_manifest, storage, briefing_id)
    if existing is not None:
        return GenerateResponse(status="ready", briefing_id=briefing_id)

    jobs.start(
        briefing_id,
        generate_briefing(
            briefing_id=briefing_id,
            length=request.length,
            llm=llm,
            tts=tts,
            gmail_fixture=gmail_fixture,
            calendar_fixture=calendar_fixture,
            weather_fixture=weather_fixture,
            rss_fixture=rss_fixture,
            # Without this the API path never clustered anything: `embeddings`
            # defaults to None and only the CLI and the cron were building a
            # provider. See app/core/providers.build_embedding_provider.
            embeddings=_embeddings_for(offline),
            storage=storage,
        ),
    )
    return GenerateResponse(status="generating", briefing_id=briefing_id)


@router.get("/{briefing_id}/status", response_model=StatusResponse)
async def status(briefing_id: str) -> StatusResponse:
    """Where a generation has got to. Cheap enough to poll every second."""
    # to_thread, not a direct call. BriefingStorage is a synchronous Protocol
    # and the R2 implementation is boto3 — blocking sockets, no async client —
    # so a slow or stalled bucket held the only event loop thread for the whole
    # round trip and made every other request on the process unresponsive. This
    # route is the one the client polls every second.
    if await asyncio.to_thread(build_storage().load_manifest, briefing_id) is not None:
        return StatusResponse(briefing_id=briefing_id, status="ready")

    job = jobs.get(briefing_id)
    if job is None:
        return StatusResponse(briefing_id=briefing_id, status="unknown")
    return StatusResponse(
        briefing_id=briefing_id,
        status=job.state.value,
        elapsed_s=round(job.elapsed_s, 1),
        error=job.error,
    )


@router.get("/{briefing_id}", response_model=BriefingManifest)
async def get_manifest(briefing_id: str) -> BriefingManifest:
    storage = build_storage()
    manifest = await asyncio.to_thread(storage.load_manifest, briefing_id)
    if manifest is None:
        raise HTTPException(
            status_code=404, detail=f"briefing {briefing_id!r} not found"
        )
    return manifest


@router.get("/{briefing_id}/digest", response_model=BriefingDigest)
async def get_digest(briefing_id: str) -> BriefingDigest:
    """The browsable half of a briefing: every newsletter that arrived and
    every story that clustered, including the ones the ranker dropped.

    Here as well as in the Worker because the Worker exists to speak these
    exact routes back to an unchanged client (see worker/src/index.js). A
    read surface implemented on only one of the two halves is a difference
    that shows up as "it works on my laptop and not in production", which is
    the class of defect ADR-008's split already makes easy to create.

    404 is the ORDINARY answer for a briefing generated before the digest
    existed, and for one whose enrichment failed — the client shows an empty
    state, and the audio is unaffected either way.
    """
    storage = build_storage()
    digest = await asyncio.to_thread(storage.load_digest, briefing_id)
    if digest is None:
        raise HTTPException(
            status_code=404, detail=f"digest for {briefing_id!r} not found"
        )
    return digest


@router.get("/{briefing_id}/audio")
async def get_audio(briefing_id: str) -> Response:
    storage = build_storage()
    # ~3MB down from R2 on a blocking socket. The longest read in the system.
    result = await asyncio.to_thread(storage.load_audio, briefing_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"audio for {briefing_id!r} not found"
        )
    audio_bytes, media_type = result
    return Response(content=audio_bytes, media_type=media_type)
