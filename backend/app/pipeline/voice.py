"""
The streaming voice-answer pipeline — Phase 3, "the Join flow".

The user taps the mic mid-briefing, asks a question, and one of the two
hosts (never a third assistant voice) answers, grounded in what was just
being discussed, then bridges back into the briefing. The whole feature
lives or dies on the timing budget in docs/phase3/voice_backend.md:
transcript -> first LLM token < 1200ms, first token -> first audio < 800ms.
Buffering the full answer before synthesising any of it blows that budget by
construction, so this module streams end to end:

    LLM tokens -> JSON field extraction -> sentence splitting -> per-sentence
    TTS -> per-sentence audio yielded to the caller

as they become available, not after the model finishes talking.

Uses `VOICE_ANSWER_PROMPT` from app.pipeline.prompts verbatim. It asks the
model to return `{"answer": "...", "bridge": "..."}` as JSON — a good shape
for a single completion, but awkward for streaming, since a naive consumer
would have to wait for the closing brace before it can start speaking. The
`_JSONFieldStreamer` below decodes each string field's text incrementally,
character-by-character, as the raw JSON text streams in, so sentence
splitting and synthesis can start on the "answer" field well before the
"bridge" field (or the closing brace) has even arrived.

CLAUDE.md rule 7 (graceful degradation), applied at two levels here:
  - a single sentence's TTS failing does not abort the answer: the rest of
    the sentences (and the bridge line) still get synthesised and yielded.
  - the LLM failing outright (bad key, network error, exhausted retries)
    yields nothing at all. There is no partial answer to salvage, and the
    caller (app/api/voice.py) resumes the briefing rather than hanging on a
    stream that will never produce audio.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.providers import LLMProvider, TTSProvider
from app.core.schemas import BriefingManifest, Segment, SourceRef, Turn
from app.pipeline.prompts import HOST_SYSTEM, VOICE_ANSWER_PROMPT
from app.pipeline.script_chain import (
    INJECTION_WARNING,
    SOURCE_FENCE_CLOSE,
    SOURCE_FENCE_OPEN,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Output type
# ──────────────────────────────────────────────────────────────


@dataclass
class VoiceChunk:
    """One unit of the streamed answer: either a synthesised sentence of the
    answer itself, the bridging line back into the briefing, or (only when
    nothing else was ever produced) a bare timing marker.

    `timing` is populated only on the LAST chunk this pipeline yields — the
    running budget instrumentation (llm_first_token_ms, first_sentence_ms,
    first_audio_ms, total_ms), for the caller to log or surface to the
    client. It is None on every earlier chunk.
    """

    speaker: str  # "A" | "B" — whichever host was talking at position_ms
    text: str
    audio: bytes
    kind: str  # "answer" | "bridge" | "done"
    index: int
    audio_failed: bool = False
    timing: dict[str, int | None] | None = None


# ──────────────────────────────────────────────────────────────
# Incremental JSON field extraction
#
# VOICE_ANSWER_PROMPT asks for `{"answer": "...", "bridge": "..."}`. Token
# chunks from LLMProvider.stream arrive at arbitrary boundaries — mid-key,
# mid-escape-sequence, anywhere — so this cannot just look for `"answer":`
# once per chunk. It is fed the raw text incrementally and tracks enough
# state to survive a boundary landing in the middle of anything.
# ──────────────────────────────────────────────────────────────

_JSON_FIELD_ORDER = ("answer", "bridge")

_JSON_ESCAPES = {
    '"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
    "n": "\n", "r": "\r", "t": "\t",
}


def _decode_json_string_prefix(raw: str, start: int) -> tuple[str, int | None]:
    """Decode as much of the JSON string starting at `raw[start]` (the first
    character AFTER the opening quote) as is fully available.

    Returns (decoded_text, end_index) where `end_index` is the index of the
    closing quote if it has arrived, or None if the string is still open
    (more chunks are expected). Stops before an incomplete trailing escape
    (a lone `\\` or a `\\u` without its full four hex digits yet) rather than
    guessing, so the next call — fed the same `start` plus more raw text —
    picks up exactly where this one left off.
    """
    out: list[str] = []
    i = start
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\":
            if i + 1 >= n:
                break  # escape started, next char not in yet
            nxt = raw[i + 1]
            if nxt == "u":
                if i + 6 > n:
                    break  # \uXXXX not fully arrived yet
                hex_digits = raw[i + 2 : i + 6]
                try:
                    out.append(chr(int(hex_digits, 16)))
                except ValueError:
                    out.append(nxt)
                i += 6
                continue
            out.append(_JSON_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        if ch == '"':
            return "".join(out), i
        out.append(ch)
        i += 1
    return "".join(out), None


class _JSONFieldStreamer:
    """Extracts the unescaped text of the "answer" then "bridge" string
    fields out of a streamed `{"answer": "...", "bridge": "..."}` payload,
    without waiting for the response to finish."""

    def __init__(self) -> None:
        self._raw = ""
        self._field_pos = 0
        self._string_start: int | None = None
        self._emitted = 0
        self._scan_pos = 0

    def feed(self, chunk: str) -> list[tuple[str, str, bool]]:
        """Feed a new raw chunk. Returns (field_name, new_text, completed)
        triples — zero, one, or (if a big chunk closes a field and opens the
        next) more than one. `completed` is True exactly once per field, on
        whichever call sees that field's closing quote arrive — even if that
        call carries no NEW text (the quote can arrive alone) — so the
        caller can react to "this field is now fully known" as its own
        event, distinct from "more text arrived"."""
        self._raw += chunk
        out: list[tuple[str, str, bool]] = []
        while self._field_pos < len(_JSON_FIELD_ORDER):
            field = _JSON_FIELD_ORDER[self._field_pos]

            if self._string_start is None:
                marker = f'"{field}"'
                idx = self._raw.find(marker, self._scan_pos)
                if idx == -1:
                    # The marker itself might be split across the chunk
                    # boundary; only skip past text we know can't contain
                    # the start of it.
                    self._scan_pos = max(0, len(self._raw) - len(marker))
                    return out
                colon = self._raw.find(":", idx + len(marker))
                if colon == -1:
                    return out
                quote = self._raw.find('"', colon)
                if quote == -1:
                    return out
                self._string_start = quote + 1
                self._scan_pos = self._string_start

            text, end = _decode_json_string_prefix(self._raw, self._string_start)
            new_text = text[self._emitted :]
            completed = end is not None
            if new_text or completed:
                out.append((field, new_text, completed))
                self._emitted += len(new_text)

            if end is None:
                return out

            self._field_pos += 1
            self._string_start = None
            self._emitted = 0
            self._scan_pos = end + 1

        return out


# ──────────────────────────────────────────────────────────────
# Sentence splitting
#
# Naturalness comes from the TTS model, not the transcript (CLAUDE.md rule
# 1) — but here the "transcript" is a live model answer, and we must decide
# where one synthesisable unit ends and the next begins as it streams in.
# A plain split on ". " breaks "Dr. Smith", "e.g. this", "3.14 percent" and
# "Wait... really?" into nonsense fragments, each mangled by TTS on its own.
# ──────────────────────────────────────────────────────────────

_ABBREVIATIONS = frozenset({
    "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "mt", "ave",
    "inc", "ltd", "co", "vs", "etc", "e.g", "i.e", "approx", "no",
    "u.s", "u.k", "a.m", "p.m", "vol",
})

# Sentence-ending punctuation (possibly repeated — "?!"), an optional
# closing quote/bracket, then whitespace. No lookahead here: whether a match
# actually counts as a boundary is decided in Python below, using two
# independent vetoes -- an ellipsis run and a known abbreviation both look at
# only what came BEFORE the punctuation (already fully in the buffer,
# regardless of streaming), and a lower-case character right after the
# whitespace (when one has arrived yet) both look at what's actually
# present. A trailing period/space with NOTHING after it yet (the buffer
# simply ends there) is treated as a genuine boundary rather than withheld —
# committing to it is what lets the pipeline start synthesising the instant
# a sentence is complete instead of waiting for the next one to begin.
_SENTENCE_BOUNDARY_RE = re.compile(
    r"(?P<term>[.!?]+)(?P<close>[\"'\)\]]*)(?P<space>\s+)"
)
_WORD_BEFORE_RE = re.compile(r"([A-Za-z]+(?:\.[A-Za-z]+)*)$")


def _is_abbreviation_before(buffer: str, term_start: int) -> bool:
    match = _WORD_BEFORE_RE.search(buffer[:term_start])
    if not match:
        return False
    word = match.group(1).lower()
    return word in _ABBREVIATIONS or len(word) == 1  # single-letter initials


def _is_ellipsis(term: str) -> bool:
    """A run of two or more plain dots ("...", "....") reads as a trailing-
    off pause, not a hard stop — it never ends a sentence by itself,
    regardless of what follows it. "?!" and "!!" are unaffected; only a
    dots-only run is excluded."""
    return len(term) >= 2 and set(term) == {"."}


def _looks_like_sentence_start(ch: str) -> bool:
    return ch.isupper() or ch.isdigit() or ch in "\"'(["


def split_complete_sentences(
    buffer: str, *, flush: bool = False
) -> tuple[list[str], str]:
    """Split `buffer` into complete sentences plus a trailing remainder that
    may still grow.

    A run of `.`/`!`/`?` followed by whitespace is a sentence boundary
    UNLESS any of:
      - it's an ellipsis ("...") — a pause, not a hard stop;
      - the word right before it is a known abbreviation ("Dr.", "e.g.",
        "i.e.", a single initial like "J.") — decimals like "3.14" are
        naturally unaffected since no whitespace ever follows that `.`;
      - a character IS already visible right after the whitespace and it's
        lower-case (a continuation, not a new sentence).
    A trailing "sentence. " with nothing after it YET is still treated as
    complete — see the comment above `_SENTENCE_BOUNDARY_RE`.

    With `flush=True` (the field is known to be finished — the model
    stopped, or the JSON string closed), any non-whitespace remainder is
    returned as one final sentence regardless of trailing punctuation, since
    nothing more is coming that could resolve it either way.
    """
    sentences: list[str] = []
    pos = 0
    for m in _SENTENCE_BOUNDARY_RE.finditer(buffer):
        term_start = m.start("term")
        term = m.group("term")
        if _is_ellipsis(term):
            continue
        if _is_abbreviation_before(buffer, term_start):
            continue
        end = m.end()
        following = buffer[end : end + 1]
        if following and not _looks_like_sentence_start(following):
            continue
        sentences.append(buffer[pos:end].strip())
        pos = end

    remainder = buffer[pos:]
    if flush and remainder.strip():
        sentences.append(remainder.strip())
        remainder = ""
    return sentences, remainder


# ──────────────────────────────────────────────────────────────
# Context selection — the turns "around" position_ms
# ──────────────────────────────────────────────────────────────

# How far back from position_ms counts as "what was just being discussed".
# Long enough to give the model real context, short enough not to dilute the
# prompt with the whole segment.
CONTEXT_WINDOW_MS = 45_000
MIN_CONTEXT_TURNS = 3


@dataclass
class _AnswerContext:
    speaker: str
    segment: Segment | None
    transcript_text: str
    sources_text: str


def _find_current_turn_index(turns: list[Turn], position_ms: int) -> int | None:
    """The turn actually playing at position_ms, or the closest one at/before
    it if position_ms lands in a gap (inter-turn silence) or past the end of
    a manifest whose last turn has no end_ms."""
    for i, turn in enumerate(turns):
        if turn.start_ms is None or turn.end_ms is None:
            continue
        if turn.start_ms <= position_ms < turn.end_ms:
            return i

    candidates = [
        i for i, t in enumerate(turns)
        if t.start_ms is not None and t.start_ms <= position_ms
    ]
    if candidates:
        return candidates[-1]
    return 0 if turns else None


def _speaker_label(speaker: str) -> str:
    settings = get_settings()
    return settings.host_a_name if speaker == "A" else settings.host_b_name


def _format_sources(sources: list[SourceRef]) -> str:
    if not sources:
        return "(no linked sources for this part of the briefing)"
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for ref in sources:
        key = (ref.title, ref.source_name)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {ref.title} ({ref.source_name}, {ref.source_type.value})")
    return "\n".join(lines)


def _fence_untrusted(text: str) -> str:
    """Fence third-party-controlled text (email subject lines, RSS titles)
    before it reaches the prompt — mirrors the fencing
    app.pipeline.script_chain._flatten_source_text applies when the script
    itself was written. The user's own spoken question is lower risk and is
    NOT fenced; the SOURCE data behind the segment is not the user's."""
    stripped = text.replace(SOURCE_FENCE_OPEN, "").replace(SOURCE_FENCE_CLOSE, "")
    return (
        f"{INJECTION_WARNING}\n\n{SOURCE_FENCE_OPEN}\n{stripped}\n"
        f"{SOURCE_FENCE_CLOSE}"
    )


