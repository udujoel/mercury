"""
Tests for app/pipeline/voice.py — the Phase 3 streaming voice-answer
pipeline.

Headline tests (per the Phase 3 brief):
  - the sentence splitter on abbreviations/decimals/ellipses
  - the first audio chunk is emitted before the LLM has finished streaming
  - context selection picks the turns actually surrounding position_ms
  - a TTS failure on one sentence does not abort the answer
  - the LLM failing outright yields nothing
"""

from __future__ import annotations

import pytest

from app.core.schemas import (
    BriefingManifest,
    Segment,
    SourceRef,
    SourceType,
    Turn,
)
from app.pipeline.voice import (
    VoiceChunk,
    _build_context,
    _decode_json_string_prefix,
    _find_current_turn_index,
    _JSONFieldStreamer,
    answer_question,
    split_complete_sentences,
)
from app.providers.fake import FakeLLMProvider
from app.providers.fake_tts import FakeTTSProvider

pytestmark = pytest.mark.usefixtures("_no_ambient_credentials")


# ──────────────────────────────────────────────────────────────
# Sentence splitting
# ──────────────────────────────────────────────────────────────


def test_split_simple_sentences():
    sentences, remainder = split_complete_sentences(
        "This is one sentence. This is another one. "
    )
    assert sentences == ["This is one sentence.", "This is another one."]
    assert remainder == ""


def test_split_does_not_break_on_abbreviation_dr():
    sentences, remainder = split_complete_sentences(
        "Dr. Smith reviewed the results. The trial continues next week. "
    )
    assert sentences == [
        "Dr. Smith reviewed the results.",
        "The trial continues next week.",
    ]


def test_split_does_not_break_on_eg():
    sentences, remainder = split_complete_sentences(
        "Bring backups, e.g. spare cables. That covers the basics. "
    )
    assert sentences == [
        "Bring backups, e.g. spare cables.",
        "That covers the basics.",
    ]


def test_split_does_not_break_on_decimal():
    sentences, remainder = split_complete_sentences(
        "The total came to 3.14 million dollars. That is the final figure. "
    )
    assert sentences == [
        "The total came to 3.14 million dollars.",
        "That is the final figure.",
    ]


def test_split_does_not_break_mid_ellipsis_before_lowercase():
    # An ellipsis followed by a lower-case continuation reads as one
    # sentence still in progress, not two.
    sentences, remainder = split_complete_sentences("Well... let's see how it goes")
    assert sentences == []
    assert remainder == "Well... let's see how it goes"


def test_split_ellipsis_followed_by_new_sentence():
    sentences, remainder = split_complete_sentences(
        "He paused... Then he answered the question. "
    )
    assert sentences == [
        "He paused... Then he answered the question.",
    ]


def test_split_flush_emits_trailing_partial_sentence():
    sentences, remainder = split_complete_sentences(
        "This never got a final period", flush=True
    )
    assert sentences == ["This never got a final period"]
    assert remainder == ""


def test_split_without_flush_holds_back_incomplete_remainder():
    sentences, remainder = split_complete_sentences("This is still being typed")
    assert sentences == []
    assert remainder == "This is still being typed"


def test_split_multiple_sentences_in_one_buffer():
    text = (
        "Dr. Lee says the numbers are up 2.5 percent. "
        "That's still short of the e.g. quarterly target, though. "
        "So what happens next?"
    )
    sentences, remainder = split_complete_sentences(text, flush=True)
    assert sentences == [
        "Dr. Lee says the numbers are up 2.5 percent.",
        "That's still short of the e.g. quarterly target, though.",
        "So what happens next?",
    ]


# ──────────────────────────────────────────────────────────────
# Incremental JSON field extraction
# ──────────────────────────────────────────────────────────────


def test_json_field_streamer_whole_payload_at_once():
    parser = _JSONFieldStreamer()
    payload = '{"answer": "Hello there.", "bridge": "Back now."}'
    events = parser.feed(payload)
    fields = {}
    for field, text, _completed in events:
        fields.setdefault(field, "")
        fields[field] += text
    assert fields["answer"] == "Hello there."
    assert fields["bridge"] == "Back now."


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 7])
def test_json_field_streamer_survives_arbitrary_chunk_boundaries(chunk_size):
    payload = (
        '{"answer": "It costs $3.14, she said \\"exactly\\".", '
        '"bridge": "Anyway, back to it."}'
    )
    parser = _JSONFieldStreamer()
    answer_text = ""
    bridge_text = ""
    answer_completed = False
    bridge_completed = False
    for i in range(0, len(payload), chunk_size):
        for field, text, completed in parser.feed(payload[i : i + chunk_size]):
            if field == "answer":
                answer_text += text
                answer_completed = answer_completed or completed
            elif field == "bridge":
                bridge_text += text
                bridge_completed = bridge_completed or completed

    assert answer_text == 'It costs $3.14, she said "exactly".'
    assert bridge_text == "Anyway, back to it."
    assert answer_completed
    assert bridge_completed


