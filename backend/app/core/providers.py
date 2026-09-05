"""
Provider protocols.

Every external AI service sits behind one of these. Free tiers get deprecated
and pricing changes, so swapping a provider must mean writing one new file, not
touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class WordTiming:
    """When one word is spoken, relative to the START OF ITS OWN CLIP.

    Relative, not absolute, because the provider synthesising a turn has no
    idea where that turn will land in the stitched file. `stitch` offsets
    them once, when it knows — which is also the only place that can stay
    correct when music is prepended in front of a turn (ROADMAP task 3).
    """

    word: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class Speech:
    """Audio for one turn, and the word timings if the provider knows them.

    `words` is empty rather than None for a provider that cannot produce
    them. Every consumer has to handle the empty case anyway — Google cannot
    supply timings without SSML mark injection, and it stays the fallback per
    rule 6 — so an empty tuple keeps that path on the same code as the full
    one instead of behind a null check.
    """

    audio: bytes
    words: tuple[WordTiming, ...] = field(default_factory=tuple)


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float = 0.85,
        json_mode: bool = False,
        response_schema: dict | None = None,
    ) -> str:
        """Single completion. Returns raw text.

        `response_schema` is an OpenAPI-subset JSON schema describing the
        shape the caller expects back. `json_mode` on its own only ASKS for
        JSON; a schema is what actually constrains the decoder, which is the
        difference between "usually parses" and "parses". Providers that
        cannot constrain decoding may ignore it — every caller still parses
        defensively regardless.
        """
        ...

    async def stream(self, system: str, prompt: str, *, temperature: float = 0.85):
        """Yields token chunks. Used for the voice interrupt path where
        time-to-first-audio matters more than anything else."""
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Batch embed. Must preserve input order."""
        ...


@runtime_checkable
class TTSProvider(Protocol):
    async def synthesize(self, text: str, voice_id: str) -> Speech:
        """Audio for one speaker turn, with word timings where available.

        Returning `Speech` rather than `bytes` is what makes word-level
        transcript highlighting possible at all (ROADMAP tasks 2 and 6). The
        manifest carries `start_ms` per TURN — a whole paragraph — and there
        is no way to get finer through a signature that only returns audio.
        """
        ...

    @property
    def voice_a(self) -> str: ...

    @property
    def voice_b(self) -> str: ...


ELEVENLABS_NAMES = {"elevenlabs", "eleven_labs", "eleven-labs", "11labs"}
GOOGLE_NAMES = {"google", "google_tts", "gcp", ""}


class FallbackTTSProvider:
    """A primary provider with a second one behind it, per turn.

    Construction-time fallback is not enough, and claiming otherwise was the
    bug: `build_tts_provider` catching a constructor error covers a missing
    key, not an outage. ElevenLabs going down at 04:00 AFTER a successful
    construction meant every turn exhausted its retries and raised, every
    clip came back None, and `synthesize_briefing` raised
    `EmptyBriefingError` — no briefing at all, which is exactly what rule 7
    exists to prevent.

    The switch is permanent for the run rather than per turn. Retrying a
    provider that has already failed its own five attempts wastes minutes of
    a run that is against the clock, and a briefing alternating between two
    voices turn by turn would be worse than one that changes voice once.

    It does change voice once, mid-briefing, and that is deliberately not
    hidden: an ERROR log names it. A briefing that is half one voice and half
    another is unpleasant and obviously wrong, which is the correct way for
    this to fail — silently shipping is the thing to avoid.
    """

    def __init__(self, primary: TTSProvider, build_fallback) -> None:
        import asyncio

        self._primary = primary
        self._build_fallback = build_fallback
        self._fallback: TTSProvider | None = None
        self._failed_over = False
        self._lock = asyncio.Lock()

    @property
    def voice_a(self) -> str:
        return self._primary.voice_a

    @property
    def voice_b(self) -> str:
        return self._primary.voice_b

    def _translated(self, voice_id: str) -> str:
        """The fallback's equivalent of a primary voice id.

        The caller picked `voice_a` or `voice_b` off THIS object, which
        reports the primary's ids. Passing an ElevenLabs voice id to Google
        would fail every remaining turn for a second, sillier reason.
        """
        assert self._fallback is not None
        if voice_id == self._primary.voice_b:
            return self._fallback.voice_b
        return self._fallback.voice_a

    async def _fail_over(self, exc: Exception) -> None:
        """Build the fallback and record the switch, once.

        Under the lock because turns are synthesised CONCURRENTLY — up to
        `MAX_CONCURRENT_TTS` of them — so several can fail before any of them
        has finished reacting. Without it the fallback provider was built
        several times and the log claimed several switches.
        """
        import logging

        log = logging.getLogger(__name__)

        async with self._lock:
            if self._failed_over:
                return
            try:
                self._fallback = self._build_fallback()
            except Exception as build_exc:
                log.error(
                    "%s failed (%s) and no fallback provider could be built "
                    "(%s) — the run has no way to produce audio.",
                    type(self._primary).__name__, exc, build_exc,
                )
                raise exc from None

            self._failed_over = True
            log.error(
                "%s failed mid-run (%s) — switching to %s for the REST of "
                "this briefing. It will change voice partway through, and "
                "turns after this point carry no word timings.",
                type(self._primary).__name__, exc,
                type(self._fallback).__name__,
            )

    async def synthesize(self, text: str, voice_id: str) -> Speech:
        if not self._failed_over:
            try:
                speech = await self._primary.synthesize(text, voice_id)
            except Exception as exc:  # noqa: BLE001 - any failure is a failover
                await self._fail_over(exc)
            else:
                # A call already in flight when the switch happened can still
                # succeed, and returning it would flip the voice BACK for one
                # turn somewhere in the middle — primary, Google, primary.
                # One voice change is a degraded briefing; two is a broken
                # one, so this clip is discarded and re-synthesised below.
                if not self._failed_over:
                    return speech

        assert self._fallback is not None
        return await self._fallback.synthesize(text, self._translated(voice_id))


