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
from datetime import datetime

from app.core.config import get_settings
from app.core.schemas import Segment, Turn

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


# ──────────────────────────────────────────────────────────────
# Rule 3, the one fact the hosts always state: what day it is.
# ──────────────────────────────────────────────────────────────

_WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)
_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)
_ORDINALS = {
    word: n
    for n, word in enumerate(
        (
            "first second third fourth fifth sixth seventh eighth ninth tenth "
            "eleventh twelfth thirteenth fourteenth fifteenth sixteenth "
            "seventeenth eighteenth nineteenth twentieth twenty-first "
            "twenty-second twenty-third twenty-fourth twenty-fifth "
            "twenty-sixth twenty-seventh twenty-eighth twenty-ninth thirtieth "
            "thirty-first"
        ).split(),
        start=1,
    )
}
_WD = "|".join(_WEEKDAYS)
_MO = "|".join(_MONTHS)
# A day of the month, as a number with an optional suffix or as a word.
# Longest words first, so "twenty-first" is not read as "twenty".
_DAY = (
    r"\d{1,2}(?:st|nd|rd|th)?|"
    + "|".join(sorted(_ORDINALS, key=len, reverse=True))
)
# "Monday, October 11th" / "Monday October 11" / "Monday, 11 October" /
# "Monday, the 11th of October" / "Monday, the eleventh of October", each
# with an optional ", 2026".
_DATED_WEEKDAY = re.compile(
    rf"\b(?P<wd>{_WD}),?\s+(?:the\s+)?"
    rf"(?:(?P<m1>{_MO})\s+(?:the\s+)?(?P<d1>{_DAY})"
    rf"|(?P<d2>{_DAY})\s+(?:of\s+)?(?P<m2>{_MO}))"
    rf"(?:,?\s+(?P<y>\d{{4}}))?\b",
    re.IGNORECASE,
)
# "It's Monday" / "It is Monday" / "Today is Monday" / "Happy Monday".
_TODAY_WEEKDAY = re.compile(
    rf"\b(?:it's|it’s|it is|today is|happy)\s+(?P<wd>{_WD})\b", re.IGNORECASE
)


def _said_date(m: re.Match) -> tuple[int, int, int | None]:
    """(month, day, year or None) from a `_DATED_WEEKDAY` match."""
    month = _MONTHS.index((m.group("m1") or m.group("m2")).capitalize()) + 1
    day_token = (m.group("d1") or m.group("d2")).lower()
    day = _ORDINALS.get(day_token) or int(re.match(r"\d+", day_token).group(0))
    year = int(m.group("y")) if m.group("y") else None
    return month, day, year


def _is_today(m: re.Match, date: datetime) -> bool:
    month, day, year = _said_date(m)
    return (month, day) == (date.month, date.day) and year in (None, date.year)


