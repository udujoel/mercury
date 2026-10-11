"""The wiring test this feature shipped without.

2026-09-16 to 2026-09-20: #51 merged, sixteen briefings generated, and the
newsletter segment ran ZERO times. Not once. The extractor was handed
`selection["emails"]`, and `select_for_briefing` caps emails at six while
`score_emails` sorts newsletters LAST — they are `Priority.LOW` precisely
because they were never meant to be read out individually.

So on a morning with eight ordinary emails, all six slots went to those and
the extractor received an empty list, then returned without logging. Every
layer was complete and tested except the one connecting them — this
project's signature failure, and its fourth occurrence after `n_stories=0`,
`music_set` and `topic_centroid`.

What let it survive four days is that it is INTERMITTENT: on an inbox that
is mostly newsletters, some do survive the cap, so a spot check passes. The
composition of the inbox decided whether the feature existed at all.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.core.schemas import BriefingLength, Item, Priority, SourceType
from app.pipeline.generate import _newsletters_for_extraction, generate_briefing
from app.pipeline.scoring import score_emails, select_for_briefing
from app.providers.fake import FakeLLMProvider
from app.providers.fake_embeddings import FakeEmbeddingProvider
from app.providers.fake_tts import FakeTTSProvider
from app.synthesis.storage import LocalFilesystemStorage


def _email(index: int, *, newsletter: bool) -> Item:
    return Item(
        source_type=SourceType.EMAIL,
        source_name=f"{'Brew' if newsletter else 'Person'}{index}",
        title=f"subject {index}",
        body="body",
        author=f"a{index}@example.test",
        timestamp=datetime(2026, 9, 20, 4, tzinfo=UTC) - timedelta(minutes=index),
        priority=Priority.LOW if newsletter else Priority.MEDIUM,
        raw={"is_newsletter": newsletter},
    )


# The exact composition of the 2026-09-20 04:49 run: 21 emails, 13 of them
# newsletters, which produced a briefing with no newsletter segment at all.
def _the_morning_that_failed() -> list[Item]:
    return [_email(i, newsletter=False) for i in range(8)] + [
        _email(100 + i, newsletter=True) for i in range(13)
    ]


def test_the_selection_cap_starves_the_newsletter_segment():
    """The mechanism, pinned so it cannot be reintroduced.

    This asserts the starvation EXISTS in `select_for_briefing`, which is
    correct behaviour for that function: its job is to cap what gets read
    out one by one, and ranking newsletters last is deliberate. The defect
    was building the newsletter segment on its output.
    """
    kept = select_for_briefing(
        score_emails(_the_morning_that_failed()),
        [],
        BriefingLength.STANDARD,
        date=datetime(2026, 9, 20, tzinfo=UTC),
    )["emails"]

    assert len(kept) == 6
    assert [e for e in kept if e.raw["is_newsletter"]] == [], (
        "selection is supposed to starve newsletters — that is exactly why "
        "the extractor must not be fed from it"
    )


def test_every_newsletter_in_the_inbox_reaches_the_extractor():
    """The fix, asserted on what `generate_briefing` actually hands over.

    Not on the selection, and not on a hand-built list of `MergedStory`
    objects — every test in `test_newsletter_items.py` did that, which is
    why they were all green while the feature did nothing.
    """
    handed = _newsletters_for_extraction(_the_morning_that_failed())
    assert len(handed) == 13, (
        f"the extractor sees {len(handed)} of 13 newsletters; for the rest "
        "the segment is as good as absent"
    )


def test_ordinary_mail_is_not_handed_to_the_newsletter_extractor():
    """The gate must not become "everything", either: a colleague's email
    is not journalism and rule 5 keeps it off that path."""
    handed = _newsletters_for_extraction(_the_morning_that_failed())
    assert all(e.raw["is_newsletter"] for e in handed)


async def _run(tmp_path, inbox, **kwargs):
    """A whole briefing off a given inbox, with no network anywhere.

    Everything but Gmail is stubbed empty so the assertions are about the
    inbox and nothing else; `fetch_emails` is a stub rather than a fixture
    file because the point of these tests is a SPECIFIC inbox composition.
    """
    return await generate_briefing(
        briefing_id="2026-09-20",
        length=BriefingLength.STANDARD,
        llm=kwargs.pop("llm", FakeLLMProvider()),
        tts=FakeTTSProvider(),
        embeddings=FakeEmbeddingProvider(),
        storage=LocalFilesystemStorage(base_dir=tmp_path),
        fetch_emails=lambda **_: _async(list(inbox)),
        fetch_events=lambda **_: _async([]),
        fetch_weather=lambda **_: _async(None),
        fetch_articles=lambda **_: _async([]),
        research_provider=None,
        **kwargs,
    )


async def _async(value):
    return value


async def test_the_census_names_the_starved_stage_on_a_real_run(tmp_path, caplog):
    """The robustness half, asserted against the real generate path.

    An earlier version of this test claimed to run a briefing end to end and
    did not: it hand-built a `RunCensus`, recorded `took=21, made=0` itself
    and asserted `report()` named it — which is `test_census.py` with
    scaffolding, and would have stayed green through any wiring defect at
    all. Review of #53 caught the docstring describing work the body never
    did. That is the same class of mistake as the bug this file is about, so
    it gets the same treatment: assert on what production runs.

    Here the extractor is starved by an LLM that returns no items, which is
    indistinguishable from a starved stage to everything downstream — and
    that is the point. Whatever the cause, the run log has to SAY it.
    """

    class _NoItems(FakeLLMProvider):
        def _dispatch(self, prompt):
            body = super()._dispatch(prompt)
            return "[]" if self.calls[-1][0] == "newsletter_items" else body

    with caplog.at_level(logging.INFO):
        await _run(tmp_path, _the_morning_that_failed(), llm=_NoItems())

    assert "run census:" in caplog.text
    assert "newsletters 13->0" in caplog.text, (
        "the census must name the intake, not just the output — 13 issues "
        "in and nothing out is the whole signal"
    )
    assert "STARVED STAGE: newsletters" in caplog.text


async def test_a_real_run_records_the_newsletter_stage_at_all(tmp_path, caplog):
    """The census has to be REACHED, not merely correct.

    `test_census.py` proves `RunCensus` reports what it is told. Nothing
    proved `generate_briefing` tells it anything — and an instrument that
    is never read is exactly the failure mode this whole PR is about.
    """
    with caplog.at_level(logging.INFO):
        await _run(tmp_path, _the_morning_that_failed())

    census_lines = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("run census:")
    ]
    assert len(census_lines) == 1, census_lines
    assert "newsletters 13->" in census_lines[0]
    assert "STARVED STAGE" not in caplog.text, (
        "13 newsletters in and items out is a healthy run; an alarm here is "
        "the false positive that trains people to ignore the real one"
    )


async def test_the_census_still_reports_when_the_run_raises(tmp_path, caplog):
    """The run that produces nothing is the one that most needs the census.

    Review of #53: `report()` sat after TTS, the digest and research, on the
    happy path only. So a run where every turn failed synthesis — the
    `EmptyBriefingError` path, which is the worst morning there is — was the
    single run with NO census line in its log. It is a context manager now,
    so the report happens in a `finally`.
    """

    class _Exploding(FakeLLMProvider):
        def _dispatch(self, prompt):
            body = super()._dispatch(prompt)
            if self.calls[-1][0] == "outline":
                raise RuntimeError("synthetic failure after the census ran")
            return body

    with (
        caplog.at_level(logging.INFO),
        pytest.raises(RuntimeError, match="synthetic failure"),
    ):
        await _run(tmp_path, _the_morning_that_failed(), llm=_Exploding())

    assert "run census:" in caplog.text, (
        "the census must survive the exception; a report that only happens "
        "when the run succeeds cannot explain a run that did not"
    )
    assert "newsletters 13->" in caplog.text


# ──────────────────────────────────────────────────────────────
# The other half of the gate: widening the input must not widen it
# past what clustering already accounted for.
# ──────────────────────────────────────────────────────────────


def _covering(name: str, subject: str, story: str) -> Item:
    return Item(
        source_type=SourceType.EMAIL,
        source_name=name,
        title=subject,
        body=story,
        author=f"{name.replace(' ', '').lower()}@example.test",
        timestamp=datetime(2026, 9, 20, 4, tzinfo=UTC),
        priority=Priority.LOW,
        raw={"is_newsletter": True},
    )


async def test_a_newsletter_the_world_segment_absorbed_is_not_extracted_again():
    """Widening the input is not the same as removing the dedup.

    Review of #53. The first cut fed the extractor the whole post-mute
    inbox, which skips the subtraction `_build_world_stories` performs: a
    newsletter absorbed into a story the world segment is about to speak
    came back through the newsletter block, and nothing compares the two —
    they are rendered independently. The hosts would cover one acquisition
    twice, which is the Phase 2 gate verbatim.

    Asserted on `inbox_emails` vs `emails` rather than on prompt text,
    because this is the subtraction itself; the prompt-level version is
    `test_news_pipeline_e2e.py::test_the_story_is_offered_to_exactly_one_
    segment_of_the_briefing`, which passed vacuously until the fake learned
    to answer the extraction prompt.
    """
    from app.pipeline.generate import _build_world_stories

    deal = (
        "Acme Corp agreed to acquire Beta Systems for four billion dollars "
        "in cash and stock, the companies said on Friday."
    )
    inbox = [
        _covering("Morning Brew", "Acme buys Beta", deal),
        _covering("Platformer", "Acme buys Beta", deal),
    ]
    wire = [
        Item(
            source_type=SourceType.RSS,
            source_name="Reuters",
            title="Acme to acquire Beta in $4bn deal",
            body=deal,
            url="https://example.test/acme",
            timestamp=datetime(2026, 9, 20, 3, tzinfo=UTC),
            raw={},
        )
    ]

    world, inbox_emails, _ = await _build_world_stories(
        emails=inbox,
        articles=wire,
        events=[],
        embeddings=FakeEmbeddingProvider(),
        length=BriefingLength.STANDARD,
        # Just after the newest fixture (04:00), not the wall clock.
        now=datetime(2026, 9, 20, 5, tzinfo=UTC),
    )
    assert world, "precondition: the wire story must reach the world segment"
    absorbed = {e.source_name for e in inbox} - {e.source_name for e in inbox_emails}
    assert absorbed, "precondition: at least one newsletter must be absorbed"

    handed = {e.source_name for e in _newsletters_for_extraction(inbox_emails)}
    assert not (handed & absorbed), (
        f"{sorted(handed & absorbed)} is spoken in the world segment AND "
        "handed to the newsletter extractor — the same story twice"
    )


async def test_the_census_sees_degradation_recorded_by_the_real_run(tmp_path, caplog):
    """The wiring, not the logic — `test_census.py` covers the logic.

    Review of #56 removed `degraded=degraded` from `generate_briefing`
    outright, leaving the census permanently blind to a degraded run, and
    the whole 811-test suite stayed green. Passing `list(degraded)` so
    later appends are invisible also passed. Both are the same mistake
    this file was created for: every test hand-built the object instead of
    asking what production hands it.

    `degraded` is shared BY REFERENCE, so this asserts on an entry the
    script chain appends from inside `generate_script`, after the census
    already exists.
    """

    class _Degrading(FakeLLMProvider):
        pass

    async def _script(**kwargs):
        # Exactly what `script_chain._give_up` does on a refusal.
        kwargs["degraded"].append("world")
        from app.pipeline.script_chain import generate_script

        return await generate_script(**kwargs)

    with caplog.at_level(logging.INFO):
        await _run(
            tmp_path,
            _the_morning_that_failed(),
            llm=_Degrading(),
            generate_script=_script,
        )

    census_line = next(
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("run census:")
    )
    assert "script" in census_line

    # The stage recorded AFTER the append must see it; the ones recorded
    # before must not. That ordering is the whole point — a refusal in the
    # script chain cannot explain an extractor that already finished.
    from app.pipeline.generate import _LAST_CENSUS_STAGES

    by_name = {s.name: s for s in _LAST_CENSUS_STAGES}
    assert by_name["newsletters"].degraded_before == (), (
        "the extractor ran before anything failed; letting a later "
        "refusal explain it is the wrong-cause alarm #56 exists to stop"
    )
    assert "world" in by_name["script"].degraded_before, (
        "the census is not sharing the run's degraded list — removing "
        "`degraded=degraded` from generate_briefing would pass silently"
    )
