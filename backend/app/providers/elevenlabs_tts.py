"""
Live TTSProvider backed by ElevenLabs.

Structurally satisfies `app.core.providers.TTSProvider` — no inheritance,
just matching methods, same as `GoogleTTSProvider`.

**Why this exists, beyond sounding better.** ADR-006 planned it as a voice
upgrade; ROADMAP tasks 2 and 6 are what make it necessary. Highlighting words
as they are spoken requires knowing when each word is spoken, and the manifest
carries `start_ms` per TURN — a paragraph. Google Cloud TTS can go finer only
through SSML `<mark>` injection plus the v1beta1 timepoints API: a word-level
markup pipeline built to be thrown away. ElevenLabs returns character-level
alignment as an ordinary part of the response, which collapses to words here.

To use it:
  1. Create an API key at elevenlabs.io (Profile → API Keys).
  2. Put it in GitHub Actions repository secrets as ELEVENLABS_API_KEY, and
     in backend/.env for local runs.
  3. Pick two voices in the ElevenLabs voice library and set
     ELEVENLABS_VOICE_A and ELEVENLABS_VOICE_B to their voice IDs.
  4. Set TTS_PROVIDER=elevenlabs.

Never invent or hardcode credentials. With no key, __init__ raises
immediately naming the env var, rather than making a doomed network call.

Google stays behind the protocol as the fallback (rule 6, and rule 7 says the
briefing still ships) — `build_tts_provider` is where that choice is made.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random

import httpx

from app.core.config import get_settings
from app.core.providers import Speech, WordTiming

logger = logging.getLogger(__name__)

API_ROOT = "https://api.elevenlabs.io/v1"

# eleven_turbo_v2_5 is the cheapest model that still returns alignment.
# Overridable per-instance rather than per-call: the two hosts must be
# synthesised by the same model or they do not sound like one recording.
DEFAULT_MODEL = "eleven_turbo_v2_5"

# Same retry posture as GoogleTTSProvider and GeminiProvider: a transient 429
# or 5xx must not fail a whole generation run.
MAX_ATTEMPTS = 5
BASE_DELAY_S = 1.5

# Generous, because a long turn on a busy endpoint is slow rather than broken,
# and the caller already bounds total concurrency.
REQUEST_TIMEOUT_S = 120.0


def _words_from_alignment(alignment: dict | None) -> tuple[WordTiming, ...]:
    """Collapse character-level alignment into words.

    ElevenLabs returns three parallel arrays — one entry per CHARACTER of the
    input, including the spaces. A word therefore runs from the start of its
    first character to the end of its last, and a space ends the word before
    it rather than belonging to either side.

    Defensive about length: the three arrays are documented to be the same
    length, and if a response ever disagrees the honest answer is no timings
    for that turn rather than timings silently shifted against the audio.
    Highlighting nothing is recoverable; highlighting the wrong word is the
    bug the whole feature exists to avoid.
    """
    if not alignment:
        return ()

    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    if not chars or not (len(chars) == len(starts) == len(ends)):
        logger.warning(
            "elevenlabs alignment arrays disagree (%d chars, %d starts, %d ends); "
            "dropping word timings for this turn",
            len(chars), len(starts), len(ends),
        )
        return ()

    out: list[WordTiming] = []
    buf: list[str] = []
    start_s: float | None = None
    end_s: float = 0.0

    def flush() -> None:
        nonlocal buf, start_s
        if buf and start_s is not None:
            word = "".join(buf)
            # A run of punctuation between two spaces — an em dash, an
            # ellipsis on its own — is not a word to light up. Punctuation
            # ATTACHED to a word is part of it, which the buffer already
            # handles because it only breaks on whitespace.
            #
            # `word.strip()` was the wrong test and always passed: the buffer
            # is built by skipping whitespace, so it can never be blank.
            if any(ch.isalnum() for ch in word):
                out.append(
                    WordTiming(
                        word=word,
                        start_ms=max(int(start_s * 1000), 0),
                        end_ms=max(int(end_s * 1000), 0),
                    )
                )
        buf = []
        start_s = None

    for ch, s, e in zip(chars, starts, ends, strict=True):
        if ch.isspace():
            flush()
            continue
        if start_s is None:
            start_s = s
        buf.append(ch)
        end_s = e
    flush()

    return tuple(out)


class ElevenLabsTTSProvider:
    """TTSProvider backed by ElevenLabs' with-timestamps endpoint."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        settings = get_settings()
        key = settings.elevenlabs_api_key.strip()
        if not key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY is not set. Create an API key at "
                "elevenlabs.io (Profile -> API Keys), then set it in "
                "backend/.env locally and as a GitHub Actions repository "
                "secret named ELEVENLABS_API_KEY for the nightly run. "
                "ElevenLabsTTSProvider will not run without it — never fake "
                "a live run."
            )

        self._key = key
        self._model = model
        self._voice_a = settings.elevenlabs_voice_a.strip()
        self._voice_b = settings.elevenlabs_voice_b.strip()
        if not self._voice_a or not self._voice_b:
            raise RuntimeError(
                "ELEVENLABS_VOICE_A and ELEVENLABS_VOICE_B must both be set "
                "to ElevenLabs voice IDs. Two hosts with one voice is not a "
                "conversation, and falling back to a default voice would "
                "quietly ship a briefing that sounds wrong rather than "
                "failing where you can see it."
            )

    @property
    def voice_a(self) -> str:
        return self._voice_a

    @property
    def voice_b(self) -> str:
        return self._voice_b

    async def _once(self, text: str, voice_id: str) -> Speech:
        url = f"{API_ROOT}/text-to-speech/{voice_id}/with-timestamps"
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
            response = await client.post(
                url,
                headers={"xi-api-key": self._key, "accept": "application/json"},
                json={
                    "text": text,
                    "model_id": self._model,
                    # No filler, no "um" — CLAUDE.md rule 1. Naturalness is
                    # the model's job and the transcript stays clean, so
                    # nothing here asks for performance markup.
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                        "speed": 1.0,
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()

        audio_b64 = payload.get("audio_base64")
        if not audio_b64:
            raise RuntimeError(
                "ElevenLabs returned no audio_base64 for a 2xx response"
            )

        return Speech(
            audio=base64.b64decode(audio_b64),
            words=_words_from_alignment(payload.get("alignment")),
        )

    async def synthesize(self, text: str, voice_id: str) -> Speech:
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await self._once(text, voice_id)
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code
                # 4xx that is not rate limiting will not become true by being
                # asked again: a bad voice id, a revoked key, a quota that is
                # spent. Fail now with the body, which is where ElevenLabs
                # puts the reason.
                if status != 429 and 400 <= status < 500:
                    logger.error(
                        "elevenlabs rejected the request (%d): %.300s",
                        status, exc.response.text,
                    )
                    raise
                if attempt == MAX_ATTEMPTS:
                    raise
            except Exception as exc:  # noqa: BLE001 - httpx raises varied types
                last_error = exc
                if attempt == MAX_ATTEMPTS:
                    raise

            delay = BASE_DELAY_S * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            logger.warning(
                "elevenlabs error on attempt %d/%d, retrying in %.1fs: %s",
                attempt, MAX_ATTEMPTS, delay, last_error,
            )
            await asyncio.sleep(delay)

        raise last_error  # pragma: no cover - loop always returns or raises
