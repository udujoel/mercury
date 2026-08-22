"""
RSS/news ingestion.

Fetches a curated list of feeds (see `app.core.feeds.DEFAULT_FEEDS`)
concurrently, normalises entries into `Item`s, and upgrades short feed
summaries to full article text with trafilatura when it is worth the cost.

CLAUDE.md rule 7 — graceful degradation. A curated list of twenty feeds
will always have one that times out, returns HTML instead of XML, or 500s.
Per-feed failures are caught, logged and skipped; they never take down the
rest of the run. The same applies to per-entry failures (a single malformed
entry) and to full-text extraction (a single failed fetch or parse falls
back to the feed's own summary, never drops the item).

Mirrors the shape of `app/ingestion/weather.py`: `fixture=<path>` reads a
JSON fixture instead of hitting the network; `fixture=None` hits the live,
keyless feeds. Unlike weather, RSS has many independent sources, so the
fixture is a dict keyed by feed URL, each value shaped like the dict
`feedparser.parse()` returns (`{"feed": {...}, "entries": [...]}`) — see
`backend/tests/fixtures/rss_feeds.json`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser
import httpx
import trafilatura
from bs4 import BeautifulSoup

from app.core.feeds import (
    DEFAULT_FEEDS,
    PUBLICATION_NAMES,
    TITLE_NOISE_PREFIXES,
    TITLE_SEPARATORS,
)
from app.core.net_guard import BlockedURLError, safe_get
from app.core.schemas import Item, SourceType

logger = logging.getLogger(__name__)

FEED_TIMEOUT_S = 10.0
EXTRACTION_TIMEOUT_S = 10.0
# Below this many characters, the feed summary is thin enough that fetching
# the full article is worth the cost.
EXTRACTION_MIN_CHARS = 500
EXTRACTION_CONCURRENCY = 5

# Tolerance for a publisher's clock running slightly fast.
MAX_CLOCK_SKEW = timedelta(hours=2)

USER_AGENT = "Mercury/0.1 (+https://github.com/; personal briefing app)"

# Query params that differentiate an otherwise-identical URL and would
# defeat `Item.id`'s URL-based dedup if left in place.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_EXACT = {"ref", "ref_src", "fbclid", "gclid", "igshid", "mc_cid", "mc_eid"}

_BOILERPLATE_PATTERNS = [
    re.compile(r"\bread more\.?\s*$", re.IGNORECASE),
    re.compile(r"\bcontinue reading\.?\s*$", re.IGNORECASE),
    re.compile(r"the post .+? appeared first on .+?\.?\s*$", re.IGNORECASE),
    re.compile(r"\bshare (this|on) (facebook|twitter|linkedin)\S*\s*$", re.IGNORECASE),
]


# ──────────────────────────────────────────────────────────────
# URL canonicalisation
# ──────────────────────────────────────────────────────────────


def _is_tracking_param(name: str) -> bool:
    name = name.lower()
    return name.startswith(_TRACKING_PREFIXES) or name in _TRACKING_EXACT


def canonicalize_url(url: str, *, base: str | None = None) -> str:
    """Resolve a relative URL against `base`, strip tracking query params
    and any fragment, so the same article behind different tracking tags
    collapses to a single `Item.id` (which is a hash of source+url+ts)."""
    if not url:
        return url
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(k)
    ]
    new_query = urlencode(kept)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, ""))


# ──────────────────────────────────────────────────────────────
# Cleaning
# ──────────────────────────────────────────────────────────────


def clean_text(html_or_text: str) -> str:
    """Strip HTML, collapse whitespace, drop trailing boilerplate."""
    if not html_or_text:
        return ""
    soup = BeautifulSoup(html_or_text, "html.parser")
    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    for pattern in _BOILERPLATE_PATTERNS:
        text = pattern.sub("", text).strip()
    return text


# ──────────────────────────────────────────────────────────────
# Dates — feed date fields are a mess in practice
# ──────────────────────────────────────────────────────────────


def entry_timestamp(entry: dict[str, Any]) -> datetime | None:
    """Return a timezone-aware UTC timestamp for a feed entry, or None.

    feedparser already normalises `published_parsed`/`updated_parsed` to a
    UTC `time.struct_time` when it can determine one, handling most of the
    zoo of real-world date formats and timezone offsets. What's left for us
    to handle: the field missing entirely, present but malformed/short, or
    (in a JSON fixture) a plain list standing in for a struct_time. When no
    usable date can be established, we exclude the item rather than
    fabricate a timestamp — CLAUDE.md rule 3, and simply good hygiene for a
    recency filter.
    """
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if not struct:
            continue
        try:
            parsed = datetime(*struct[:6], tzinfo=UTC)
        except (TypeError, ValueError):
            continue
        # Reject dates in the future. Misconfigured feeds publish these
        # routinely, and such an item is permanently "recent": it always
        # passes the recency window, and ranking.py clamps a negative age to
        # zero, giving it recency = exp(0) = 1.0 — the maximum — in every
        # briefing from now on. One bad timestamp would quietly outrank all
        # genuinely fresh news, forever.
        if parsed > datetime.now(UTC) + MAX_CLOCK_SKEW:
            logger.warning(
                "ignoring future-dated entry %r (%s)", entry.get("title"), parsed
            )
            continue
        return parsed
    return None


# ──────────────────────────────────────────────────────────────
# Full-text extraction
# ──────────────────────────────────────────────────────────────


async def _extract_full_text(
    client: httpx.AsyncClient, url: str, semaphore: asyncio.Semaphore
) -> str | None:
    """Fetch the linked article and extract its body with trafilatura.

    Any failure — network, timeout, non-HTML response, trafilatura finding
    nothing — returns None. The caller falls back to the feed summary;
    extraction failure must never drop the item.
    """
    async with semaphore:
        try:
            # safe_get validates every redirect hop against the SSRF guard and
            # caps the body size. These URLs come from third-party feeds, so
            # they are attacker-controlled input, not trusted config.
            resp = await safe_get(
                client,
                url,
                timeout=EXTRACTION_TIMEOUT_S,
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            html = resp.text
        except BlockedURLError as exc:
            logger.warning("refusing to fetch article URL %s: %s", url, exc)
            return None
        except Exception:
            logger.debug("full-text fetch failed for %s", url, exc_info=True)
            return None

        try:
            # trafilatura.extract is a synchronous, CPU-bound HTML parse.
            # Called directly it blocks the event loop, freezing every other
            # feed fetch and extraction for its duration — and ~84% of a live
            # run's articles need extraction. Run it in a thread, and keep it
            # inside the semaphore so the concurrency cap covers the parse and
            # not just the fetch.
            return await asyncio.to_thread(
                lambda: trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=False,
                    favor_recall=True,
                )
            )
        except Exception:
            logger.debug("trafilatura extraction raised for %s", url, exc_info=True)
            return None


# ──────────────────────────────────────────────────────────────
# Feed fetch
# ──────────────────────────────────────────────────────────────


async def _fetch_feed_live(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    # Several real feeds (fortune.com, marketwatch.com at time of writing)
    # 301 to a moved feed URL; follow it rather than treating it as dead.
    resp = await safe_get(
        client,
        url,
        timeout=FEED_TIMEOUT_S,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    # feedparser sets bozo=1 for malformed XML but still does its best to
    # parse what it can; a feed returning an HTML error page instead of XML
    # typically yields bozo=1 AND zero entries, which we treat as a failure.
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"unparseable feed ({parsed.get('bozo_exception')})")
    return parsed


def _load_fixture(fixture: Path) -> dict[str, Any]:
    return json.loads(fixture.read_text())


def _feed_source_name(url: str, parsed: dict[str, Any]) -> str:
    """The name a host will say out loud.

    Prefer the curated per-domain name. Feed <title> elements are written to
    be read, not spoken — a live run yielded "US Top News and Analysis" for
    CNBC and "Latest Science News -- ScienceDaily" — so an uncurated title is
    only a fallback, and gets trimmed at the first separator before use.
    """
    host = urlsplit(url).netloc.removeprefix("www.").lower()
    if host in PUBLICATION_NAMES:
        return PUBLICATION_NAMES[host]
    # Also try the registrable-ish suffix, so news.bbc.co.uk finds bbc.co.uk.
    parts = host.split(".")
    for i in range(1, len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in PUBLICATION_NAMES:
            return PUBLICATION_NAMES[candidate]

    title = (parsed.get("feed") or {}).get("title")
    if title:
        return _tidy_feed_title(clean_text(title))
    return host


def _tidy_feed_title(title: str) -> str:
    """Trim a reader-facing feed title down to something speakable."""
    for sep in TITLE_SEPARATORS:
        if sep in title:
            title = title.split(sep, 1)[0].strip()
            break
    lowered = title.lower()
    for prefix in TITLE_NOISE_PREFIXES:
        if lowered.startswith(prefix):
            title = title[len(prefix):].strip()
            break
    return title or "an unnamed feed"


# ──────────────────────────────────────────────────────────────
# Entry -> Item
# ──────────────────────────────────────────────────────────────


async def _entry_to_item(
    *,
    source_name: str,
    entry: dict[str, Any],
    feed_url: str,
    hours: int,
    now: datetime,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> Item | None:
    timestamp = entry_timestamp(entry)
    if timestamp is None:
        return None
    if timestamp < now - timedelta(hours=hours):
        return None

    title = clean_text(entry.get("title") or "")
    link_raw = entry.get("link") or ""
    if not title or not link_raw:
        return None
    link = canonicalize_url(link_raw, base=feed_url)

    summary = clean_text(entry.get("summary") or entry.get("description") or "")

    body = summary
    if len(summary) < EXTRACTION_MIN_CHARS:
        extracted = await _extract_full_text(client, link, semaphore)
        if extracted:
            extracted = clean_text(extracted)
            if len(extracted) > len(summary):
                body = extracted

    return Item(
        source_type=SourceType.RSS,
        source_name=source_name,
        title=title,
        body=body,
        url=link,
        author=clean_text(entry.get("author") or "") or None,
        timestamp=timestamp,
        raw={
            "feed_url": feed_url,
            "summary_len": len(summary),
            "body_len": len(body),
            "full_text_used": body != summary,
        },
    )


# ──────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────


async def fetch_articles(
    *,
    feeds: list[str] | None = None,
    fixture: Path | None = None,
    hours: int = 24,
    max_per_feed: int = 25,
    now: datetime | None = None,
) -> list[Item]:
    """Fetch and normalise recent RSS/news entries into `Item`s.

    feeds=None uses every URL in `app.core.feeds.DEFAULT_FEEDS`.
    fixture=<path> reads a JSON file shaped `{feed_url: feedparser-dict}`
    instead of hitting the network for feed fetches. fixture=None fetches
    every feed live and concurrently; a feed that times out, 404s, or
    returns unparseable content is logged and skipped — the rest of the run
    proceeds (CLAUDE.md rule 7).

    now=<datetime> overrides the recency-window anchor; it exists so tests
    can pin "now" against a fixture's fixed dates without the assertions
    rotting as wall-clock time moves on. Callers following the pipeline
    contract never need to pass it.
    """
    if feeds is None:
        feeds = [url for urls in DEFAULT_FEEDS.values() for url in urls]

    now = now or datetime.now(UTC)
    semaphore = asyncio.Semaphore(EXTRACTION_CONCURRENCY)

    async with httpx.AsyncClient() as client:
        if fixture is not None:
            fixture_data = _load_fixture(fixture)
            parsed_feeds: list[tuple[str, dict[str, Any]]] = []
            for url in feeds:
                if url not in fixture_data:
                    logger.warning("no fixture entry for feed %s; skipping", url)
                    continue
                parsed_feeds.append((url, fixture_data[url]))
        else:
            results = await asyncio.gather(
                *(_fetch_feed_live(client, url) for url in feeds),
                return_exceptions=True,
            )
            parsed_feeds = []
            for url, result in zip(feeds, results, strict=True):
                if isinstance(result, BaseException):
                    logger.warning("feed fetch failed for %s: %s", url, result)
                    continue
                parsed_feeds.append((url, result))

        # Choose ONE candidate per (publication, canonical url) before doing
        # any work.
        #
        # Two feeds can map to a single publication — Hacker News front page
        # and its AI feed both become "Hacker News" — and an article carried
        # by both produced two Items with an identical Item.id (the id hashes
        # source_name + url + timestamp). Live run before this: 187 items,
        # 186 distinct ids. The duplicate counted twice in cluster_items,
        # inflating Story.cluster_size and handing the article a fake
        # popularity boost via ranking's w_cluster term, plus a doubled
        # show-notes entry.
        #
        # Picking the BEST candidate rather than the first matters: an entry
        # with a missing date or title normalises to None, so first-wins
        # silently dropped an article whose other copy was perfectly usable.
        # Ranking by summary length too means we skip a full-text fetch when
        # one feed already carries the whole piece.
        def _candidate_rank(entry: dict[str, Any]) -> tuple[int, int]:
            usable = bool(entry_timestamp(entry)) and bool(entry.get("title"))
            summary = entry.get("summary") or entry.get("description") or ""
            return (1 if usable else 0, len(summary))

        chosen: dict[tuple[str, str], tuple[tuple[int, int], str, str, dict]] = {}
        unkeyed: list[tuple[str, str, dict[str, Any]]] = []

        for url, parsed in parsed_feeds:
            source_name = _feed_source_name(url, parsed)
            for entry in (parsed.get("entries") or [])[:max_per_feed]:
                canonical_link = canonicalize_url(entry.get("link") or "", base=url)
                if not canonical_link:
                    unkeyed.append((source_name, url, entry))
                    continue
                key = (source_name, canonical_link)
                rank = _candidate_rank(entry)
                existing = chosen.get(key)
                if existing is None or rank > existing[0]:
                    chosen[key] = (rank, source_name, url, entry)

        selected = [(s, u, e) for _rank, s, u, e in chosen.values()] + unkeyed

        entry_tasks = [
            _entry_to_item(
                source_name=source_name,
                entry=entry,
                feed_url=url,
                hours=hours,
                now=now,
                client=client,
                semaphore=semaphore,
            )
            for source_name, url, entry in selected
        ]

        results = await asyncio.gather(*entry_tasks, return_exceptions=True)

    # Dedup AFTER normalisation, not before it. Marking a canonical link as
    # seen at scheduling time meant the first occurrence won even when it was
    # unusable — an entry with a missing date or title normalises to None, and
    # a later, valid copy of the same article from another feed was then
    # skipped entirely. Reachable with the two Hacker News feeds, which both
    # map to the publication name "Hacker News".
    items: list[Item] = []
    seen: set[tuple[str, str | None]] = set()
    for result in results:
        if isinstance(result, BaseException):
            logger.warning("failed to normalise a feed entry", exc_info=result)
            continue
        if result is None:
            continue
        key = (result.source_name, result.url)
        if result.url and key in seen:
            continue
        if result.url:
            seen.add(key)
        items.append(result)
    return items
