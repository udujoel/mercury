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


# ── review findings (PR #61) ──────────────────────────────────────────


async def test_the_briefing_budget_is_shared_across_calls():
    """Regression: the ceiling bounded one call, and a briefing makes four.

    Production runs passes=4, so triage, the gates on the draft, the gates
    on the revision and the final relay check are four separate calls. Four
    25s ceilings is 100s against a 90s budget. The provider is built once
    per briefing, so the budget lives on the instance.
    """
    import time

    import typesafe_sdk

    from app.core.config import Settings
    from app.providers import typesafe_jev

    class _Hanging:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False
        async def system_one(self, *, state, questions):
            import asyncio
            await asyncio.sleep(3600)

    import pytest as _pytest
    monkeypatch = _pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            "app.core.config.get_settings",
            lambda: Settings(
                typesafe_api_key="k",
                judgment_concurrency=2,
                judgment_timeout_seconds=30.0,
                judgment_total_budget_seconds=30.0,   # per call: generous
                judgment_briefing_budget_seconds=0.4,  # per briefing: tight
            ),
        )
        monkeypatch.setattr(typesafe_sdk, "AsyncTypeSafeClient", _Hanging)
        monkeypatch.setattr(typesafe_jev, "_questions", lambda: {})
        provider = typesafe_jev.JevJudgmentProvider()

        started = time.perf_counter()
        # Four calls, as a real briefing makes. Without a shared budget each
        # would wait out its own 30s ceiling.
        await provider.triage_emails([{"subject": "a"}])
        for _ in range(3):
            await provider.judge_claims({"lines": ["x"]}, [Claim("k", "i")])
        elapsed = time.perf_counter() - started

        assert elapsed < 3.0, f"briefing budget not shared: took {elapsed:.1f}s"
    finally:
        monkeypatch.undo()


async def test_a_spent_budget_returns_unjudged_rather_than_waiting():
    """A spent budget must SHORT-CIRCUIT, not merely fail.

    Asserting only on the `None` result is not enough: a blocked network
    produces the same `None`, so the first version of this test passed with
    the guard reverted. The client is therefore replaced with one that
    raises if it is constructed at all — the point is that no request is
    attempted once the budget is gone.
    """
    import typesafe_sdk

    from app.core.config import Settings
    from app.providers import typesafe_jev

    class _Forbidden:
        def __init__(self, *a, **k):
            raise AssertionError(
                "a client was built although the briefing budget was spent"
            )

    import pytest as _pytest
    monkeypatch = _pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            "app.core.config.get_settings",
            lambda: Settings(
                typesafe_api_key="k", judgment_briefing_budget_seconds=-1.0
            ),
        )
        monkeypatch.setattr(typesafe_sdk, "AsyncTypeSafeClient", _Forbidden)
        provider = typesafe_jev.JevJudgmentProvider()
        got = await provider.judge_claims({"lines": ["x"]}, [Claim("a", "i")])
        assert got == {"a": None}
        assert await provider.triage_emails([{"subject": "s"}]) == [None]
    finally:
        monkeypatch.undo()


async def test_a_judged_report_is_distinguishable_from_a_fallback_one():
    """The signal the draft/revision comparison keys on.

    If a fallback report were indistinguishable from a judged one, the
    comparison in `_run_critique_and_revise` could not tell that the two
    sides were scored on different scales.
    """
    turns = [turn("A line.", Segment.INBOX)]
    judged = await run_all_gates_semantic(turns, SOURCE, FakeClaimJudge())
    fell_back = await run_all_gates_semantic(turns, SOURCE, None)
    assert judged.get("semantic") is True
    assert fell_back.get("semantic") is None


async def test_a_one_sided_failure_regrades_both_with_the_heuristic(caplog):
    """Regression: a semantic count was compared against a heuristic one.

    Each gate call falls back independently, so a transient failure on the
    revision alone put Jev's flag count (2 on the verification script) next
    to the keyword pass's (3, one a false positive) and decided which
    script ships on the difference.
    """
    import json

    from app.pipeline.script_chain import (
        _parse_draft_turns,
        _run_critique_and_revise,
    )
    from app.providers.fake import FakeLLMProvider, _draft_json

    class _SucceedsThenFails:
        def __init__(self) -> None:
            self.calls = 0

        async def judge_claims(self, state, claims, *, timeout=20.0):
            self.calls += 1
            if self.calls == 1:
                return {c.key: 0.98 for c in claims}
            raise RuntimeError("transient failure on the revision")

    # The known-good draft the fake LLM produces a VALID revision for. A
    # hand-rolled two-line draft returns early on "revision invalid" and
    # never reaches the comparison this test is about.
    draft_turns = _parse_draft_turns(json.loads(_draft_json()))
    budget = sum(len(t.text.split()) for t in draft_turns)
    with caplog.at_level(logging.WARNING, logger="app.pipeline.script_chain"):
        await _run_critique_and_revise(
            date="Friday, 21 August 2026",
            llm=FakeLLMProvider(),
            draft_turns=draft_turns,
            source_data="source",
            budget=budget,
            judgment_provider=_SucceedsThenFails(),
        )
    assert any(
        "graded the draft and the revision differently" in r.message
        for r in caplog.records
    )


def test_a_gate_with_no_polarity_is_refused():
    """Latent: `probability > None` from inside a never-raises function."""
    from app.pipeline.semantic_quality import _regrade_per_turn

    with pytest.raises(ValueError, match="exactly one of flag_when_below"):
        _regrade_per_turn(
            [turn("x")], {"ground:0": 0.5}, [], "ground", kind="k",
        )


