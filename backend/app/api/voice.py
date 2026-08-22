"""
Voice interrupt API — Phase 3, "the Join flow".

    POST /voice/ask     stream a host's answer to a question asked mid-briefing

The manifest already exists (this endpoint answers questions about a
briefing that has already been generated and is currently playing); this is
NOT the /briefing/generate long-running pipeline. Given a briefing id, the
position the listener was at, and their question, this streams the answer
back as it is produced so the client can start playing audio well before the
whole answer is ready — see app.pipeline.voice for why that matters and how.

Framing: newline-delimited JSON (NDJSON), one object per line, audio
base64-encoded inline. Chosen over a raw binary/length-prefixed framing
because:
  - it is trivially parsable on both ends with stdlib-only tooling (split on
    "\\n", json.loads each line) — no custom binary protocol to keep in sync
    between the Swift client and this backend while both are still moving;
  - it lets a single frame carry both audio AND metadata (speaker, text,
    timing) without a second side-channel;
  - the ~33% base64 size overhead is a real cost or a compressed ~10-30KB
    sentence clip, but is not the bottleneck against the timing budget this
    endpoint is graded on (time-to-first-audio, not total bytes moved) — see
    docs/phase3/voice_backend.md for the measured numbers and the tradeoff.
A future optimisation, if bandwidth turns out to matter more than it does
today, is a multipart/binary framing that skips the base64 tax; that is
unproven and out of scope here.

Guarded with `require_api_token` like every other route touching personal
data (CLAUDE.md rule 4 / app/core/auth.py's fail-closed posture) — a voice
question carries the listener's spoken transcript plus whatever inbox- and
calendar-derived context the answer is grounded in.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.auth import require_api_token
from app.core.config import get_settings
from app.pipeline.voice import answer_question
from app.providers.fake import FakeLLMProvider
from app.providers.fake_tts import FakeTTSProvider
from app.synthesis.storage import LocalFilesystemStorage

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_api_token)])


class VoiceAskRequest(BaseModel):
    briefing_id: str
    question: str
    position_ms: int
    prior_questions: list[str] = Field(default_factory=list)
    # Same escape hatch as GenerateRequest.offline in app/api/briefing.py:
    # None means "live if this machine has credentials, fake otherwise",
    # letting a client (or a test) force either explicitly.
    offline: bool | None = None

    @property
    def resolved_offline(self) -> bool:
        if self.offline is not None:
            return self.offline
        return not _live_credentials_available()


def _live_credentials_available() -> bool:
    """Whether this machine can run a live LLM + TTS voice answer.

    Deliberately narrower than app.api.briefing._credentials_configured:
    answering a question about an already-generated briefing never touches
    Gmail/Calendar ingestion, so — unlike a full /briefing/generate run — no
    cached Google OAuth user token is required here, only Gemini + Google
    TTS credentials.
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        return False
    creds = Path(settings.google_tts_credentials)
    if not creds.is_absolute():
        creds = (Path(__file__).resolve().parents[2] / creds).resolve()
    return creds.is_file()


def _build_voice_providers(offline: bool):
    if offline:
        return FakeLLMProvider(), FakeTTSProvider()

    # Imported lazily, same reasoning as app.api.briefing._build_providers:
    # these raise a clear RuntimeError at construction time if credentials
    # are missing, and importing them eagerly would make the whole router
    # fail to mount on a machine with none configured.
    from app.providers.gemini import GeminiProvider
    from app.providers.google_tts import GoogleTTSProvider

    # Latency-tuned model for this path — see Settings.voice_llm_model. The
    # script chain keeps the stronger model; this one has 2.5 seconds total.
    return (
        GeminiProvider(model=get_settings().voice_llm_model),
        GoogleTTSProvider(),
    )


def _frame(**fields) -> str:
    return json.dumps(fields) + "\n"


@router.post("/ask")
async def ask(request: VoiceAskRequest) -> StreamingResponse:
    storage = LocalFilesystemStorage()
    manifest = storage.load_manifest(request.briefing_id)
    if manifest is None:
        raise HTTPException(
            status_code=404, detail=f"briefing {request.briefing_id!r} not found"
        )

    if not request.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")
    if request.position_ms < 0:
        raise HTTPException(status_code=400, detail="position_ms must not be negative")

    try:
        llm, tts = _build_voice_providers(request.resolved_offline)
    except RuntimeError as exc:
        # Missing credentials for a live run — a client error naming the
        # exact fix, not a server bug. Same posture as
        # app.api.briefing.generate.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def _frames():
        chunk_count = 0
        got_final_frame = False
        try:
            async for chunk in answer_question(
                llm=llm,
                tts=tts,
                question=request.question,
                manifest=manifest,
                position_ms=request.position_ms,
                prior_questions=request.prior_questions,
            ):
                chunk_count += 1
                fields = {
                    "kind": chunk.kind,
                    "index": chunk.index,
                    "speaker": chunk.speaker,
                    "text": chunk.text,
                    "audio_b64": (
                        base64.b64encode(chunk.audio).decode("ascii")
                        if chunk.audio
                        else ""
                    ),
                    "audio_failed": chunk.audio_failed,
                }
                if chunk.timing is not None:
                    fields["timing"] = chunk.timing
                    got_final_frame = True
                yield _frame(**fields)
        except Exception:
            # answer_question already degrades internally (CLAUDE.md rule 7:
            # a bad sentence's TTS, or the LLM failing outright, both end
            # the generator cleanly). This is a last-resort guard so a bug
            # THERE closes the HTTP stream with an explicit final frame
            # instead of the connection just dying mid-response, which a
            # client cannot distinguish from a network drop.
            log.exception("voice: unexpected error while streaming an answer")

        if not got_final_frame:
            # Reached whenever answer_question produced no timing-bearing
            # final chunk — either it yielded nothing at all (the LLM failed
            # outright) or the guard above caught an unexpected error. The
            # client still needs an explicit end-of-stream marker; otherwise
            # zero frames is indistinguishable from a hung connection.
            yield _frame(kind="done", index=0, speaker="", text="",
                         audio_b64="", audio_failed=True, timing=None,
                         chunks_emitted=chunk_count)

    return StreamingResponse(_frames(), media_type="application/x-ndjson")
