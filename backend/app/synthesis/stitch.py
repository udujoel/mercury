"""
Audio synthesis and stitching.

Three things here are easy to skip and all three are the difference between
"two voices alternating" and "a conversation":

1. Parallel synthesis. A 60-turn briefing serialised takes minutes.
2. Inter-turn pacing. Different-speaker gaps must be longer than
   same-speaker gaps. This is what your ear reads as turn-taking.
3. Loudness normalisation. Different voices synthesise at different levels
   and the jump between them is jarring.
"""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime, timezone

from pydub import AudioSegment
from pydub.effects import normalize

from app.core.providers import TTSProvider
from app.core.schemas import (
    SEGMENT_LABELS,
    BriefingManifest,
    SourceRef,
    Turn,
)

log = logging.getLogger(__name__)

# Pacing. Tuned by ear — these numbers matter more than they look.
GAP_DIFFERENT_SPEAKER_MS = 220
GAP_SAME_SPEAKER_MS = 80
TARGET_DBFS = -16.0
MAX_CONCURRENT_TTS = 8


async def synthesize_briefing(
    turns: list[Turn],
    *,
    tts: TTSProvider,
    briefing_id: str,
    audio_url: str,
    sources: list[SourceRef],
) -> tuple[bytes, BriefingManifest]:
    """Synthesise every turn, stitch, and emit the timing manifest."""

    clips = await _synthesize_all(turns, tts)

    combined = AudioSegment.silent(duration=0)
    cursor_ms = 0

    for idx, (turn, clip) in enumerate(zip(turns, clips, strict=True)):
        if clip is None:
            log.error("turn %d failed to synthesize, skipping", idx)
            continue

        clip = _normalize_loudness(clip)

        if idx > 0:
            prev = turns[idx - 1]
            gap = (
                GAP_SAME_SPEAKER_MS
                if prev.speaker == turn.speaker
                else GAP_DIFFERENT_SPEAKER_MS
            )
            combined += AudioSegment.silent(duration=gap)
            cursor_ms += gap

        turn.start_ms = cursor_ms
        turn.end_ms = cursor_ms + len(clip)
        combined += clip
        cursor_ms = turn.end_ms

    buf = io.BytesIO()
    combined.export(buf, format="mp4", codec="aac", bitrate="64k",
                    parameters=["-ac", "1"])
    audio_bytes = buf.getvalue()

    manifest = BriefingManifest(
        briefing_id=briefing_id,
        generated_at=datetime.now(timezone.utc),
        duration_ms=len(combined),
        audio_url=audio_url,
        segments=_segment_offsets(turns),
        turns=turns,
        sources=sources,
    )

    log.info("synthesized %s: %d turns, %.1fs, %.1fKB",
             briefing_id, len(turns), len(combined) / 1000,
             len(audio_bytes) / 1024)

    return audio_bytes, manifest


async def _synthesize_all(
    turns: list[Turn], tts: TTSProvider
) -> list[AudioSegment | None]:
    sem = asyncio.Semaphore(MAX_CONCURRENT_TTS)

    async def one(turn: Turn) -> AudioSegment | None:
        async with sem:
            voice = tts.voice_a if turn.speaker == "A" else tts.voice_b
            try:
                raw = await tts.synthesize(turn.text, voice)
                return AudioSegment.from_file(io.BytesIO(raw))
            except Exception:
                log.exception("TTS failed for turn: %.60s", turn.text)
                return None

    return await asyncio.gather(*(one(t) for t in turns))


def _normalize_loudness(clip: AudioSegment) -> AudioSegment:
    clip = normalize(clip)
    return clip.apply_gain(TARGET_DBFS - clip.dBFS)


def _segment_offsets(turns: list[Turn]) -> list[dict]:
    """First turn of each segment becomes the chapter seek point."""
    out: list[dict] = []
    seen: set[str] = set()
    for turn in turns:
        key = turn.segment.value
        if key in seen or turn.start_ms is None:
            continue
        seen.add(key)
        out.append({
            "id": key,
            "label": SEGMENT_LABELS[turn.segment],
            "start_ms": turn.start_ms,
        })
    return out