# ── review findings (2026-10-11, ROADMAP task 47) ─────────────────────


class _JevAnswer:
    def __init__(self, value: float):
        self.noul = value


def _fake_jev_client(on_call=None, value: float = 0.9):
    """An `AsyncTypeSafeClient` stand-in that answers every question with
    `value`. `on_call` runs inside each request (to advance a fake clock,
    say, or to await something)."""

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False
        async def system_one(self, *, state, questions):
            if on_call is not None:
                await on_call()
            return type(
                "R", (), {"nouls": {q: _JevAnswer(value) for q in questions}}
            )()

    return _Client


def _jev_provider(monkeypatch, client, clock, budget: float = 20.0):
    import typesafe_sdk

    from app.core.config import Settings
    from app.providers import typesafe_jev

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: Settings(
            typesafe_api_key="k", judgment_briefing_budget_seconds=budget
        ),
    )
    monkeypatch.setattr(typesafe_sdk, "AsyncTypeSafeClient", client)
    # The module's own clock seam, not `time.monotonic`: asyncio's event
    # loop reads `time.monotonic` for every timer, so freezing it there
    # would freeze the loop's timeouts too.
    monkeypatch.setattr(typesafe_jev, "_clock", lambda: clock[0])
    return typesafe_jev.JevJudgmentProvider()


async def test_time_between_calls_does_not_spend_the_budget(monkeypatch):
    """The budget is time spent JUDGING, not time since the first judgment.

    It was a wall-clock window opened by the first call. Triage and
    cluster verification make that call before the script exists, and the
    semantic gates run about two minutes later, after script generation. So
    in every production run checked (2026-10-08 to 2026-10-11) the window
    had closed before the gates ran: 67-93 claims a run, all unjudged, and
    rule 3's grounding gate only ever ran as its keyword fallback.
    """
    clock = [1000.0]
    asked = []

    async def record():
        asked.append(1)

    provider = _jev_provider(monkeypatch, _fake_jev_client(record), clock)

    # Cluster verification, before the script exists.
    first = await provider.judge_claims({"x": 1}, [Claim("verify:0", "i")])
    # Script generation: two minutes in which nothing is judged.
    clock[0] += 120.0
    # The semantic gates, on the finished script.
    second = await provider.judge_claims({"x": 1}, [Claim("ground:0", "i")])

    assert first == {"verify:0": 0.9}
    assert second == {"ground:0": 0.9}, (
        "the gates were refused a budget that judging had barely touched"
    )
    assert len(asked) == 2


async def test_time_spent_judging_still_spends_the_budget(monkeypatch):
    """The other half: the ceiling still holds across calls, counted in time
    actually spent inside them."""
    import typesafe_sdk

    clock = [1000.0]

    async def fifteen_seconds():
        clock[0] += 15.0

    provider = _jev_provider(monkeypatch, _fake_jev_client(fifteen_seconds), clock)
    await provider.judge_claims({}, [Claim("a", "i")])  # 15s spent
    await provider.judge_claims({}, [Claim("b", "i")])  # 30s spent

    class _Forbidden:
        def __init__(self, *a, **k):
            raise AssertionError("a third call was made past the budget")

    monkeypatch.setattr(typesafe_sdk, "AsyncTypeSafeClient", _Forbidden)
    assert await provider.judge_claims({}, [Claim("c", "i")]) == {"c": None}


async def test_overlapping_calls_are_charged_once(monkeypatch):
    """Two judgment calls running at the same time cost the wall time they
    overlap, not the sum of both. Every call is awaited in turn today, so
    this guards the first change that gathers two of them."""
    import asyncio

    clock = [1000.0]
    both_inside = asyncio.Event()
    inside = []

    async def overlap():
        inside.append(1)
        if len(inside) == 2:
            clock[0] += 12.0  # twelve seconds pass while BOTH are in flight
            both_inside.set()
        await both_inside.wait()

    provider = _jev_provider(monkeypatch, _fake_jev_client(overlap), clock)
    await asyncio.gather(
        provider.judge_claims({}, [Claim("a", "i")]),
        provider.judge_claims({}, [Claim("b", "i")]),
    )

    assert provider._remaining() == pytest.approx(8.0), (
        "12 seconds of overlapping judging was charged as more than 12"
    )


async def test_triage_time_is_charged_to_the_same_budget(monkeypatch):
    """Triage spends from the same budget as the gates. It is the first
    judgment call of a briefing, so it is the one most likely to use the
    budget up."""
    import typesafe_sdk

    from app.providers import typesafe_jev

    clock = [1000.0]

    class _Scored:
        score = 0.5
        confidence = 0.5

    class _TriageClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False
        async def system_one(self, *, state, questions):
            clock[0] += 18.0  # triage takes eighteen of the twenty seconds
            return type("R", (), {
                "scores": {"consequence": _Scored()},
                "nouls": {"awaiting_reply": _JevAnswer(0.1), "bulk": _JevAnswer(0.1)},
            })()

    provider = _jev_provider(monkeypatch, _TriageClient, clock)
    monkeypatch.setattr(typesafe_jev, "_questions", lambda: {})
    judged = await provider.triage_emails([{"subject": "s"}])

    assert judged[0] is not None
    assert provider._remaining() == pytest.approx(2.0)
    monkeypatch.setattr(typesafe_sdk, "AsyncTypeSafeClient", _fake_jev_client())
    # Two seconds are left, so the gates are still asked.
    assert await provider.judge_claims({}, [Claim("g", "i")]) == {"g": 0.9}