def _build_context(
    manifest: BriefingManifest, position_ms: int
) -> _AnswerContext | None:
    """Build the "what was just being discussed" context VOICE_ANSWER_PROMPT
    needs: the turns actually around position_ms, and the SourceRefs
    attached to those specific turns (not the whole briefing) — this is what
    makes the answer feel like it's about the briefing rather than a generic
    reply. Returns None if the manifest has no turns to anchor on."""
    turns = manifest.turns
    idx = _find_current_turn_index(turns, position_ms)
    if idx is None:
        return None
    current = turns[idx]

    window_start_ms = max(position_ms - CONTEXT_WINDOW_MS, 0)
    selected_indices = [
        i for i, t in enumerate(turns[: idx + 1])
        if t.end_ms is not None and t.end_ms >= window_start_ms
    ]
    if len(selected_indices) < MIN_CONTEXT_TURNS:
        start = max(0, idx - MIN_CONTEXT_TURNS + 1)
        selected_indices = list(range(start, idx + 1))

    transcript_lines = [
        f"{_speaker_label(turns[i].speaker)}: {turns[i].text}"
        for i in selected_indices
    ]
    transcript_text = (
        "\n".join(transcript_lines) if transcript_lines else "(nothing yet)"
    )

    selected_set = set(selected_indices)
    sources = [ref for ref in manifest.sources if ref.turn_index in selected_set]

    return _AnswerContext(
        speaker=current.speaker,
        segment=current.segment,
        transcript_text=transcript_text,
        sources_text=_format_sources(sources),
    )


