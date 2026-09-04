"""
ElevenLabs word alignment, the provider factory, and the one offset.

None of this calls ElevenLabs. What is worth testing is the arithmetic
between their response and the manifest — collapsing characters into words,
and turning clip-relative timings into absolute ones — because a word
highlight that is subtly wrong is worse than no highlight at all, and neither
of those needs a network to be checked.
"""

from __future__ import annotations

import pytest

from app.core.providers import Speech, WordTiming, build_tts_provider
from app.providers.elevenlabs_tts import _words_from_alignment


def _alignment(text: str, ms_per_char: int = 100) -> dict:
    """A well-formed alignment for `text`, one entry per character."""
    return {
        "characters": list(text),
        "character_start_times_seconds": [
            i * ms_per_char / 1000 for i in range(len(text))
        ],
        "character_end_times_seconds": [
            (i + 1) * ms_per_char / 1000 for i in range(len(text))
        ],
    }


# ── collapsing characters into words ──────────────────────────


def test_words_run_from_their_first_character_to_their_last():
    words = _words_from_alignment(_alignment("hi there"))

    assert [w.word for w in words] == ["hi", "there"]
    # "hi" is characters 0-1, so 0ms to 200ms.
    assert (words[0].start_ms, words[0].end_ms) == (0, 200)
    # "there" starts at character 3 and runs to the end of character 7.
    assert (words[1].start_ms, words[1].end_ms) == (300, 800)


def test_the_space_belongs_to_neither_word():
    words = _words_from_alignment(_alignment("ab cd"))
    assert words[0].end_ms == 200
    assert words[1].start_ms == 300


def test_punctuation_attached_to_a_word_stays_part_of_it():
    words = _words_from_alignment(_alignment("Northwind, yes."))
    assert [w.word for w in words] == ["Northwind,", "yes."]


def test_a_run_of_punctuation_alone_is_not_a_word():
    # An em dash set off by spaces is not something to light up.
    words = _words_from_alignment(_alignment("a — b"))
    assert [w.word for w in words] == ["a", "—", "b"]


def test_multiple_spaces_and_newlines_do_not_produce_empty_words():
    words = _words_from_alignment(_alignment("a  b\nc"))
    assert [w.word for w in words] == ["a", "b", "c"]
    assert all(w.word.strip() for w in words)


def test_trailing_word_is_not_dropped():
    """The last word has no space after it, so only the final flush emits
    it — the classic place this loop loses one."""
    words = _words_from_alignment(_alignment("one two"))
    assert [w.word for w in words] == ["one", "two"]


# ── refusing to guess ─────────────────────────────────────────


def test_no_alignment_means_no_words():
    assert _words_from_alignment(None) == ()
    assert _words_from_alignment({}) == ()


def test_arrays_of_different_lengths_produce_nothing_rather_than_a_guess():
    """Highlighting nothing is recoverable. Highlighting the wrong word is
    the exact bug the feature exists to avoid, so a malformed response drops
    the timings for that turn instead of zipping to the shortest array."""
    bad = _alignment("hello")
    bad["character_end_times_seconds"] = bad["character_end_times_seconds"][:2]

    assert _words_from_alignment(bad) == ()


def test_negative_times_are_clamped_rather_than_emitted():
    a = _alignment("hi")
    a["character_start_times_seconds"] = [-1.0, -0.5]
    a["character_end_times_seconds"] = [-0.5, 0.2]

    words = _words_from_alignment(a)
    assert words[0].start_ms == 0


# ── the provider choice, made in one place ────────────────────


def test_an_unset_provider_is_google(monkeypatch):
    """`tts_provider` sat in Settings read by nothing while three
    entrypoints each hardcoded Google. The default must stay Google, but it
    must now be the FACTORY saying so."""
    import app.core.config as config

    monkeypatch.setattr(config, "get_settings", _settings(tts_provider=""))
    built = _build_with_stubs(monkeypatch)
    assert built == "google"


def test_elevenlabs_is_selected_when_asked_for(monkeypatch):
    import app.core.config as config

    monkeypatch.setattr(config, "get_settings", _settings(tts_provider="elevenlabs"))
    built = _build_with_stubs(monkeypatch)
    assert built == "elevenlabs"


