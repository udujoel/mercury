"""
Provider protocols.

Every external AI service sits behind one of these. Free tiers get deprecated
and pricing changes, so swapping a provider must mean writing one new file, not
touching the pipeline.
"""

from __future__ import annotations

import logging
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


class ContentBlocked(RuntimeError):
    """The model refused on content grounds, and asking again will not help.

    Distinct from a transport error on purpose. A 429 or a 503 says *try
    later*; this says *not this text*, and the filter is deterministic on
    content — so the only recovery that can work is to change the input.
    Retrying an identical blocked prompt burns quota to receive the identical
    refusal, which is what a generic `except Exception: retry` would do.

    Named here rather than in `gemini.py` because it is a fact about the
    LLMProvider CONTRACT, not about one vendor: every hosted model has a
    filter, and a caller that degrades gracefully must be able to catch this
    without importing a provider.

    2026-09-15 is why it exists. Gemini refused a draft prompt with
    `block_reason: PROHIBITED_CONTENT`, the SDK's `.text` accessor raised a
    bare `ValueError` about a "quick accessor", nothing recognised it, and
    the morning's briefing was lost to a news story.
    """

    # The reasons where asking again with the same input gets the same
    # answer, so these skip the backoff and go straight to the caller's
    # degradation.
    #
    # The first four are a POLICY decision about the text itself: the filter
    # read the input and said no, and it will say no again.
    #
    # MAX_TOKENS is here for a different reason and was originally left out
    # of this set, described as "a bad roll rather than a verdict". Review
    # of #53 showed what that costs. MAX_TOKENS means the answer did not fit
    # the output window, which is a property of how much was ASKED FOR — so
    # an identical prompt asks for the same amount again. The retry is not
    # merely unlikely to help; it is five attempts at the largest prompt the
    # pipeline sends, with 1.5+3+6+12s of backoff, against a 90-second
    # budget. Spending a quarter of the morning to re-lose the same segment
    # is the opposite of rule 7.
    #
    # Everything else that yields no text — STOP with an empty part list,
    # RECITATION, a bare OTHER — stays retryable. RECITATION in particular
    # means the model was echoing source wording, which a re-draft at a
    # different temperature routinely clears.
    DETERMINISTIC = frozenset(
        {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "MAX_TOKENS"}
    )

    def __init__(self, reason: str, *, stage: str) -> None:
        # `stage` says WHICH half was refused — "prompt" means the filter
        # rejected the source material before generating, "response" means it
        # stopped partway through its own output. The recovery differs: the
        # first is fixed by sending less, the second sometimes by asking
        # again.
        super().__init__(f"model refused the {stage}: {reason}")
        self.reason = reason
        self.stage = stage

    @property
    def worth_retrying(self) -> bool:
        """Whether an identical second attempt could plausibly succeed.

        A blocked PROMPT is always final — the filter read the input and
        said no. An empty RESPONSE may not be: the model simply produced
        nothing this time.
        """
        return self.stage != "prompt" and self.reason not in self.DETERMINISTIC


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


@dataclass(frozen=True)
class ResearchSource:
    """One source a research answer drew on.

    `title` is the publisher as the provider reports it, usually a bare
    domain. It is shown to the user, so it must stay human-readable — a row
    reading "reuters.com" is a source; a row reading a UUID is not.
    """

    title: str
    url: str


@dataclass(frozen=True)
class Findings:
    """What research turned up about one subject.

    Empty is a legitimate, common answer: the subject may be too local, too
    recent, or simply not covered anywhere else. Rule 7 — the briefing ships
    either way.
    """

    subject: str
    summary: str = ""
    sources: list[ResearchSource] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        """Both halves or neither.

        A summary with no sources is exactly what rule 3 forbids, and it is
        WORSE on a page than in audio because it looks authoritative. So an
        ungrounded answer is discarded rather than shown unattributed.
        """
        return bool(self.summary) and bool(self.sources)


@runtime_checkable
class ResearchProvider(Protocol):
    async def research(self, subject: str, *, timeout: float = 30.0) -> Findings:
        """What is independently established about this subject.

        Takes a SUBJECT — a short noun phrase the enrichment pass extracted —
        and never an email body, so rule 5 holds structurally: no
        implementation of this protocol can be handed one.

        Must never raise. Every failure returns empty `Findings`, because a
        briefing that dies because the research step died is a worse product
        than a briefing without research (rule 7), and putting that
        guarantee in the protocol means callers need one path rather than
        two.
        """
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

    **`probe()` exists so that switch does not usually happen mid-briefing.**
    The original reasoning here was that a briefing changing voice partway is
    "unpleasant and obviously wrong, which is the correct way for this to
    fail" — sound when the failure is a rare outage, and wrong once it became
    the normal case. ElevenLabs answers `402 paid_plan_required` for library
    voices and `401 detected_unusual_activity` from CI's datacenter IPs, so
    every morning shipped a briefing in THREE voices: two ElevenLabs hosts
    for the first turns, then a Google one for the rest. The listener heard a
    stranger take over, and the transcript stopped tracking the words because
    only the ElevenLabs turns carry timings.

    A loud failure that fires daily is not a signal, it is the product. One
    short call before the run decides the provider once, so a briefing has
    exactly two voices and either all of its word timings or none.

    A genuine mid-run outage still switches, still logs at ERROR, and still
    ships — which is what rule 7 asks for and what the probe cannot cover.
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

    async def probe(self) -> None:
        """Decide the provider ONCE, before any turn is synthesised.

        BOTH voices, because a briefing has two hosts and either can be the
        broken one. Probing only `voice_a` left the exact bug this method
        was added to remove: when host A is fine and host B is a library
        voice the plan cannot speak, the probe passes, the first A turns are
        synthesised by the primary, and the first B turn fails over — a
        briefing in three voices with word timings on only some of it.

        Two short calls rather than one is the price of that, and it is two
        calls of two words against a run that makes forty.

        Deliberately not fatal. `_fail_over` re-raises when the fallback
        itself cannot be built, and letting that propagate from HERE would
        turn a preflight into the thing that ends a run which used to
        degrade and ship (rule 7). If neither provider can speak, the
        failure belongs to the turn that discovers it, with all the context
        that carries — not to a probe.
        """
        if self._failed_over:
            return
        for voice in (self._primary.voice_a, self._primary.voice_b):
            try:
                await self._primary.synthesize("Good morning.", voice)
            except Exception as exc:  # noqa: BLE001 - any failure is a failover
                try:
                    await self._fail_over(exc)
                except Exception:  # noqa: BLE001 - preflight is never fatal
                    logging.getLogger(__name__).exception(
                        "preflight could not switch providers; leaving the "
                        "decision to the first turn that fails"
                    )
                return

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
