"""
The Phase 2 gate: the same story never appears twice, however many
newsletters covered it.

Everything in this file runs the REAL code — real Gmail normalisation, real
RSS normalisation, real embedding, real clustering, real ranking, real
selection, real prompt assembly. Only the three external services are faked
(LLM, TTS, embeddings), and the embedding fake is driven by its input rather
than returning noise, so a clustering assertion built on it means something.
That matters here more than usual: the existing end-to-end test injects fakes
for calendar, weather, scoring and the script chain, which is exactly why
every defect this branch fixes survived a green suite.

The fixtures are shaped to the PRD's own acceptance line — "a story covered
by three newsletters and two news sources appears exactly once in the
briefing" — and deliberately split across two transports: Stratechery,
Benedict Evans and Exponential View arrive as newsletters over Gmail,
Reuters and Platformer as articles over RSS. That is the case CLAUDE.md says
the shared `Item` schema exists for, and no fixture in this repo previously
covered it — `clustering_corpus.json` carries four of the same sources, but
as pre-normalised JSON that never touches an ingestion module, so it could
not have caught a wiring defect.

Two controls ride along and must NOT merge: an unrelated rocket launch on
RSS, and a personal email from a colleague.
"""

from __future__ import annotations

import functools
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.schemas import BriefingLength, Item, Segment, SourceType, Turn
from app.ingestion import rss
from app.ingestion.gmail import fetch_emails
from app.pipeline.dedup import build_stories
from app.pipeline.generate import (
    _build_world_stories,
    failed_personal_sources,
    generate_briefing,
)
from app.pipeline.script_chain import _build_source_refs
from app.providers.fake_embeddings import FakeEmbeddingProvider
from app.providers.fake_tts import FakeTTSProvider
from app.synthesis.storage import LocalFilesystemStorage
from tests.test_script_chain_prompt_wiring import PromptCapturingLLM

FIXTURES = Path(__file__).parent / "fixtures"
GMAIL_FIXTURE = FIXTURES / "crosssource_gmail.json"
RSS_FIXTURE = FIXTURES / "crosssource_rss.json"

FEEDS = [
    "https://www.reuters.com/technology/rss",
    "https://www.platformer.news/rss",
    "https://arstechnica.com/space/feed/",
]

# Anchors "now" inside the fixture's dated window, the same way
# test_ingestion_rss.py does, so assertions do not rot as time passes.
FIXED_NOW = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)

# FakeEmbeddingProvider is a feature-hashing proxy, not a semantic model, so
# it needs a lower threshold than the 0.78 tuned for Gemini. This is the same
# value test_clustering_integration.py verified by sweep for the same
# provider — not a number picked to make this file pass. The sweep below
# re-checks the margin on THIS corpus.
FAKE_PROVIDER_TEST_THRESHOLD = 0.60

# Three newsletters (Gmail) and two news sources (RSS), per the PRD's
# acceptance line. `Story.source_names` is sorted.
ACQUISITION_NEWSLETTERS = ["Benedict Evans", "Exponential View", "Stratechery"]
ACQUISITION_NEWS_SOURCES = ["Platformer", "Reuters"]
ACQUISITION_SOURCES = sorted(ACQUISITION_NEWSLETTERS + ACQUISITION_NEWS_SOURCES)


async def _ingest() -> tuple[list, list]:
    """Real Gmail + real RSS ingestion off the cross-source fixtures."""
    emails = await fetch_emails(fixture=GMAIL_FIXTURE)
    articles = await rss.fetch_articles(
        feeds=FEEDS, fixture=RSS_FIXTURE, now=FIXED_NOW
    )
    return emails, articles


async def _stories(threshold: float = FAKE_PROVIDER_TEST_THRESHOLD):
    emails, articles = await _ingest()
    return await build_stories(
        emails + articles, embeddings=FakeEmbeddingProvider(), threshold=threshold
    )


