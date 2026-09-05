"""
Offline, deterministic TTSProvider. No network, no ffmpeg.

Synthesises a short sine-wave tone per turn using only the stdlib `wave`
module, so the output is real, valid, playable audio produced with zero
external dependencies. Duration is derived from the turn's word count at
~150 words per minute — the PRD's speech-rate calibration — so the timing
manifest this feeds is genuinely testable end to end today, without a live
TTS call or ffmpeg installed.

Host A and host B synthesise at different pitches (a low tone vs a higher
one) purely so the two voices are audibly distinguishable when stitched —
useful both for a human listening with headphones and for a test asserting
the two clips differ.
"""

from __future__ import annotations

import io
import math
import struct
import wave

from app.core.providers import Speech, WordTiming

# Natural conversational speech, per app/core/schemas.py LENGTH_BUDGETS.
WORDS_PER_MINUTE = 150
MIN_DURATION_S = 0.6
SAMPLE_RATE = 24000
AMPLITUDE = 8000
FADE_SAMPLES = 200  # short linear fade in/out so clips don't click at the edges

VOICE_A_ID = "fake-voice-a"
VOICE_B_ID = "fake-voice-b"

_FREQUENCY_BY_VOICE = {
    VOICE_A_ID: 220.0,  # low tone — host A
    VOICE_B_ID: 330.0,  # higher tone — host B
}


def _even_words(text: str, duration_s: float) -> tuple[WordTiming, ...]:
    """Word timings spread evenly across the clip."""
    words = text.split()
    if not words:
        return ()
    total_ms = int(duration_s * 1000)
    each = max(total_ms // len(words), 1)
    return tuple(
        WordTiming(word=w, start_ms=i * each, end_ms=min((i + 1) * each, total_ms))
        for i, w in enumerate(words)
    )


def _synthesize_tone(frequency: float, duration_s: float) -> bytes:
    n_samples = max(int(SAMPLE_RATE * duration_s), 1)
    frames = bytearray()
    fade = min(FADE_SAMPLES, n_samples // 2)
    for i in range(n_samples):
        t = i / SAMPLE_RATE
        envelope = 1.0
        if fade:
            envelope = min(1.0, i / fade, (n_samples - i) / fade)
        sample = int(AMPLITUDE * envelope * math.sin(2 * math.pi * frequency * t))
        frames += struct.pack("<h", sample)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


class FakeTTSProvider:
    """Deterministic, offline TTSProvider. Satisfies
    `app.core.providers.TTSProvider` structurally."""

    @property
    def voice_a(self) -> str:
        return VOICE_A_ID

    @property
    def voice_b(self) -> str:
        return VOICE_B_ID

    async def synthesize(self, text: str, voice_id: str) -> Speech:
        word_count = max(len(text.split()), 1)
        duration_s = max(word_count / WORDS_PER_MINUTE * 60.0, MIN_DURATION_S)
        frequency = _FREQUENCY_BY_VOICE.get(voice_id, _FREQUENCY_BY_VOICE[VOICE_B_ID])
        return Speech(
            audio=_synthesize_tone(frequency, duration_s),
            # Evenly divided across the clip. Not real timings — nothing here
            # is really spoken — but real ENOUGH that the manifest a test
            # builds has word data in the right shape and the right total
            # span, so the offsetting in `stitch` and the client's decoding
            # can both be exercised without a live ElevenLabs call.
            words=_even_words(text, duration_s),
        )
