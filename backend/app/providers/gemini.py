"""
Live LLMProvider implementation backed by Gemini 2.5 Flash via a free
Google AI Studio key.

Structurally satisfies `app.core.providers.LLMProvider` (a runtime_checkable
Protocol) — no explicit inheritance required, just matching methods.

Get a key at https://aistudio.google.com/app/apikey, then put it in
backend/.env as:

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

# Free-tier AI Studio keys get 429 (rate limit) and occasionally 503
# (model overloaded) responses. Retry with exponential backoff rather than
# failing the whole generation run over a transient blip.
MAX_ATTEMPTS = 5
BASE_DELAY_S = 1.5


class GeminiProvider:
    """LLMProvider backed by google-generativeai / Gemini 2.5 Flash."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        key = api_key if api_key is not None else settings.gemini_api_key
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Get a free key at "
                "https://aistudio.google.com/app/apikey and put it in "
                "backend/.env as GEMINI_API_KEY=... "
                "(GeminiProvider will not run without it — never fake a live run.)"
            )

        import google.generativeai as genai

        genai.configure(api_key=key)
        self._genai = genai
        self._model_name = model or settings.llm_model

    def _build_model(self, system: str, json_mode: bool):
        generation_config = {}
        if json_mode:
            # The reliable way to get parseable JSON out of Gemini: ask the
            # SDK to constrain the response MIME type rather than hoping the
            # prompt's "return JSON only" instruction is obeyed. Even so,
            # keep robust parsing downstream — it can still wrap output in
            # markdown fences or add stray whitespace/prose on some calls.
            generation_config["response_mime_type"] = "application/json"

        return self._genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=system,
            generation_config=generation_config or None,
        )

    def _log_usage(self, response) -> None:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return
        logger.info(
            "gemini usage: prompt_tokens=%s candidates_tokens=%s total_tokens=%s",
            getattr(usage, "prompt_token_count", "?"),
            getattr(usage, "candidates_token_count", "?"),
            getattr(usage, "total_token_count", "?"),
        )

    def _sync_complete(self, system: str, prompt: str, temperature: float,
                        json_mode: bool) -> str:
        model = self._build_model(system, json_mode)
        response = model.generate_content(
            prompt,
            generation_config={"temperature": temperature},
        )
        self._log_usage(response)
        return response.text

    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float = 0.85,
        json_mode: bool = False,
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                # The google-generativeai SDK is synchronous. Run it in a
                # thread so this provider is genuinely async and never blocks
                # the event loop the rest of the pipeline runs on.
                return await asyncio.to_thread(
                    self._sync_complete, system, prompt, temperature, json_mode
                )
            except Exception as exc:  # noqa: BLE001 - broad: SDK raises varied types
                last_error = exc
                status = _status_code(exc)
                if status not in (429, 503) or attempt == MAX_ATTEMPTS:
                    raise
                delay = BASE_DELAY_S * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                logger.warning(
                    "gemini %s on attempt %d/%d, retrying in %.1fs",
                    status, attempt, MAX_ATTEMPTS, delay,
                )
                await asyncio.sleep(delay)
        raise last_error  # pragma: no cover - unreachable, loop always returns/raises

    async def stream(self, system: str, prompt: str, *, temperature: float = 0.85):
        """Yields token chunks. Used by the Phase 3 voice interrupt path
        where time-to-first-audio matters more than total latency."""

        def _sync_stream():
            model = self._build_model(system, json_mode=False)
            return model.generate_content(
                prompt,
                generation_config={"temperature": temperature},
                stream=True,
            )

        # generate_content(stream=True) still blocks synchronously while
        # iterating, so pull the whole iterator construction and each next()
        # call off the event loop via to_thread.
        response_iter = await asyncio.to_thread(_sync_stream)
        iterator = iter(response_iter)
        while True:
            chunk = await asyncio.to_thread(_next_or_none, iterator)
            if chunk is None:
                break
            if chunk.text:
                yield chunk.text


def _next_or_none(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return None


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
