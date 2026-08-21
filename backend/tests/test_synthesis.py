"""
Tests for app.synthesis.stitch and app.providers.fake_tts.
"""

from __future__ import annotations

import wave
from io import BytesIO

import pytest

from app.core.schemas import Segment, SourceRef, SourceType, Turn
from app.providers.fake_tts import VOICE_A_ID, VOICE_B_ID, FakeTTSProvider
from app.synthesis.stitch import (
    FALLBACK_FORMAT,
    GAP_DIFFERENT_SPEAKER_MS,
    GAP_SAME_SPEAKER_MS,
    ffmpeg_available,
    synthesize_briefing,
)


def _turns() -> list[Turn]:
    return [
        Turn(speaker="A", segment=Segment.COLD_OPEN, text="Good morning."),
        Turn(speaker="A", segment=Segment.COLD_OPEN, text="Let's get into it."),
        Turn(speaker="B", segment=Segment.WEATHER, text="Sixteen degrees in Aveiro."),
    ]


# ──────────────────────────────────────────────────────────────
# FakeTTSProvider
# ──────────────────────────────────────────────────────────────


async def test_fake_tts_produces_valid_wav_bytes():
    tts = FakeTTSProvider()
    audio = await tts.synthesize("Hello there, this is a test.", tts.voice_a)
    # Must be openable as a real WAV file, no ffmpeg involved.
    with wave.open(BytesIO(audio), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getnframes() > 0


async def test_fake_tts_duration_scales_with_word_count():
    tts = FakeTTSProvider()
    short = await tts.synthesize("One two three.", tts.voice_a)
    long = await tts.synthesize(" ".join(["word"] * 60), tts.voice_a)

    def _duration_s(audio: bytes) -> float:
        with wave.open(BytesIO(audio), "rb") as wf:
            return wf.getnframes() / wf.getframerate()

    assert _duration_s(long) > _duration_s(short)


async def test_fake_tts_voice_a_and_b_are_different_pitch():
    tts = FakeTTSProvider()
    assert tts.voice_a == VOICE_A_ID
    assert tts.voice_b == VOICE_B_ID

    clip_a = await tts.synthesize("Same text here for both.", tts.voice_a)
    clip_b = await tts.synthesize("Same text here for both.", tts.voice_b)
    # Same text, same duration, different pitch -> different sample bytes.
    assert clip_a != clip_b


# ──────────────────────────────────────────────────────────────
# stitch.synthesize_briefing
# ──────────────────────────────────────────────────────────────


async def test_synthesize_briefing_produces_monotonic_non_overlapping_turns():
    turns = _turns()
    tts = FakeTTSProvider()
    sources = [
        SourceRef(
            turn_index=0, title="x", url=None, source_name="y",
            source_type=SourceType.EMAIL,
        )
    ]

    audio_bytes, manifest, audio_format = await synthesize_briefing(
        turns,
        tts=tts,
        briefing_id="2026-08-21",
        audio_url_for=lambda fmt: f"/briefing/2026-08-21/audio.{fmt}",
        sources=sources,
    )

    assert len(audio_bytes) > 0
    assert audio_format == FALLBACK_FORMAT  # no ffmpeg on this machine
    assert manifest.audio_url.endswith(FALLBACK_FORMAT)

    prev_end = -1
    for turn in manifest.turns:
        assert turn.start_ms is not None
        assert turn.end_ms is not None
        assert turn.start_ms >= prev_end
        assert turn.end_ms > turn.start_ms
        prev_end = turn.end_ms

    assert manifest.duration_ms == prev_end


async def test_synthesize_briefing_gap_is_larger_across_speakers():
    # Two same-speaker turns then a different-speaker turn: the gap before
    # turn 2 (A->B) must be larger than the gap before turn 1 (A->A).
    turns = _turns()
    tts = FakeTTSProvider()

    _, manifest, _ = await synthesize_briefing(
        turns, tts=tts, briefing_id="x",
        audio_url_for=lambda fmt: "url", sources=[],
    )

    gap_same = manifest.turns[1].start_ms - manifest.turns[0].end_ms
    gap_diff = manifest.turns[2].start_ms - manifest.turns[1].end_ms
    assert gap_same == GAP_SAME_SPEAKER_MS
    assert gap_diff == GAP_DIFFERENT_SPEAKER_MS
    assert gap_diff > gap_same


async def test_synthesize_briefing_segments_point_at_first_turn_of_segment():
    turns = _turns()
    tts = FakeTTSProvider()

    _, manifest, _ = await synthesize_briefing(
        turns, tts=tts, briefing_id="x",
        audio_url_for=lambda fmt: "url", sources=[],
    )

    seg_ids = [s["id"] for s in manifest.segments]
    assert seg_ids == [Segment.COLD_OPEN.value, Segment.WEATHER.value]

    cold_open_start = manifest.segments[0]["start_ms"]
    weather_start = manifest.segments[1]["start_ms"]
    assert cold_open_start == manifest.turns[0].start_ms
    assert weather_start == manifest.turns[2].start_ms


async def test_synthesize_briefing_skips_failed_turn_without_failing_run():
    class FlakyTTS(FakeTTSProvider):
        async def synthesize(self, text: str, voice_id: str) -> bytes:
            if "boom" in text:
                raise RuntimeError("synthetic TTS failure")
            return await super().synthesize(text, voice_id)

    turns = [
        Turn(speaker="A", segment=Segment.COLD_OPEN, text="Good morning."),
        Turn(speaker="B", segment=Segment.COLD_OPEN, text="This will boom."),
        Turn(speaker="A", segment=Segment.WEATHER, text="Still sunny."),
    ]

    audio_bytes, manifest, _ = await synthesize_briefing(
        turns, tts=FlakyTTS(), briefing_id="x",
        audio_url_for=lambda fmt: "url", sources=[],
    )

    assert len(audio_bytes) > 0
    # The failed turn is skipped, not fatal — it has no timing.
    assert manifest.turns[1].start_ms is None
    assert manifest.turns[1].end_ms is None
    assert manifest.turns[0].start_ms is not None
    assert manifest.turns[2].start_ms is not None


def test_ffmpeg_not_available_on_this_machine():
    # Documents the environment this suite runs in: no ffmpeg, WAV fallback
    # must be exercised, not skipped.
    assert ffmpeg_available() is False


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")
async def test_synthesize_briefing_uses_compressed_format_when_ffmpeg_present():
    turns = _turns()
    _, manifest, audio_format = await synthesize_briefing(
        turns, tts=FakeTTSProvider(), briefing_id="x",
        audio_url_for=lambda fmt: f"url.{fmt}", sources=[],
    )
    assert audio_format != FALLBACK_FORMAT
