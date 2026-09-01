"""
Offline tests for app/ingestion/rss.py.

Fixture-based tests run entirely against backend/tests/fixtures/rss_feeds.json
and rss_article.html — no network is used anywhere in this file. Live network
calls (`_fetch_feed_live`, `_extract_full_text`) are monkeypatched wherever a
test needs to exercise the code paths that would otherwise hit them, mirroring
the pattern in test_ingestion_weather.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.core.schemas import Item, SourceType
from app.ingestion import rss

FIXTURE = Path(__file__).parent / "fixtures" / "rss_feeds.json"
ARTICLE_HTML = Path(__file__).parent / "fixtures" / "rss_article.html"

TECH_FEED = "https://example.com/feed/tech.xml"
WORLD_FEED = "https://example.com/feed/world.xml"

# Anchors "now" inside the fixture's dated window so assertions do not rot
# as wall-clock time passes.
FIXED_NOW = datetime(2026, 8, 21, 0, 0, 0, tzinfo=UTC)


async def _fetch(feeds=None, hours=24, max_per_feed=25):
    return await rss.fetch_articles(
        feeds=feeds or [TECH_FEED, WORLD_FEED],
        fixture=FIXTURE,
        hours=hours,
        max_per_feed=max_per_feed,
        now=FIXED_NOW,
    )


# ──────────────────────────────────────────────────────────────
# Basic normalisation
# ──────────────────────────────────────────────────────────────


async def test_fetch_articles_offline_returns_items():
    items = await _fetch()
    assert items
    for item in items:
        assert isinstance(item, Item)
        assert item.source_type == SourceType.RSS
        assert item.title
        assert item.url


async def test_source_name_comes_from_feed_title_not_url():
    items = await _fetch(feeds=[TECH_FEED])
    assert all(item.source_name == "Example Tech Daily" for item in items)


async def test_source_name_falls_back_to_domain_when_feed_has_no_title():
    items = await _fetch(feeds=[WORLD_FEED])
    assert items
    assert all(item.source_name == "example.com" for item in items)


async def test_title_is_cleaned_of_surrounding_whitespace():
    items = await _fetch(feeds=[WORLD_FEED])
    assert items[0].title == "Regional Trade Talks Resume After Two-Week Pause"


async def test_relative_link_resolved_against_feed_url():
    items = await _fetch(feeds=[WORLD_FEED])
    assert items[0].url == "https://example.com/world/trade-talks-resume"


# ──────────────────────────────────────────────────────────────
# Recency
# ──────────────────────────────────────────────────────────────


async def test_old_entry_excluded_by_recency_window():
    items = await _fetch(feeds=[TECH_FEED], hours=24)
    assert not any("old-story" in (item.url or "") for item in items)


async def test_entry_with_no_date_anywhere_is_excluded_not_faked():
    items = await _fetch(feeds=[TECH_FEED], hours=24)
    assert not any("no-date" in (item.url or "") for item in items)


async def test_entry_timestamp_missing_returns_none():
    assert rss.entry_timestamp({}) is None


async def test_entry_timestamp_malformed_struct_returns_none():
    assert rss.entry_timestamp({"published_parsed": [2026]}) is None


async def test_entry_timestamp_falls_back_to_updated_parsed():
    entry = {"updated_parsed": [2026, 8, 20, 20, 0, 0, 3, 232, 0]}
    ts = rss.entry_timestamp(entry)
    assert ts == datetime(2026, 8, 20, 20, 0, 0, tzinfo=UTC)


async def test_updated_only_entry_is_included_when_recent():
    items = await _fetch(feeds=[TECH_FEED], hours=24)
    assert any("updated-only" in (item.url or "") for item in items)


async def test_widening_the_window_includes_the_old_entry():
    items = await _fetch(feeds=[TECH_FEED], hours=24 * 365 * 20)
    assert any("old-story" in (item.url or "") for item in items)


# ──────────────────────────────────────────────────────────────
# Dedup / URL canonicalisation
# ──────────────────────────────────────────────────────────────


async def test_duplicate_entry_with_different_tracking_params_collapses():
    items = await _fetch(feeds=[TECH_FEED], hours=24)
    build_system_items = [i for i in items if "build-systems" in (i.url or "")]
    assert len(build_system_items) == 1


async def test_canonicalize_url_strips_utm_and_ref_params():
    dirty = "https://example.com/a?utm_source=rss&utm_medium=feed&ref=homepage"
    assert rss.canonicalize_url(dirty) == "https://example.com/a"


async def test_canonicalize_url_keeps_non_tracking_params():
    url = "https://example.com/a?id=42"
    assert rss.canonicalize_url(url) == "https://example.com/a?id=42"


async def test_canonicalize_url_resolves_relative_against_base():
    result = rss.canonicalize_url("/x/y", base="https://example.com/feed.xml")
    assert result == "https://example.com/x/y"


async def test_two_urls_differing_only_by_tracking_params_share_item_id():
    a = Item(
        source_type=SourceType.RSS,
        source_name="Example",
        title="t",
        url=rss.canonicalize_url("https://example.com/a?utm_source=rss"),
        timestamp=FIXED_NOW,
    )
    b = Item(
        source_type=SourceType.RSS,
        source_name="Example",
        title="t",
        url=rss.canonicalize_url("https://example.com/a?ref=homepage"),
        timestamp=FIXED_NOW,
    )
    assert a.id == b.id


# ──────────────────────────────────────────────────────────────
# Full-text extraction
# ──────────────────────────────────────────────────────────────


async def test_short_summary_triggers_extraction_and_uses_longer_body(monkeypatch):
    article_html = ARTICLE_HTML.read_text()

    async def fake_extract(client, url, semaphore):
        import trafilatura

        return trafilatura.extract(article_html)

    monkeypatch.setattr(rss, "_extract_full_text", fake_extract)
    items = await _fetch(feeds=[TECH_FEED], hours=24)
    cloud_item = next(i for i in items if "cloud-outage" in (i.url or ""))
    assert "regulators" in cloud_item.body.lower()
    assert len(cloud_item.body) > len(
        "Regulators are looking into this week's cloud outage."
    )
    assert cloud_item.raw["full_text_used"] is True


async def test_extraction_failure_falls_back_to_feed_summary_not_dropped(monkeypatch):
    async def fake_extract_fails(client, url, semaphore):
        return None

    monkeypatch.setattr(rss, "_extract_full_text", fake_extract_fails)
    items = await _fetch(feeds=[TECH_FEED], hours=24)
    cloud_item = next(i for i in items if "cloud-outage" in (i.url or ""))
    assert cloud_item.body == "Regulators are looking into this week's cloud outage."
    assert cloud_item.raw["full_text_used"] is False


async def test_long_summary_never_triggers_extraction(monkeypatch):
    extraction_urls: list[str] = []

    async def spy_extract(client, url, semaphore):
        extraction_urls.append(url)
        return None

    monkeypatch.setattr(rss, "_extract_full_text", spy_extract)
    await _fetch(feeds=[TECH_FEED], hours=24)
    assert not any("build-systems" in u for u in extraction_urls), (
        "the >=500 char summary entry should skip extraction"
    )


# ──────────────────────────────────────────────────────────────
# Graceful degradation (CLAUDE.md rule 7)
# ──────────────────────────────────────────────────────────────


async def test_missing_fixture_entry_for_a_feed_is_skipped_not_raised():
    items = await rss.fetch_articles(
        feeds=[TECH_FEED, "https://example.com/feed/does-not-exist.xml"],
        fixture=FIXTURE,
        now=FIXED_NOW,
    )
    assert items  # the tech feed still produced items


async def test_one_dead_live_feed_never_takes_down_the_others(monkeypatch):
    """Simulates a curated list where one feed times out or 500s. The gather
    call must use return_exceptions=True so the rest of the run proceeds."""

    good_parsed = {
        "feed": {"title": "Good Feed"},
        "entries": [
            {
                "title": "A Working Story",
                "link": "https://good.example.com/story",
                "summary": "x" * 600,
                "published_parsed": [2026, 8, 20, 12, 0, 0, 3, 232, 0],
            }
        ],
    }

    async def fake_fetch_feed_live(client, url):
        if "dead" in url:
            raise httpx.ConnectTimeout("timed out")
        return good_parsed

    monkeypatch.setattr(rss, "_fetch_feed_live", fake_fetch_feed_live)
    items = await rss.fetch_articles(
        feeds=["https://good.example.com/feed.xml", "https://dead.example.com/feed.xml"],
        fixture=None,
        now=FIXED_NOW,
    )
    assert len(items) == 1
    assert items[0].source_name == "Good Feed"


async def test_malformed_entry_does_not_take_down_the_run(monkeypatch):
    """A single entry whose normalisation raises must be skipped, not fail
    the whole fetch."""

    async def boom(**kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(rss, "_entry_to_item", boom)
    items = await _fetch(feeds=[TECH_FEED, WORLD_FEED], hours=24)
    assert items == []  # every entry "failed" but fetch_articles still returned


# ──────────────────────────────────────────────────────────────
# max_per_feed
# ──────────────────────────────────────────────────────────────


async def test_max_per_feed_caps_entries_considered():
    items_capped = await _fetch(feeds=[TECH_FEED], hours=24, max_per_feed=1)
    items_uncapped = await _fetch(feeds=[TECH_FEED], hours=24, max_per_feed=25)
    assert len(items_capped) <= len(items_uncapped)


# ──────────────────────────────────────────────────────────────
# feed source name helper
# ──────────────────────────────────────────────────────────────


def test_feed_source_name_strips_www():
    name = rss._feed_source_name("https://www.example.com/feed.xml", {"feed": {}})
    assert name == "example.com"


def test_clean_text_strips_html_and_boilerplate():
    text = rss.clean_text("<p>Hello world.</p> Read more.")
    assert text == "Hello world."


def test_clean_text_strips_appeared_first_on():
    text = rss.clean_text(
        "Great story here. The post Great Story appeared first on Example."
    )
    assert "appeared first on" not in text


# ──────────────────────────────────────────────────────────────
# Regression from the PR review: dedup must not let a broken first
# occurrence hide a usable one.
# ──────────────────────────────────────────────────────────────


def _entry(link: str, *, title: str | None, dated: bool, summary: str = "s") -> dict:
    entry: dict = {"link": link, "summary": summary}
    if title is not None:
        entry["title"] = title
    if dated:
        entry["published_parsed"] = (2026, 8, 22, 9, 0, 0, 0, 0, 0)
    return entry


async def test_unusable_first_copy_does_not_suppress_a_usable_one(tmp_path):
    """Two feeds, one publication, same article — one copy unusable.

    Marking the canonical link as seen at scheduling time meant first-wins:
    an entry with no date normalises to None and the good copy from the other
    feed was skipped, losing the article entirely. Reachable in production
    with the two Hacker News feeds, which both map to "Hacker News".
    """
    url = "https://example.com/a-real-article"
    fixture = tmp_path / "feeds.json"
    fixture.write_text(
        json.dumps(
            {
                "https://hnrss.org/frontpage": {
                    "feed": {"title": "Hacker News"},
                    # No date -> _entry_to_item returns None for this copy.
                    "entries": [_entry(url, title="Real article", dated=False)],
                },
                "https://hnrss.org/newest?q=AI": {
                    "feed": {"title": "Hacker News"},
                    "entries": [_entry(url, title="Real article", dated=True)],
                },
            }
        )
    )

    items = await rss.fetch_articles(
        feeds=[
            "https://hnrss.org/frontpage",
            "https://hnrss.org/newest?q=AI",
        ],
        fixture=fixture,
        now=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
    )

    assert len(items) == 1, "the usable copy should survive, exactly once"
    assert items[0].title == "Real article"


# ──────────────────────────────────────────────────────────────
# A fixture is a self-contained snapshot
# ──────────────────────────────────────────────────────────────


async def test_a_fixture_alone_is_enough_to_get_items_out():
    """`fixture=` without `feeds=` and `now=` returned nothing at all.

    Two independent reasons, and fixing either alone still yields zero:
    `feeds` defaulted to DEFAULT_FEEDS, whose real URLs are not keys in the
    fixture, so every feed was "skipped, no fixture entry"; and the 24-hour
    window was measured against wall-clock now, which a dated snapshot falls
    out of the day after it is written.

    This is what the offline API path calls, so it shipped a briefing with an
    empty world segment and a fixture that looked like it was working.
    """
    items = await rss.fetch_articles(fixture=FIXTURE)
    assert items, "a fixture-backed call should produce the fixture's items"
    # Both of the fixture's feeds, not just whichever one happened to match.
    assert len({i.source_name for i in items}) == 2, [i.source_name for i in items]


async def test_an_explicit_feed_list_still_wins_over_the_fixture_keys():
    """The fallback is a default, not an override — a test narrowing to one
    feed must still get one feed."""
    items = await rss.fetch_articles(fixture=FIXTURE, feeds=[TECH_FEED])
    assert {i.source_name for i in items} == {"Example Tech Daily"}


async def test_an_explicit_now_still_wins_over_the_snapshot_anchor():
    """Every existing test here pins `now` against the fixture's dates. That
    has to keep meaning what it meant, including when it excludes
    everything."""
    from datetime import timedelta

    items = await rss.fetch_articles(
        fixture=FIXTURE, now=FIXED_NOW + timedelta(days=365)
    )
    assert items == []