def _format_prior_questions(prior: list[str] | None) -> str:
    if not prior:
        return "(none yet this session)"
    return "\n".join(f"- {q}" for q in prior)


# ──────────────────────────────────────────────────────────────
# The pipeline
# ──────────────────────────────────────────────────────────────


def _elapsed_ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


async def _synthesize_chunk(
    tts: TTSProvider, voice_id: str, speaker: str, text: str, kind: str, index: int
) -> VoiceChunk:
    """Synthesise one sentence/bridge line. Never raises: a TTS failure here
    must not abort the rest of the answer (CLAUDE.md rule 7) — mirrors the
    per-turn degrade-and-log pattern in app.synthesis.stitch._synthesize_all."""
    try:
        audio = await tts.synthesize(text, voice_id)
    except Exception as exc:  # noqa: BLE001 - provider SDKs raise varied types
        logger.error(
            "voice: TTS failed for %s sentence %d (%d chars) [%s: %s]: %.60s",
            kind, index, len(text), type(exc).__name__, exc, text,
        )
        return VoiceChunk(
            speaker=speaker, text=text, audio=b"", kind=kind, index=index,
            audio_failed=True,
        )
    return VoiceChunk(speaker=speaker, text=text, audio=audio, kind=kind, index=index)


async def answer_question(
    *,
    llm: LLMProvider,
    tts: TTSProvider,
    question: str,
    manifest: BriefingManifest,
    position_ms: int,
    prior_questions: list[str] | None = None,
) -> AsyncIterator[VoiceChunk]:
    """Stream a host's answer to an interrupting question, sentence by
    sentence, synthesising and yielding each one as soon as it is complete
    rather than waiting for the whole answer.

    Degrades per CLAUDE.md rule 7:
      - a sentence whose TTS call fails is skipped (logged); the rest of the
        answer, and the bridge line, still play.
      - if the LLM fails outright, this yields NOTHING. There is no partial
        answer worth salvaging, and the caller resumes the briefing rather
        than waiting on a stream that was never going to produce audio.

    Instrumentation: logs (and attaches to the final chunk's `.timing`) the
    elapsed ms at first LLM token, first complete sentence, first ready
    audio chunk, and total — the numbers the Phase 3 timing budget is
    graded on.
    """
    t0 = time.monotonic()
    timing: dict[str, int | None] = {
        "llm_first_token_ms": None,
        "first_sentence_ms": None,
        "first_audio_ms": None,
        "total_ms": None,
    }

    context = _build_context(manifest, position_ms)
    if context is None:
        logger.error(
            "voice: manifest %s has no turns to anchor position_ms=%d on; "
            "cannot answer", manifest.briefing_id, position_ms,
        )
        return

    settings = get_settings()
    host_name = (
        settings.host_a_name if context.speaker == "A" else settings.host_b_name
    )
    voice_id = tts.voice_a if context.speaker == "A" else tts.voice_b

    prompt = VOICE_ANSWER_PROMPT.format(
        user_name=settings.user_name,
        host_name=host_name,
        position_ms=position_ms,
        recent_transcript=context.transcript_text,
        segment_sources=_fence_untrusted(context.sources_text),
        prior_questions=_format_prior_questions(prior_questions),
        question=question,
    )

    parser = _JSONFieldStreamer()
    answer_buffer = ""
    bridge_buffer = ""
    answer_index = 0
    answer_field_done = False
    bridge_field_done = False
    got_first_token = False
    # One-chunk lookahead: hold the most recently built chunk so the timing
    # dict can be attached to whichever one turns out to be last, without
    # knowing in advance which one that will be. Every chunk still gets
    # released (yielded) the moment the NEXT unit is ready — see below — so
    # this never holds a sentence back waiting for the whole answer; it only
    # ever holds back the single most-recent one.
    pending: VoiceChunk | None = None

    def _mark_first_sentence() -> None:
        if timing["first_sentence_ms"] is None:
            timing["first_sentence_ms"] = _elapsed_ms(t0)
            logger.info(
                "voice: first sentence complete at %dms", timing["first_sentence_ms"]
            )

    def _mark_first_audio() -> None:
        if timing["first_audio_ms"] is None:
            timing["first_audio_ms"] = _elapsed_ms(t0)
            logger.info(
                "voice: first audio chunk ready at %dms", timing["first_audio_ms"]
            )

    def _offer(chunk: VoiceChunk) -> VoiceChunk | None:
        """Hold `chunk` as the new pending one and return whichever chunk
        was pending before it (the caller should yield that, if not None).
        A no-audio chunk (TTS failed for it) is dropped without disturbing
        `pending` at all — rule 7: skip it and keep going."""
        nonlocal pending
        if not chunk.audio:
            return None
        _mark_first_audio()
        released, pending = pending, chunk
        return released

    try:
        async for raw_chunk in llm.stream(HOST_SYSTEM, prompt, temperature=0.7):
            if not raw_chunk:
                continue
            if not got_first_token:
                got_first_token = True
                timing["llm_first_token_ms"] = _elapsed_ms(t0)
                logger.info(
                    "voice: first LLM token at %dms", timing["llm_first_token_ms"]
                )

            for field, text, completed in parser.feed(raw_chunk):
                if field == "answer":
                    answer_buffer += text
                    sentences, answer_buffer = split_complete_sentences(
                        answer_buffer, flush=completed
                    )
                    for sentence in sentences:
                        _mark_first_sentence()
                        chunk = await _synthesize_chunk(
                            tts, voice_id, context.speaker, sentence, "answer",
                            answer_index,
                        )
                        answer_index += 1
                        # TTS failure: already logged inside _synthesize_chunk;
                        # _offer() returns None for an empty-audio chunk, so a
                        # failed sentence is skipped and the rest keeps going
                        # — rule 7.
                        released = _offer(chunk)
                        if released is not None:
                            yield released
                    if completed:
                        answer_field_done = True
                elif field == "bridge":
                    # The bridge line is a single short sentence by design
                    # (VOICE_ANSWER_PROMPT asks for one) — buffered whole and
                    # synthesised the moment the field closes, not
                    # sentence-split. Synthesising here — still inside the
                    # `async for raw_chunk` loop, before the LLM stream has
                    # necessarily finished producing its last few tokens
                    # (the closing brace, if nothing else) — is what lets the
                    # last answer sentence's audio be released without
                    # waiting for the whole response to end.
                    bridge_buffer += text
                    if completed:
                        bridge_field_done = True
                        bridge_text = bridge_buffer.strip()
                        if bridge_text:
                            chunk = await _synthesize_chunk(
                                tts, voice_id, context.speaker, bridge_text,
                                "bridge", 0,
                            )
                            released = _offer(chunk)
                            if released is not None:
                                yield released
    except Exception as exc:  # noqa: BLE001 - provider SDKs raise varied types
        logger.error(
            "voice: LLM streaming failed outright, yielding nothing further: %r",
            exc,
        )
        timing["total_ms"] = _elapsed_ms(t0)
        logger.info(
            "voice timing (failed, briefing %s): %s", manifest.briefing_id, timing
        )
        if pending is not None:
            # Genuine partial progress had already been made (and
            # synthesised) before the failure -- surface it rather than
            # discarding good audio. "Yield nothing" covers the case this
            # except block exists for: the LLM fails before ANY of that
            # happens, in which case pending is still None here.
            pending.timing = timing
            yield pending
        return

    # The stream ended without the corresponding field ever reporting
    # `completed` (a truncated response, most likely) -- flush whatever was
    # buffered as a best-effort finish rather than dropping it silently.
    if not answer_field_done and answer_buffer.strip():
        sentences, answer_buffer = split_complete_sentences(answer_buffer, flush=True)
        for sentence in sentences:
            _mark_first_sentence()
            chunk = await _synthesize_chunk(
                tts, voice_id, context.speaker, sentence, "answer", answer_index
            )
            answer_index += 1
            released = _offer(chunk)
            if released is not None:
                yield released

    if not bridge_field_done and bridge_buffer.strip():
        chunk = await _synthesize_chunk(
            tts, voice_id, context.speaker, bridge_buffer.strip(), "bridge", 0
        )
        released = _offer(chunk)
        if released is not None:
            yield released

    timing["total_ms"] = _elapsed_ms(t0)
    logger.info("voice timing (briefing %s): %s", manifest.briefing_id, timing)

    if pending is not None:
        pending.timing = timing
        yield pending
    elif got_first_token:
        # The LLM ran and produced text, but every synthesis call failed —
        # still worth telling the caller how long it took and that nothing
        # played, rather than silently closing with zero frames (which is
        # reserved for "the LLM itself never responded").
        yield VoiceChunk(
            speaker=context.speaker, text="", audio=b"", kind="done", index=0,
            timing=timing,
        )