def test_a_broken_elevenlabs_falls_back_to_google_rather_than_failing_the_run(
    monkeypatch, caplog
):
    """Rule 6 keeps Google behind the protocol so a paid tier going down at
    4am costs the voice and not the briefing; rule 7 says the run still
    ships. The fallback is logged at ERROR because a briefing that quietly
    sounds like the old one is how you find out weeks later."""
    import app.core.config as config

    monkeypatch.setattr(config, "get_settings", _settings(tts_provider="elevenlabs"))
    built = _build_with_stubs(monkeypatch, elevenlabs_raises=True)

    assert built == "google"
    assert any(r.levelname == "ERROR" for r in caplog.records)


# ── helpers ───────────────────────────────────────────────────


def _settings(**kwargs):
    class _S:
        def __init__(self) -> None:
            self.tts_provider = kwargs.get("tts_provider", "google")

    return lambda: _S()


def _build_with_stubs(monkeypatch, *, elevenlabs_raises: bool = False) -> str:
    """Builds a provider with both implementations stubbed, returning which
    one came back. Neither real provider is constructed, so this needs no
    credentials of any kind."""
    import sys
    import types

    el = types.ModuleType("app.providers.elevenlabs_tts")

    class _Eleven:
        def __init__(self) -> None:
            if elevenlabs_raises:
                raise RuntimeError("ELEVENLABS_API_KEY is not set")

    el.ElevenLabsTTSProvider = _Eleven

    goog = types.ModuleType("app.providers.google_tts")

    class _Google:
        pass

    goog.GoogleTTSProvider = _Google

    monkeypatch.setitem(sys.modules, "app.providers.elevenlabs_tts", el)
    monkeypatch.setitem(sys.modules, "app.providers.google_tts", goog)

    provider = build_tts_provider()
    return "elevenlabs" if isinstance(provider, _Eleven) else "google"


# ── the offset that music will move ───────────────────────────


@pytest.mark.asyncio
async def test_word_timings_become_absolute_in_the_stitched_file():
    """The provider returns times relative to its own clip; the manifest
    needs them relative to the whole briefing. That conversion happens once,
    in `stitch`, at the point that already knows where the turn landed —
    which is what will keep it correct when intro music moves everything
    (ROADMAP task 3)."""
    from app.core.schemas import Segment, Turn
    from app.synthesis.stitch import synthesize_briefing

    class _TwoWordTTS:
        voice_a = "a"
        voice_b = "b"

        async def synthesize(self, text: str, voice_id: str) -> Speech:
            from app.providers.fake_tts import FakeTTSProvider

            base = await FakeTTSProvider().synthesize(text, FakeTTSProvider().voice_a)
            return Speech(
                audio=base.audio,
                words=(
                    WordTiming(word="first", start_ms=0, end_ms=200),
                    WordTiming(word="second", start_ms=200, end_ms=400),
                ),
            )

    turns = [
        Turn(speaker="A", segment=Segment.COLD_OPEN, text="first second"),
        Turn(speaker="B", segment=Segment.COLD_OPEN, text="first second"),
    ]

    _, manifest, _ = await synthesize_briefing(
        turns,
        tts=_TwoWordTTS(),
        briefing_id="2026-09-04",
        audio_url_for=lambda fmt: f"x.{fmt}",
        sources=[],
    )

    first, second = manifest.turns[0], manifest.turns[1]
    assert first.words is not None and second.words is not None

    # The first turn starts at zero, so its words are unchanged.
    assert first.words[0].start_ms == 0

    # The second turn starts later, and every one of its words moved with it
    # by exactly that much — no more and no less.
    assert second.start_ms is not None and second.start_ms > 0
    assert second.words[0].start_ms == second.start_ms
    assert second.words[1].start_ms == second.start_ms + 200


@pytest.mark.asyncio
async def test_a_provider_without_timings_leaves_words_none():
    """Google's path, and every manifest generated before this existed. The
    client falls back to whole-line highlighting."""
    from app.core.schemas import Segment, Turn
    from app.synthesis.stitch import synthesize_briefing

    class _NoTimings:
        voice_a = "a"
        voice_b = "b"

        async def synthesize(self, text: str, voice_id: str) -> Speech:
            from app.providers.fake_tts import FakeTTSProvider

            base = await FakeTTSProvider().synthesize(text, FakeTTSProvider().voice_a)
            return Speech(audio=base.audio)

    turns = [Turn(speaker="A", segment=Segment.COLD_OPEN, text="hello there")]

    _, manifest, _ = await synthesize_briefing(
        turns,
        tts=_NoTimings(),
        briefing_id="2026-09-04",
        audio_url_for=lambda fmt: f"x.{fmt}",
        sources=[],
    )

    assert manifest.turns[0].words is None
