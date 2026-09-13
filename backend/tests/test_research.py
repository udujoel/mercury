"""Task 18 — research a subject against the open web, with citations.

The point of the feature, in the owner's words: "make sure the story is rich
and informed, not just what's in the email." A newsletter is a POINTER to
something; Mercury used to repeat what it said.

These do not call Gemini. The provider was verified against the live API
separately (a real subject came back with straitstimes.com, timesofisrael.com,
theguardian.com and cfr.org as resolved publisher links); what is pinned here
is our own contract — the ranking, the caps, the rule-3 discard and the rule-7
degradation.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from app.core.schemas import DigestSource, DigestStory, SourceType
from app.pipeline.digest import (
    RESEARCH_MAX_SUBJECTS,
    attach_research,
    research_subjects,
)
from app.providers.gemini_research import Findings, ResearchSource


def _story(sid: str, subject: str | None, outlets: list[str]) -> DigestStory:
    return DigestStory(
        id=sid, headline=f"headline {sid}", subject=subject,
        source_count=len(set(outlets)),
        sources=[
            DigestSource(source_name=o, title="t", url=None,
                         published_at=datetime(2026, 9, 13, tzinfo=UTC),
                         source_type=SourceType.RSS)
            for o in outlets
        ],
    )


class _Fake:
    """Records what it was asked, so the ranking can be asserted."""

    def __init__(self, answer=None, *, delay: float = 0.0, raises: bool = False):
        self.asked: list[str] = []
        self._answer = answer
        self._delay = delay
        self._raises = raises

    async def research(self, subject: str, **_):
        self.asked.append(subject)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises:
            raise RuntimeError("synthetic research failure")
        if self._answer is not None:
            return self._answer(subject)
        return Findings(
            subject=subject, summary=f"about {subject}",
            sources=[ResearchSource(title="reuters.com", url="https://r.com/a")],
        )


# ── Which subjects get researched ─────────────────────────────────────


async def test_one_subject_is_researched_once_however_many_stories_share_it():
    """The live digest carried two Houthi clusters sharing one subject.

    Researching it twice would spend two of six slots on one question and
    print the same paragraph under two headlines.
    """
    houthi = "Houthi forces advancing on Red Sea shipping lanes"
    fake = _Fake()
    found = await research_subjects(
        [_story("a", houthi, ["CNBC", "BBC"]), _story("b", houthi, ["Al Jazeera"])],
        fake,
    )
    assert fake.asked == [houthi]
    assert len(found) == 1


async def test_the_most_corroborated_subjects_go_first():
    fake = _Fake()
    await research_subjects(
        [
            _story("a", "one outlet", ["A"]),
            _story("b", "three outlets", ["A", "B", "C"]),
            _story("c", "two outlets", ["A", "B"]),
        ],
        fake,
    )
    assert fake.asked == ["three outlets", "two outlets", "one outlet"]


async def test_stories_with_no_subject_are_not_researched():
    """Every digest written before 2026-09-13 has none."""
    fake = _Fake()
    found = await research_subjects([_story("a", None, ["A", "B"])], fake)
    assert fake.asked == []
    assert found == {}


async def test_the_cap_holds():
    fake = _Fake()
    stories = [_story(str(i), f"subject {i}", ["A", "B"]) for i in range(20)]
    await research_subjects(stories, fake)
    assert len(fake.asked) == RESEARCH_MAX_SUBJECTS


# ── Rule 7: the briefing ships either way ─────────────────────────────


async def test_no_provider_means_no_research_and_no_error():
    assert await research_subjects([_story("a", "s", ["A"])], None) == {}


async def test_a_provider_that_raises_does_not_take_down_the_run():
    found = await research_subjects([_story("a", "s", ["A"])], _Fake(raises=True))
    assert found == {}


async def test_the_wall_clock_budget_is_enforced():
    """A cap on COUNT alone still lets one hanging request eat the morning.

    Generation has a 06:00 deadline and already runs four to seven minutes.
    """
    fake = _Fake(delay=5.0)
    found = await research_subjects(
        [_story("a", "s", ["A"])], fake, budget_seconds=0.05
    )
    assert found == {}


# ── Rule 3: nothing unattributed reaches the page ─────────────────────


async def test_an_answer_with_no_sources_is_discarded():
    """A researched paragraph with no citation is worse on a page than in
    audio, because it LOOKS authoritative."""
    fake = _Fake(lambda s: Findings(subject=s, summary="confident and unsourced"))
    assert await research_subjects([_story("a", "s", ["A"])], fake) == {}


async def test_sources_survive_onto_the_wire():
    found = await research_subjects([_story("a", "s", ["A"])], _Fake())
    assert found["s"].sources[0].title == "reuters.com"
    assert found["s"].sources[0].url == "https://r.com/a"


# ── Attaching ─────────────────────────────────────────────────────────


async def test_a_finding_reaches_every_story_that_shares_its_subject():
    houthi = "Houthi forces advancing on Red Sea shipping lanes"
    stories = [_story("a", houthi, ["CNBC"]), _story("b", houthi, ["BBC"]),
               _story("c", "something else", ["A"])]
    found = await research_subjects(stories[:2], _Fake())
    attached = attach_research(stories, found)
    assert attached[0].research is not None
    assert attached[1].research is not None
    assert attached[2].research is None, "a different subject gets nothing"


def test_attaching_nothing_returns_the_stories_unchanged():
    stories = [_story("a", "s", ["A"])]
    assert attach_research(stories, {}) is stories


# ── The provider's own rules ──────────────────────────────────────────


def test_findings_need_both_halves_to_be_usable():
    assert not Findings(subject="s").is_usable
    assert not Findings(subject="s", summary="text").is_usable
    assert not Findings(
        subject="s", sources=[ResearchSource(title="t", url="u")]
    ).is_usable
    assert Findings(
        subject="s", summary="text",
        sources=[ResearchSource(title="t", url="u")],
    ).is_usable


@pytest.mark.asyncio
async def test_research_without_a_key_returns_empty_rather_than_calling():
    """What the test suite itself relies on: conftest sets the key to "",
    so nothing here can make a paid API call."""
    from app.providers.gemini_research import GeminiResearchProvider

    provider = GeminiResearchProvider(api_key="")
    assert not (await provider.research("anything")).is_usable
