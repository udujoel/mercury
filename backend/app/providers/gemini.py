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
from app.core.providers import ContentBlocked

logger = logging.getLogger(__name__)

# Free-tier AI Studio keys get 429 (rate limit) and occasionally 503
# (model overloaded) responses. Retry with exponential backoff rather than
# failing the whole generation run over a transient blip.
MAX_ATTEMPTS = 5
BASE_DELAY_S = 1.5

# The four adjustable harm categories, turned down as far as the API allows.
#
# Mercury reads the news to an adult who asked for it. A wire report of a
# strike on a train is DANGEROUS_CONTENT to a filter tuned for a general
# audience, and a briefing that silently omits the day's largest story
# because of it is not the product. So the categories that CAN be turned
# down are turned down.
#
# What this does not do — and the reason it is not the fix on its own — is
# stop `PROHIBITED_CONTENT`. That is a separate `BlockReason` with no
# corresponding `HarmCategory`, so no threshold here reaches it; verified
# against the installed SDK, whose `BlockReason` enum lists SAFETY, OTHER,
# BLOCKLIST and PROHIBITED_CONTENT while `HarmCategory` lists only the four
# below. The 2026-09-15 failure was PROHIBITED_CONTENT. This narrows the
# opening; `ContentBlocked` plus the caller's degradation is what survives
# what gets through it.
_SAFETY_SETTINGS = {
    "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE",
    "HARM_CATEGORY_HATE_SPEECH": "BLOCK_NONE",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_NONE",
    "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_NONE",
}


def _name_of(value) -> str:
    """The enum's name, or its repr for a plain int the SDK did not map."""
    return getattr(value, "name", None) or str(value)


def _text_of(response) -> str:
    """`response.text`, but with a legible error when there is no text.

    The SDK's accessor raises `ValueError: Invalid operation: The
    `response.parts` quick accessor requires a single candidate...` — a
    message about an accessor, for a condition that has nothing to do with
    accessors. It cost a morning partly because nothing about it reads as
    "the model refused".

    Two distinct refusals are checked, because they need different
    recoveries. `prompt_feedback.block_reason` means the SOURCE material was
    refused before a single token was generated. A candidate `finish_reason`
    outside STOP/MAX_TOKENS means the model began answering and stopped
    itself partway.
    """
    feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(feedback, "block_reason", None)
    # 0 is BLOCK_REASON_UNSPECIFIED — "no block", not a falsy accident.
    if block_reason:
        raise ContentBlocked(_name_of(block_reason), stage="prompt")

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        # No candidates and no stated reason. Rare, and still a refusal from
        # the caller's point of view: there is no text and asking again with
        # the same input is not indicated.
        raise ContentBlocked("no candidates returned", stage="prompt")

    finish = getattr(candidates[0], "finish_reason", None)
    if finish is not None and _name_of(finish) not in (
        "STOP", "MAX_TOKENS", "FINISH_REASON_UNSPECIFIED", "0", "1", "2",
    ):
        raise ContentBlocked(_name_of(finish), stage="response")

    return response.text


def _generation_config(
    temperature: float, json_mode: bool, response_schema: dict | None
) -> dict:
    """Build the single generation config a request uses.

    There is exactly one of these per call. It goes to GenerativeModel at
    construction and generate_content() is passed none, so there is no
    second config that could shadow this one.

    On the 2026-08-27 outage this was suspected of dropping
    response_mime_type — a model-level setting plus a per-call
    {"temperature": ...} looks like it would replace it. It does not:
    GenerativeModel._prepare_request does `merged_gc =
    self._generation_config.copy(); merged_gc.update(generation_config)`,
    so the MIME type was reaching the API all along. Merging is the real
    behaviour but it is undocumented, and keeping two settings alive across
    a dict.update() nobody owns is a bet with no upside. One config, one
    place, pinned by a test.

    The genuine gap was response_schema. `response_mime_type` alone only
    ASKS the model for JSON; the schema is what constrains the decoder.
    Without one the model is free to emit a stray unescaped quote inside a
    string value, which is exactly what it did.
    """
    config: dict = {"temperature": temperature}
    if json_mode:
        config["response_mime_type"] = "application/json"
        if response_schema is not None:
            config["response_schema"] = response_schema
    return config


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

    def _build_model(self, system: str, generation_config: dict):
        return self._genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=system,
            generation_config=generation_config,
            safety_settings=_SAFETY_SETTINGS,
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
                        json_mode: bool, response_schema: dict | None) -> str:
        # One config, built once, handed to the model at construction, with
        # nothing passed per-call. generate_content() takes no
        # generation_config here on purpose — see _generation_config below.
        model = self._build_model(
            system, _generation_config(temperature, json_mode, response_schema)
        )
        response = model.generate_content(prompt)
        self._log_usage(response)
        return _text_of(response)

    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float = 0.85,
        json_mode: bool = False,
        response_schema: dict | None = None,
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                # The google-generativeai SDK is synchronous. Run it in a
                # thread so this provider is genuinely async and never blocks
                # the event loop the rest of the pipeline runs on.
                return await asyncio.to_thread(
                    self._sync_complete, system, prompt, temperature,
                    json_mode, response_schema,
                )
            except ContentBlocked:
                # Explicit, and above the broad clause on purpose. The filter
                # is deterministic on content, so the backoff below would
                # spend five attempts and ~7s to be refused five times. It
                # also must not be reclassified as a transport error by a
                # future edit to `_status_code`.
                raise
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
            model = self._build_model(
                system,
                _generation_config(temperature, json_mode=False,
                                    response_schema=None),
            )
            return model.generate_content(prompt, stream=True)

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
