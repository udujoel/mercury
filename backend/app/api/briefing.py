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
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

<<<<<<< HEAD
from app.core.config import get_settings
=======
from app.core.auth import require_api_token
>>>>>>> main
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


def _credentials_configured() -> bool:
    """Whether a live run is actually possible on this machine."""
    s = get_settings()
    if not s.gemini_api_key:
        return False
    creds = Path(s.google_tts_credentials)
    if not creds.is_absolute():
        creds = (Path(__file__).resolve().parents[2] / creds).resolve()
    return creds.is_file()


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
    status: str
    manifest: BriefingManifest


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


@router.post("/generate", response_model=GenerateResponse)
async def generate(
    request: GenerateRequest | None = None,
) -> GenerateResponse:
    # Every field on GenerateRequest has a default, so "no body" is a
    # meaningful request: a standard-length briefing, live if this
    # machine has credentials and fixture-backed if it does not.
    # Requiring the body made a bodyless POST fail with a 422 that says
    # nothing useful to a client that just wants the default.
    request = request or GenerateRequest()
    offline = request.resolved_offline
    try:
        llm, tts = _build_providers(offline)
    except RuntimeError as exc:
        # Missing credentials for a live run — a client error, not a server
        # bug, and the message already names the exact fix.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    gmail_fixture = FIXTURES_DIR / "gmail_messages.json" if offline else None
    calendar_fixture = (
        FIXTURES_DIR / "calendar_events.json" if offline else None
    )

    try:
        manifest = await generate_briefing(
            briefing_id=request.briefing_id,
            length=request.length,
            llm=llm,
            tts=tts,
            gmail_fixture=gmail_fixture,
            calendar_fixture=calendar_fixture,
            weather_fixture=None,
        )
    except Exception as exc:
        log.exception("briefing generation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return GenerateResponse(status="ok", manifest=manifest)


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
