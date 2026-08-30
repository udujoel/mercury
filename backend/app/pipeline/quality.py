"""
Quality gates for a generated script — encodes CLAUDE.md rules 1-3.

Promoted from `scripts/spike_04_llm.py`, where these four functions were
proven against live Gemini output during Phase 0/1. That spike file is not
touched by this module (and stays the throwaway harness it always was); this
is the real, importable version the Phase 2 critique pass depends on.

Operates on `list[Turn]` (the shared schema in app.core.schemas) rather than
the spike's raw dicts.
"""

from __future__ import annotations

import re

from app.core.config import get_settings
from app.core.schemas import Turn

# ──────────────────────────────────────────────────────────────
# Rule 1 — no filler words. Naturalness comes from the TTS model.
# ──────────────────────────────────────────────────────────────

FILLER_WORDS = ["um", "uh", "you know", "i mean", "like,"]

_FILLER_PATTERNS = [
    (word, re.compile(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])"))
    for word in FILLER_WORDS
]


def check_filler_words(turns: list[Turn]) -> list[dict]:
    """Rule 1: no filler words anywhere. Any hit here is an unconditional
    failure. Matches on word boundaries, not substring — a naive
    `"um" in text` check false-positives on ordinary words like "bump" or
    "album"."""
    hits = []
    for i, turn in enumerate(turns):
        lowered = turn.text.lower()
        for word, pattern in _FILLER_PATTERNS:
            if pattern.search(lowered):
                hits.append({"turn_index": i, "word": word, "text": turn.text})
    return hits


# ──────────────────────────────────────────────────────────────
# Turns run one to four sentences. Longer turns kill the format.
# ──────────────────────────────────────────────────────────────

_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+(?:\s|$)")


def _sentence_count(text: str) -> int:
    parts = [p for p in _SENTENCE_SPLIT_RE.split(text.strip()) if p.strip()]
    return len(parts)


def check_monologues(turns: list[Turn]) -> list[dict]:
    """Turns run one to four sentences (prompts.py rule 7). Anything longer
    kills the format — flag it."""
    hits = []
    for i, turn in enumerate(turns):
        n = _sentence_count(turn.text)
        if n > 4:
            hits.append({"turn_index": i, "sentence_count": n, "text": turn.text})
    return hits


# ──────────────────────────────────────────────────────────────
# Rule 2 — hosts must not always agree. Scope narrowed to substantive
# segments only — see check_disagreement docstring below.
# ──────────────────────────────────────────────────────────────

# Markers are written with ASCII apostrophes, but models emit the
# typographic form ("I\u2019d argue") at least as often. That used to be
# invisible: script_chain.parse_json_response normalised every curly quote
# in the response before parsing, so these markers only ever saw ASCII.
# Fixing that pre-pass (it corrupted valid JSON) means real apostrophes now
# reach this gate intact — and "i'd argue" stops matching "i\u2019d argue".
# The gate would have got quietly worse while every test stayed green, so
# normalise here instead, where the comparison actually happens.
_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'"})


def _normalise(text: str) -> str:
    return text.lower().translate(_APOSTROPHES)


_CHALLENGE_MARKERS = [
    "push back", "actually", "i'd disagree", "disagree", "not quite",
    "hold on", "to be fair", "i'd argue", "counterpoint", "fair, but",
    "i'm not sure", "wait", "hmm", "stretch", "narrower",
]
_AGREEABLE_MARKERS = [
    "agreed", "agree", "sounds good", "exactly", "totally", "no notes",
    "nothing to add", "yep", "yeah",
]

# CLAUDE.md rule 2 says "hosts must not always agree" and "at least once per
# SEGMENT one host challenges or reframes the other" — but the segment
# definitions in prompts.py make clear not every segment is a place for
# substantive back-and-forth. Demanding a challenge marker in cold_open,
# weather, follow_ups and sign_off was tried and produces hosts who bicker
# in the greeting and on the way out the door ("Actually, hold on, happy
# Friday to you too") purely to satisfy the gate — worse writing, not better.
# This was observed on live output, not hypothesised. Rule 2 is about
# substantive content, so the gate now only applies to the three segments
# where real editorial tension belongs: calendar, inbox, world. The fix is
# narrowing the gate's scope, not loosening its threshold — a flat
# "calendar" segment still gets flagged exactly as before.
_DISAGREEMENT_SEGMENTS = {"calendar", "inbox", "world"}


