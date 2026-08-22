# RSS/news ingestion — Phase 2, week 8

`app/ingestion/rss.py` fetches a curated list of feeds (`app/core/feeds.py`,
`DEFAULT_FEEDS`), fetches them concurrently, and normalises entries into
`Item`s (`source_type=SourceType.RSS`), upgrading thin feed summaries to
full article text with trafilatura when it's worth the cost.

## Contract

```python
async def fetch_articles(
    *,
    feeds: list[str] | None = None,
    fixture: Path | None = None,
    hours: int = 24,
    max_per_feed: int = 25,
    now: datetime | None = None,   # test-only anchor for the recency window
) -> list[Item]: ...
```

`now` is an addition beyond the four contracted kwargs: an optional testing
hook so fixture-based recency assertions stay pinned to fixed dates instead
of rotting as wall-clock time moves forward. It defaults to `None` (real
`datetime.now(UTC)`), so callers using only the four contracted kwargs see
identical behaviour.

## What was proven offline (27 tests, all against fixtures, zero network)

`backend/tests/test_ingestion_rss.py` against `backend/tests/fixtures/rss_feeds.json`
(two feeds shaped like `feedparser.parse()`'s own output dict) and
`rss_article.html` (a realistic article page with byline, boilerplate,
share-bar and a "the post ... appeared first on ..." footer):

- Basic normalisation: `source_type`, `title`, `url` all populated.
- `source_name` — pulled from the feed channel's `<title>` (e.g. "Example
  Tech Daily"), falling back to the bare domain when the feed has no title.
- Title/summary cleaning — HTML stripped, whitespace collapsed, leading/
  trailing whitespace trimmed.
- Relative article links resolved against the feed URL.
- Recency — an entry ~11 years old is excluded at `hours=24` and included
  once the window is widened; an entry with **no** date field anywhere
  (`published_parsed` and `updated_parsed` both absent) is excluded rather
  than given a fabricated timestamp; an entry with only `updated_parsed`
  is still picked up.
- `entry_timestamp()` unit-tested directly against missing and malformed
  `published_parsed` structs.
- Dedup — two entries in the same feed pointing at the same article under
  different GUIDs and different tracking query strings (`utm_source=rss`
  vs `utm_source=newsletter`, one also carrying `ref=homepage`) collapse to
  one `Item`; `canonicalize_url()` unit-tested for stripping `utm_*`/`ref`
  while keeping ordinary params like `?id=42`; a direct check that two URLs
  differing only by tracking params land on the same `Item.id`.
- Full-text extraction — a short (<500 char) summary triggers extraction
  (`_extract_full_text` monkeypatched to run real trafilatura against the
  local `rss_article.html` fixture, proving the actual extraction call
  works end to end offline); extraction failure (monkeypatched to return
  `None`) falls back to the feed summary and the item is **not** dropped; a
  long (≥500 char) summary is confirmed to never trigger extraction at all
  (spied, not just mocked).
- Graceful degradation (CLAUDE.md rule 7) — a feed URL missing from the
  fixture is skipped, not raised; a live-path test monkeypatches
  `_fetch_feed_live` so one feed raises `httpx.ConnectTimeout` and the
  other succeeds, proving `asyncio.gather(..., return_exceptions=True)`
  keeps the run alive; a monkeypatched `_entry_to_item` that always raises
  proves a malformed entry never takes down `fetch_articles`.
- `max_per_feed` caps entries considered.

```
$ ./.venv/bin/python -m pytest -q
.......................................................................
.......................................................................
.......................................................................
.......                                                                [100%]
239 passed, 1 warning in 5.37s
```

(The one warning is a pre-existing pydub/audioop `DeprecationWarning`,
unrelated to this work.)

```
$ ./.venv/bin/ruff check app/ingestion/rss.py app/core/feeds.py tests/test_ingestion_rss.py
All checks passed!
```

Two files outside this module's ownership currently fail
`ruff check .` (`tests/test_dedup.py`, four `E501` line-length errors) —
that belongs to the parallel clustering agent's work and was not touched
or introduced here.

## Live run — real numbers

`fetch_articles(hours=24*7, max_per_feed=25)` against all 17
`DEFAULT_FEEDS` URLs, no fixture, real network, run twice (numbers below
are from the second run, after fixing the redirect bug found in the
first — see "what misbehaved" below):

```
Feeds requested: 17
Elapsed: 14.9s
Total items: 290
Items per source:
  AI - Ars Technica: 7
  Al Jazeera – Breaking News, World News and Video from Al Jazeera: 25
  Ars Technica - All content: 20
  BBC News: 50                          (business + world feeds share this title)
  Fortune | FORTUNE: 10
  Hacker News - Newest: "AI": 20
  Hacker News: Front Page: 20
  Latest Science News -- ScienceDaily: 25
  MIT Technology Review: 10
  MarketWatch.com - Top Stories: 10
  NYT > World News: 25
  Nature: 25
  Stratechery by Ben Thompson: 4
  The Verge: 10
  The world this week: 4                (The Economist)
  US Top News and Analysis: 25          (CNBC's actual feed title)
Full-text extraction used: 245/290
```

17/17 feeds succeeded; 0 failed. Full-text extraction fired for 245 of 290
items (84%) — most feed summaries here are thin teasers under 500 characters,
so this is close to the ceiling for this feed set.

## Feeds that misbehaved, and how

**`fortune.com/feed/` and `marketwatch.com/rss/topstories` both 301-redirect
to a different URL** (`fortune.com/feed/fortune-feeds/?id=...` and
`feeds.marketwatch.com/marketwatch/topstories` respectively). The first live
run — before `follow_redirects=True` was added to the feed-fetch client —
failed both of them outright:

```
WARNING app.ingestion.rss: feed fetch failed for https://fortune.com/feed/:
  Redirect response '301 Moved Permanently' ...
WARNING app.ingestion.rss: feed fetch failed for https://www.marketwatch.com/rss/topstories:
  Redirect response '301 Found' ...
```

`httpx.AsyncClient` does not follow redirects by default. This is exactly
the class of bug the brief warned a fixture can't catch — `curl -L` and a
browser both "just work," which is precisely what hid it until the live
run. Fixed by passing `follow_redirects=True` on the feed-fetch `client.get`
call (article-page fetches for extraction already had it). Both feeds
succeeded on the second run with the two prior failures gone and the run
returning 270 → 290 items.

**One `MarkupResemblesLocatorWarning` from BeautifulSoup** on some short,
bare (no-HTML) author/title strings that "look like a filename" to bs4's
heuristics — cosmetic only, does not affect output; not fixed here since it
doesn't change behaviour and touching the warning filter felt out of scope
for this file's job.

**No feed returned HTML instead of XML** in this run (`_fetch_feed_live`'s
`bozo and not entries` guard exists for exactly that case but was not
exercised live — it is covered by intent, not by a live example).

## Gotchas / notes for downstream agents

- `source_name` is genuinely the feed's own `<title>`, which is sometimes
  surprising: CNBC's feed self-identifies as "US Top News and Analysis",
  not "CNBC". If the hosts saying this aloud sounds odd, that'll need a
  per-feed override map in a later phase — not solved here, flagged.
- Two different BBC feeds (world, business) both self-title "BBC News";
  `source_name` alone won't disambiguate them if that ever matters upstream.
- `hours=24*7` was used for the live run (not the `hours=24` default)
  because RSS-heavy feeds like Nature and ScienceDaily publish in bursts
  and a same-day-only window produced too few items to be a useful
  live-behaviour sample; the pipeline's actual nightly cron will use the
  real default.
- Extraction concurrency is capped at 5 concurrent fetches
  (`EXTRACTION_CONCURRENCY`) and each article fetch has its own 10s timeout,
  independent of the per-feed 10s timeout — a slow article page cannot stall
  feed parsing for other feeds, only its own entry.
- `DEFAULT_FEEDS` is intentionally small (2-4 per topic) and every URL was
  curl-checked live before being added; see the module docstring — it's a
  starting point the user is expected to edit, not a product decision.

## Files created

- `backend/app/ingestion/rss.py`
- `backend/app/core/feeds.py`
- `backend/tests/fixtures/rss_feeds.json`
- `backend/tests/fixtures/rss_article.html`
- `backend/tests/test_ingestion_rss.py`
- `docs/phase2/rss_ingestion.md` (this file)

## What was not verified

- Behaviour against a feed that returns HTML instead of XML (the `bozo`
  guard is written and unit-testable in principle but no live feed in the
  curated list happened to be in that state during this run).
- Behaviour under a genuinely slow/hanging feed (timeout path is exercised
  only via the monkeypatched `httpx.ConnectTimeout` test, not a live slow
  server).
