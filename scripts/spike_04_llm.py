#!/usr/bin/env python3
"""
Phase 0, Spike 4 — Gemini 2.5 Flash, fake data in, two-host JSON script out.

Runs the multi-pass script chain (outline -> draft -> critique -> revise)
using the prompts in app/pipeline/prompts.py verbatim, against the fixture in
backend/tests/fixtures/spike_04_input.json (five emails, two calendar events,
weather — one email is directly about one of the meetings, so the hosts have
a real connection to find).

    python scripts/spike_04_llm.py --fixture     # offline, FakeLLMProvider
    python scripts/spike_04_llm.py --live         # needs GEMINI_API_KEY

--fixture is the default when GEMINI_API_KEY is unset. --live never falls
back to the fake — if the key is missing it fails loudly instead.

Writes the final script to out/spike_04_script.json and prints a pass/fail
summary of four quality gates plus word count.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.core.schemas import (  # noqa: E402
    LENGTH_BUDGETS,
    BriefingLength,
    Segment,
    Turn,
)
from app.pipeline.prompts import (  # noqa: E402
    CRITIQUE_PROMPT,
    DRAFT_PROMPT,
    HOST_SYSTEM,
    OUTLINE_PROMPT,
    REVISE_PROMPT,
)
from app.providers.fake import FakeLLMProvider  # noqa: E402

CANONICAL_SEGMENT_ORDER = [
    Segment.COLD_OPEN,
    Segment.WEATHER,
    Segment.CALENDAR,
    Segment.INBOX,
    Segment.WORLD,
    Segment.FOLLOW_UPS,
    Segment.SIGN_OFF,
]

FILLER_WORDS = ["um", "uh", "you know", "i mean", "like,"]


# ──────────────────────────────────────────────────────────────
# Robust JSON parsing — the riskiest part of talking to any LLM.
# ──────────────────────────────────────────────────────────────


class JSONParseError(ValueError):
    def __init__(self, message: str, raw_text: str) -> None:
        super().__init__(f"{message}\n\n--- raw response ---\n{raw_text}\n---")
        self.raw_text = raw_text


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_SMART_QUOTES = str.maketrans({
    "“": '"', "”": '"', "‘": "'", "’": "'",
})


def parse_json_response(text: str) -> Any:
    """Parse a JSON object or array out of raw LLM text.

    LLMs routinely wrap JSON in ```json ... ``` fences, add trailing or
    leading prose ("Here is the JSON you asked for:"), or use smart quotes.
    This strips fences, normalises quotes, and extracts the outermost JSON
    object or array by bracket matching before falling back to a plain
    `json.loads`. Raises JSONParseError with the raw text on failure so a
    parse failure is debuggable instead of a bare traceback.
    """
    if text is None:
        raise JSONParseError("response was None", "")

    cleaned = text.strip()

    fence_match = _FENCE_RE.search(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    cleaned = cleaned.translate(_SMART_QUOTES)

    # Try the straightforward case first.
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Fall back to extracting the outermost {...} or [...] by scanning for
    # the first opening bracket and its matching close, tracking string
    # state so brackets inside string values don't confuse the count.
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
# Quality gates — encode CLAUDE.md's non-negotiable rules. Real functions,
# reused as the Phase 2 critique harness, not just spike-only checks.
# ──────────────────────────────────────────────────────────────


_FILLER_PATTERNS = [
    (word, re.compile(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])"))
    for word in FILLER_WORDS
]


def detect_filler_words(turns: list[Turn]) -> list[dict]:
    """Rule 1: no filler words anywhere. Naturalness comes from TTS, not
    the transcript. Any hit here is an unconditional failure.

    Matches on word boundaries, not substring — a naive `"um" in text`
    check false-positives on ordinary words like "bump" or "album".
    """
    hits = []
    for i, turn in enumerate(turns):
        lowered = turn.text.lower()
        for word, pattern in _FILLER_PATTERNS:
            if pattern.search(lowered):
                hits.append({"turn_index": i, "word": word, "text": turn.text})
    return hits


_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+(?:\s|$)")


def _sentence_count(text: str) -> int:
    parts = [p for p in _SENTENCE_SPLIT_RE.split(text.strip()) if p.strip()]
    return len(parts)


def detect_monologues(turns: list[Turn]) -> list[dict]:
    """Rule from prompts.py: turns run one to four sentences. Anything
    longer kills the format — flag it."""
    hits = []
    for i, turn in enumerate(turns):
        n = _sentence_count(turn.text)
        if n > 4:
            hits.append({"turn_index": i, "sentence_count": n, "text": turn.text})
    return hits


_CHALLENGE_MARKERS = [
    "push back", "actually", "i'd disagree", "disagree", "not quite",
    "hold on", "to be fair", "i'd argue", "counterpoint", "fair, but",
    "i'm not sure", "wait", "hmm", "stretch", "narrower",
]
_AGREEABLE_MARKERS = [
    "agreed", "agree", "sounds good", "exactly", "totally", "no notes",
    "nothing to add", "yep", "yeah",
]


def detect_missing_disagreement(turns: list[Turn]) -> dict[str, list[dict]]:
    """Rule 2: hosts must not always agree — at least one challenge or
    reframe per segment. HEURISTIC, not a semantic judge: it looks for
    challenge-marker phrases per segment and flags segments with none,
    plus segments that lean heavily on agreement markers with zero
    challenge markers. Expect false positives on segments that are
    legitimately short or uncontroversial (e.g. a two-line follow_ups
    segment with nothing to discuss)."""
    by_segment: dict[str, list[Turn]] = {}
    for turn in turns:
        by_segment.setdefault(turn.segment.value, []).append(turn)

    flagged = []
    for segment, seg_turns in by_segment.items():
        text_blob = " ".join(t.text.lower() for t in seg_turns)
        has_challenge = any(m in text_blob for m in _CHALLENGE_MARKERS)
        agreeable_count = sum(
            1 for m in _AGREEABLE_MARKERS if m in text_blob
        )
        if not has_challenge and len(seg_turns) > 1:
            flagged.append({
                "segment": segment,
                "reason": "no challenge/reframe marker found",
                "agreeable_marker_hits": agreeable_count,
            })
    return {"segments_missing_disagreement": flagged}


_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")
_NUMBER_RE = re.compile(r"\b\d[\d,.:]*\b")

# Common words that are capitalised at sentence-start but aren't proper
# nouns referring to source facts — filtering these out reduces (but does
# not eliminate) false positives in the grounding check.
_STOPWORDS = {
    "The", "This", "That", "These", "Those", "Good", "Morning", "First",
    "Then", "And", "But", "So", "One", "Two", "Three", "No", "Yep", "Yeah",
    "Fair", "Which", "Also", "Agreed", "Have", "Talk", "Nothing", "Bit",
    "Actually", "Might", "She", "He", "Worth", "Clean", "Enjoy",
    "Unrelated", "Weather", "Wait", "Hmm", "Wow", "Really", "Honestly",
}


def check_grounding(turns: list[Turn], source_text: str) -> list[dict]:
    """Rule 3: ground every factual claim in the source data. HEURISTIC:
    flags proper nouns and numbers in the script that do not appear
    anywhere in the flattened source text. This is a blunt instrument —
    it will false-positive on host names, weekday names, generic phrasing
    ("Meeting Room 3" vs "Meeting Room 3" case differences), and words
    that happen to share no substring with the source. It cannot verify
    that a *true* fact is stated in a way that's actually supported, only
    that the token appears somewhere in the source. Treat it as a
    starting point for a human or LLM critique pass, not a verdict."""
    source_lower = source_text.lower()
    settings = get_settings()
    allowed_names = {settings.host_a_name, settings.host_b_name, settings.user_name}

    flags = []
    for i, turn in enumerate(turns):
        for match in _PROPER_NOUN_RE.finditer(turn.text):
            word = match.group(0)
            if word in _STOPWORDS or word in allowed_names:
                continue
            if word.lower() not in source_lower:
                flags.append({
                    "turn_index": i, "token": word, "kind": "proper_noun",
                })
        for match in _NUMBER_RE.finditer(turn.text):
            token = match.group(0)
            if token not in source_text:
                flags.append({"turn_index": i, "token": token, "kind": "number"})
    return flags


# ──────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────


def validate_turns(raw: Any) -> list[Turn]:
    if not isinstance(raw, list):
        raise ValueError(f"expected a JSON array of turns, got {type(raw)}")
    turns = [Turn.model_validate(item) for item in raw]
    if not turns:
        raise ValueError("turn array is empty")
    for turn in turns:
        if turn.speaker not in ("A", "B"):
            raise ValueError(f"invalid speaker {turn.speaker!r}, must be A or B")
    return turns


def validate_segment_coverage(turns: list[Turn]) -> None:
    present_order: list[Segment] = []
    for turn in turns:
        if turn.segment not in present_order:
            present_order.append(turn.segment)

    missing = [s for s in CANONICAL_SEGMENT_ORDER if s not in present_order]
    if missing:
        raise ValueError(f"missing segments: {[s.value for s in missing]}")

    # present_order must be a subsequence in canonical order (segments must
    # not interleave / repeat out of order).
    expected_index = {s: i for i, s in enumerate(CANONICAL_SEGMENT_ORDER)}
    last_idx = -1
    for seg in present_order:
        idx = expected_index[seg]
        if idx < last_idx:
            raise ValueError(
                f"segments out of canonical order: {[s.value for s in present_order]}"
            )
        last_idx = idx


def word_count(turns: list[Turn]) -> int:
    return sum(len(t.text.split()) for t in turns)


# ──────────────────────────────────────────────────────────────
# The chain
# ──────────────────────────────────────────────────────────────


def load_fixture() -> dict:
    path = Path(__file__).parent.parent / "backend/tests/fixtures/spike_04_input.json"
    return json.loads(path.read_text())


def flatten_source_text(fixture: dict) -> str:
    parts = [json.dumps(fixture["weather"])]
    for ev in fixture["calendar"]:
        parts.append(json.dumps(ev))
    for em in fixture["emails"]:
        parts.append(json.dumps(em))
    return "\n".join(parts)


def format_calendar(fixture: dict) -> str:
    lines = []
    for ev in fixture["calendar"]:
        lines.append(
            f"- {ev['title']} ({ev['start']} to {ev['end']}) "
            f"@ {ev.get('location') or 'no location'}: "
            f"{ev.get('description', '')}"
        )
    return "\n".join(lines)


def format_emails(fixture: dict) -> str:
    lines = []
    for em in fixture["emails"]:
        lines.append(
            f"- From {em['source_name']} <{em.get('author')}> "
            f"[{em['timestamp']}]: {em['title']}\n  {em['body']}"
        )
    return "\n".join(lines)


async def run_chain(provider, fixture: dict) -> dict:
    budget = LENGTH_BUDGETS[BriefingLength.STANDARD]["words"]
    source_data = flatten_source_text(fixture)

    outline_prompt = OUTLINE_PROMPT.format(
        date=fixture["date"],
        weather=json.dumps(fixture["weather"]),
        n_events=len(fixture["calendar"]),
        calendar=format_calendar(fixture),
        n_emails=len(fixture["emails"]),
        emails=format_emails(fixture),
        n_stories=0,
        stories="(none in this fixture)",
        follow_ups="(none)",
        total_words=budget,
    )
    print("--- PASS 1: outline ---")
    outline_raw = await provider.complete(
        HOST_SYSTEM, outline_prompt, temperature=0.7, json_mode=True
    )
    outline = parse_json_response(outline_raw)
    print(f"  {len(outline.get('segments', []))} segments planned")

    draft_prompt = DRAFT_PROMPT.format(
        outline=json.dumps(outline), source_data=source_data
    )
    print("--- PASS 2: draft ---")
    draft_raw = await provider.complete(
        HOST_SYSTEM, draft_prompt, temperature=0.85, json_mode=True
    )
    draft_json = parse_json_response(draft_raw)
    draft_turns = validate_turns(draft_json)
    validate_segment_coverage(draft_turns)
    print(f"  {len(draft_turns)} turns, {word_count(draft_turns)} words")

    critique_prompt = CRITIQUE_PROMPT.format(
        script=json.dumps(draft_json), source_data=source_data
    )
    print("--- PASS 3: critique ---")
    critique_raw = await provider.complete(
        HOST_SYSTEM, critique_prompt, temperature=0.5, json_mode=True
    )
    critique = parse_json_response(critique_raw)
    print(f"  {len(critique.get('issues', []))} issues found")

    revise_prompt = REVISE_PROMPT.format(
        script=json.dumps(draft_json), critique=json.dumps(critique)
    )
    print("--- PASS 4: revise ---")
    revised_raw = await provider.complete(
        HOST_SYSTEM, revise_prompt, temperature=0.6, json_mode=True
    )
    revised_json = parse_json_response(revised_raw)
    revised_turns = validate_turns(revised_json)
    validate_segment_coverage(revised_turns)
    print(f"  {len(revised_turns)} turns, {word_count(revised_turns)} words")

    return {
        "outline": outline,
        "draft_turns": draft_turns,
        "critique": critique,
        "revised_turns": revised_turns,
        "source_text": source_data,
        "word_budget": budget,
    }


def print_script(turns: list[Turn]) -> None:
    print("\n=== FINAL SCRIPT ===\n")
    for turn in turns:
        print(f"[{turn.segment.value:>10}] {turn.speaker}: {turn.text}")


def run_quality_gates(turns: list[Turn], source_text: str, budget: int) -> bool:
    print("\n=== QUALITY GATES ===")
    all_ok = True

    filler = detect_filler_words(turns)
    ok = not filler
    all_ok &= ok
    print(f"  [{'PASS' if ok else 'FAIL'}] filler words: {len(filler)} found")
    for h in filler:
        print(f"         turn {h['turn_index']}: {h['word']!r} in {h['text']!r}")

    monologues = detect_monologues(turns)
    ok = not monologues
    all_ok &= ok
    print(f"  [{'PASS' if ok else 'FAIL'}] monologues (>4 sentences): "
          f"{len(monologues)} found")
    for h in monologues:
        print(f"         turn {h['turn_index']}: {h['sentence_count']} sentences")

    disagreement = detect_missing_disagreement(turns)
    missing = disagreement["segments_missing_disagreement"]
    ok = not missing
    all_ok &= ok
    print(f"  [{'PASS' if ok else 'FAIL'}] disagreement per segment "
          f"(heuristic): {len(missing)} segment(s) flagged")
    for h in missing:
        print(f"         segment {h['segment']!r}: {h['reason']}")

    grounding = check_grounding(turns, source_text)
    print(f"  [INFO] grounding check (heuristic, expect false positives): "
          f"{len(grounding)} token(s) flagged")
    for h in grounding[:10]:
        print(f"         turn {h['turn_index']}: {h['kind']} {h['token']!r}")

    words = word_count(turns)
    lo, hi = budget * 0.8, budget * 1.2
    ok = lo <= words <= hi
    all_ok &= ok
    print(f"  [{'PASS' if ok else 'FAIL'}] word count: {words} "
          f"(target {budget}, range {lo:.0f}-{hi:.0f})")

    return bool(all_ok)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true",
                         help="offline run with FakeLLMProvider (default)")
    parser.add_argument("--live", action="store_true",
                         help="live run against Gemini 2.5 Flash")
    args = parser.parse_args()

    settings = get_settings()
    use_live = args.live
    if args.fixture and args.live:
        print("Pass only one of --fixture / --live.", file=sys.stderr)
        sys.exit(2)
    if not args.fixture and not args.live:
        use_live = bool(settings.gemini_api_key)
        print(f"No mode flag given, defaulting to "
              f"{'--live' if use_live else '--fixture'} "
              f"({'GEMINI_API_KEY set' if use_live else 'GEMINI_API_KEY unset'})")

    if use_live:
        from app.providers.gemini import GeminiProvider
        provider = GeminiProvider()
        print(f"Running LIVE against {settings.llm_model}\n")
    else:
        provider = FakeLLMProvider()
        print("Running OFFLINE against FakeLLMProvider (no network)\n")

    fixture = load_fixture()
    result = await run_chain(provider, fixture)

    print_script(result["revised_turns"])
    gates_ok = run_quality_gates(
        result["revised_turns"], result["source_text"], result["word_budget"]
    )

    out_dir = Path(__file__).parent.parent / "out"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "spike_04_script.json"
    out_path.write_text(json.dumps({
        "outline": result["outline"],
        "critique": result["critique"],
        "turns": [t.model_dump(mode="json") for t in result["revised_turns"]],
    }, indent=2))
    print(f"\nWrote {out_path}")

    print(f"\n{'ALL GATES PASSED' if gates_ok else 'SOME GATES FAILED'}")


if __name__ == "__main__":
    asyncio.run(main())
