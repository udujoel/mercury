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

from app.core.providers import TTSProvider, WordTiming
from app.core.schemas import (
    SEGMENT_LABELS,
    BriefingManifest,
    SourceRef,
    Turn,
    WordSpan,
)
from app.synthesis.music import (
    DUCK_GAIN_DB,
    DUCK_MS,
    MusicSet,
    load_music_set,
)

log = logging.getLogger(__name__)


class EmptyBriefingError(RuntimeError):
    """Every turn failed to synthesize, so there is no briefing to store.

    This is a hard failure rather than the graceful degradation CLAUDE.md rule
    7 asks for, and the distinction is the whole point. Rule 7 is about a
    briefing that ships missing a segment; this is a briefing with nothing
    audible in it at all, and shipping it is worse than shipping nothing:

      - it exits 0, so Actions goes green and no failure email is sent;
      - it stores a manifest, and generation is idempotent, so every later run
        that day declines to replace it;
      - the phone gets a valid manifest and 0ms of audio.

    Silent, self-perpetuating, and indistinguishable from success. A run that
    produced no audible turns must fail loudly and must not persist.
    """


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
    degraded_sources: list[str] | None = None,
    degraded_attempts: int = 0,
    music_set: str | None = None,
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

    # None means NO music here, not "the default set".
    #
    # The stitcher adds only what it is told to add. Defaulting at this
    # level would give every caller music — including the tests that assert
    # turn timings, which would then be asserting against an eight-second
    # offset nobody asked for. The PRODUCT default lives one level up, in
    # `generate_briefing`, where it is a decision about briefings rather
    # than about audio assembly.
    music = load_music_set(music_set) if music_set else None

    combined = AudioSegment.silent(duration=0)
    cursor_ms = 0
    # Where the voice starts, so the duck knows what it is ducking under.
    intro_ms = 0

    # The intro goes in FIRST, and `cursor_ms` moves with it.
    #
    # This is the whole trick, and the failure ADR-006 named. Every turn's
    # `start_ms` is an offset into the finished file, so prepending eight
    # seconds of music moves every turn, every chapter boundary, every
    # word timing and every story deep-link by eight seconds. Doing it here
    # — before the loop, on the same cursor the loop advances — means all
    # of them move for free.
    #
    # The alternative, adding the intro after the loop and correcting the
    # numbers, is a second place doing the arithmetic. That is how the
    # words end up lighting up eight seconds ahead of the voice.
    if music and music.intro is not None:
        combined += music.intro
        cursor_ms += len(music.intro)
        intro_ms = len(music.intro)

    # Only turns that actually made it into the audio belong in the manifest.
    # A skipped turn keeps start_ms/end_ms as None, and the manifest's whole
    # job is driving transcript sync — shipping a turn with no timing means
    # the client either crashes decoding it (the iOS model types start_ms as
    # a non-optional Int) or renders a sentence that is never spoken, which
    # desyncs everything after it. Rule 7 says degrade gracefully, and the
    # graceful degradation is a briefing missing one line, not a manifest
    # that lies about its own audio.
    rendered: list[Turn] = []
    # Old index -> new index, for remapping SourceRefs after compaction.
    index_map: dict[int, int] = {}

    for idx, (turn, synthesized) in enumerate(zip(turns, clips, strict=True)):
        if synthesized is None:
            log.error(
                "turn %d failed to synthesize; dropping it from the manifest "
                "so transcript sync stays aligned: %.60s",
                idx, turn.text,
            )
            continue

        clip, word_timings = synthesized
        # Loudness normalisation is gain only; it does not change duration,
        # so the timings stay valid across it.
        clip = _normalize_loudness(clip)

        # An interlude at each segment boundary, on the same cursor.
        #
        # Not before the first turn: the intro has just played, and a
        # transition sting immediately after it says "we are moving on"
        # before anything has been said.
        if (
            music
            and music.interlude is not None
            and rendered
            and turn.segment is not rendered[-1].segment
        ):
            combined += music.interlude
            cursor_ms += len(music.interlude)

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
        # Relative to the clip -> absolute in the stitched file, computed
        # HERE and only here. When intro music is prepended (ROADMAP task 3)
        # it moves `cursor_ms`, and every word moves with it for free — the
        # failure ADR-006 named is a second place doing this arithmetic.
        turn.words = [
            WordSpan(
                word=w.word,
                start_ms=cursor_ms + w.start_ms,
                end_ms=cursor_ms + w.end_ms,
            )
            for w in word_timings
        ] or None
        index_map[idx] = len(rendered)
        rendered.append(turn)
        combined += clip
        cursor_ms = turn.end_ms

    if not rendered:
        # Before _export, deliberately: there is nothing worth encoding, and
        # raising here means `generate_briefing` never reaches storage.save(),
        # so no empty manifest is written to block tomorrow's replacement.
        raise EmptyBriefingError(
            f"every one of the {len(turns)} turns in briefing {briefing_id} "
            "failed to synthesize, so there is no audio to store. The TTS "
            "provider is the thing to check — credentials, quota, or the "
            "service itself. Refusing to store an empty briefing: it would "
            "exit 0, look like success, and then block its own replacement "
            "for the rest of the day."
        )

    # The bed ducked under the voice at each end, before the outro is
    # appended and after every `end_ms` is fixed.
    #
    # `overlay` and not append or crossfade, which is the entire point: it
    # mixes into audio that already exists, so `len(combined)` does not
    # change and not one timing in the manifest moves. Appending a tail
    # would push every following turn later; a crossfade would pull them
    # earlier. ADR-006's rule is that offsets are computed in one place, and
    # this is how a mixing change respects it.
    if music is not None:
        combined = _duck(combined, music, intro_ms=intro_ms)

    # The outro, after every turn and after the last `end_ms` is fixed. It
    # carries no timings of its own, so nothing needs to move for it — but
    # it must be added before the export, which is why it is here rather
    # than anywhere more obvious.
    if music and music.outro is not None:
        combined += music.outro

    audio_bytes, audio_format = _export(combined)

    manifest = BriefingManifest(
        briefing_id=briefing_id,
        generated_at=datetime.now(UTC),
        # The duration of the FILE, not of the segment it was built from.
        #
        # AAC encoding pads to a frame boundary, so the exported audio can
        # be a few milliseconds longer than `len(combined)`. That gap was
        # invisible until music arrived and the e2e test — which asserts the
        # manifest against the real decoded audio — caught 20ms of it.
        #
        # Small, but the manifest's whole job is describing the file the
        # phone downloads. A duration that is not that file's duration is
        # the same class of untruth as a turn timing that does not match
        # its audio, and this project has shipped one of those already.
        duration_ms=_exported_duration_ms(audio_bytes, audio_format, combined),
        audio_url=audio_url_for(audio_format),
        segments=_segment_offsets(rendered),
        turns=rendered,
        sources=_remap_source_refs(sources, index_map),
        degraded_sources=list(degraded_sources or []),
        degraded_attempts=degraded_attempts,
    )

    log.info("synthesized %s: %d turns, %.1fs, %.1fKB, format=%s",
             briefing_id, len(turns), len(combined) / 1000,
             len(audio_bytes) / 1024, audio_format)

    return audio_bytes, manifest, audio_format