def build_tts_provider() -> TTSProvider:
    """The TTS provider named by TTS_PROVIDER, with Google behind it.

    A factory for the reason `build_embedding_provider` documents, and this
    one had the disease already: `tts_provider` has been in `Settings` since
    the ADR-006 groundwork and was read by NOTHING. Three entrypoints —
    `cli.py`, `api/briefing.py`, `scheduler/cron.py` — each constructed
    `GoogleTTSProvider()` directly, so setting TTS_PROVIDER=elevenlabs would
    have changed nothing at all, silently, on every path.

    Rule 6 keeps Google behind the protocol precisely so a paid tier going
    down at 4am costs the voice and not the briefing, and rule 7 says the run
    still ships — so the fallback covers BOTH a provider that cannot be built
    and one that stops working mid-run. See `FallbackTTSProvider`.

    Every fallback is logged at ERROR: a briefing that quietly sounds like
    the old one is how you find out weeks later.
    """
    import logging

    log = logging.getLogger(__name__)
    from app.core.config import get_settings

    def _google() -> TTSProvider:
        from app.providers.google_tts import GoogleTTSProvider

        return GoogleTTSProvider()

    choice = (get_settings().tts_provider or "google").strip().lower()

    if choice in ELEVENLABS_NAMES:
        try:
            from app.providers.elevenlabs_tts import ElevenLabsTTSProvider

            return FallbackTTSProvider(ElevenLabsTTSProvider(), _google)
        except Exception as exc:
            log.error(
                "TTS_PROVIDER=elevenlabs but ElevenLabs could not be built "
                "(%s) — falling back to Google. The briefing will ship in the "
                "old voice and WITHOUT word timings, so transcript "
                "highlighting degrades to whole-line.",
                exc,
            )
    elif choice not in GOOGLE_NAMES:
        # A typo is the case this docstring warns about, so it must not be
        # the case that passes silently. "elvenlabs" used to land here and
        # get Google without a word in the log.
        log.error(
            "TTS_PROVIDER=%r is not a provider this build knows (expected one "
            "of %s) — using Google. If you meant ElevenLabs, this is a typo "
            "and the briefing is about to ship in the wrong voice.",
            choice, sorted(ELEVENLABS_NAMES | {"google"}),
        )

    return _google()


def build_embedding_provider() -> EmbeddingProvider | None:
    """The live embedding provider, or None when this machine has no key.

    None is a supported answer, not an error: `build_stories` degrades to
    unclustered singletons, so a keyless machine still ships a briefing —
    it just ships with the same story told twice.

    A factory rather than three copies because there were three copies, and
    the third entrypoint is the one that forgot. `app/cli.py` and the cron
    each built a provider; the API did not, so a briefing generated by
    tapping the greeting card had cross-source dedup silently switched off —
    the Phase 2 gate, off, on the one path a user actually touches.

    Imported lazily so a machine with no google-generativeai installed can
    still import the router and the CLI.
    """
    import logging

    try:
        from app.providers.gemini_embeddings import GeminiEmbeddingProvider

        return GeminiEmbeddingProvider()
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "no embedding provider (%s) — news will not be clustered and a "
            "story covered by several sources will be told more than once",
            exc,
        )
        return None