def correct_spoken_date(
    turns: list[Turn], date: datetime
) -> tuple[list[Turn], list[dict]]:
    """Make the weekday the hosts say the weekday it is.

    The 2026-10-11 briefing opened "It's Monday, October 11th"; it was a
    Sunday. Only the outline pass is told the date, and a model that
    derives a weekday gets it wrong often enough that this is the third
    time (ROADMAP task 48). The grounding gate can't help, because a
    weekday is a word that appears in any inbox.

    Corrects ONLY the weekday word, and only where the sentence is
    unambiguously about today:

    - a weekday followed by TODAY's date (numeric or spelled, with or
      without the year), in any segment;
    - "it's / today is / happy <weekday>" in the cold open, unless a
      different date follows it.

    A weekday followed by a DIFFERENT date (or year) is left alone. If that
    is the cold open's "It's ...", it is reported as `other_date`: the date
    itself is wrong, and swapping the weekday would make it wrong in a new
    way.

    Names come from this module's English tables, never `strftime`, whose
    `%A` follows the host's locale.

    Returns new turns and a list of what changed or was reported. The
    turns passed in are not mutated.
    """
    actual = _WEEKDAYS[date.weekday()]
    out: list[Turn] = []
    found: list[dict] = []

    for i, turn in enumerate(turns):
        text = turn.text
        spans: list[tuple[int, int]] = []

        for m in _DATED_WEEKDAY.finditer(text):
            if _is_today(m, date) and m.group("wd").capitalize() != actual:
                spans.append(m.span("wd"))
                found.append(
                    {"turn_index": i, "said": m.group("wd"), "kind": "weekday"}
                )

        if turn.segment == Segment.COLD_OPEN:
            for m in _TODAY_WEEKDAY.finditer(text):
                dated = _DATED_WEEKDAY.match(text, m.start("wd"))
                if dated:
                    if not _is_today(dated, date):
                        found.append({
                            "turn_index": i,
                            "said": dated.group(0),
                            "kind": "other_date",
                        })
                    # Today's date: the loop above has already handled it.
                    continue
                if m.group("wd").capitalize() != actual:
                    spans.append(m.span("wd"))
                    found.append(
                        {"turn_index": i, "said": m.group("wd"), "kind": "weekday"}
                    )

        for start, end in sorted(set(spans), reverse=True):
            text = text[:start] + actual + text[end:]
        if text != turn.text:
            turn = turn.model_copy(update={"text": text})
        out.append(turn)

    return out, found


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
# An instruction the sender wants the listener to CARRY OUT. Widened
# after review of #57, which found 24 of 28 realistic relays missing and
# the list internally inconsistent — "confirm your payment" caught while
# "verify your payment" did not.
#
# Deliberately generous, because the cost is now asymmetric in the other
# direction: a miss ships a phishing instruction in a trusted voice, and
# a false positive costs one line of the script being rewritten.
_ACTION_VERB = (
    r"chang(?:e|ing)|reset(?:ting)?|updat(?:e|ing)|verif(?:y|ying)"
    r"|confirm(?:ing)?|validat(?:e|ing)|secur(?:e|ing)|re-?authenticat(?:e|ing)"
    r"|authoris(?:e|ing)|authoriz(?:e|ing)|transfer(?:ring)?"
)
_ACTION_OBJECT = (
    r"password|passcode|credentials?|account|identity|payment|billing"
    r"|card details|bank details|details|pin|otp|two[- ]factor|2fa"
    # "authorise the transfer" — the object is the money, not a credential.
    r"|transfer|payment"
)
# Verbs that are an instruction on their own, with no object to name.
_STANDALONE_ACTION = r"re-?authenticat(?:e|ing)|re-?enter (?:your )?details"
_SECURITY_ACTION = re.compile(
    r"(?:"
    # verb + (the|your|their|a) + object — "reset the password on that
    # account" as well as "reset your password".
    rf"\b(?:{_ACTION_VERB})\s+(?:the\s+|your\s+|their\s+|a\s+)?"
    rf"(?:{_ACTION_OBJECT})\b"
    rf"|\b(?:{_STANDALONE_ACTION})\b"
    # the follow-a-lure family, which has no object of its own
    r"|\bclick (?:the|that|this|a|on) (?:link|button)\b|\bclick here\b"
    r"|\btap (?:the|that|this|a) (?:link|button)\b"
    r"|\bfollow (?:the|that|those) (?:link|links|steps|instructions)\b"
    r"|\bopen (?:the|that|their) attachment\b"
    r"|\bcall (?:the|that) number\b"
    r"|\blog ?in to|\bsign ?in to|\bshare (?:your )?(?:code|otp|pin)\b"
    # the consequence clause every credential-phish carries
    r"|\byour account will be (?:closed|suspended|locked|restricted)\b"
    r"|\bunless you (?:act|respond|verify|confirm)\b"
    r")",
    re.I,
)

