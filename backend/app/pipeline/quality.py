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
# Rule 4-adjacent — the hosts must not relay a security instruction.
# ──────────────────────────────────────────────────────────────

# The fence stops the model OBEYING an email. It does nothing about the
# model faithfully RELAYING one, and relaying is the more dangerous half.
#
# Live, 2026-09-13-1835: "It advises changing your password... This is a
# critical security step to protect your account." Every safeguard worked
# as designed. The model did not obey the email; it reported it, which is
# exactly what the prompt asks for. But audio strips what a reader uses to
# judge a phishing mail — the sender address, the headers, the link — and
# replaces it with two trusted voices calling the instruction critical.
#
# The selection is attacker-controlled: `classify_priority` gives HIGH plus
# `requires_response` to anything addressed to the listener containing a
# "?". Task 27 widened this further, deliberately — account and security
# mail can now escalate past the newsletter floor — so the path from "a
# stranger sends a well-worded email" to "the hosts read it out as urgent"
# is shorter than it was, not longer.
#
# And `check_grounding` makes it WORSE rather than better: a phishing claim
# quoted accurately from the email scores as MORE grounded, because the
# email is the source data.
#
# Two things have to be true for a flag, because either alone is fine. The
# hosts SAYING an email asks for something is journalism. The hosts telling
# the listener to do it, or vouching for it, is the product taking the
# sender's side.
_SECURITY_ACTION = re.compile(
    r"\b(?:"
    r"chang(?:e|ing) (?:your )?password|reset(?:ting)? (?:your )?password"
    r"|verify (?:your )?(?:account|identity|email|details)"
    r"|confirm (?:your )?(?:account|identity|details|payment)"
    r"|updat(?:e|ing) (?:your )?(?:payment|billing|card|details)"
    r"|click (?:the|that|this) link|follow (?:the|that) link"
    r"|log ?in to (?:your )?account|sign ?in to (?:your )?account"
    r"|enable two[- ]factor|share (?:your )?(?:code|otp|pin)"
    r")\b",
    re.I,
)

# The hosts endorsing it, or issuing it themselves.
_ENDORSEMENT = re.compile(
    r"\b(?:"
    r"you should|you'?ll want to|make sure (?:you|to)|be sure to"
    r"|worth doing|definitely (?:do|worth)|don'?t ignore|do that (?:today|now)"
    r"|critical|urgent(?:ly)?|important that you|act (?:on it|now|today)"
    r"|right away|as soon as possible|straight away|first thing"
    r")\b",
    re.I,
)

# Only where third-party text is spoken. The cold open and the weather do
# not quote anybody's email.
_RELAY_SEGMENTS = {"inbox", "follow_ups", "world"}


def check_relayed_instructions(turns: list[Turn]) -> list[dict]:
    """The hosts must not pass on a security instruction from a sender.

    HEURISTIC, in the shape of the other gates and with the same honesty
    about it: it looks for a security ACTION phrase and an ENDORSEMENT in
    the same turn. Reporting that an email asks for something is allowed
    and must stay allowed — that is journalism, and the segment's job.
    What this catches is the hosts adding their own weight to it.

    False positives are expected and are the cheap direction. "This is a
    critical security step" about a legitimate password-reset the listener
    themselves requested is a flag worth raising anyway: the hosts still
    cannot tell the listener whether the mail is genuine, so naming the
    sender and stopping is the right behaviour either way.
    """
    flagged = []
    for index, turn in enumerate(turns):
        if turn.segment.value not in _RELAY_SEGMENTS:
            continue
        action = _SECURITY_ACTION.search(turn.text)
        if not action:
            continue
        endorsement = _ENDORSEMENT.search(turn.text)
        if not endorsement:
            continue
        flagged.append({
            "turn_index": index,
            "segment": turn.segment.value,
            "action": action.group(0).lower(),
            "endorsement": endorsement.group(0).lower(),
            "reason": (
                "the hosts relay a security instruction and add their own "
                "weight to it; audio has already stripped the sender "
                "address, the headers and the link"
            ),
        })
    return flagged


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
    relayed = check_relayed_instructions(turns)
    return {
        "filler": filler,
        "monologues": monologues,
        "disagreement": disagreement,
        "grounding": grounding,
        "relayed_instructions": relayed,
        "total_issues": (
            len(filler) + len(monologues) + len(disagreement)
            + len(grounding) + len(relayed)
        ),
    }