def test_json_field_streamer_handles_newline_and_unicode_escapes():
    parser = _JSONFieldStreamer()
    payload = '{"answer": "Line one\\nLine two \\u2014 dash.", "bridge": "ok"}'
    fields: dict[str, str] = {}
    for field, text, _completed in parser.feed(payload):
        fields[field] = fields.get(field, "") + text
    assert fields["answer"] == "Line one\nLine two — dash."


def test_decode_json_string_prefix_stops_before_incomplete_escape():
    # A lone trailing backslash must not be guessed at -- decoding stops
    # right before it so a later call (with more text) can resolve it.
    text, end = _decode_json_string_prefix('abc\\', 0)
    assert text == "abc"
    assert end is None


def test_json_field_streamer_completed_fires_even_with_no_new_text():
    """The closing quote can arrive in a chunk on its own, after all the
    field's text already streamed in a previous chunk -- `completed` must
    still fire so the caller knows to flush/finalise that field."""
    parser = _JSONFieldStreamer()
    events1 = parser.feed('{"answer": "hello')
    assert events1 == [("answer", "hello", False)]
    events2 = parser.feed('"')
    assert events2 == [("answer", "", True)]


# ──────────────────────────────────────────────────────────────
# Context selection
# ──────────────────────────────────────────────────────────────


def _turn(
    speaker: str, segment: Segment, text: str, start_ms: int, end_ms: int
) -> Turn:
    return Turn(
        speaker=speaker, segment=segment, text=text,
        start_ms=start_ms, end_ms=end_ms,
    )


def _manifest_with_turns() -> BriefingManifest:
    # Time gaps here are deliberately much larger than a real briefing's
    # inter-turn silence (tens of ms — see app.synthesis.stitch) so that
    # CONTEXT_WINDOW_MS (45s) actually excludes the earliest turns from a
    # position later in the manifest, the way it would across a multi-
    # minute real segment. A tighter timeline would make every turn fall
    # inside the window regardless of whether the windowing logic works.
    turns = [
        _turn("A", Segment.COLD_OPEN, "Good morning, Joel.", 0, 10_000),
        _turn("B", Segment.COLD_OPEN, "It's a big day ahead.", 10_200, 20_000),
        _turn("A", Segment.CALENDAR, "First, the nine o'clock review.",
              20_200, 40_000),
        _turn("B", Segment.CALENDAR, "Priya wants your sign-off first.",
              40_200, 70_000),
        _turn("A", Segment.INBOX, "Speaking of Priya, she emailed the plan.",
              70_200, 100_000),
        _turn("B", Segment.INBOX, "The migration window is August 27th.",
              100_200, 130_000),
        _turn("A", Segment.WORLD, "In other news, a story about AI assistants.",
              130_200, 160_000),
    ]
    sources = [
        SourceRef(turn_index=2, title="Provider upgrade review", url=None,
                   source_name="Calendar", source_type=SourceType.CALENDAR),
        SourceRef(turn_index=4, title="Rollout plan sign-off", url=None,
                   source_name="Priya Sharma", source_type=SourceType.EMAIL),
        SourceRef(turn_index=6, title="AI assistants piece", url="https://example.com/a",
                   source_name="Stratechery", source_type=SourceType.RSS),
    ]
    return BriefingManifest(
        briefing_id="2026-08-21",
        generated_at="2026-08-21T07:00:00+00:00",
        duration_ms=160_000,
        audio_url="/briefing/2026-08-21/audio",
        segments=[],
        turns=turns,
        sources=sources,
    )


def test_find_current_turn_index_lands_inside_a_turn():
    manifest = _manifest_with_turns()
    assert _find_current_turn_index(manifest.turns, 90_000) == 4


def test_find_current_turn_index_lands_in_a_gap_falls_back_to_prior_turn():
    manifest = _manifest_with_turns()
    # 20100 sits between turn 1 (ends 20000) and turn 2 (starts 20200).
    assert _find_current_turn_index(manifest.turns, 20_100) == 1


def test_find_current_turn_index_past_the_end_falls_back_to_last_turn():
    manifest = _manifest_with_turns()
    assert _find_current_turn_index(manifest.turns, 999_999) == 6


