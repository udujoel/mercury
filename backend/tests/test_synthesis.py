"""
Tests for app.synthesis.stitch and app.providers.fake_tts.
"""

from __future__ import annotations

import wave
from io import BytesIO

import pytest

from app.core.schemas import Segment, SourceRef, SourceType, Turn
from app.providers.fake_tts import VOICE_A_ID, VOICE_B_ID, FakeTTSProvider
from app.synthesis import stitch
from app.synthesis.stitch import (
    FALLBACK_FORMAT,
    GAP_DIFFERENT_SPEAKER_MS,
    GAP_SAME_SPEAKER_MS,
    EmptyBriefingError,
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
    speech = await tts.synthesize("Hello there, this is a test.", tts.voice_a)
    # Must be openable as a real WAV file, no ffmpeg involved.
    with wave.open(BytesIO(speech.audio), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getnframes() > 0


async def test_fake_tts_duration_scales_with_word_count():
    tts = FakeTTSProvider()
    short = await tts.synthesize("One two three.", tts.voice_a)
    long = await tts.synthesize(" ".join(["word"] * 60), tts.voice_a)

    def _duration_s(audio: bytes) -> float:
        with wave.open(BytesIO(audio), "rb") as wf:
            return wf.getnframes() / wf.getframerate()

    assert _duration_s(long.audio) > _duration_s(short.audio)


async def test_fake_tts_voice_a_and_b_are_different_pitch():
    tts = FakeTTSProvider()
    assert tts.voice_a == VOICE_A_ID
    assert tts.voice_b == VOICE_B_ID

    clip_a = await tts.synthesize("Same text here for both.", tts.voice_a)
    clip_b = await tts.synthesize("Same text here for both.", tts.voice_b)
    # Same text, same duration, different pitch -> different sample bytes.
    assert clip_a.audio != clip_b.audio


# ──────────────────────────────────────────────────────────────
# stitch.synthesize_briefing
# ──────────────────────────────────────────────────────────────


async def test_synthesize_briefing_produces_monotonic_non_overlapping_turns(
    monkeypatch,
):
    # Pin the export branch so this test asserts timing invariants, not
    # whichever audio format this machine happens to be able to produce.
    monkeypatch.setattr(stitch, "ffmpeg_available", lambda: False)
    turns = _turns()
    tts = FakeTTSProvider()
    sources = [
        SourceRef(
            turn_index=0,
            title="x",
            url=None,
            source_name="y",
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
    # Format is pinned by the fixture below, not read off the machine, so the
    # manifest/URL agreement is asserted the same way on any dev box or in CI.
    assert audio_format == FALLBACK_FORMAT
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
        turns,
        tts=tts,
        briefing_id="x",
        audio_url_for=lambda fmt: "url",
        sources=[],
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
        turns,
        tts=tts,
        briefing_id="x",
        audio_url_for=lambda fmt: "url",
        sources=[],
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
        turns,
        tts=FlakyTTS(),
        briefing_id="x",
        audio_url_for=lambda fmt: "url",
        sources=[],
    )

    assert len(audio_bytes) > 0

    # A failed turn is dropped from the manifest, not carried with null
    # timing. This test previously asserted the opposite, and that shipped a
    # manifest the iOS client could not decode at all: `start_ms` is a
    # non-optional Int there, so one un-synthesised turn failed the whole
    # briefing with "Expected value of type Int but found null instead. Path:
    # manifest.turns[4].start_ms". Even with a tolerant client, a turn with no
    # audio would render a sentence that is never spoken and desync every
    # turn after it. Losing one line is the graceful degradation; a manifest
    # that misdescribes its own audio is not.
    assert [turn.text for turn in manifest.turns] == [
        "Good morning.",
        "Still sunny.",
    ]
    assert all(turn.start_ms is not None for turn in manifest.turns)
    assert all(turn.end_ms is not None for turn in manifest.turns)

    # And the surviving turns stay contiguous and ordered.
    assert manifest.turns[0].start_ms == 0
    assert manifest.turns[1].start_ms >= manifest.turns[0].end_ms


async def test_falls_back_to_wav_when_ffmpeg_is_missing(monkeypatch):
    """The fallback branch must be exercised on every machine.

    This used to `assert ffmpeg_available() is False`, which asserted a fact
    about the developer's laptop rather than about the code: installing
    ffmpeg turned it red and silently stopped testing the fallback at all.
    Force the branch instead.
    """
    monkeypatch.setattr(stitch, "ffmpeg_available", lambda: False)

    _, manifest, audio_format = await synthesize_briefing(
        turns=_turns(),
        tts=FakeTTSProvider(),
        briefing_id="x",
        audio_url_for=lambda fmt: f"url.{fmt}",
        sources=[],
    )
    assert audio_format == FALLBACK_FORMAT
    assert manifest.audio_url.endswith(FALLBACK_FORMAT)


@pytest.mark.skipif(
    not ffmpeg_available(),
    reason="ffmpeg genuinely required: this asserts real AAC encoding",
)
async def test_synthesize_briefing_uses_compressed_format_when_ffmpeg_present():
    """The compressed branch cannot be faked — it needs a real encoder.

    Skipped where ffmpeg is absent, which is honest; the fallback test above
    still runs everywhere.
    """
    _, manifest, audio_format = await synthesize_briefing(
        turns=_turns(),
        tts=FakeTTSProvider(),
        briefing_id="x",
        audio_url_for=lambda fmt: f"url.{fmt}",
        sources=[],
    )
    assert audio_format != FALLBACK_FORMAT
    assert manifest.audio_url.endswith(audio_format)


async def test_source_refs_are_remapped_when_a_turn_is_dropped():
    """SourceRef.turn_index must follow the compacted turn list.

    `_build_source_refs` numbers refs against the ORIGINAL script, but turns
    whose TTS failed are dropped from the manifest. Passing refs through
    unchanged meant a ref for a dropped turn attached to whatever unrelated
    turn now sat at that index, and every later ref was off by one or pointed
    past the end — so a show-notes tap would seek to the wrong moment and the
    wrong source would be credited for a claim. That is a rule 3 grounding
    failure, not cosmetics: the audit trail would confidently cite the wrong
    thing.
    """

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
    sources = [
        SourceRef(
            turn_index=0,
            title="kept-first",
            url=None,
            source_name="A",
            source_type=SourceType.EMAIL,
        ),
        SourceRef(
            turn_index=1,
            title="belongs-to-dropped-turn",
            url=None,
            source_name="B",
            source_type=SourceType.EMAIL,
        ),
        SourceRef(
            turn_index=2,
            title="kept-last",
            url=None,
            source_name="C",
            source_type=SourceType.EMAIL,
        ),
    ]

    _, manifest, _ = await synthesize_briefing(
        turns,
        tts=FlakyTTS(),
        briefing_id="x",
        audio_url_for=lambda fmt: "url",
        sources=sources,
    )

    # The ref for the dropped turn is gone, not silently reattached.
    titles = [s.title for s in manifest.sources]
    assert "belongs-to-dropped-turn" not in titles

    # Survivors point at the turns they actually describe.
    by_title = {s.title: s.turn_index for s in manifest.sources}
    assert by_title["kept-first"] == 0
    assert by_title["kept-last"] == 1, "index 2 no longer exists after compaction"
    assert all(s.turn_index < len(manifest.turns) for s in manifest.sources)


# ──────────────────────────────────────────────────────────────
# A briefing with nothing audible in it
# ──────────────────────────────────────────────────────────────


class _TotallyBrokenTTS(FakeTTSProvider):
    """What a bad TTS key, an exhausted quota or a provider outage looks like
    from in here: every call fails, not just some."""

    async def synthesize(self, text: str, voice_id: str) -> bytes:
        raise RuntimeError("synthetic total TTS failure")


async def test_total_tts_failure_raises_instead_of_producing_a_manifest():
    """Rule 7 covers a briefing that ships missing a segment. It does not
    cover a briefing with no audio at all, and the two must not be handled the
    same way — this one used to exit 0 with a green tick."""
    with pytest.raises(EmptyBriefingError) as excinfo:
        await synthesize_briefing(
            _turns(),
            tts=_TotallyBrokenTTS(),
            briefing_id="2026-08-27",
            audio_url_for=lambda fmt: "url",
            sources=[],
        )

    # The message has to name the briefing and point at the provider; this is
    # what lands in the Actions log and it is all the operator gets.
    message = str(excinfo.value)
    assert "2026-08-27" in message
    assert "TTS" in message