@pytest.fixture
def clustering_threshold(monkeypatch):
    """Point `settings.cluster_similarity_threshold` at the fake provider's
    calibrated value, for the paths that read it rather than taking an
    explicit argument."""
    monkeypatch.setenv(
        "CLUSTER_SIMILARITY_THRESHOLD", str(FAKE_PROVIDER_TEST_THRESHOLD)
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ──────────────────────────────────────────────────────────────
# The gate
# ──────────────────────────────────────────────────────────────


async def test_the_fixtures_really_do_carry_the_same_story_twice():
    """Guard the premise. A dedup test whose input contains no duplicates
    passes for the wrong reason, and this project has shipped tests that
    encoded the bug they claimed to guard.
    """
    emails, articles = await _ingest()
    assert {e.source_name for e in emails} >= set(ACQUISITION_NEWSLETTERS)
    assert {a.source_name for a in articles} >= set(ACQUISITION_NEWS_SOURCES)

    covering = [
        item for item in emails + articles if "DataFlow" in f"{item.title} {item.body}"
    ]
    assert len(covering) == 5
    assert sorted(
        i.source_name for i in covering if i.source_type is SourceType.EMAIL
    ) == ACQUISITION_NEWSLETTERS
    assert sorted(
        i.source_name for i in covering if i.source_type is SourceType.RSS
    ) == ACQUISITION_NEWS_SOURCES


async def test_a_story_covered_by_three_newsletters_and_two_news_sources_appears_once():
    """THE PHASE 2 GATE, in the PRD's own words.

    Seven normalised items in, five of them the same acquisition. Out: three
    stories — the acquisition, the rocket launch, the personal email.

    Asserted on the merged story's identity and its full source list rather
    than on a total: "three stories" alone would also be satisfied by three
    wrong clusters.
    """
    stories = await _stories()

    assert len(stories) == 3

    acquisition = max(stories, key=lambda s: s.cluster_size)
    assert acquisition.cluster_size == 5
    assert acquisition.source_names == ACQUISITION_SOURCES

    # And the other two did not get swept in with it.
    singletons = [s for s in stories if s is not acquisition]
    assert sorted(s.canonical.source_name for s in singletons) == [
        "Ars Technica",
        "Maria Chen",
    ]


async def test_the_merge_holds_across_a_band_of_thresholds():
    """One lucky value is not evidence. The four-source merge and the two
    controls have to survive the whole band, or the gate is measuring the
    threshold rather than the pipeline."""
    for threshold in (0.50, 0.55, 0.60, 0.65, 0.70):
        stories = await _stories(threshold)
        biggest = max(stories, key=lambda s: s.cluster_size)
        assert biggest.source_names == ACQUISITION_SOURCES, threshold
        assert len(stories) == 3, threshold


async def test_prd_f08_newsletter_and_news_feed_merge_with_both_sources_attributed():
    """PRD F-08 exactly: "a story arriving via newsletter and via news feed
    is merged into one item with both sources attributed."

    The narrowest statement of the requirement — one newsletter, one feed —
    asserted on the item they merge INTO, so a run that merged the
    newsletters with each other but left the feeds alone would fail.
    """
    stories = await _stories()
    merged = [
        s for s in stories
        if {m.source_type for m in s.members} == {SourceType.EMAIL, SourceType.RSS}
    ]

    assert len(merged) == 1
    story = merged[0]
    assert sorted(
        m.source_name for m in story.members if m.source_type is SourceType.EMAIL
    ) == ACQUISITION_NEWSLETTERS
    assert sorted(
        m.source_name for m in story.members if m.source_type is SourceType.RSS
    ) == ACQUISITION_NEWS_SOURCES


async def test_the_wire_report_represents_the_cluster():
    """ADR-003's point about which item is canonical, and the reason it
    matters to the user: Gmail `Item`s carry no URL, so a newsletter winning
    the canonical slot leaves the day's biggest story with no show-notes
    link (PRD F-19)."""
    stories = await _stories()
    acquisition = max(stories, key=lambda s: s.cluster_size)

    assert acquisition.canonical.source_name == "Reuters"
    assert acquisition.canonical.source_type is SourceType.RSS
    assert acquisition.canonical.url == (
        "https://www.reuters.com/technology/techcorp-dataflow"
    )


async def test_the_cluster_really_does_mix_naive_and_aware_timestamps():
    """Not incidental — the fixture is built this way on purpose.

    `msg_evans_001` carries `Date: ... -0000`, RFC 2822's "unknown timezone",
    and no `internalDate`. Gmail's `_parse_date` falls back to
    `parsedate_to_datetime`, which returns a NAIVE datetime for that offset;
    every other member is aware. Before `_as_utc`, sorting this exact cluster
    raised TypeError inside `_pick_canonical`, `build_stories` caught it, and
    all six items shipped as unclustered singletons — so the acquisition was
    read out four times and the only trace was one log line.

    Verified by running the pre-fix rank tuple against these very members.
    """
    stories = await _stories()
    acquisition = max(stories, key=lambda s: s.cluster_size)
    awareness = {m.timestamp.tzinfo is None for m in acquisition.members}
    assert awareness == {True, False}


async def test_every_source_that_carried_the_story_reaches_the_show_notes():
    """"Four sources attributed", the visible half of ADR-003. One ref per
    member, each carrying the cluster size so the client can group them into
    a single show-notes entry."""
    stories = await _stories()
    acquisition = max(stories, key=lambda s: s.cluster_size)
    turns = [
        Turn(
            speaker="A",
            segment=Segment.WORLD,
            text=(
                "TechCorp is buying DataFlow for 2.3 billion, and Reuters "
                "has the announcement."
            ),
        )
    ]

    refs = _build_source_refs(turns, [], [], [acquisition])

    assert sorted(r.source_name for r in refs) == ACQUISITION_SOURCES
    assert {r.cluster_size for r in refs} == {5}


# ──────────────────────────────────────────────────────────────
# Through the whole pipeline
# ──────────────────────────────────────────────────────────────


# The real `fetch_articles`, with only its clock and feed list pinned.
# `now=` exists for exactly this (see its docstring): the fixture carries
# fixed publication dates, and the 24-hour recency filter is real code that
# must actually run. Everything downstream — normalisation, URL
# canonicalisation, per-publication dedup — is untouched.
_pinned_fetch_articles = functools.partial(
    rss.fetch_articles, feeds=FEEDS, now=FIXED_NOW
)


async def _generate(tmp_path, llm, **kwargs):
    return await generate_briefing(
        briefing_id="2026-08-21",
        length=BriefingLength.STANDARD,
        llm=llm,
        tts=FakeTTSProvider(),
        embeddings=kwargs.pop("embeddings", FakeEmbeddingProvider()),
        fetch_articles=kwargs.pop("fetch_articles", _pinned_fetch_articles),
        storage=LocalFilesystemStorage(base_dir=tmp_path),
        gmail_fixture=GMAIL_FIXTURE,
        rss_fixture=RSS_FIXTURE,
        calendar_fixture=None,
        weather_fixture=FIXTURES / "weather_openmeteo.json",
        **kwargs,
    )


async def test_the_world_segment_receives_the_clustered_story(
    tmp_path, clustering_threshold
):
    """`script_chain` hardcoded `n_stories=0` and "(none — Phase 1 has no
    news clustering)". This is the line that stops being a placeholder."""
    llm = PromptCapturingLLM()
    await _generate(tmp_path, llm)

    prompt = llm.outline_prompt

    # Two: the four-source acquisition, and the rocket launch nobody else
    # carried. Six ingested items, two things to talk about.
    assert "WORLD (2 clustered stories)" in prompt
    assert (
        "Covered by 5 source(s): Benedict Evans, Exponential View, "
        "Platformer, Reuters, Stratechery"
    ) in prompt
    assert "Covered by 1 source(s): Ars Technica" in prompt

    # Every publication named, so the hosts can say "three of your
    # newsletters led with this" without inventing it (ADR-003).
    for source in ACQUISITION_SOURCES:
        assert source in prompt, source


async def test_the_story_is_offered_to_exactly_one_segment_of_the_briefing(
    tmp_path, clustering_threshold
):
    """"Appears exactly once IN THE BRIEFING", not merely "clusters into one
    story". Clustering the acquisition correctly and then also handing all
    three newsletters to the inbox segment would satisfy every assertion
    above and still read the story out four times.

    So: each newsletter's subject line appears exactly once across the whole
    outline prompt — inside the world story it was absorbed into, and
    nowhere else. The colleague's email merged with nothing and is still
    offered to the inbox, which is what stops this passing by way of an
    empty briefing.
    """
    llm = PromptCapturingLLM()
    await _generate(tmp_path, llm)

    prompt = llm.outline_prompt
    inbox_block = prompt.split("INBOX (", 1)[1].split("WORLD (", 1)[0]
    world_block = prompt.split("WORLD (", 1)[1].split("FOLLOW-UPS", 1)[0]

    for subject in (
        "The DataFlow Acquisition and What It Means for the Bundle",
        "TechCorp buys DataFlow: notes on distribution",
        "The DataFlow deal and where the scarcity really is",
    ):
        assert subject not in inbox_block, subject

    # Named once in the world block, as members of the one story.
    for newsletter in ACQUISITION_NEWSLETTERS:
        assert world_block.count(newsletter) == 1, newsletter

    assert "Q3 budget review before Friday" in inbox_block
    assert inbox_block.startswith("1 items)")


async def test_the_story_text_is_in_the_source_block_the_gates_check(
    tmp_path, clustering_threshold
):
    """Rule 3 depends on this. `check_grounding` and the critique pass both
    check the script against SOURCE DATA, so an article absent from that
    block makes every claim drawn from it read as an invention."""
    llm = PromptCapturingLLM()
    await _generate(tmp_path, llm)

    draft_prompt = llm.draft_prompt
    source_block = draft_prompt.split("SOURCE DATA:", 1)[1]
    assert "TechCorp" in source_block
    assert "2.3 billion" in source_block
    assert "Reuters" in source_block


# ──────────────────────────────────────────────────────────────
# Rule 7 — the news half is optional
# ──────────────────────────────────────────────────────────────


async def test_a_dead_feed_does_not_take_the_briefing_down(tmp_path):
    async def _explode(**kwargs):
        raise RuntimeError("synthetic feed outage")

    manifest = await _generate(
        tmp_path, PromptCapturingLLM(), fetch_articles=_explode
    )
    assert manifest.turns


async def test_a_news_failure_is_not_reported_as_a_missing_personal_source(
    tmp_path,
):
    """News is not personal content. Folding it into
    `failed_personal_sources` would print "shipped without news" as a
    degraded-run warning on a morning that was fine."""

    async def _explode(**kwargs):
        raise RuntimeError("synthetic feed outage")

    await _generate(tmp_path, PromptCapturingLLM(), fetch_articles=_explode)
    assert "news" not in failed_personal_sources()


async def test_no_embedding_provider_still_ships_a_briefing(tmp_path):
    """A machine with no GEMINI_API_KEY gets a briefing without clustering,
    not an exception. `build_stories` treats a missing provider the same way
    it treats a failing one."""
    manifest = await _generate(tmp_path, PromptCapturingLLM(), embeddings=None)
    assert manifest.turns


# ──────────────────────────────────────────────────────────────
# The layer between clustering and the briefing.
#
# The gate tests above drive `build_stories` directly. Everything that turns
# a cluster into something a listener hears — the at-least-one-RSS promotion
# filter, the world cap, the absorbed subtraction, and the newsletter-only
# collapse — lives in `_build_world_stories` and had no coverage at all.
# That is exactly why a story covered only by newsletters was read out three
# times while a suite asserting the Phase 2 gate stayed green.
# ──────────────────────────────────────────────────────────────



def _newsletter(sender: str, title: str, body: str) -> Item:
    return Item(
        source_type=SourceType.EMAIL,
        source_name=sender,
        title=title,
        body=body,
        author=f"news@{sender.lower().replace(' ', '')}.com",
        timestamp=datetime(2026, 8, 31, 6, 0, tzinfo=UTC),
    )


ACQUISITION_BODY = (
    "Northwind has agreed to acquire Halcyon Robotics for 4.2 billion "
    "dollars, the largest robotics deal on record."
)


async def _world(emails, articles, length=BriefingLength.STANDARD):
    return await _build_world_stories(
        emails=emails,
        articles=articles,
        events=[],
        embeddings=FakeEmbeddingProvider(),
        length=length,
    )


async def test_a_story_only_newsletters_covered_is_still_read_out_once():
    """THE GATE, in the half PRD F-08 does not state.

    CLAUDE.md: "the same story never appears twice regardless of how many
    newsletters covered it." F-08 covers newsletter AND news feed, which the
    promotion filter handles. This is the case with no feed coverage at all
    — and since Mercury's news arrives largely as newsletters, it is the
    common one, not the edge.
    """
    emails = [
        _newsletter("Stratechery", "The Halcyon acquisition", ACQUISITION_BODY),
        _newsletter("Benedict Evans", "Northwind buys Halcyon", ACQUISITION_BODY),
        _newsletter("Platformer", "Northwind acquires Halcyon", ACQUISITION_BODY),
    ]

    world, inbox = await _world(emails, articles=[])

    # Not promoted: no publication outside the inbox carried it.
    assert world == []
    # But read out ONCE, not three times.
    assert len(inbox) == 1, [e.source_name for e in inbox]


async def test_the_collapse_names_the_other_newsletters_rather_than_hiding_them():
    """Dropping the duplicates silently would trade one wrong behaviour for
    another: a story three publications thought worth writing about would be
    read out as though one did."""
    emails = [
        _newsletter("Stratechery", "The Halcyon acquisition", ACQUISITION_BODY),
        _newsletter("Benedict Evans", "Northwind buys Halcyon", ACQUISITION_BODY),
        _newsletter("Platformer", "Northwind acquires Halcyon", ACQUISITION_BODY),
    ]

    _, inbox = await _world(emails, articles=[])
    survivor = inbox[0]
    others = survivor.raw.get("also_covered_by") or []

    assert len(others) == 2
    assert survivor.source_name not in others
    assert sorted(others + [survivor.source_name]) == [
        "Benedict Evans", "Platformer", "Stratechery",
    ]

    # ...and it reaches the model, which is the only reason to record it.
    from app.pipeline.script_chain import _format_emails

    rendered = _format_emails(inbox)
    assert "also covered by" in rendered
    for name in others:
        assert name in rendered


async def test_unrelated_emails_are_not_collapsed_into_each_other():
    """The control. A collapse that swallowed ordinary mail would be far
    worse than the duplication it replaces."""
    emails = [
        _newsletter("Stratechery", "The Halcyon acquisition", ACQUISITION_BODY),
        _newsletter("Benedict Evans", "Northwind buys Halcyon", ACQUISITION_BODY),
        _newsletter(
            "Maria Chen", "Lunch Thursday?",
            "Are you free Thursday? There is a new place near the office.",
        ),
    ]

    _, inbox = await _world(emails, articles=[])

    assert "Maria Chen" in {e.source_name for e in inbox}
    assert len(inbox) == 2


async def test_a_single_newsletter_is_left_completely_alone():
    """One member is not a cluster. Nothing to collapse, nothing to annotate."""
    emails = [_newsletter("Stratechery", "The Halcyon acquisition", ACQUISITION_BODY)]

    _, inbox = await _world(emails, articles=[])

    assert len(inbox) == 1
    assert not inbox[0].raw.get("also_covered_by")


async def test_a_newsletter_absorbed_by_the_world_segment_leaves_the_inbox():
    """The promotion path, asserted on BOTH outputs rather than on clustering
    alone — the contract the gate actually depends on."""
    emails = [
        _newsletter("Stratechery", "The Halcyon acquisition", ACQUISITION_BODY),
        _newsletter(
            "Maria Chen", "Lunch Thursday?",
            "Are you free Thursday? There is a new place near the office.",
        ),
    ]
    articles = [
        Item(
            source_type=SourceType.RSS,
            source_name="Reuters",
            title="Northwind to acquire Halcyon Robotics",
            body=ACQUISITION_BODY,
            url="https://example.com/northwind-halcyon",
            timestamp=datetime(2026, 8, 31, 5, 30, tzinfo=UTC),
        )
    ]

    world, inbox = await _world(emails, articles)

    assert len(world) == 1
    assert "Stratechery" in world[0].source_names
    assert "Reuters" in world[0].source_names
    # Absorbed out of the inbox, so it is not read twice.
    assert {e.source_name for e in inbox} == {"Maria Chen"}
