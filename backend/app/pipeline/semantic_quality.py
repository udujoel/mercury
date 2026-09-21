"""
The quality gates, asked as judgments instead of matched as keywords.

`app/pipeline/quality.py` stays exactly as it is and keeps running. This
module re-answers its three JUDGEABLE gates with Jev and leaves the two
mechanical ones (filler words, monologue length) alone — those are counting
problems, and a counting problem does not need a model.

Why these three. Each one's own docstring already says it is the wrong tool:

  check_grounding      "HEURISTIC ... a blunt instrument ... Treat it as a
                       starting point for a human or LLM critique pass, not
                       a verdict."
  check_disagreement   "HEURISTIC, not a semantic judge: it looks for
                       challenge-marker phrases."
  check_relayed_...    a vocabulary that review of #57 found missing 24 of
                       28 realistic relays, catching "confirm your payment"
                       while missing "verify your payment".

All three are one request. The claims are independent and share one state,
so a whole script costs a single round trip rather than one per line.

The heuristic remains the floor: a turn whose claim came back unjudged
keeps whatever `quality.py` said about it (rule 7).
"""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.core.providers import Claim, JudgmentProvider
from app.core.schemas import Turn
from app.pipeline.quality import (
    _DISAGREEMENT_SEGMENTS,
    check_relayed_instructions,
    run_all_gates,
)

log = logging.getLogger(__name__)

_GROUND = "ground"
_RELAY = "relay"
_CHALLENGE = "challenge"


def _state(turns: list[Turn], source_text: str) -> dict:
    """One state for all three gates.

    `lines` is indexed by turn position so a claim can point at exactly one
    line, and `segments` holds each segment's turns joined, because whether
    a host pushed back is a property of the exchange rather than of any
    single line in it.
    """
    segments: dict[str, str] = {}
    for turn in turns:
        segments.setdefault(turn.segment.value, "")
        speaker = turn.speaker
        segments[turn.segment.value] += f"\n{speaker}: {turn.text}"
    return {
        "source_material": source_text,
        "lines": [t.text for t in turns],
        "segments": segments,
    }


def _relay_claim(i: int) -> Claim:
    """The one claim that decides whether a script is safe to ship.

    Phrased as the two-condition rule `quality.py` arrived at the hard way:
    the hosts SAYING an email asks for something is journalism; the hosts
    telling the listener to do it, or vouching for it, is the product taking
    the sender's side.
    """
    return Claim(
        key=f"{_RELAY}:{i}",
        instructions=(
            f"In `lines[{i}]`, the speakers tell the listener to carry out "
            "an instruction that came from the sender of a message, or "
            "vouch for such an instruction as legitimate. Reporting that a "
            "sender asked for something, or warning the listener about it, "
            "does NOT count — only telling the listener to do it, or "
            "endorsing it, counts."
        ),
    )


def _claims(turns: list[Turn]) -> list[Claim]:
    claims: list[Claim] = []
    for i in range(len(turns)):
        claims.append(Claim(
            key=f"{_GROUND}:{i}",
            instructions=(
                f"Every factual claim in `lines[{i}]` is supported by "
                "`source_material`. Rewording, paraphrase and conversational "
                "delivery are fine. Inventing a name, a number, a date, a "
                "quotation or an attribution that is not in `source_material` "
                "is not. A line that makes no factual claim at all counts as "
                "supported."
            ),
        ))
        claims.append(_relay_claim(i))
    for segment in sorted({t.segment.value for t in turns}):
        if segment not in _DISAGREEMENT_SEGMENTS:
            continue
        claims.append(Claim(
            key=f"{_CHALLENGE}:{segment}",
            instructions=(
                f"Somewhere in `segments.{segment}`, one speaker challenges, "
                "disagrees with, pushes back on, qualifies or reframes what "
                "the other said. Two speakers who agree throughout, or who "
                "merely add separate facts, do NOT count."
            ),
        ))
    return claims


async def run_all_gates_semantic(
    turns: list[Turn], source_text: str, provider: JudgmentProvider | None
) -> dict:
    """`run_all_gates`, with the three judgeable gates re-answered by Jev.

    Same keys and same shape, so every existing caller and comparison keeps
    working. Never raises: any failure returns the heuristic report, which
    is what shipped before this module existed.
    """
    report = run_all_gates(turns, source_text)
    if provider is None or not turns:
        return report

    settings = get_settings()
    claims = _claims(turns)
    try:
        answers = await provider.judge_claims(
            _state(turns, source_text),
            claims,
            timeout=settings.judgment_timeout_seconds,
        )
    except Exception as exc:
        log.warning(
            "semantic quality gates failed (%s) — keeping the heuristic "
            "report for all %d turn(s)", exc, len(turns),
        )
        return report

    grounding, unjudged = _regrade_per_turn(
        turns, answers, report["grounding"], _GROUND,
        flag_when_below=settings.quality_grounding_threshold,
        kind="unsupported_claim",
    )
    relayed, relay_unjudged = _regrade_per_turn(
        turns, answers, report["relayed_instructions"], _RELAY,
        flag_when_above=settings.quality_relay_threshold,
        kind="relayed_instruction",
    )
    disagreement, seg_unjudged = _regrade_segments(
        turns, answers, report["disagreement"],
        settings.quality_challenge_threshold,
    )

    report["grounding"] = grounding
    report["relayed_instructions"] = relayed
    report["disagreement"] = disagreement
    report["total_issues"] = (
        len(report["filler"]) + len(report["monologues"])
        + len(disagreement) + len(grounding) + len(relayed)
    )
    total_unjudged = unjudged + relay_unjudged + seg_unjudged
    report["semantic"] = True
    report["semantic_unjudged"] = total_unjudged

    # WARNING when NOTHING was judged, for the same reason email triage
    # does it: a pass that answered nothing degrades to the heuristic
    # silently, and silence is what made the last one invisible.
    every_claim_unjudged = total_unjudged >= len(claims)
    (log.warning if every_claim_unjudged else log.info)(
        "semantic quality gates: %d claim(s), %d unjudged; "
        "grounding %d, relays %d, disagreement %d",
        len(claims), total_unjudged, len(grounding), len(relayed),
        len(disagreement),
    )
    return report


