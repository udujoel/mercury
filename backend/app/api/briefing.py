"""
Briefing API.

    POST /briefing/generate          run the pipeline, return the manifest
    GET  /briefing/{briefing_id}     the stored manifest
    GET  /briefing/{briefing_id}/audio   the audio file, correct media type

Generation is long-running (minutes, live; seconds, offline with fakes), so
the POST endpoint simply awaits the run and returns the finished manifest —
there's no job queue in the Phase 1 MVP.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.api import jobs
from app.core.auth import require_api_token
from app.core.config import get_settings
from app.core.schemas import BriefingLength, BriefingManifest
from app.pipeline.generate import generate_briefing
from app.providers.fake import FakeLLMProvider
from app.providers.fake_tts import FakeTTSProvider
from app.synthesis.storage import LocalFilesystemStorage

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

    tts_creds = Path(s.google_tts_credentials)
    if not tts_creds.is_absolute():
        tts_creds = (Path(__file__).resolve().parents[2] / tts_creds).resolve()
    if not tts_creds.is_file():
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


def _build_providers(offline: bool):
    if offline:
        return FakeLLMProvider(), FakeTTSProvider()

    # Imported lazily: these raise a clear, actionable RuntimeError at
    # construction time if the required credentials aren't configured, and
    # importing them at module load would make the whole router fail to
    # mount on a machine with no credentials at all.
    from app.providers.gemini import GeminiProvider
    from app.providers.google_tts import GoogleTTSProvider

    return GeminiProvider(), GoogleTTSProvider()


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

    briefing_id = request.briefing_id or _today_briefing_id()
    storage = LocalFilesystemStorage()

    # Already generated: hand it back without spending money regenerating it.
    existing = storage.load_manifest(briefing_id)
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
            weather_fixture=None,
            storage=storage,
        ),
    )
    return GenerateResponse(status="generating", briefing_id=briefing_id)


@router.get("/{briefing_id}/status", response_model=StatusResponse)
async def status(briefing_id: str) -> StatusResponse:
    """Where a generation has got to. Cheap enough to poll every second."""
    if LocalFilesystemStorage().load_manifest(briefing_id) is not None:
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
    storage = LocalFilesystemStorage()
    manifest = storage.load_manifest(briefing_id)
    if manifest is None:
        raise HTTPException(
            status_code=404, detail=f"briefing {briefing_id!r} not found"
        )
    return manifest


@router.get("/{briefing_id}/audio")
async def get_audio(briefing_id: str) -> Response:
    storage = LocalFilesystemStorage()
    result = storage.load_audio(briefing_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"audio for {briefing_id!r} not found"
        )
    audio_bytes, media_type = result
    return Response(content=audio_bytes, media_type=media_type)
