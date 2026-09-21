"""
The quality gates asked as judgments (ADR-015).

Written against what the heuristics get wrong, because that is the reason
the module exists. `quality.py` keeps running underneath and every test
here also pins the fallback: no provider must never mean no gate.
"""

from __future__ import annotations

import logging

import pytest

from app.core.providers import Claim
from app.core.schemas import Segment, Turn
from app.pipeline.quality import check_relayed_instructions, run_all_gates
from app.pipeline.semantic_quality import (
    check_relayed_semantic,
    run_all_gates_semantic,
)
from app.providers.fake_judgment import FakeClaimJudge

UNGROUNDED = "__UNGROUNDED__"
NO_CHALLENGE = "__NO_CHALLENGE__"
RELAYS = "__RELAYS__"

SOURCE = "TechCorp acquired DataFlow for $2.1 billion. The deal closes in Q1."


def turn(text, segment=Segment.WORLD, speaker="A"):
    return Turn(speaker=speaker, segment=segment, text=text)


# ── fallback: no provider must not mean no gate ───────────────────────


async def test_without_a_provider_the_report_is_the_heuristic_one():
    turns = [turn("Ben Thompson called it the end of the mid-market.")]
    assert await run_all_gates_semantic(turns, SOURCE, None) == run_all_gates(
        turns, SOURCE
    )


async def test_a_provider_that_raises_falls_back_to_the_heuristic():
    class Exploding:
        async def judge_claims(self, state, claims, *, timeout=20.0):
            raise RuntimeError("TypeSafe is down")

    turns = [turn("Ben Thompson called it the end of the mid-market.")]
    report = await run_all_gates_semantic(turns, SOURCE, Exploding())
    assert report == run_all_gates(turns, SOURCE)


async def test_the_relay_gate_alone_also_falls_back():
    turns = [turn("Go and verify your identity at that link, it's legitimate.",
                  Segment.INBOX)]
    assert await check_relayed_semantic(turns, SOURCE, None) == (
        check_relayed_instructions(turns)
    )


# ── the gates actually regrade ────────────────────────────────────────


async def test_a_judged_line_is_flagged_as_ungrounded():
    turns = [turn(f"A claim with no support {UNGROUNDED}.")]
    report = await run_all_gates_semantic(turns, SOURCE, FakeClaimJudge())
    assert [f["kind"] for f in report["grounding"]] == ["unsupported_claim"]
    assert report["grounding"][0]["turn_index"] == 0


async def test_a_supported_line_is_not_flagged():
    turns = [turn("TechCorp acquired DataFlow.")]
    report = await run_all_gates_semantic(turns, SOURCE, FakeClaimJudge())
    assert report["grounding"] == []


async def test_a_relayed_instruction_is_flagged():
    turns = [turn(f"Do what the sender asked {RELAYS}.", Segment.INBOX)]
    report = await run_all_gates_semantic(turns, SOURCE, FakeClaimJudge())
    assert [f["kind"] for f in report["relayed_instructions"]] == [
        "relayed_instruction"
    ]


async def test_a_segment_without_a_challenge_is_flagged():
    turns = [
        turn(f"Quite so {NO_CHALLENGE}.", Segment.INBOX, "A"),
        turn("Agreed.", Segment.INBOX, "B"),
    ]
    report = await run_all_gates_semantic(turns, SOURCE, FakeClaimJudge())
    assert [f["segment"] for f in report["disagreement"]] == ["inbox"]


async def test_total_issues_is_recomputed_from_the_judged_flags():
    turns = [
        turn(f"Unsupported {UNGROUNDED}.", Segment.INBOX, "A"),
        turn("A second line.", Segment.INBOX, "B"),
    ]
    report = await run_all_gates_semantic(turns, SOURCE, FakeClaimJudge())
    assert report["total_issues"] == (
        len(report["filler"]) + len(report["monologues"])
        + len(report["disagreement"]) + len(report["grounding"])
        + len(report["relayed_instructions"])
    )


# ── unjudged is not the same as judged-fine ───────────────────────────


async def test_an_unjudged_turn_keeps_the_heuristic_verdict():
    """`None` and 0.98 lead to opposite decisions and must not collapse.

    The heuristic flags "Thompson" on this line. If an unjudged claim were
    read as "fine", the gate would come back clean and the hallucinated
    attribution would ship unremarked.
    """
    turns = [turn("Ben Thompson called it the end of the mid-market.")]
    judge = FakeClaimJudge(unjudged_keys=frozenset({"ground:0"}))
    report = await run_all_gates_semantic(turns, SOURCE, judge)
    assert report["semantic_unjudged"] >= 1
    assert [f.get("token") for f in report["grounding"]] == ["Ben", "Thompson"]


async def test_everything_unjudged_is_reported_at_warning(caplog):
    turns = [turn("Anything at all.", Segment.INBOX)]
    keys = {"ground:0", "relay:0", "challenge:inbox"}
    judge = FakeClaimJudge(unjudged_keys=frozenset(keys))
    with caplog.at_level(logging.INFO, logger="app.pipeline.semantic_quality"):
        await run_all_gates_semantic(turns, SOURCE, judge)
    records = [r for r in caplog.records if "semantic quality gates:" in r.message]
    assert records and records[-1].levelno == logging.WARNING


# ── one request, not one per line ─────────────────────────────────────


async def test_the_whole_script_costs_one_request():
    turns = [turn(f"Line {i}.", Segment.INBOX) for i in range(12)]
    judge = FakeClaimJudge()
    await run_all_gates_semantic(turns, SOURCE, judge)
    assert len(judge.claim_calls) == 1
    state, claims = judge.claim_calls[0]
    # two per turn (grounding, relay) plus one per judgeable segment
    assert len(claims) == 24 + 1
    assert len(state["lines"]) == 12


async def test_the_state_carries_the_source_and_the_segments():
    turns = [turn("A line.", Segment.INBOX, "A"), turn("Another.", Segment.WORLD, "B")]
    judge = FakeClaimJudge()
    await run_all_gates_semantic(turns, SOURCE, judge)
    state, _ = judge.claim_calls[0]
    assert state["source_material"] == SOURCE
    assert set(state["segments"]) == {"inbox", "world"}
    assert "A: A line." in state["segments"]["inbox"]


async def test_claim_keys_are_unique():
    turns = [turn(f"Line {i}.", Segment.INBOX) for i in range(5)]
    judge = FakeClaimJudge()
    await run_all_gates_semantic(turns, SOURCE, judge)
    _, claims = judge.claim_calls[0]
    keys = [c.key for c in claims]
    assert len(keys) == len(set(keys))


async def test_an_empty_script_makes_no_call():
    judge = FakeClaimJudge()
    await run_all_gates_semantic([], SOURCE, judge)
    assert judge.claim_calls == []


# ── Claim plumbing ────────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["ground:0", "relay:3", "challenge:world"])
def test_claim_keys_round_trip(key):
    assert Claim(key=key, instructions="x").key == key
