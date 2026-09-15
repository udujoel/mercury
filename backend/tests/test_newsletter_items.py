"""A newsletter is a bundle; these tests are about taking it apart.

The premise, in one sentence: two newsletters that both lead with the Fed
never cluster, because each embedding is dominated by its own four-way
mixture of unrelated stories. That is why cross-source dedup fired 8 times in
5,590 stories — not because Joel's sources do not overlap, but because the
unit being compared was a bundle.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime

from app.core.providers import ContentBlocked, LLMProvider
from app.core.schemas import Item, SourceType
from app.pipeline.newsletter_items import (
    MAX_ITEMS_PER_NEWSLETTER,
    NewsletterItem,
    extract_items,
    merge_by_subject,
    rank,
)


def _newsletter(name: str, title: str, body: str = "body", hours_ago: int = 0) -> Item:
    return Item(
        source_type=SourceType.EMAIL,
        source_name=name,
        title=title,
        body=body,
        author=f"{name.replace(' ', '').lower()}@example.test",
        timestamp=datetime(2026, 9, 15, 6 - hours_ago, 0, tzinfo=UTC),
        raw={"is_newsletter": True},
    )


def _item(subject: str, source: str, headline: str = "H", hours_ago: int = 0):
    return NewsletterItem(
        headline=headline, gist="G", subject=subject, source_name=source,
        received_at=datetime(2026, 9, 15, 6 - hours_ago, 0, tzinfo=UTC),
    )


class _Extractor:
    """Returns a scripted payload. Signature matches `LLMProvider` exactly.

    Spelled out rather than `**kwargs`: a fake that accepts anything cannot
    fail when the real signature changes, which is this project's most
    expensive recurring bug.
    """

    def __init__(self, payload, *, blocked: bool = False) -> None:
        self.payload = payload
        self.blocked = blocked
        self.prompts: list[str] = []

    async def complete(
        self, system: str, prompt: str, *, temperature: float = 0.85,
        json_mode: bool = False, response_schema: dict | None = None,
    ) -> str:
        self.prompts.append(prompt)
        if self.blocked:
            raise ContentBlocked("PROHIBITED_CONTENT", stage="prompt")
        return (
            self.payload if isinstance(self.payload, str)
            else json.dumps(self.payload)
        )

    async def stream(
        self, system: str, prompt: str, *, temperature: float = 0.85
    ):  # pragma: no cover
        raise NotImplementedError


def test_the_fake_satisfies_the_protocol():
    assert isinstance(_Extractor([]), LLMProvider)
    assert (
        inspect.signature(_Extractor.complete, eval_str=True)
        == inspect.signature(LLMProvider.complete, eval_str=True)
    )


# ── Extraction ────────────────────────────────────────────────────────


async def test_one_newsletter_yields_several_items():
    """The whole point. Morning Brew's edition is four stories, not one."""
    llm = _Extractor([
        {"source": 0, "headline": "Fed holds rates", "gist": "G",
         "subject": "US Federal Reserve September rate decision"},
        {"source": 0, "headline": "Chip ruling lands", "gist": "G",
         "subject": "US chip export ruling"},
    ])
    items = await extract_items(llm, [_newsletter("Morning Brew", "Tuesday")])
    assert len(items) == 2
    assert {i.subject for i in items} == {
        "US Federal Reserve September rate decision", "US chip export ruling"
    }
    assert all(i.source_name == "Morning Brew" for i in items)


async def test_newsletter_bodies_are_fenced_and_the_instructions_are_outside():
    """The mistake script_chain.py documents, and that this project made once.

    A rule about the data, fenced together with the data, tells the model to
    obey it and to distrust it in the same breath.
    """
    from app.pipeline.prompts import INJECTION_WARNING, SOURCE_FENCE_OPEN

    llm = _Extractor([])
    await extract_items(llm, [_newsletter("Brew", "T", body="SECRET-BODY")])
    prompt = llm.prompts[0]
    assert SOURCE_FENCE_OPEN in prompt
    assert INJECTION_WARNING in prompt
    # Mercury's own instruction must come BEFORE the fenced block opens.
    assert prompt.index("USE THE IDENTICAL PHRASE") < prompt.index(SOURCE_FENCE_OPEN)