def _remap_source_refs(
    sources: list[SourceRef], index_map: dict[int, int]
) -> list[SourceRef]:
    """Rebase SourceRef.turn_index onto the compacted turn list.

    `_build_source_refs` numbers refs against the ORIGINAL script, but turns
    whose TTS failed are dropped from the manifest. Passing the refs through
    unchanged meant a ref for a dropped turn attached to whatever unrelated
    turn now occupies that index, and every later ref was off by one or
    pointed past the end — so tapping a show-notes entry would seek to the
    wrong moment, or the wrong source would be credited for a claim. That is
    a grounding failure (rule 3), not a cosmetic one: the audit trail would
    confidently cite the wrong thing.
    """
    remapped: list[SourceRef] = []
    for ref in sources:
        new_index = index_map.get(ref.turn_index)
        if new_index is None:
            log.debug(
                "dropping source ref for un-synthesised turn %d (%s)",
                ref.turn_index, ref.title[:60],
            )
            continue
        remapped.append(ref.model_copy(update={"turn_index": new_index}))
    return remapped


def _exported_duration_ms(
    data: bytes, audio_format: str, fallback: AudioSegment
) -> int:
    """How long the exported bytes actually are.

    Falls back to the pre-export segment when the result cannot be decoded
    — on a machine with no ffmpeg an m4a is unreadable, and a manifest that
    is a few milliseconds out beats a run that dies measuring itself.
    """
    try:
        return len(AudioSegment.from_file(io.BytesIO(data), format=_sniff_format(data)))
    except Exception:
        log.warning(
            "could not measure the exported %s; using the pre-export length, "
            "which may differ by a frame", audio_format,
        )
        return len(fallback)


def _duck(
    combined: AudioSegment, music: MusicSet, *, intro_ms: int
) -> AudioSegment:
    """Let the bed run quietly under the first and last turns.

    The roadmap asked for the music to recede under the voice rather than
    stop dead. Both obvious implementations move every timing in the
    manifest — a crossfade shortens the file, appending a tail lengthens it
    — so this OVERLAYS instead: the tail is mixed into audio that already
    exists, `len(combined)` is unchanged, and ADR-006's rule that offsets
    are computed in exactly one place is untouched.

    Silently does nothing when there is no room. A briefing shorter than the
    duck is not a briefing anyone is listening to the end of, and a mixing
    nicety must never be able to cost the morning (CLAUDE.md rule 7).
    """
    duration = len(combined)
    if duration <= 0:
        return combined

    if music.intro is not None and intro_ms > 0:
        # The last DUCK_MS of the intro, faded out and laid under the voice
        # that follows it — so the bed thins away under the greeting rather
        # than stopping the instant a host speaks.
        window = min(DUCK_MS, len(music.intro), duration - intro_ms)
        if window > 0:
            tail = (
                music.intro[-window:]
                .apply_gain(DUCK_GAIN_DB)
                .fade_out(window)
            )
            combined = combined.overlay(tail, position=intro_ms)

    if music.outro is not None:
        # The head of the outro, faded in under the sign-off, so the close
        # arrives with the voice rather than after it.
        window = min(DUCK_MS, len(music.outro), duration)
        if window > 0:
            head = (
                music.outro[:window]
                .apply_gain(DUCK_GAIN_DB)
                .fade_in(window)
            )
            combined = combined.overlay(head, position=duration - window)

    return combined


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
) -> list[tuple[AudioSegment, tuple[WordTiming, ...]] | None]:
    """The clip for each turn and its word timings, or None where TTS failed.

    The timings come back RELATIVE to their own clip, because the provider
    has no idea where the turn will land. They are made absolute in
    `synthesize_and_stitch`, at the one point that knows.
    """
    sem = asyncio.Semaphore(MAX_CONCURRENT_TTS)

    async def one(turn: Turn) -> tuple[AudioSegment, tuple[WordTiming, ...]] | None:
        async with sem:
            voice = tts.voice_a if turn.speaker == "A" else tts.voice_b
            try:
                speech = await tts.synthesize(turn.text, voice)
                raw = speech.audio
                return (
                    AudioSegment.from_file(
                        io.BytesIO(raw), format=_sniff_format(raw)
                    ),
                    speech.words,
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
