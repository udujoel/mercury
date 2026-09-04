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


def build_tts_provider() -> TTSProvider:
    """The TTS provider named by TTS_PROVIDER, with Google as the fallback.

    A factory for the reason `build_embedding_provider` documents, and this
    one had the disease already: `tts_provider` has been in `Settings` since
    the ADR-006 groundwork and was read by NOTHING. Three entrypoints —
    `cli.py`, `api/briefing.py`, `scheduler/cron.py` — each constructed
    `GoogleTTSProvider()` directly, so setting TTS_PROVIDER=elevenlabs would
    have changed nothing at all, silently, on every path.

    Falls back rather than failing. Rule 6 keeps Google behind the protocol
    precisely so a paid tier going down at 4am costs the voice and not the
    briefing, and rule 7 says the run still ships. A fallback is logged
    loudly: a briefing that quietly sounds like the old one is how you find
    out weeks later.
    """
    import logging

    log = logging.getLogger(__name__)
    from app.core.config import get_settings

    choice = (get_settings().tts_provider or "google").strip().lower()

    if choice in {"elevenlabs", "eleven_labs", "11labs"}:
        try:
            from app.providers.elevenlabs_tts import ElevenLabsTTSProvider

            return ElevenLabsTTSProvider()
        except Exception as exc:
            log.error(
                "TTS_PROVIDER=elevenlabs but ElevenLabs is unavailable (%s) — "
                "falling back to Google. The briefing will ship in the old "
                "voice and WITHOUT word timings, so transcript highlighting "
                "degrades to whole-line.",
                exc,
            )

    from app.providers.google_tts import GoogleTTSProvider

    return GoogleTTSProvider()


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
