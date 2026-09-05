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

from app.core.providers import (
    FallbackTTSProvider,
    Speech,
    WordTiming,
    build_tts_provider,
)
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


def _provider_answering(monkeypatch, *, status: int, body: str):
    """An `ElevenLabsTTSProvider` whose HTTP call returns one fixed response.

    Patched at the client rather than over the network: what is under test
    is which sentence reaches the log for which error code, and that needs
    no socket.
    """
    import httpx

    from app.core.config import get_settings
    from app.providers.elevenlabs_tts import ElevenLabsTTSProvider

    # The provider reads the key from settings rather than taking one, so
    # the environment is where a test supplies it.
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    get_settings.cache_clear()
    provider = ElevenLabsTTSProvider()

    async def _post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(status, text=body, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    return provider


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
    """The test that was wrong in the first cut of this file.

    It asserted the em dash WAS in the list while carrying this name, and
    the production comment said the opposite — the name, the comment and the
    assertion all disagreed in one diff. The name was right: an em dash set
    off by spaces is not something to light up.

    The bug underneath was the guard. `word.strip()` can never be falsy,
    because the buffer is built by skipping whitespace, so every
    punctuation-only token passed it.
    """
    words = _words_from_alignment(_alignment("a — b"))
    assert [w.word for w in words] == ["a", "b"]


def test_an_ellipsis_on_its_own_is_not_a_word_either():
    words = _words_from_alignment(_alignment("wait ... really"))
    assert [w.word for w in words] == ["wait", "really"]


def test_a_number_is_a_word():
    """`isalnum`, not `isalpha`: "4.2" and "2026" are spoken and highlightable."""
    words = _words_from_alignment(_alignment("paid 4.2 billion"))
    assert [w.word for w in words] == ["paid", "4.2", "billion"]


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
    # ElevenLabs comes back wrapped, because Google sits behind it for the
    # whole run rather than only at construction time.
    if isinstance(provider, FallbackTTSProvider):
        return "elevenlabs" if isinstance(provider._primary, _Eleven) else "google"
    return "google"


def test_a_typo_in_the_provider_name_is_logged_rather_than_silently_ignored(
    monkeypatch, caplog
):
    """The case the factory's own docstring warns about. `elvenlabs` fell
    through to Google with no log line at all — a briefing shipping in the
    wrong voice with no breadcrumb, which is precisely the failure the
    fallback logging exists to prevent."""
    import app.core.config as config

    monkeypatch.setattr(config, "get_settings", _settings(tts_provider="elvenlabs"))
    built = _build_with_stubs(monkeypatch)

    assert built == "google"
    assert any("elvenlabs" in r.getMessage() for r in caplog.records)
    assert any(r.levelname == "ERROR" for r in caplog.records)


def test_google_spelled_normally_is_not_logged_as_a_typo(monkeypatch, caplog):
    import app.core.config as config

    monkeypatch.setattr(config, "get_settings", _settings(tts_provider="google"))
    _build_with_stubs(monkeypatch)
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


# ── an outage mid-run, not just a missing key ─────────────────


class _Boom:
    """A provider that works until it doesn't."""

    voice_a = "el-a"
    voice_b = "el-b"

    def __init__(self, fail_from: int = 0) -> None:
        self.calls = 0
        self._fail_from = fail_from

    async def synthesize(self, text: str, voice_id: str) -> Speech:
        self.calls += 1
        if self.calls > self._fail_from:
            raise RuntimeError("elevenlabs is down")
        return Speech(audio=b"primary", words=(WordTiming("w", 0, 1),))


class _Backup:
    voice_a = "g-a"
    voice_b = "g-b"

    def __init__(self) -> None:
        self.voices_asked: list[str] = []

    async def synthesize(self, text: str, voice_id: str) -> Speech:
        self.voices_asked.append(voice_id)
        return Speech(audio=b"backup")


@pytest.mark.asyncio
async def test_an_outage_after_construction_switches_provider_instead_of_failing(
    caplog,
):
    """Catching a constructor error covers a MISSING KEY, not an outage.
    With only that, ElevenLabs going down at 04:00 meant every turn
    exhausted its retries, every clip came back None, and the run raised
    EmptyBriefingError — no briefing, which is what rule 7 exists to
    prevent."""
    backup = _Backup()
    p = FallbackTTSProvider(_Boom(fail_from=1), lambda: backup)

    first = await p.synthesize("hello", p.voice_a)
    second = await p.synthesize("there", p.voice_a)

    assert first.audio == b"primary"
    assert second.audio == b"backup"
    assert any(r.levelname == "ERROR" for r in caplog.records)


@pytest.mark.asyncio
async def test_the_fallback_is_asked_for_its_own_voice_not_the_primarys():
    """The caller reads `voice_b` off the wrapper, which reports ElevenLabs
    ids. Handing one of those to Google would fail every remaining turn for
    a second, sillier reason."""
    backup = _Backup()
    p = FallbackTTSProvider(_Boom(fail_from=0), lambda: backup)

    await p.synthesize("x", p.voice_b)

    assert backup.voices_asked == ["g-b"]


@pytest.mark.asyncio
async def test_it_does_not_keep_retrying_a_provider_that_already_failed():
    """Permanent for the run. Re-attempting a provider that just burned five
    retries wastes minutes of a run against the clock, and a briefing
    alternating voices turn by turn is worse than one that changes once."""
    boom = _Boom(fail_from=0)
    p = FallbackTTSProvider(boom, _Backup)

    for _ in range(4):
        await p.synthesize("x", p.voice_a)

    assert boom.calls == 1


@pytest.mark.asyncio
async def test_with_no_fallback_available_the_original_failure_is_raised():
    """An ElevenLabs-only deployment. The honest error is the one that
    actually happened, not the one from trying to build a provider the
    machine was never configured for."""
    def _no_fallback():
        raise RuntimeError("GOOGLE_TTS_CREDENTIALS is not set")

    p = FallbackTTSProvider(_Boom(fail_from=0), _no_fallback)

    with pytest.raises(RuntimeError, match="elevenlabs is down"):
        await p.synthesize("x", p.voice_a)


@pytest.mark.asyncio
async def test_concurrent_turns_do_not_build_the_fallback_more_than_once():
    """Turns are synthesised CONCURRENTLY — up to MAX_CONCURRENT_TTS of them
    — so several can fail before any has finished reacting. Without a lock
    the fallback was constructed once per failure and the log claimed
    several switches had happened."""
    import asyncio

    builds = 0

    def _build():
        nonlocal builds
        builds += 1
        return _Backup()

    p = FallbackTTSProvider(_Boom(fail_from=0), _build)
    await asyncio.gather(*(p.synthesize("x", p.voice_a) for _ in range(8)))

    assert builds == 1


@pytest.mark.asyncio
async def test_the_voice_never_flips_back_after_the_switch():
    """A primary call already in flight when the switch happens can still
    SUCCEED, and returning it would put one primary turn after the
    changeover — primary, Google, primary. One voice change is a degraded
    briefing; two is a broken one."""
    import asyncio

    class _SlowThenBroken:
        voice_a = "el-a"
        voice_b = "el-b"

        def __init__(self) -> None:
            self.calls = 0

        async def synthesize(self, text: str, voice_id: str) -> Speech:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("elevenlabs is down")
            # Started before the switch, lands after it.
            await asyncio.sleep(0.01)
            return Speech(audio=b"primary")

    p = FallbackTTSProvider(_SlowThenBroken(), _Backup)
    results = await asyncio.gather(
        p.synthesize("first", p.voice_a), p.synthesize("second", p.voice_a)
    )

    assert all(r.audio == b"backup" for r in results)


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


# ── what a 402 is allowed to claim ────────────────────────────


@pytest.mark.asyncio
async def test_a_paid_plan_402_names_the_voice(caplog, monkeypatch):
    """The one 402 that IS about the voice says so.

    A library voice on a free plan returns `paid_plan_required`, and that
    cost a whole briefing: the free tier refused host A, the run degraded
    to Google for that host alone, and shipped a two-man briefing while
    reporting success. The raw body names a code and no remedy.
    """
    import logging

    import httpx

    provider = _provider_answering(
        monkeypatch,
        status=402,
        body='{"detail":{"status":"paid_plan_required",'
        '"message":"Free users cannot use library voices via the API."}}',
    )

    with caplog.at_level(logging.ERROR), pytest.raises(httpx.HTTPStatusError):
        await provider.synthesize("hello", "voice-xyz")

    assert any("PREMADE" in r.getMessage() for r in caplog.records), (
        "a paid_plan_required 402 must say what to do about it"
    )
    assert any("voice-xyz" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_spent_quota_402_does_not_blame_the_voice(caplog, monkeypatch):
    """402 also means `insufficient_credits`, where the voice is blameless.

    Advice to go and pick a different voice sends the reader after a
    perfectly good one while synthesis stays unavailable for the real
    reason. The body is still logged; only the remedy is withheld.
    """
    import logging

    import httpx

    provider = _provider_answering(
        monkeypatch,
        status=402,
        body='{"detail":{"status":"insufficient_credits",'
        '"message":"You have run out of credits."}}',
    )

    with caplog.at_level(logging.ERROR), pytest.raises(httpx.HTTPStatusError):
        await provider.synthesize("hello", "voice-xyz")

    rendered = " ".join(r.getMessage() for r in caplog.records)
    assert "insufficient_credits" in rendered, "the body still goes to the log"
    assert "PREMADE" not in rendered, (
        "a spent quota is not a reason to change the voice"
    )
