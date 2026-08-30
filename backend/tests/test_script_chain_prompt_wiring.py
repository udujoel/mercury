"""
What actually reaches the model.

Every defect covered here was a wiring bug, not a logic bug: something the
pipeline computed correctly and then failed to hand to the prompt, or handed
over in a shape that made the hosts say something false. None of them could
be caught by testing the functions in isolation, and the e2e test could not
catch them either because it injects a fake `generate_script`. So these
capture the real prompt strings the real chain builds.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.schemas import (
    BriefingLength,
    CalendarEvent,
    Item,
    Priority,
    SourceType,
    Weather,
)
from app.pipeline.quality import check_grounding
from app.pipeline.script_chain import (
    SOURCE_FENCE_CLOSE,
    SOURCE_FENCE_OPEN,
    _flatten_source_text,
    generate_script,
)
from app.providers.fake import FakeLLMProvider

DATE = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)


class PromptCapturingLLM(FakeLLMProvider):
    """FakeLLMProvider that also keeps every prompt it was handed."""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    async def complete(self, system, prompt, *, temperature=0.85,
                       json_mode=False, response_schema=None):
        self.prompts.append(prompt)
        return await super().complete(
            system, prompt, temperature=temperature, json_mode=json_mode,
            response_schema=response_schema,
        )

    @property
    def outline_prompt(self) -> str:
        return next(p for p in self.prompts if "Produce an OUTLINE" in p)

    @property
    def draft_prompt(self) -> str:
        return next(p for p in self.prompts if "Write the full script" in p)


def _email(n: int) -> Item:
    return Item(
        source_type=SourceType.EMAIL,
        source_name=f"Sender {n}",
        title=f"Subject {n}",
        body=f"Body of message {n}.",
        author=f"s{n}@example.com",
        timestamp=DATE,
        priority=Priority.MEDIUM,
    )


def _event() -> CalendarEvent:
    return CalendarEvent(
        id="evt1",
        title="Provider upgrade review",
        start=datetime(2026, 8, 21, 9, 0, tzinfo=UTC),
        end=datetime(2026, 8, 21, 9, 30, tzinfo=UTC),
    )


def _weather() -> Weather:
    return Weather(
        temperature_c=16, condition="clear sky", high_c=24, low_c=14,
        location_name="Aveiro",
    )


async def _run(**kwargs) -> PromptCapturingLLM:
    llm = PromptCapturingLLM()
    await generate_script(
        llm=llm,
        emails=kwargs.pop("emails", [_email(1)]),
        events=kwargs.pop("events", [_event()]),
        weather=kwargs.pop("weather", _weather()),
        length=BriefingLength.STANDARD,
        date=kwargs.pop("date", DATE),
        **kwargs,
    )
    return llm


# ──────────────────────────────────────────────────────────────
# The rest of the inbox
# ──────────────────────────────────────────────────────────────


async def test_the_unread_remainder_reaches_the_outline_prompt():
    """`select_for_briefing` has always computed `aggregated_count` — how
    many emails were below the PRD F-05 cut — and nothing ever passed it on.
    Most of the inbox vanished with no acknowledgement at all.
    """
    llm = await _run(emails=[_email(i) for i in range(3)], aggregated_email_count=11)
    assert "11" in llm.outline_prompt


async def test_the_remainder_instruction_sits_outside_the_untrusted_fence():
    """How to treat the remainder is OUR instruction, not source data. The
    calendar policy learned this the hard way: fenced along with the block it
    describes, it tells the model to follow a rule and to distrust it in the
    same breath."""
    llm = await _run(aggregated_email_count=11)
    prompt = llm.outline_prompt
    instruction_at = prompt.index("11 further")
    last_fence_close = prompt.rindex(SOURCE_FENCE_CLOSE, 0, instruction_at)
    last_fence_open = prompt.rindex(SOURCE_FENCE_OPEN, 0, instruction_at)
    assert last_fence_close > last_fence_open, (
        "the remainder instruction is inside an untrusted fence"
    )


async def test_the_remainder_count_is_grounded_in_the_source_data():
    """The grounding gate flags any number in the script that is absent from
    the source block. Put the count only in the outline and the hosts saying
    "plus eleven others" is flagged as an invention by the very pass that
    exists to catch inventions."""
    source = _flatten_source_text([_email(1)], [_event()], _weather(), 11)
    assert "11" in source


def test_grounding_gate_accepts_the_remainder_count():
    from app.core.schemas import Segment, Turn

    source = _flatten_source_text([_email(1)], [_event()], _weather(), 11)
    turns = [
        Turn(
            speaker="A",
            segment=Segment.INBOX,
            text="Plus 11 others, none of them urgent.",
        )
    ]
    numbers = [f for f in check_grounding(turns, source) if f["kind"] == "number"]
    assert numbers == []


async def test_no_remainder_means_no_remainder_instruction():
    llm = await _run(aggregated_email_count=0)
    assert "further emails" not in llm.outline_prompt


def test_no_remainder_leaves_the_source_block_unchanged():
    with_zero = _flatten_source_text([_email(1)], [_event()], _weather(), 0)
    without = _flatten_source_text([_email(1)], [_event()], _weather())
    assert with_zero == without
