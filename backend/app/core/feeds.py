"""
Curated default RSS/news feeds, grouped by topic.

This is a STARTING POINT, not a product decision. The user is expected to
edit this list to match their own interests — swap publications, add a
subreddit's RSS bridge, drop a topic entirely. Nothing about the pipeline
depends on these specific URLs; `app.ingestion.rss.fetch_articles` accepts
an explicit `feeds=` list and only falls back to `DEFAULT_FEEDS` when the
caller passes none.

Kept deliberately small (3-5 feeds per topic) and checked live before being
added here — a curated list that quietly goes stale is worse than an empty
one. If a feed here starts dying, replace it rather than leaving it.
"""

from __future__ import annotations

DEFAULT_FEEDS: dict[str, list[str]] = {
    "tech": [
        "https://feeds.arstechnica.com/arstechnica/index",
        "https://www.theverge.com/rss/index.xml",
        "https://stratechery.com/feed/",
        "https://hnrss.org/frontpage",
    ],
    "business": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://fortune.com/feed/",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://www.marketwatch.com/rss/topstories",
    ],
    "world": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "https://www.economist.com/the-world-this-week/rss.xml",
    ],
    "science": [
        "https://www.sciencedaily.com/rss/all.xml",
        "https://www.nature.com/nature.rss",
    ],
    "ai": [
        "https://arstechnica.com/ai/feed/",
        "https://www.technologyreview.com/feed/",
        "https://hnrss.org/newest?q=AI",
    ],
}


# ──────────────────────────────────────────────────────────────
# Spoken publication names
# ──────────────────────────────────────────────────────────────

# `Item.source_name` is not metadata — the hosts SAY it out loud, and it is
# what show notes display. Feed <title> elements are written for readers, not
# listeners, and a live run produced these:
#
#     "US Top News and Analysis"                                  (CNBC)
#     "Al Jazeera – Breaking News, World News and Video from Al Jazeera"
#     "NYT > World News"
#     "Latest Science News -- ScienceDaily"
#     "Hacker News - Newest: \"AI\""
#
# A host reading "according to US Top News and Analysis" is a broken product.
# Map the domain to what a person would actually call the publication.
PUBLICATION_NAMES: dict[str, str] = {
    "arstechnica.com": "Ars Technica",
    "feeds.arstechnica.com": "Ars Technica",
    "theverge.com": "The Verge",
    "stratechery.com": "Stratechery",
    "hnrss.org": "Hacker News",
    "bbci.co.uk": "the BBC",
    "feeds.bbci.co.uk": "the BBC",
    "fortune.com": "Fortune",
    "cnbc.com": "CNBC",
    "marketwatch.com": "MarketWatch",
    "aljazeera.com": "Al Jazeera",
    "nytimes.com": "the New York Times",
    "rss.nytimes.com": "the New York Times",
    "reuters.com": "Reuters",
    "theguardian.com": "the Guardian",
    "sciencedaily.com": "ScienceDaily",
    "phys.org": "Phys.org",
    "nature.com": "Nature",
    "technologyreview.com": "MIT Technology Review",
    "deepmind.com": "DeepMind",
    "openai.com": "OpenAI",
}

# Trailing cruft feeds append to their own titles. Used only when a domain is
# not in the map above, so an unknown feed still reads acceptably aloud.
TITLE_SEPARATORS = (" – ", " — ", " | ", " > ", " -- ", ": ", " - ")

TITLE_NOISE_PREFIXES = ("latest ", "the latest ", "all ")
