"""
Live EmbeddingProvider implementation backed by Gemini's `text-embedding-004`
via google-generativeai.

Structurally satisfies `app.core.providers.EmbeddingProvider` (a
runtime_checkable Protocol) — no explicit inheritance required, just matching
methods.

Uses the same key as `app/providers/gemini.py`:

    GEMINI_API_KEY=your-key-here

Never invent or hardcode a key. If GEMINI_API_KEY is unset, __init__ raises
immediately with that instruction rather than making a doomed network call.
"""

from __future__ import annotations

import asyncio
import logging
import random

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Free-tier AI Studio keys get 429 (rate limit) and occasionally 503 (model
# overloaded) responses. Retry with exponential backoff rather than failing
# the whole clustering pass over a transient blip.
MAX_ATTEMPTS = 5
BASE_DELAY_S = 1.5

# The embed_content API accepts a batch of texts per call, but very large
# batches are more likely to hit request-size limits and make a single
# retryable failure more expensive. Chunk conservatively.
BATCH_SIZE = 50


class GeminiEmbeddingProvider:
    """EmbeddingProvider backed by google-generativeai / text-embedding-004."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        key = api_key if api_key is not None else settings.gemini_api_key
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Get a free key at "
                "https://aistudio.google.com/app/apikey and put it in "
                "backend/.env as GEMINI_API_KEY=... "
                "(GeminiEmbeddingProvider will not run without it — never "
                "fake a live run.)"
            )

        import google.generativeai as genai

        genai.configure(api_key=key)
        self._genai = genai
        self._model_name = model or settings.embedding_model

    def _sync_embed_batch(self, texts: list[str]) -> list[list[float]]:
        model_name = self._model_name
        if not model_name.startswith("models/"):
            model_name = f"models/{model_name}"
        result = self._genai.embed_content(model=model_name, content=texts)
        # embed_content returns {"embedding": [[...], [...], ...]} for a
        # batch (list) content argument, and {"embedding": [...]} for a
        # single string. We always pass a list, so normalise defensively.
        embeddings = result["embedding"]
        if embeddings and isinstance(embeddings[0], float):
            embeddings = [embeddings]
        return list(embeddings)

    async def _embed_chunk_with_retry(self, chunk: list[str]) -> list[list[float]]:
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                # The google-generativeai SDK is synchronous. Run it in a
                # thread so this provider is genuinely async and never blocks
                # the event loop the rest of the pipeline runs on.
                return await asyncio.to_thread(self._sync_embed_batch, chunk)
            except Exception as exc:  # noqa: BLE001 - SDK raises varied types
                last_error = exc
                status = _status_code(exc)
                if status not in (429, 503) or attempt == MAX_ATTEMPTS:
                    raise
                delay = BASE_DELAY_S * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                logger.warning(
                    "gemini embeddings %s on attempt %d/%d, retrying in %.1fs",
                    status,
                    attempt,
                    MAX_ATTEMPTS,
                    delay,
                )
                await asyncio.sleep(delay)
        raise last_error  # pragma: no cover - unreachable, loop always returns/raises

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Batch embed. Preserves input order exactly.

        The protocol promises order preservation and clustering silently
        produces garbage if it drifts, with no error anywhere — so texts are
        embedded in fixed-size chunks, in order, and results are
        concatenated in the same order, never reordered or deduplicated.
        """
        if not texts:
            return []

        results: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            chunk = texts[start : start + BATCH_SIZE]
            chunk_embeddings = await self._embed_chunk_with_retry(chunk)
            if len(chunk_embeddings) != len(chunk):
                raise RuntimeError(
                    f"gemini embed_content returned {len(chunk_embeddings)} "
                    f"embeddings for {len(chunk)} input texts — refusing to "
                    "guess an order mapping."
                )
            results.extend(chunk_embeddings)
        return results


def _status_code(exc: Exception) -> int | None:
    """Best-effort extraction of an HTTP-ish status code from whatever the
    SDK raised, since google-generativeai wraps google.api_core exceptions
    that expose `.code` inconsistently across versions."""
    for attr in ("code", "status_code", "grpc_status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    name = type(exc).__name__
    if "ResourceExhausted" in name:
        return 429
    if "ServiceUnavailable" in name:
        return 503
    return None
