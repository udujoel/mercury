"""
Live TTSProvider backed by Google Cloud Text-to-Speech.

Structurally satisfies `app.core.providers.TTSProvider` (a runtime_checkable
Protocol) — no explicit inheritance required, just matching methods.

To use this provider:
  1. In the Google Cloud Console, enable the "Cloud Text-to-Speech API" on
     your project.
  2. Create a service account, grant it a role that can call that API
     (e.g. "Cloud Text-to-Speech User"), and download a JSON key for it.
  3. Set GOOGLE_TTS_CREDENTIALS in backend/.env to the path of that JSON
     key file.

Never invent or hardcode credentials. If GOOGLE_TTS_CREDENTIALS does not
point at a real file, __init__ raises immediately with the exact env var
name and the Console step, rather than making a doomed network call.
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

from app.core.config import get_settings
from app.core.providers import Speech

logger = logging.getLogger(__name__)

# Same retry posture as GeminiProvider: transient rate limiting or backend
# unavailability should not fail a whole generation run.
MAX_ATTEMPTS = 5
BASE_DELAY_S = 1.5


# The backend package root (…/backend), i.e. the directory .env lives in.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _resolve_credentials_path(raw: str) -> Path:
    """Resolve a credentials path relative to backend/, not the cwd.

    .env ships `GOOGLE_TTS_CREDENTIALS=./credentials/gcp-tts.json`, which is
    written relative to backend/ because that is where .env sits. Resolving it
    against the process cwd meant the provider worked when launched from
    backend/ and failed everywhere else — including from the repo root, from a
    cron entry, and from any deployment whose working directory differs. The
    error it raised ("not set to a valid service account JSON key file") sent
    you looking at your credentials instead of your working directory.
    """
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return (_BACKEND_ROOT / path).resolve()


class GoogleTTSProvider:
    """TTSProvider backed by google-cloud-texttospeech."""

    def __init__(self) -> None:
        settings = get_settings()
        creds_path = _resolve_credentials_path(settings.google_tts_credentials)
        if not creds_path.is_file():
            raise RuntimeError(
                "GOOGLE_TTS_CREDENTIALS is not set to a valid service account "
                f"JSON key file (looked for: {creds_path}). In the Google "
                "Cloud Console, enable the 'Cloud Text-to-Speech API', create "
                "a service account with access to it, download its JSON key, "
                "and set GOOGLE_TTS_CREDENTIALS=/path/to/key.json in "
                "backend/.env. GoogleTTSProvider will not run without it — "
                "never fake a live run."
            )

        from google.cloud import texttospeech

        self._texttospeech = texttospeech
        self._client = texttospeech.TextToSpeechClient.from_service_account_file(
            str(creds_path)
        )
        self._voice_a = settings.tts_voice_host_a
        self._voice_b = settings.tts_voice_host_b

    @property
    def voice_a(self) -> str:
        return self._voice_a

    @property
    def voice_b(self) -> str:
        return self._voice_b

    def _sync_synthesize(self, text: str, voice_name: str) -> bytes:
        tts = self._texttospeech
        response = self._client.synthesize_speech(
            input=tts.SynthesisInput(text=text),
            voice=tts.VoiceSelectionParams(language_code="en-US", name=voice_name),
            audio_config=tts.AudioConfig(
                audio_encoding=tts.AudioEncoding.MP3,
                speaking_rate=1.0,
            ),
        )
        return response.audio_content

    async def synthesize(self, text: str, voice_id: str) -> Speech:
        """No word timings.

        Neural2 can only produce them through SSML `<mark>` injection plus
        the v1beta1 timepoints API — a word-level markup pipeline built to
        be thrown away the moment ElevenLabs is the default. Empty rather
        than None so consumers stay on one code path; transcript
        highlighting degrades to whole-line, which is what the client
        already does for every manifest generated before timings existed.
        """
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                # The SDK is synchronous; run it in a thread so this
                # provider never blocks the event loop the rest of the
                # pipeline runs on.
                audio = await asyncio.to_thread(self._sync_synthesize, text, voice_id)
                return Speech(audio=audio)
            except Exception as exc:  # noqa: BLE001 - SDK raises varied types
                last_error = exc
                if attempt == MAX_ATTEMPTS:
                    raise
                delay = BASE_DELAY_S * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                logger.warning(
                    "google tts error on attempt %d/%d, retrying in %.1fs: %s",
                    attempt,
                    MAX_ATTEMPTS,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
        raise last_error  # pragma: no cover - unreachable, loop always returns/raises
