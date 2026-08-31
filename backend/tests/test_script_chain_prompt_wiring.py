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


# ──────────────────────────────────────────────────────────────
# The clock the hosts do not have
# ──────────────────────────────────────────────────────────────


async def test_the_outline_prompt_states_the_day_and_date():
    llm = await _run()
    assert "Friday, 21 August 2026" in llm.outline_prompt


async def test_the_outline_prompt_states_no_time_of_day():
    """`briefing_id` is a date, so `_parse_briefing_date` returns midnight and
    `{date:%H:%M}` was structurally 00:00 in every briefing ever generated.
    It was never a stale fact; it was never a fact.
    """
    llm = await _run()
    # The DATE line only. Event start/end isoformats legitimately carry
    # clock times further down the prompt.
    date_line = next(
        line for line in llm.outline_prompt.splitlines() if line.startswith("DATE:")
    )
    assert "00:00" not in date_line
    assert "local time" not in date_line
    assert date_line == "DATE: Friday, 21 August 2026"


async def test_a_generation_time_is_not_smuggled_in_either():
    """Not "fix the value" — remove it. Generation runs on an overnight cron
    and the briefing is heard hours later (ADR-001, ADR-010), so the server
    has no time worth stating: the phone knows when it is playing and the
    server does not."""
    llm = await _run(date=datetime(2026, 8, 21, 4, 12, tzinfo=UTC))
    date_line = next(
        line for line in llm.outline_prompt.splitlines() if line.startswith("DATE:")
    )
    assert "04:12" not in date_line


def _outline_segment_definition(name: str) -> str:
    """The whole `- <name> — …` block from OUTLINE_PROMPT, continuation lines
    folded into one string.

    Reading only the first physical line is what the earlier version of the
    test below did, and the definition is four lines long — so a positive
    instruction re-added on line two would not have been seen.
    """
    import re

    from app.pipeline.prompts import OUTLINE_PROMPT

    lines = OUTLINE_PROMPT.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"- {name}"))
    block = [lines[start]]
    for line in lines[start + 1:]:
        if line.startswith("- ") or not line.strip():
            break
        block.append(line)
    return re.sub(r"\s+", " ", " ".join(block)).strip()


def test_the_cold_open_is_not_asked_for_a_time_it_has_not_been_given():
    """Both halves have to move together. Leaving "state the day, date and
    time" in the prompt while removing the value makes it worse: the model
    derives or invents the time instead. That is exactly how the cold open
    once announced "Thursday" for a Friday — see the comment in
    generate_script about never making the model derive a stateable fact.

    Checked across the whole definition rather than its first line, and the
    prohibition is cut out before the search rather than searched for: the
    prohibition is itself the only place the word "time" is allowed to
    appear, so "is `time` absent?" over the raw block can only be satisfied
    by deleting the prohibition too.
    """
    import re

    section = _outline_segment_definition("cold_open")

    prohibition = re.search(r"Do NOT state a clock time\b[^.]*\.", section)
    assert prohibition, f"the cold open no longer forbids a clock time: {section}"

    remainder = section.replace(prohibition.group(0), "")
    assert "time" not in remainder.lower(), (
        f"the cold open is asked for a time somewhere else in its "
        f"definition: {remainder}"
    )


# ──────────────────────────────────────────────────────────────
# Counts the hosts will read out loud
# ──────────────────────────────────────────────────────────────


def _article(n: int, source_name: str) -> Item:
    return Item(
        source_type=SourceType.RSS,
        source_name=source_name,
        title=f"Northwind buys Halcyon, take {n}",
        body="Northwind confirmed the acquisition this morning.",
        url=f"https://example.com/story-{n}",
        timestamp=DATE,
    )


async def test_a_publication_that_ran_the_story_twice_is_still_one_source():
    """`cluster_size` counts MEMBERS. The label calls them sources.

    One publication running a follow-up on its own story is ordinary — the
    RSS ingestion comment already documents two feeds resolving to one
    publication — and it told the model three sources had covered a story
    one had. Rule 3: the hosts say nothing the source data does not support,
    and "three of them led with this" is exactly the kind of claim the
    prompt is supposed to make safe.
    """
    from app.core.schemas import Story

    members = [_article(n, "The Verge") for n in range(3)]
    story = Story(cluster_id="c1", canonical=members[0], members=members)
    llm = await _run(stories=[story])
    assert "Covered by 1 source(s): The Verge" in llm.outline_prompt


async def test_distinct_publications_are_still_counted_separately():
    """The discipline must not cost the signal. Three publications on one
    story is the whole ADR-003 promise."""
    from app.core.schemas import Story

    members = [
        _article(0, "The Verge"), _article(1, "Reuters"), _article(2, "BBC News")
    ]
    story = Story(cluster_id="c1", canonical=members[0], members=members)
    llm = await _run(stories=[story])
    assert "Covered by 3 source(s):" in llm.outline_prompt


async def test_the_remainder_example_uses_the_real_count():
    """The inbox policy handed the model a worked example with a hardcoded
    number in it. On a morning with four emails below the cut it still read
    "plus eleven others" — and spelled out, so `check_grounding` (which
    matches digits) would not have flagged the hosts repeating it.
    """
    llm = await _run(aggregated_email_count=4)
    prompt = llm.outline_prompt
    assert "plus 4 others" in prompt
    assert "eleven" not in prompt.lower()
