"""
Script generation chain — Phase 2: outline -> draft -> critique -> revise
(four passes).

Uses OUTLINE_PROMPT / DRAFT_PROMPT / CRITIQUE_PROMPT / REVISE_PROMPT /
HOST_SYSTEM from `app.pipeline.prompts` verbatim. The JSON-parsing helper is
lifted from `scripts/spike_04_llm.py` (`parse_json_response`) rather than
reinvented — LLMs routinely wrap JSON in fences and prose, and that helper
is already proven against those shapes.

CLAUDE.md rule 7: graceful degradation. The critique and revise passes are
pure upside when they work and must never take down a briefing when they
don't:
  - if the critique response fails to parse, log it and return the draft
    unrevised.
  - if the revision response fails to parse, or validates to zero turns, or
    drops segment coverage, log it and return the draft unrevised.
  - if the revised script scores worse on the quality gates (app.pipeline.
    quality) than the draft did, keep the draft and log the deltas — a
    revision pass is not trusted blindly just because it ran.
A worse script beats no briefing; a validated draft always beats a broken
"revision" of it.

CLAUDE.md rule 5: raw email bodies are ephemeral. This module reads
`Item.body` only to format the source-data block sent to the LLM for this
one generation call; nothing here persists a body anywhere.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from app.core.providers import LLMProvider
from app.core.schemas import (
    LENGTH_BUDGETS,
    BriefingLength,
    CalendarEvent,
    Item,
    Segment,
    SourceRef,
    SourceType,
    Turn,
    Weather,
)
from app.pipeline.prompts import (
    CRITIQUE_PROMPT,
    DRAFT_PROMPT,
    HOST_SYSTEM,
    OUTLINE_PROMPT,
    REVISE_PROMPT,
)
from app.pipeline.quality import run_all_gates

logger = logging.getLogger(__name__)

_VALID_SEGMENTS = {s.value for s in Segment}
_VALID_SPEAKERS = {"A", "B"}


# ──────────────────────────────────────────────────────────────
# Robust JSON parsing — lifted from scripts/spike_04_llm.py.
# ──────────────────────────────────────────────────────────────


class JSONParseError(ValueError):
    def __init__(self, message: str, raw_text: str) -> None:
        super().__init__(f"{message}\n\n--- raw response ---\n{raw_text}\n---")
        self.raw_text = raw_text


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


def parse_json_response(text: str) -> Any:
    """Parse a JSON object or array out of raw LLM text.

    LLMs routinely wrap JSON in ```json ... ``` fences, add trailing or
    leading prose, or use smart quotes. This strips fences, normalises
    quotes, and extracts the outermost JSON object or array by bracket
    matching before falling back to a plain `json.loads`. Raises
    JSONParseError with the raw text on failure.
    """
    if text is None:
        raise JSONParseError("response was None", "")

    cleaned = text.strip()

    fence_match = _FENCE_RE.search(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    cleaned = cleaned.translate(_SMART_QUOTES)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    extracted = _extract_outermost_json(cleaned)
    if extracted is not None:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError as exc:
            raise JSONParseError(
                f"extracted candidate still failed to parse: {exc}", text
            ) from exc

    raise JSONParseError("no JSON object or array found in response", text)


def _extract_outermost_json(text: str) -> str | None:
    start = None
    open_ch = close_ch = ""
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            open_ch = ch
            close_ch = "}" if ch == "{" else "]"
            break
    if start is None:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ──────────────────────────────────────────────────────────────
# Formatting source data for the prompts
# ──────────────────────────────────────────────────────────────


def _format_weather(weather: Weather | None) -> str:
    if weather is None:
        return "(unavailable — weather source failed, skip this segment)"
    return (
        f"{weather.condition}, currently {weather.temperature_c:.0f}°C, "
        f"high {weather.high_c:.0f}°C / low {weather.low_c:.0f}°C "
        f"in {weather.location_name}"
    )


def _format_calendar(
    events: list[CalendarEvent],
    background: list[CalendarEvent] | None = None,
) -> str:
    lines = []
    if events:
        for ev in events:
            lines.append(
                f"- {ev.title} ({ev.start.isoformat()} to {ev.end.isoformat()}) "
                f"@ {ev.location or 'no location'}: {ev.description or ''}"
            )
    else:
        lines.append("(no scheduled events)")

    if background:
        # Ranked down, not dropped. These are all-day entries from subscribed
        # calendars — holidays, week numbers, religious dates. The PRD asks
        # for all calendars, but walking them chronologically buries the real
        # appointments, so they are handed to the model explicitly labelled
        # as ambient with an instruction about how much airtime they get.
        titles = ", ".join(ev.title for ev in background)
        lines.append("")
        lines.append(
            "BACKGROUND (ambient observances and all-day entries from "
            "subscribed calendars — do NOT walk these chronologically and do "
            "NOT give them their own turns; mention them collectively in at "
            "most one short clause, or skip them entirely if the day is "
            f"otherwise busy): {titles}"
        )
    return "\n".join(lines)


def _format_emails(emails: list[Item]) -> str:
    if not emails:
        return "(no emails)"
    lines = []
    for em in emails:
        lines.append(
            f"- From {em.source_name} <{em.author}> [{em.timestamp.isoformat()}]: "
            f"{em.title}\n  {em.body}"
        )
    return "\n".join(lines)


def _flatten_source_text(
    emails: list[Item], events: list[CalendarEvent], weather: Weather | None
) -> str:
    parts = [_format_weather(weather)]
    parts.extend(
        f"{ev.title} {ev.start.isoformat()} {ev.location or ''} {ev.description or ''}"
        for ev in events
    )
    parts.extend(f"{em.title} {em.author} {em.body}" for em in emails)
    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────
# Turn validation
# ──────────────────────────────────────────────────────────────


def _validate_turn(raw_turn: Any, index: int) -> Turn | None:
    """Validate one raw turn dict into a `Turn`. Returns None (and logs) on
    any malformed turn rather than raising, so one bad turn from the LLM
    never fails the whole generation run."""
    if not isinstance(raw_turn, dict):
        logger.warning("skipping turn %d: not an object (%r)", index, raw_turn)
        return None

    speaker = raw_turn.get("speaker")
    segment = raw_turn.get("segment")
    text = raw_turn.get("text")

    if speaker not in _VALID_SPEAKERS:
        logger.warning("skipping turn %d: invalid speaker %r", index, speaker)
        return None
    if segment not in _VALID_SEGMENTS:
        logger.warning("skipping turn %d: invalid segment %r", index, segment)
        return None
    if not isinstance(text, str) or not text.strip():
        logger.warning("skipping turn %d: empty or non-string text", index)
        return None

    try:
        return Turn(speaker=speaker, segment=Segment(segment), text=text.strip())
    except Exception as exc:  # pydantic validation failure
        logger.warning("skipping turn %d: %s", index, exc)
        return None


def _parse_draft_turns(raw: Any) -> list[Turn]:
    if not isinstance(raw, list):
        raise JSONParseError(f"expected a JSON array of turns, got {type(raw)}", "")

    turns: list[Turn] = []
    for i, raw_turn in enumerate(raw):
        turn = _validate_turn(raw_turn, i)
        if turn is not None:
            turns.append(turn)
    return turns


_CANONICAL_SEGMENT_ORDER = list(Segment)


def _has_valid_segment_coverage_and_order(turns: list[Turn]) -> bool:
    """Every canonical segment must appear, and segments must not
    interleave or repeat out of order. Same rule the Phase 0 spike enforces
    (`spike_04_llm.validate_segment_coverage`), reimplemented here as a
    boolean check rather than a raise — a revision that drops or reorders a
    segment is a degradation, not a hard failure, and the caller decides
    what to do with a bad result rather than catching an exception."""
    present_order: list[Segment] = []
    for turn in turns:
        if turn.segment not in present_order:
            present_order.append(turn.segment)

    missing = [s for s in _CANONICAL_SEGMENT_ORDER if s not in present_order]
    if missing:
        return False

    expected_index = {s: i for i, s in enumerate(_CANONICAL_SEGMENT_ORDER)}
    last_idx = -1
    for seg in present_order:
        idx = expected_index[seg]
        if idx < last_idx:
            return False
        last_idx = idx
    return True


def _is_within_word_budget(turns: list[Turn], budget: int) -> bool:
    words = sum(len(t.text.split()) for t in turns)
    lo, hi = budget * 0.8, budget * 1.2
    return lo <= words <= hi


def _is_valid_script(turns: list[Turn], budget: int) -> bool:
    """A script is usable when it has turns, covers every segment in
    canonical order, and lands within the word budget. Applied identically
    to the draft and to the revision — the revision gets no special pass."""
    return bool(turns) and _has_valid_segment_coverage_and_order(turns) and (
        _is_within_word_budget(turns, budget)
    )


# ──────────────────────────────────────────────────────────────
# SourceRef building — grounding audit trail
# ──────────────────────────────────────────────────────────────


def _distinctive_tokens(text: str) -> set[str]:
    """Content-bearing tokens from a title, used for fuzzy matching.

    Drops short words and the filler that shows up in almost every subject
    line, so "Q3 budget review before Friday" reduces to {q3, budget,
    review, friday} and can still match a host paraphrasing it as "the Q3
    budget review".
    """
    stop = {
        "the", "and", "for", "you", "your", "with", "from", "this", "that",
        "about", "into", "our", "are", "was", "were", "will", "have", "has",
        "not", "but", "all", "any", "can", "out", "new", "now", "get", "got",
        "before", "after", "week", "today", "tomorrow", "please", "update",
        "updates", "meeting", "re", "fwd",
    }
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in stop}


# A turn counts as discussing a source when it reproduces most of that
# source's distinctive tokens. Full-title substring matching was tried first
# and is useless in practice: hosts paraphrase ("Priya sent the rollout
# plan"), so an exact subject line almost never appears verbatim in speech
# and the manifest ships with an empty sources list.
_TOKEN_MATCH_RATIO = 0.6
_MIN_TOKEN_HITS = 2


def _mentions(turn_text: str, title: str, sender: str | None = None) -> bool:
    lowered = turn_text.lower()

    if title and title.lower() in lowered:
        return True

    # A sender's name is a strong, low-noise signal on its own. Match the
    # full name or any single distinctive part of it, since hosts routinely
    # say "Priya" rather than "Priya Raman".
    if sender:
        sender_l = sender.lower()
        if sender_l in lowered:
            return True
        for part in sender_l.split():
            if len(part) > 2 and re.search(rf"\b{re.escape(part)}\b", lowered):
                return True

    tokens = _distinctive_tokens(title)
    if not tokens:
        return False
    hits = sum(1 for tok in tokens if re.search(rf"\b{re.escape(tok)}\b", lowered))
    if hits < _MIN_TOKEN_HITS:
        return False
    return hits / len(tokens) >= _TOKEN_MATCH_RATIO


def _build_source_refs(
    turns: list[Turn], emails: list[Item], events: list[CalendarEvent]
) -> list[SourceRef]:
    """Link each turn back to the emails/events it plausibly discusses.

    This is the grounding audit trail rule 3 depends on, and the data behind
    show notes (PRD F-19): every item mentioned in audio needs a tappable
    entry. Matching is deliberately fuzzy — see `_mentions`.
    """
    refs: list[SourceRef] = []

    for i, turn in enumerate(turns):
        for ev in events:
            if _mentions(turn.text, ev.title):
                refs.append(
                    SourceRef(
                        turn_index=i,
                        title=ev.title,
                        url=None,
                        source_name="Calendar",
                        source_type=SourceType.CALENDAR,
                    )
                )

        for em in emails:
            if _mentions(turn.text, em.title, em.source_name):
                refs.append(
                    SourceRef(
                        turn_index=i,
                        title=em.title,
                        url=em.url,
                        source_name=em.source_name,
                        source_type=SourceType.EMAIL,
                    )
                )

    if not refs and (emails or events):
        # An empty trail means show notes are blank and no factual claim in
        # the briefing can be checked against its source. That is a rule 3
        # failure, so it must be visible rather than silent.
        logger.warning(
            "no SourceRefs matched across %d turns despite %d emails and "
            "%d events — show notes will be empty and the briefing has no "
            "grounding trail",
            len(turns), len(emails), len(events),
        )

    return refs


# ──────────────────────────────────────────────────────────────
# The chain
# ──────────────────────────────────────────────────────────────


def _turns_to_json(turns: list[Turn]) -> list[dict]:
    return [
        {"speaker": t.speaker, "segment": t.segment.value, "text": t.text}
        for t in turns
    ]


async def _run_critique_and_revise(
    *,
    llm: LLMProvider,
    draft_turns: list[Turn],
    source_data: str,
    budget: int,
) -> list[Turn]:
    """Passes 3 and 4: critique -> revise. Always returns a usable script —
    the draft itself, if anything along the way goes wrong or makes things
    worse (CLAUDE.md rule 7).

    Uses CRITIQUE_PROMPT and REVISE_PROMPT verbatim, per the Phase 2 brief.
    """
    draft_json = _turns_to_json(draft_turns)

    critique_prompt = CRITIQUE_PROMPT.format(
        script=json.dumps(draft_json), source_data=source_data
    )
    try:
        critique_raw = await llm.complete(
            HOST_SYSTEM, critique_prompt, temperature=0.5, json_mode=True
        )
        critique = parse_json_response(critique_raw)
    except JSONParseError as exc:
        logger.warning("critique pass failed to parse, keeping draft: %s", exc)
        return draft_turns

    revise_prompt = REVISE_PROMPT.format(
        script=json.dumps(draft_json), critique=json.dumps(critique)
    )
    try:
        revised_raw = await llm.complete(
            HOST_SYSTEM, revise_prompt, temperature=0.6, json_mode=True
        )
        revised_json = parse_json_response(revised_raw)
    except JSONParseError as exc:
        logger.warning("revise pass failed to parse, keeping draft: %s", exc)
        return draft_turns

    revised_turns = _parse_draft_turns(revised_json)

    if not _is_valid_script(revised_turns, budget):
        logger.warning(
            "revision invalid (empty, missing/out-of-order segments, or "
            "outside word budget) — keeping draft. revised turn count: %d",
            len(revised_turns),
        )
        return draft_turns

    # Guard against a revision that "fixes" the flagged issues but makes the
    # script worse overall. Run the same gates on both and only take the
    # revision if it does not regress.
    draft_report = run_all_gates(draft_turns, source_data)
    revised_report = run_all_gates(revised_turns, source_data)
    if revised_report["total_issues"] > draft_report["total_issues"]:
        logger.warning(
            "revision scored worse than draft on quality gates "
            "(draft=%d issues, revised=%d issues) — keeping draft. "
            "draft=%s revised=%s",
            draft_report["total_issues"], revised_report["total_issues"],
            draft_report, revised_report,
        )
        return draft_turns

    logger.info(
        "revision accepted: draft=%d issues, revised=%d issues",
        draft_report["total_issues"], revised_report["total_issues"],
    )
    return revised_turns


async def generate_script(
    *,
    llm: LLMProvider,
    emails: list[Item],
    events: list[CalendarEvent],
    background_events: list[CalendarEvent] | None = None,
    weather: Weather | None = None,
    length: BriefingLength,
    date: datetime,
    passes: int = 2,
) -> tuple[list[Turn], list[SourceRef]]:
    """Script chain: outline -> draft (2 passes, the default), or
    outline -> draft -> critique -> revise (4 passes) when `passes=4`.

    The default stays 2 to preserve the existing behaviour of every current
    caller (app/pipeline/generate.py, and the Phase 1 test suite) byte for
    byte — this module only adds the critique/revise capability, it does not
    flip on the extra cost and latency for every caller unasked. Pass
    `passes=4` explicitly to run the full Phase 2 chain; that is what
    backend/tests/test_script_chain_critique.py and the live verification
    for this change both do.

    Returns (turns, source_refs), built from whichever script (draft or
    revised) ends up being used. Malformed turns from the LLM are skipped
    and logged rather than failing the run (CLAUDE.md rule 7); the critique
    and revise passes degrade the same way — see _run_critique_and_revise.
    """
    if passes not in (2, 4):
        raise ValueError(f"passes must be 2 or 4, got {passes}")

    budget = LENGTH_BUDGETS[length]["words"]
    source_data = _flatten_source_text(emails, events, weather)

    outline_prompt = OUTLINE_PROMPT.format(
        # Spell the weekday out rather than handing over an ISO timestamp.
        # Live runs opened with "It's Thursday, August 21st" for a date that
        # was a Friday: given "2026-08-21T00:00:00+01:00" the model has to
        # compute the day of week itself, and it gets it wrong. The grounding
        # gate caught it ("Thursday" appears nowhere in the source) but
        # neither critique pass fixed it — because from the model's side it
        # was a derivation, not an invention. Never make the model derive a
        # fact you can simply state.
        date=date.strftime("%A, %d %B %Y") + f" (local time {date:%H:%M})",
        weather=_format_weather(weather),
        n_events=len(events),
        calendar=_format_calendar(events, background_events),
        n_emails=len(emails),
        emails=_format_emails(emails),
        n_stories=0,
        stories="(none — Phase 1 has no news clustering)",
        follow_ups="(none)",
        total_words=budget,
    )
    outline_raw = await llm.complete(
        HOST_SYSTEM, outline_prompt, temperature=0.7, json_mode=True
    )
    outline = parse_json_response(outline_raw)

    draft_prompt = DRAFT_PROMPT.format(
        outline=json.dumps(outline), source_data=source_data
    )
    draft_raw = await llm.complete(
        HOST_SYSTEM, draft_prompt, temperature=0.85, json_mode=True
    )
    draft_json = parse_json_response(draft_raw)
    draft_turns = _parse_draft_turns(draft_json)

    if passes == 4 and draft_turns:
        final_turns = await _run_critique_and_revise(
            llm=llm,
            draft_turns=draft_turns,
            source_data=source_data,
            budget=budget,
        )
    else:
        final_turns = draft_turns

    source_refs = _build_source_refs(final_turns, emails, events)

    return final_turns, source_refs
