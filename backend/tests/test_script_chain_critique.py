"""
Tests for the Phase 2 four-pass script chain (outline -> draft -> critique
-> revise) in app/pipeline/script_chain.py.

Two levels of coverage:

  1. End-to-end against FakeLLMProvider with `passes=4` — this exercises
     CRITIQUE_PROMPT / REVISE_PROMPT verbatim (via the fake's canned
     responses) and asserts revision actually REMOVES the two defects
     FakeLLMProvider deliberately plants in its draft (a 7-sentence
     monologue and an over-agreeable calendar exchange). A test that only
     checks the chain ran proves nothing — these assert the specific
     before/after change.

  2. Unit tests of `_run_critique_and_revise` against a small scripted
     provider, covering the CLAUDE.md rule 7 degradation paths: critique
     fails to parse, revision fails to parse, revision is structurally
     invalid (drops a segment), and revision scores worse on the quality
     gates than the draft. Each must fall back to the draft rather than
     failing the run.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.schemas import (
    BriefingLength,
    CalendarEvent,
    Item,
    Priority,
    Segment,
    SourceType,
    Turn,
    Weather,
)
from app.pipeline import quality
from app.pipeline.script_chain import (
    _run_critique_and_revise,
    generate_script,
)
from app.providers.fake import FakeLLMProvider

DATE = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)


def make_email() -> Item:
    return Item(
        source_type=SourceType.EMAIL,
        source_name="Priya Sharma",
        title="Rollout plan sign-off",
        body="Can you review the provider upgrade rollout plan before nine?",
        author="priya@acmecorp.com",
        timestamp=DATE,
        priority=Priority.HIGH,
        requires_response=True,
    )


def make_event() -> CalendarEvent:
    return CalendarEvent(
        id="evt1", title="Provider upgrade review", start=DATE, end=DATE,
    )


def make_weather() -> Weather:
    return Weather(
        temperature_c=16, condition="clear sky", high_c=24, low_c=14,
        location_name="Aveiro",
    )


# ──────────────────────────────────────────────────────────────
# End-to-end, passes=4, against FakeLLMProvider
# ──────────────────────────────────────────────────────────────


async def test_four_passes_invokes_outline_draft_critique_revise():
    llm = FakeLLMProvider()
    turns, source_refs = await generate_script(
        llm=llm,
        emails=[make_email()],
        events=[make_event()],
        weather=make_weather(),
        length=BriefingLength.STANDARD,
        date=DATE,
        passes=4,
    )
    kinds = [kind for kind, _ in llm.calls]
    assert kinds == ["outline", "draft", "critique", "revise"]
    assert turns
    assert isinstance(source_refs, list)


async def test_two_passes_is_still_the_default_and_skips_critique_revise():
    llm = FakeLLMProvider()
    turns, _ = await generate_script(
        llm=llm,
        emails=[make_email()],
        events=[make_event()],
        weather=make_weather(),
        length=BriefingLength.STANDARD,
        date=DATE,
        # passes not given — must default to 2, matching every existing
        # caller's expectations (app/pipeline/generate.py and the Phase 1
        # test suite).
    )
    kinds = [kind for kind, _ in llm.calls]
    assert kinds == ["outline", "draft"]
    assert turns


async def test_invalid_passes_value_raises():
    llm = FakeLLMProvider()
    with pytest.raises(ValueError, match="passes must be 2 or 4"):
        await generate_script(
            llm=llm,
            emails=[make_email()],
            events=[make_event()],
            weather=make_weather(),
            length=BriefingLength.STANDARD,
            date=DATE,
            passes=3,
        )


async def test_revision_removes_planted_monologue_defect():
    llm = FakeLLMProvider()
    turns, _ = await generate_script(
        llm=llm,
        emails=[make_email()],
        events=[make_event()],
        weather=make_weather(),
        length=BriefingLength.STANDARD,
        date=DATE,
        passes=4,
    )
    # FakeLLMProvider's draft plants a 7-sentence monologue in the world
    # segment. The revised script it hands back splits it into three short
    # turns. Assert the FINAL script (what generate_script returns) is
    # clean, not merely that the chain ran.
    monologues = quality.check_monologues(turns)
    assert monologues == [], f"revision should have removed the monologue: {monologues}"


async def test_revision_removes_planted_agreeable_calendar_defect():
    llm = FakeLLMProvider()
    turns, _ = await generate_script(
        llm=llm,
        emails=[make_email()],
        events=[make_event()],
        weather=make_weather(),
        length=BriefingLength.STANDARD,
        date=DATE,
        passes=4,
    )
    disagreement = quality.check_disagreement(turns)
    flagged_segments = {g["segment"] for g in disagreement}
    assert "calendar" not in flagged_segments, (
        f"revision should have added a challenge to the calendar segment: "
        f"{disagreement}"
    )


async def test_revised_script_scores_no_worse_than_draft_on_all_gates():
    """The end-to-end assertion the brief asks for: run the gates on both
    draft and final, and confirm revision actually improved (or at worst
    did not regress) the total issue count."""
    llm = FakeLLMProvider()

    # Reconstruct what the draft alone would have scored, by running the
    # chain at passes=2 first (draft only) against a fresh fake instance
    # with the same deterministic canned output.
    draft_llm = FakeLLMProvider()
    draft_turns, _ = await generate_script(
        llm=draft_llm,
        emails=[make_email()],
        events=[make_event()],
        weather=make_weather(),
        length=BriefingLength.STANDARD,
        date=DATE,
        passes=2,
    )
    final_turns, _ = await generate_script(
        llm=llm,
        emails=[make_email()],
        events=[make_event()],
        weather=make_weather(),
        length=BriefingLength.STANDARD,
        date=DATE,
        passes=4,
    )

    source_text = "Priya Sharma priya@acmecorp.com Provider upgrade review"
    draft_report = quality.run_all_gates(draft_turns, source_text)
    final_report = quality.run_all_gates(final_turns, source_text)

    assert draft_report["monologues"], "draft should contain the planted monologue"
    assert not final_report["monologues"]
    assert final_report["total_issues"] <= draft_report["total_issues"]


# ──────────────────────────────────────────────────────────────
# _run_critique_and_revise — degradation paths (CLAUDE.md rule 7)
# ──────────────────────────────────────────────────────────────


def _canonical_turns(agreeable_calendar: bool = False) -> list[Turn]:
    """A small, valid, canonical-order script covering all seven segments,
    used as the draft input for the unit tests below. `agreeable_calendar`
    plants the same kind of defect FakeLLMProvider plants, for tests that
    need the draft to already have an issue."""
    calendar_b_text = "Sounds good, agreed." if agreeable_calendar else (
        "Actually, I'd push that back — the room's still booked then."
    )
    return [
        Turn(speaker="A", segment=Segment.COLD_OPEN,
             text="Good morning, Joel. It's Friday, the twenty-first."),
        Turn(speaker="A", segment=Segment.WEATHER,
             text="Clear skies, sixteen degrees this morning."),
        Turn(speaker="A", segment=Segment.CALENDAR,
             text="Provider upgrade review at nine with Priya."),
        Turn(speaker="B", segment=Segment.CALENDAR, text=calendar_b_text),
        Turn(speaker="A", segment=Segment.INBOX,
             text="Priya sent the rollout plan overnight."),
        Turn(speaker="A", segment=Segment.WORLD,
             text="Stratechery published a piece on bundling this week."),
        Turn(speaker="A", segment=Segment.FOLLOW_UPS,
             text="Nothing outstanding from yesterday."),
        Turn(speaker="A", segment=Segment.SIGN_OFF,
             text="Have a good one, Joel."),
    ]


def _word_count(turns: list[Turn]) -> int:
    return sum(len(t.text.split()) for t in turns)


class ScriptedLLMProvider:
    """A minimal, fully controllable LLMProvider for exercising the
    critique/revise degradation paths in isolation. Dispatches on the same
    fixed strings FakeLLMProvider uses (unique to each prompt template in
    app.pipeline.prompts), but the caller supplies the exact critique/revise
    response bodies rather than canned ones."""

    def __init__(self, *, critique_response: str, revise_response: str) -> None:
        self.critique_response = critique_response
        self.revise_response = revise_response
        self.calls: list[str] = []

    async def complete(self, system, prompt, *, temperature=0.85, json_mode=False):
        if "Review this draft script critically" in prompt:
            self.calls.append("critique")
            return self.critique_response
        if "Apply these fixes to the script" in prompt:
            self.calls.append("revise")
            return self.revise_response
        raise AssertionError(f"unexpected prompt in test harness: {prompt[:60]!r}")

    async def stream(self, system, prompt, *, temperature=0.85):
        yield await self.complete(system, prompt, temperature=temperature)


async def test_critique_parse_failure_falls_back_to_draft():
    draft = _canonical_turns()
    budget = _word_count(draft)
    llm = ScriptedLLMProvider(
        critique_response="not JSON at all, sorry",
        revise_response="[]",  # never reached
    )
    result = await _run_critique_and_revise(
        llm=llm, draft_turns=draft, source_data="irrelevant", budget=budget,
    )
    assert result == draft
    assert llm.calls == ["critique"], (
        "revise must not be called after a critique failure"
    )


async def test_revise_parse_failure_falls_back_to_draft():
    draft = _canonical_turns()
    budget = _word_count(draft)
    llm = ScriptedLLMProvider(
        critique_response='{"issues": []}',
        revise_response="also not JSON",
    )
    result = await _run_critique_and_revise(
        llm=llm, draft_turns=draft, source_data="irrelevant", budget=budget,
    )
    assert result == draft
    assert llm.calls == ["critique", "revise"]


async def test_revise_missing_segment_falls_back_to_draft():
    import json

    draft = _canonical_turns()
    budget = _word_count(draft)
    # Valid JSON, valid turns, but drops the "world" segment entirely —
    # must be treated as an invalid revision, not silently accepted.
    incomplete = [
        {"speaker": t.speaker, "segment": t.segment.value, "text": t.text}
        for t in draft
        if t.segment != Segment.WORLD
    ]
    llm = ScriptedLLMProvider(
        critique_response='{"issues": []}',
        revise_response=json.dumps(incomplete),
    )
    result = await _run_critique_and_revise(
        llm=llm, draft_turns=draft, source_data="irrelevant", budget=budget,
    )
    assert result == draft


async def test_revise_empty_array_falls_back_to_draft():
    draft = _canonical_turns()
    budget = _word_count(draft)
    llm = ScriptedLLMProvider(
        critique_response='{"issues": []}',
        revise_response="[]",
    )
    result = await _run_critique_and_revise(
        llm=llm, draft_turns=draft, source_data="irrelevant", budget=budget,
    )
    assert result == draft


async def test_revise_outside_word_budget_falls_back_to_draft():
    import json

    draft = _canonical_turns()
    budget = _word_count(draft)
    # Valid, complete, canonical-order script, but wildly over budget.
    bloated = [
        {"speaker": t.speaker, "segment": t.segment.value,
         "text": (t.text + " ") * 10}
        for t in draft
    ]
    llm = ScriptedLLMProvider(
        critique_response='{"issues": []}',
        revise_response=json.dumps(bloated),
    )
    result = await _run_critique_and_revise(
        llm=llm, draft_turns=draft, source_data="irrelevant", budget=budget,
    )
    assert result == draft


async def test_revise_that_scores_worse_than_draft_falls_back_to_draft():
    import json

    # Draft already has zero issues (no agreeable-calendar defect planted).
    draft = _canonical_turns(agreeable_calendar=False)
    budget = _word_count(draft)
    source_text = " ".join(t.text for t in draft)  # everything "grounded"

    # A structurally valid revision that is nonetheless WORSE: it introduces
    # a filler word that wasn't in the draft.
    worse = [
        {"speaker": t.speaker, "segment": t.segment.value, "text": t.text}
        for t in draft
    ]
    worse[0]["text"] = "Um, good morning, Joel. It's Friday, the twenty-first."

    llm = ScriptedLLMProvider(
        critique_response='{"issues": []}',
        revise_response=json.dumps(worse),
    )
    result = await _run_critique_and_revise(
        llm=llm, draft_turns=draft, source_data=source_text, budget=budget,
    )
    assert result == draft, "a revision that scores worse must be rejected"


async def test_revise_that_improves_on_draft_is_accepted():
    import json

    # Draft has the agreeable-calendar defect planted.
    draft = _canonical_turns(agreeable_calendar=True)
    budget = _word_count(draft)
    source_text = " ".join(t.text for t in draft)

    fixed = [
        {"speaker": t.speaker, "segment": t.segment.value, "text": t.text}
        for t in draft
    ]
    # Replace the agreeable line with a genuine challenge.
    for turn in fixed:
        if turn["text"] == "Sounds good, agreed.":
            turn["text"] = (
                "Actually, hold on — is the room definitely free at nine?"
            )

    llm = ScriptedLLMProvider(
        critique_response='{"issues": []}',
        revise_response=json.dumps(fixed),
    )
    result = await _run_critique_and_revise(
        llm=llm, draft_turns=draft, source_data=source_text, budget=budget,
    )
    result_texts = [t.text for t in result]
    assert "Sounds good, agreed." not in result_texts
    assert any("hold on" in t for t in result_texts)