def test_build_context_picks_turns_actually_around_position_ms():
    manifest = _manifest_with_turns()
    # Position inside turn 4 (Priya's email, 70200-100000ms).
    context = _build_context(manifest, 90_000)
    assert context is not None
    assert context.speaker == "A"
    assert context.segment == Segment.INBOX

    # The nearby turns (inbox segment, and enough of calendar for context)
    # must be present...
    assert (
        "Priya emailed the plan" in context.transcript_text
        or "Priya" in context.transcript_text
    )
    # ...but the far-away cold_open turn from the very start of the
    # briefing (well outside CONTEXT_WINDOW_MS) must NOT be pulled in --
    # this is the negative assertion that actually proves the window is
    # doing something, not just "some text came back".
    assert "Good morning, Joel." not in context.transcript_text


def test_build_context_sources_are_scoped_to_the_selected_turns():
    manifest = _manifest_with_turns()
    context = _build_context(manifest, 90_000)
    assert context is not None
    # Priya's rollout-plan source (attached to turn 4, in-window) should be
    # present...
    assert "Rollout plan sign-off" in context.sources_text
    # ...but the world-segment source attached to turn 6 -- far past
    # position_ms and not part of "what was just being discussed" -- must
    # not leak into this answer's grounding.
    assert "AI assistants piece" not in context.sources_text


def test_build_context_at_position_zero_does_not_crash():
    manifest = _manifest_with_turns()
    context = _build_context(manifest, 0)
    assert context is not None
    assert context.speaker == "A"


def test_build_context_returns_none_for_manifest_with_no_turns():
    manifest = _manifest_with_turns().model_copy(update={"turns": []})
    assert _build_context(manifest, 5000) is None


# ──────────────────────────────────────────────────────────────
# Full pipeline: streaming behaviour
# ──────────────────────────────────────────────────────────────