async def test_a_refusal_costs_the_segment_not_the_run():
    """Rule 7, one level down."""
    items = await extract_items(
        _Extractor(None, blocked=True), [_newsletter("Brew", "T")]
    )
    assert items == []


async def test_malformed_entries_are_dropped_individually():
    """One bad entry in thirty is not a reason to lose twenty-nine."""
    llm = _Extractor([
        {"source": 0, "headline": "Good", "gist": "G", "subject": "S"},
        {"source": 0, "headline": "", "gist": "G", "subject": "S"},
        {"source": 0, "headline": "No subject", "gist": "G", "subject": ""},
        {"source": 99, "headline": "Out of range", "gist": "G", "subject": "S"},
        "not even an object",
    ])
    items = await extract_items(llm, [_newsletter("Brew", "T")])
    assert [i.headline for i in items] == ["Good"]


async def test_unparseable_json_returns_nothing_rather_than_raising():
    items = await extract_items(_Extractor("{not json"), [_newsletter("B", "T")])
    assert items == []


async def test_one_newsletter_cannot_crowd_out_the_others():
    """The cap is enforced here, not requested in the prompt.

    A link-dump digest with forty one-line blurbs would otherwise take every
    slot from the four newsletters that actually wrote something.
    """
    llm = _Extractor([
        {"source": 0, "headline": f"H{i}", "gist": "G", "subject": f"S{i}"}
        for i in range(MAX_ITEMS_PER_NEWSLETTER + 5)
    ])
    items = await extract_items(llm, [_newsletter("Link Dump", "T")])
    assert len(items) == MAX_ITEMS_PER_NEWSLETTER


# ── The merge, which is the reason for all of the above ───────────────


def test_the_same_story_from_three_newsletters_becomes_one():
    merged = merge_by_subject([
        _item("US Federal Reserve September rate decision", "Morning Brew"),
        _item("US Federal Reserve September rate decision", "The Information"),
        _item("US chip export ruling", "Morning Brew"),
        _item("US Federal Reserve September rate decision", "Axios"),
    ])
    assert len(merged) == 2
    fed = merged[0]
    assert fed.outlets == ["Morning Brew", "The Information", "Axios"]


def test_one_newsletter_mentioning_a_story_twice_is_still_one_outlet():
    """The editorial claim is 'several independent people thought this
    mattered'. Counting one of them twice makes that claim false."""
    merged = merge_by_subject([
        _item("Fed rate decision", "Morning Brew"),
        _item("Fed rate decision", "Morning Brew"),
    ])
    assert merged[0].outlets == ["Morning Brew"]
    assert len(merged[0].items) == 2


def test_subjects_match_regardless_of_case_and_whitespace():
    merged = merge_by_subject([
        _item("Fed rate decision", "A"),
        _item("  fed rate decision  ", "B"),
    ])
    assert len(merged) == 1


def test_the_most_covered_story_leads():
    """Outlet count first: several independent editors choosing the same
    story is evidence about the day, not about the summariser."""
    ranked = rank(merge_by_subject([
        _item("Solo story", "A", hours_ago=0),
        _item("Everyone has this", "B", hours_ago=5),
        _item("Everyone has this", "C", hours_ago=5),
        _item("Everyone has this", "D", hours_ago=5),
    ]))
    assert ranked[0].subject == "Everyone has this"
    assert len(ranked[0].outlets) == 3


def test_recency_breaks_a_tie():
    ranked = rank(merge_by_subject([
        _item("Older", "A", hours_ago=5),
        _item("Newer", "B", hours_ago=0),
    ]))
    assert [s.subject for s in ranked] == ["Newer", "Older"]


def test_the_headline_shown_is_the_most_specific_one_offered():
    """Not the first: arrival order is not editorial quality."""
    merged = merge_by_subject([
        _item("Fed", "A", headline="Fed holds"),
        _item("Fed", "B", headline="Fed holds rates, signals two cuts in 2027"),
    ])
    assert merged[0].headline == "Fed holds rates, signals two cuts in 2027"
