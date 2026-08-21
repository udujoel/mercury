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
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydub import AudioSegment
from pydub.effects import normalize
from pydub.utils import which

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

# pydub's mp4/aac export shells out to ffmpeg. Machines without it (this one,
# for example) can only export formats pydub/audioop handle natively — WAV
# chief among them. Detect availability once per call rather than assuming.
COMPRESSED_FORMAT = "mp4"
COMPRESSED_EXTENSION = "m4a"
FALLBACK_FORMAT = "wav"


def ffmpeg_available() -> bool:
    return which("ffmpeg") is not None


async def synthesize_briefing(
    turns: list[Turn],
    *,
    tts: TTSProvider,
    briefing_id: str,
    audio_url_for: Callable[[str], str],
    sources: list[SourceRef],
) -> tuple[bytes, BriefingManifest, str]:
    """Synthesise every turn, stitch, and emit the timing manifest.

    `audio_url_for` is called with the audio format actually produced
    ("m4a" if ffmpeg is available, "wav" if it fell back) and must return
    the URL/path the manifest should point at — callers decide that once
    they know the real extension, since it's typically the storage layer
    that names the file.

    Returns (audio_bytes, manifest, audio_format).
    """

    clips = await _synthesize_all(turns, tts)

    combined = AudioSegment.silent(duration=0)
    cursor_ms = 0

    # Only turns that actually made it into the audio belong in the manifest.
    # A skipped turn keeps start_ms/end_ms as None, and the manifest's whole
    # job is driving transcript sync — shipping a turn with no timing means
    # the client either crashes decoding it (the iOS model types start_ms as
    # a non-optional Int) or renders a sentence that is never spoken, which
    # desyncs everything after it. Rule 7 says degrade gracefully, and the
    # graceful degradation is a briefing missing one line, not a manifest
    # that lies about its own audio.
    rendered: list[Turn] = []

    for idx, (turn, clip) in enumerate(zip(turns, clips, strict=True)):
        if clip is None:
            log.error(
                "turn %d failed to synthesize; dropping it from the manifest "
                "so transcript sync stays aligned: %.60s",
                idx, turn.text,
            )
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
        rendered.append(turn)
        combined += clip
        cursor_ms = turn.end_ms

    audio_bytes, audio_format = _export(combined)

    manifest = BriefingManifest(
        briefing_id=briefing_id,
        generated_at=datetime.now(UTC),
        duration_ms=len(combined),
        audio_url=audio_url_for(audio_format),
        segments=_segment_offsets(rendered),
        turns=rendered,
        sources=sources,
    )

    log.info("synthesized %s: %d turns, %.1fs, %.1fKB, format=%s",
             briefing_id, len(turns), len(combined) / 1000,
             len(audio_bytes) / 1024, audio_format)

    return audio_bytes, manifest, audio_format


def _export(combined: AudioSegment) -> tuple[bytes, str]:
    """Export the stitched audio, preferring compressed m4a/aac but falling
    back to WAV — loudly — when ffmpeg isn't available or the export fails.
    Never crashes the run over an audio format; a briefing with WAV audio
    beats no briefing at all."""

    if ffmpeg_available():
        try:
            # Export to a real .m4a file rather than a BytesIO buffer. pydub
            # hands a buffer to ffmpeg as an extensionless temp file, and the
            # mp4 muxer needs a seekable, identifiable target — it fails there
            # with a bare "error code: -5" and no message. The "ipod" muxer is
            # ffmpeg's m4a writer and is the reliable path for AAC-in-MP4.
            with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                combined.export(
                    str(tmp_path), format="ipod", codec="aac", bitrate="64k",
                    parameters=["-ac", "1"],
                )
                data = tmp_path.read_bytes()
            finally:
                tmp_path.unlink(missing_ok=True)
            if not data:
                raise RuntimeError("ffmpeg produced an empty m4a file")
            return data, COMPRESSED_EXTENSION
        except Exception:
            log.exception(
                "ffmpeg is installed but m4a/aac export failed; falling "
                "back to WAV"
            )
    else:
        log.warning(
            "ffmpeg not found on this machine — pydub cannot export "
            "compressed audio (mp4/aac) without it. Falling back to WAV. "
            "Install ffmpeg to produce smaller, streamable audio in "
            "production."
        )

    buf = io.BytesIO()
    combined.export(buf, format=FALLBACK_FORMAT)
    return buf.getvalue(), FALLBACK_FORMAT


async def _synthesize_all(
    turns: list[Turn], tts: TTSProvider
) -> list[AudioSegment | None]:
    sem = asyncio.Semaphore(MAX_CONCURRENT_TTS)

    async def one(turn: Turn) -> AudioSegment | None:
        async with sem:
            voice = tts.voice_a if turn.speaker == "A" else tts.voice_b
            try:
                raw = await tts.synthesize(turn.text, voice)
                return AudioSegment.from_file(
                    io.BytesIO(raw), format=_sniff_format(raw)
                )
            except Exception as exc:
                # Log the cause inline, not just a traceback: these failures
                # are intermittent (2 of 43 turns on a live run, both
                # speakers, ordinary text lengths), so the message is the
                # only way to tell a rate limit from a bad payload.
                log.error(
                    "TTS failed for turn (%s, %d chars) [%s: %s]: %.60s",
                    turn.speaker, len(turn.text),
                    type(exc).__name__, exc, turn.text,
                )
                return None

    return await asyncio.gather(*(one(t) for t in turns))


def _sniff_format(raw: bytes) -> str | None:
    """Best-effort container sniffing so WAV clips (FakeTTSProvider's
    output, produced with the stdlib `wave` module) can be decoded by
    pydub's native WAV path — which needs neither ffmpeg nor ffprobe —
    instead of falling through to the ffmpeg-backed generic path. Returns
    None (auto-detect via ffmpeg/ffprobe) for anything that isn't WAV,
    which is what live providers' MP3 output already requires."""
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        return "wav"
    return None


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