def _regrade_per_turn(
    turns: list[Turn],
    answers: dict[str, float | None],
    heuristic_flags: list[dict],
    prefix: str,
    *,
    kind: str,
    flag_when_below: float | None = None,
    flag_when_above: float | None = None,
) -> tuple[list[dict], int]:
    """Replace per-turn heuristic flags with judged ones, turn by turn.

    A turn whose claim came back `None` keeps the heuristic's flags for
    that turn rather than being treated as clean — "not judged" and "judged
    fine" lead to opposite decisions and must not collapse together.
    """
    if (flag_when_below is None) == (flag_when_above is None):
        # Not defensive padding: polarity is the thing most easily got
        # wrong here (grounding and challenge flag LOW, relay flags HIGH)
        # and it already cost a debugging round in the fake. Without this
        # a missing threshold surfaces as `float > None` from inside a
        # function whose contract is that gate failures never escape.
        raise ValueError(
            "_regrade_per_turn needs exactly one of flag_when_below / "
            f"flag_when_above (got below={flag_when_below!r}, "
            f"above={flag_when_above!r})"
        )

    by_turn: dict[int, list[dict]] = {}
    for flag in heuristic_flags:
        by_turn.setdefault(flag.get("turn_index", -1), []).append(flag)

    out: list[dict] = []
    unjudged = 0
    for i, turn in enumerate(turns):
        probability = answers.get(f"{prefix}:{i}")
        if probability is None:
            unjudged += 1
            out.extend(by_turn.get(i, []))
            continue
        hit = (
            probability < flag_when_below
            if flag_when_below is not None
            else probability > flag_when_above
        )
        if hit:
            out.append({
                "turn_index": i,
                "segment": turn.segment.value,
                "kind": kind,
                "probability": round(probability, 3),
                "reason": f"judged by Jev, not matched by keyword ({kind})",
            })
    return out, unjudged


def _regrade_segments(
    turns: list[Turn],
    answers: dict[str, float | None],
    heuristic_flags: list[dict],
    threshold: float,
) -> tuple[list[dict], int]:
    by_segment = {f.get("segment"): f for f in heuristic_flags}
    present = sorted({t.segment.value for t in turns})

    out: list[dict] = []
    unjudged = 0
    for segment in present:
        if segment not in _DISAGREEMENT_SEGMENTS:
            continue
        probability = answers.get(f"{_CHALLENGE}:{segment}")
        if probability is None:
            unjudged += 1
            if segment in by_segment:
                out.append(by_segment[segment])
            continue
        if probability < threshold:
            out.append({
                "segment": segment,
                "kind": "no_challenge",
                "probability": round(probability, 3),
                "reason": "no host challenges or reframes the other (judged)",
            })
    return out, unjudged


async def check_relayed_semantic(
    turns: list[Turn], source_text: str, provider: JudgmentProvider | None
) -> list[dict]:
    """The relay gate on its own, for the last-word check before shipping.

    Separate from `run_all_gates_semantic` because that one runs inside the
    critique pass, and review of #57 established that EVERY early return
    above the critique skips it — an invalid revision, a refused critique, a
    raised exception, `passes` below 4. The draft is what ships on those
    paths, and nothing was scanning it. So this runs unconditionally on the
    final turns and pays for its own request.

    Falls back to `check_relayed_instructions` rather than to an empty list:
    no provider must not mean no gate.
    """
    if provider is None or not turns:
        return check_relayed_instructions(turns)

    settings = get_settings()
    claims = [_relay_claim(i) for i in range(len(turns))]
    try:
        answers = await provider.judge_claims(
            _state(turns, source_text),
            claims,
            timeout=settings.judgment_timeout_seconds,
        )
    except Exception as exc:
        log.warning(
            "semantic relay gate failed (%s) — falling back to the keyword "
            "list for all %d turn(s)", exc, len(turns),
        )
        return check_relayed_instructions(turns)

    flags, unjudged = _regrade_per_turn(
        turns, answers, check_relayed_instructions(turns), _RELAY,
        flag_when_above=settings.quality_relay_threshold,
        kind="relayed_instruction",
    )
    if unjudged:
        log.warning(
            "semantic relay gate: %d of %d turn(s) unjudged, keyword list "
            "used for those", unjudged, len(turns),
        )
    return flags


__all__ = ["run_all_gates_semantic", "check_relayed_semantic"]
