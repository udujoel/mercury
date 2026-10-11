"""The weekday the hosts say is the weekday it is (ROADMAP task 48).

The 2026-10-11 06:41 briefing opened "It's Monday, October 11th, 2026"; it
was a Sunday. Only the outline pass is told the date. The draft and the
critique passes rewrite the cold open without it, and the keyword
grounding gate can't object, because "Monday" appears somewhere in 35
emails. This is the third weekday bug. A prompt can ask; only code can
check.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.schemas import Segment, Turn
from app.pipeline.quality import correct_spoken_date

SUNDAY = datetime(2026, 10, 11, tzinfo=UTC)


def _turn(text: str, segment: Segment = Segment.COLD_OPEN) -> Turn:
    return Turn(speaker="A", segment=segment, text=text)


@pytest.mark.parametrize(
    ("said", "fixed"),
    [
        # The production line, verbatim.
        (
            "Good morning, Joel. It's Monday, October 11th, 2026.",
            "Good morning, Joel. It's Sunday, October 11th, 2026.",
        ),
        ("It is Monday, the 11th of October.", "It is Sunday, the 11th of October."),
        ("Happy Saturday, Joel.", "Happy Sunday, Joel."),
        ("Today is Monday 11 October.", "Today is Sunday 11 October."),
        ("it's monday and the inbox is quiet", "it's Sunday and the inbox is quiet"),
    ],
)
def test_the_cold_open_names_the_real_weekday(said, fixed):
    turns, corrections = correct_spoken_date([_turn(said)], SUNDAY)

    assert turns[0].text == fixed
    assert len(corrections) == 1


def test_todays_date_is_corrected_wherever_it_is_said():
    """'Monday, October 11th' names today, whichever segment it is in."""
    said = "Your Monday, October 11th looks clear after ten."
    turns, _ = correct_spoken_date([_turn(said, Segment.CALENDAR)], SUNDAY)

    assert turns[0].text == "Your Sunday, October 11th looks clear after ten."


@pytest.mark.parametrize(
    ("said", "segment"),
    [
        # Already right.
        ("Good morning, Joel. It's Sunday, October 11th, 2026.", Segment.COLD_OPEN),
        # Another day, correctly named: tomorrow is a Monday.
        ("Your dentist is on Monday, October 12th, at nine.", Segment.CALENDAR),
        # A weekday with no date outside the cold open is about some other day.
        ("The vote moved to Tuesday.", Segment.CALENDAR),
    ],
)
def test_other_days_are_left_alone(said, segment):
    turns, corrections = correct_spoken_date([_turn(said, segment)], SUNDAY)

    assert turns[0].text == said
    assert corrections == []


def test_a_wrong_date_is_reported_not_rewritten():
    """'It's Monday, October 12th' on the 11th is wrong in its DATE, and
    swapping the weekday would make it wrong in a new way. Leave the words,
    report it."""
    said = "It's Monday, October 12th."
    turns, corrections = correct_spoken_date([_turn(said)], SUNDAY)

    assert turns[0].text == said
    assert corrections == [
        {"turn_index": 0, "said": "Monday, October 12th", "kind": "other_date"}
    ]


def test_the_turns_passed_in_are_not_mutated():
    original = _turn("It's Monday, October 11th.")
    correct_spoken_date([original], SUNDAY)

    assert original.text == "It's Monday, October 11th."


async def test_the_shipped_script_carries_the_corrected_weekday():
    """End to end through `generate_script`: an LLM that names the wrong
    weekday in every pass still ships the right one."""
    from app.core.schemas import BriefingLength
    from app.pipeline.script_chain import generate_script
    from tests.test_script_chain_prompt_wiring import (
        DATE,
        PromptCapturingLLM,
        _email,
        _event,
        _weather,
    )

    assert DATE.strftime("%A") == "Friday"

    class WrongWeekdayLLM(PromptCapturingLLM):
        async def complete(self, *a, **k):
            text = await super().complete(*a, **k)
            return text.replace("It's Friday", "It's Monday")

    turns, _ = await generate_script(
        llm=WrongWeekdayLLM(),
        emails=[_email(1)],
        events=[_event()],
        weather=_weather(),
        length=BriefingLength.STANDARD,
        date=DATE,
    )

    cold_open = " ".join(t.text for t in turns if t.segment == Segment.COLD_OPEN)
    assert "It's Monday" not in cold_open
    assert "It's Friday" in cold_open