# The hosts adding their own weight to it.
_ENDORSEMENT = re.compile(
    r"\b(?:"
    r"you should|you'?ll want to|make sure (?:you|to)|be sure to"
    r"|worth doing|definitely (?:do|worth)|don'?t ignore|do that (?:today|now)"
    r"|critical|urgent(?:ly)?|important that you|act (?:on it|now|today)"
    r"|right away|as soon as possible|straight away|first thing"
    # Review of #57: the three most natural co-host endorsements carried
    # none of the markers above. A recommendation does not need an
    # intensifier to be a recommendation.
    r"|i'?d (?:do|handle|sort|deal with) (?:that|it|this)"
    r"|that'?s the one to|the one to (?:handle|do|sort)"
    r"|before (?:your|anything|everything)|top of (?:the|your) list"
    r"|handle (?:that|it|this) (?:first|today|now)"
    r")\b",
    re.I,
)

# Where third-party text is spoken.
#
# `calendar` is here after review of #57. `_format_calendar` interpolates
# `ev.description` verbatim, and an invite from a stranger auto-populates
# Google Calendar — so that field is an injection surface on a par with an
# email body, and the calendar contract's "flag anything needing prep"
# invites exactly the endorsing phrasing.
_RELAY_SEGMENTS = {"inbox", "follow_ups", "world", "calendar"}

# Leading words that do not stop a sentence being an order.
_IMPERATIVE_LEAD = re.compile(
    r"^(?:so|then|and|but|ok(?:ay)?|right|now|just|please|go and|go|"
    r"you need to|you have to)[\s,]+", re.I
)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _is_an_order(text: str) -> bool:
    """Does a sentence here TELL the listener to do the thing?

    The hosts issuing the instruction themselves — "Change your password.
    Today." — carries no endorsement marker at all, so requiring two
    signals made the most dangerous output unreachable. Review of #57.

    An imperative is detected positionally: the action opens the sentence,
    once conversational lead-ins are stripped. "It advises changing your
    password" does not match, because the sentence opens with "It".
    """
    for sentence in _sentences(text):
        stripped = _IMPERATIVE_LEAD.sub("", sentence.strip())
        match = _SECURITY_ACTION.match(stripped)
        if match:
            return True
    return False


def _normalise_spaces(text: str) -> str:
    """NBSP and friends collapse to a plain space.

    HTML email text flows into the source data and out through the script,
    and `"change\u00a0your\u00a0password"` defeated every literal-space
    pattern here. Review of #57.
    """
    return re.sub(r"\s+", " ", text.replace("\u00a0", " "))


def check_relayed_instructions(turns: list[Turn]) -> list[dict]:
    """The hosts must not pass on a security instruction from a sender.

    HEURISTIC, in the shape of the other gates. A turn is flagged when it
    carries a security ACTION and either an ENDORSEMENT — in the same turn
    or in the co-host's adjacent reply — or is phrased as an ORDER.

    **Adjacent turns, not one turn.** Review of #57: the product is two
    hosts alternating, so the natural shape puts the report in one turn
    and the endorsement in the reply. A per-turn scan caught the monologue
    form and missed the dialogue form, in a product that is entirely
    dialogue — and the inbox contract this PR added ("name the domain and
    stop") makes the reporting turn compliant on its own, pushing the
    endorsement into the reply where nothing was looking.

    Reporting that a message asks for something is allowed and must stay
    allowed: that is journalism, and the segment's job. What is caught is
    the hosts adding their own weight, or issuing the instruction.
    """
    flagged = []
    for index, turn in enumerate(turns):
        if turn.segment.value not in _RELAY_SEGMENTS:
            continue
        text = _normalise_spaces(turn.text)
        action = _SECURITY_ACTION.search(text)
        if not action:
            continue

        if _is_an_order(text):
            flagged.append({
                "turn_index": index, "segment": turn.segment.value,
                "action": action.group(0).lower(), "endorsement": "(an order)",
                "reason": "the hosts issue a security instruction themselves",
            })
            continue

        # The co-host's reply, and the line that set this one up.
        window = [text]
        for neighbour in (index - 1, index + 1):
            if 0 <= neighbour < len(turns) and (
                turns[neighbour].segment is turn.segment
            ):
                window.append(_normalise_spaces(turns[neighbour].text))
        endorsement = next(
            (m for m in (_ENDORSEMENT.search(w) for w in window) if m), None
        )
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