def check_disagreement(turns: list[Turn]) -> list[dict]:
    """Rule 2: hosts must not always agree — at least one challenge or
    reframe per substantive segment (calendar, inbox, world only; see
    module docstring above for why cold_open/weather/follow_ups/sign_off
    are excluded). HEURISTIC, not a semantic judge: it looks for
    challenge-marker phrases per segment and flags segments with none,
    plus segments that lean heavily on agreement markers with zero
    challenge markers. Expect false positives on segments that are
    legitimately short (a two-line inbox segment with one item)."""
    by_segment: dict[str, list[Turn]] = {}
    for turn in turns:
        by_segment.setdefault(turn.segment.value, []).append(turn)

    flagged = []
    for segment, seg_turns in by_segment.items():
        if segment not in _DISAGREEMENT_SEGMENTS:
            continue
        text_blob = " ".join(_normalise(t.text) for t in seg_turns)
        has_challenge = any(m in text_blob for m in _CHALLENGE_MARKERS)
        agreeable_count = sum(1 for m in _AGREEABLE_MARKERS if m in text_blob)
        if not has_challenge and len(seg_turns) > 1:
            flagged.append({
                "segment": segment,
                "reason": "no challenge/reframe marker found",
                "agreeable_marker_hits": agreeable_count,
            })
    return flagged


# ──────────────────────────────────────────────────────────────
# Rule 3 — ground every factual claim in source data.
# ──────────────────────────────────────────────────────────────

_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")
_NUMBER_RE = re.compile(r"\b\d[\d,.:]*\b")

# Common words that are capitalised at sentence-start but aren't proper
# nouns referring to source facts — filtering these out reduces (but does
# not eliminate) false positives in the grounding check.
#
# Filtering happens by VOCABULARY, not sentence position. A positional
# filter (skip whatever word opens a sentence) was tried first and
# rejected — it dropped a hallucinated name that happened to open a
# sentence. On a live run, 15 of 17 flags from the naive version were
# ordinary sentence-openers ("Looking", "Exactly", "However"); adding
# those specific words to this stopword set took false positives from
# 15/17 down to ~1, while still catching "Thompson" — a real
# Ben-Thompson attribution the model supplied from world knowledge that
# appeared nowhere in the source data.
_STOPWORDS = {
    "The", "This", "That", "These", "Those", "Good", "Morning", "First",
    "Then", "And", "But", "So", "One", "Two", "Three", "No", "Yep", "Yeah",
    "Fair", "Which", "Also", "Agreed", "Have", "Talk", "Nothing", "Bit",
    "Actually", "Might", "She", "He", "Worth", "Clean", "Enjoy",
    "Unrelated", "Weather", "Wait", "Hmm", "Wow", "Really", "Honestly",
    "Looking", "Exactly", "Precisely", "However", "Indeed", "Okay", "Yes",
    "Speaking", "Starting", "While", "From", "They", "Another", "Beyond",
    "Doesn", "Isn", "Let", "Right", "Still", "Meanwhile", "Otherwise",
    "Before", "After", "Given", "Because", "Though", "Although", "Just",
    "Now", "Here", "There", "When", "Where", "What", "Why", "How", "If",
    "It", "We", "You", "Your", "My", "Our", "Their", "His", "Her", "Its",
    "For", "With", "About", "Into", "Over", "Under", "Between", "Across",
}


def check_grounding(turns: list[Turn], source_text: str) -> list[dict]:
    """Rule 3: ground every factual claim in the source data. HEURISTIC:
    flags proper nouns and numbers in the script that do not appear
    anywhere in the flattened source text. This is a blunt instrument —
    it will false-positive on host names, weekday names, generic phrasing,
    and words that happen to share no substring with the source. It cannot
    verify that a *true* fact is stated in a way that's actually supported,
    only that the token appears somewhere in the source. Treat it as a
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
# Run all gates together
# ──────────────────────────────────────────────────────────────


def run_all_gates(turns: list[Turn], source_text: str) -> dict:
    """Run every gate and return a single report dict.

    Used by script_chain.py to compare draft vs revised quality and by
    tests to assert on before/after deltas.
    """
    filler = check_filler_words(turns)
    monologues = check_monologues(turns)
    disagreement = check_disagreement(turns)
    grounding = check_grounding(turns, source_text)
    return {
        "filler": filler,
        "monologues": monologues,
        "disagreement": disagreement,
        "grounding": grounding,
        "total_issues": (
            len(filler) + len(monologues) + len(disagreement) + len(grounding)
        ),
    }
