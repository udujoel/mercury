"""
Provider protocols.

Every external AI service sits behind one of these. Free tiers get deprecated
and pricing changes, so swapping a provider must mean writing one new file, not
touching the pipeline.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float = 0.85,
        json_mode: bool = False,
    ) -> str:
        """Single completion. Returns raw text."""
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
    async def synthesize(self, text: str, voice_id: str) -> bytes:
        """Returns raw audio bytes for one speaker turn."""
        ...

    @property
    def voice_a(self) -> str: ...

    @property
    def voice_b(self) -> str: ...