class _TrackingLLM:
    """Wraps a real LLMProvider's stream() to record whether the underlying
    generator has been fully drained yet -- lets a test prove the voice
    pipeline starts producing audio before the LLM has finished talking,
    rather than buffering the whole answer first."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.exhausted = False
        self.chunks_yielded = 0

    async def complete(self, *args, **kwargs):
        return await self._inner.complete(*args, **kwargs)

    async def stream(self, system: str, prompt: str, *, temperature: float = 0.85):
        async for chunk in self._inner.stream(system, prompt, temperature=temperature):
            self.chunks_yielded += 1
            yield chunk
        self.exhausted = True


async def test_first_audio_chunk_emitted_before_llm_stream_exhausted():
    manifest = _manifest_with_turns()
    tracking = _TrackingLLM(FakeLLMProvider())
    tts = FakeTTSProvider()

    exhausted_at_first_audio: bool | None = None
    chunks_yielded_at_first_audio: int | None = None
    chunks: list[VoiceChunk] = []

    async for chunk in answer_question(
        llm=tracking,
        tts=tts,
        question="What's Priya's plan about?",
        manifest=manifest,
        position_ms=90_000,
        prior_questions=None,
    ):
        chunks.append(chunk)
        if exhausted_at_first_audio is None and chunk.audio:
            exhausted_at_first_audio = tracking.exhausted
            chunks_yielded_at_first_audio = tracking.chunks_yielded

    assert chunks, "expected at least one audio chunk from the fake pipeline"
    assert exhausted_at_first_audio is False, (
        "the LLM stream was already fully drained by the time the first "
        "audio chunk was yielded -- the pipeline buffered the whole answer "
        "instead of streaming sentence by sentence"
    )
    # Sanity: the tracked run really did stop partway through the raw LLM
    # stream at that point -- fewer raw chunks had been pulled than the
    # eventual total once the whole answer was done -- not that it happened
    # to need exactly one chunk overall.
    assert chunks_yielded_at_first_audio is not None
    assert chunks_yielded_at_first_audio < tracking.chunks_yielded


async def test_answer_uses_the_same_host_voice_as_the_current_turn():
    manifest = _manifest_with_turns()
    # position_ms=90_000 lands in turn 4, speaker "A".
    chunks = [
        c async for c in answer_question(
            llm=FakeLLMProvider(), tts=FakeTTSProvider(),
            question="Tell me about the rollout plan.",
            manifest=manifest, position_ms=90_000,
        )
    ]
    real_chunks = [c for c in chunks if c.kind in ("answer", "bridge")]
    assert real_chunks
    assert all(c.speaker == "A" for c in real_chunks)


async def test_bridge_line_is_produced_last():
    manifest = _manifest_with_turns()
    chunks = [
        c async for c in answer_question(
            llm=FakeLLMProvider(), tts=FakeTTSProvider(),
            question="What's on the calendar?",
            manifest=manifest, position_ms=90_000,
        )
    ]
    real_chunks = [c for c in chunks if c.kind in ("answer", "bridge")]
    assert real_chunks, "expected at least the bridge chunk"
    assert real_chunks[-1].kind == "bridge"


async def test_fake_response_differs_for_different_questions():
    """A fake that returns the same blob regardless of input can't prove
    the context wiring works -- this asserts FakeLLMProvider actually
    responds to the question text asked."""
    manifest = _manifest_with_turns()

    async def _answer_text(question: str) -> str:
        chunks = [
            c async for c in answer_question(
                llm=FakeLLMProvider(), tts=FakeTTSProvider(),
                question=question, manifest=manifest, position_ms=90_000,
            )
        ]
        return "".join(c.text for c in chunks if c.kind == "answer")

    weather_answer = await _answer_text("What's the weather like?")
    plan_answer = await _answer_text("What does Priya's plan say?")
    assert weather_answer != plan_answer


# ──────────────────────────────────────────────────────────────
# Graceful degradation (CLAUDE.md rule 7)
# ──────────────────────────────────────────────────────────────


class _FlakyTTS:
    """Fails on the Nth synthesize() call, succeeds on every other one."""

    def __init__(self, inner: FakeTTSProvider, fail_on_call: int) -> None:
        self._inner = inner
        self._fail_on_call = fail_on_call
        self._calls = 0

    @property
    def voice_a(self) -> str:
        return self._inner.voice_a

    @property
    def voice_b(self) -> str:
        return self._inner.voice_b

    async def synthesize(self, text: str, voice_id: str) -> bytes:
        self._calls += 1
        if self._calls == self._fail_on_call:
            raise RuntimeError("simulated TTS outage")
        return await self._inner.synthesize(text, voice_id)


async def test_tts_failure_on_one_sentence_does_not_abort_the_answer():
    manifest = _manifest_with_turns()
    flaky = _FlakyTTS(FakeTTSProvider(), fail_on_call=1)

    chunks = [
        c async for c in answer_question(
            llm=FakeLLMProvider(), tts=flaky,
            question="What does Priya's plan say about the rollout?",
            manifest=manifest, position_ms=90_000,
        )
    ]
    real_chunks = [c for c in chunks if c.kind in ("answer", "bridge")]
    assert real_chunks

    # The failed sentence is REPORTED, not silently dropped. This assertion
    # used to be `all(c.audio for c in real_chunks)`, which encoded the old
    # behaviour: a chunk whose TTS failed never reached the caller at all, so
    # a client receiving sentences 1 and 3 of a three-sentence answer could
    # not tell that sentence 2 had ever existed. A silently truncated answer
    # was indistinguishable from a complete one. Rule 7 says keep going, not
    # keep quiet.
    failed = [c for c in real_chunks if c.audio_failed]
    assert len(failed) == 1, "the one failed synth call should be surfaced"
    assert not failed[0].audio
    assert failed[0].text, "and it should carry the text that was lost"

    # Everything else still played — the failure did not abort the answer.
    survivors = [c for c in real_chunks if not c.audio_failed]
    assert survivors
    assert all(c.audio for c in survivors)


class _ImmediatelyFailingLLM:
    async def complete(self, *args, **kwargs) -> str:
        raise RuntimeError("simulated LLM outage")

    async def stream(self, system: str, prompt: str, *, temperature: float = 0.85):
        raise RuntimeError("simulated LLM outage")
        yield ""  # pragma: no cover - unreachable, makes this an async generator


async def test_llm_failing_outright_yields_nothing():
    manifest = _manifest_with_turns()
    chunks = [
        c async for c in answer_question(
            llm=_ImmediatelyFailingLLM(), tts=FakeTTSProvider(),
            question="What does Priya's plan say?",
            manifest=manifest, position_ms=90_000,
        )
    ]
    assert chunks == []


# ──────────────────────────────────────────────────────────────
# Timing instrumentation sanity
# ──────────────────────────────────────────────────────────────


async def test_timing_metrics_are_monotonically_sane():
    manifest = _manifest_with_turns()
    chunks = [
        c async for c in answer_question(
            llm=FakeLLMProvider(), tts=FakeTTSProvider(),
            question="What does Priya's plan say?",
            manifest=manifest, position_ms=90_000,
        )
    ]
    timed = [c for c in chunks if c.timing is not None]
    assert len(timed) == 1, "exactly one chunk should carry the final timing"
    timing = timed[0].timing
    assert timing["llm_first_token_ms"] is not None
    assert timing["first_sentence_ms"] is not None
    assert timing["first_audio_ms"] is not None
    assert timing["total_ms"] is not None
    assert timing["llm_first_token_ms"] <= timing["first_sentence_ms"]
    assert timing["first_sentence_ms"] <= timing["first_audio_ms"]
    assert timing["first_audio_ms"] <= timing["total_ms"]
    # All non-negative -- a naive ms computation using wall-clock time
    # instead of a monotonic clock could go negative across a clock
    # adjustment; time.monotonic() must never.
    assert all(v >= 0 for v in timing.values())
