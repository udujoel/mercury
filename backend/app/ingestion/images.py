"""
Finding a story's lead image in a feed entry.

Feeds put images in at least four places and agree on none of them, so this
is a search rather than a field read: Media RSS `media:content`, Media RSS
`media:thumbnail`, an RSS `enclosure`, and — last, because it is the least
reliable — the first `<img>` in the entry's own HTML.

**URLs only, never bytes.** The image stays on the publisher's CDN. That
keeps R2 small, keeps the retention sweep honest (nothing outlives the story
it belongs to, because there is nothing extra to delete), and means Mercury
never redistributes an image it has no licence to.

The bar for accepting one is deliberately high. A wrong image is worse than
none: a story card with a tracking pixel stretched across it, or with another
story's art, is more obviously broken than a card with no picture. So
anything that is not plainly a content image — a 1x1 beacon, a `data:` URI,
a non-image enclosure, a bare scheme-relative fragment — is rejected rather
than guessed at.
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urljoin, urlparse

# Media RSS `medium` is a free-text field; only these mean "a picture".
_IMAGE_MEDIUMS = {"image", "photo"}

# Extensions worth accepting when no MIME type is offered. Deliberately not
# exhaustive: an unrecognised extension falls through to the next candidate
# rather than being accepted on faith.
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")

# Tracking pixels are served as ordinary images from ordinary hosts, so the
# only tell in a feed is the declared size.
_MIN_PIXELS = 64

# The whole tag, so its own width/height can be read. Capturing only `src`
# meant a beacon declared `<img src="…" width="1" height="1">` skipped the
# size check every feed node goes through — the fallback path quietly held
# to a lower standard than the paths above it.
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_ATTR = re.compile(r"""\b(src|width|height)\s*=\s*["']([^"']*)["']""", re.I)


def _img_node(tag: str) -> dict[str, Any]:
    """An `<img>` tag as the same shape a feed node has, so one set of
    eligibility rules covers both."""
    attrs = {k.lower(): v for k, v in _ATTR.findall(tag)}
    return {
        # HTML-encoded, so `&amp;` in a query string arrives as a literal
        # five characters. Stored that way a signed CDN URL fails its own
        # signature check and the image silently 403s.
        "url": html.unescape(attrs.get("src", "")),
        "width": attrs.get("width"),
        "height": attrs.get("height"),
    }


def _plausible(url: str | None, *, base: str | None = None) -> str | None:
    """A usable absolute http(s) image URL, or None.

    `base` is the entry's own link, used to resolve the relative `src`
    values that appear in feed HTML. Without it a relative path would be
    stored as-is and render as a broken image on every client.
    """
    if not url:
        return None
    url = url.strip()
    if not url or url.startswith("data:"):
        return None

    # `urlparse` RAISES on some malformed input — `https://[` is a
    # ValueError, not a parse with empty fields. Uncaught it escapes
    # `_entry_to_item` into rss.py's per-entry handler, which drops the whole
    # story: one bad image URL costing an article, and the log saying only
    # that an entry failed.
    try:
        if base and not urlparse(url).scheme:
            url = urljoin(base, url)
        parsed = urlparse(url)
        # `urlparse` defers port validation until the property is READ, so
        # `https://host:99999/x.jpg` parses happily and only raises here.
        # Reading it inside the try is what turns that into another rejected
        # candidate rather than a stored URL nothing can load. Assigned
        # rather than left bare so it does not read as dead code.
        _port = parsed.port  # noqa: F841 - read for its ValueError
    except ValueError:
        return None

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def _big_enough(node: dict[str, Any]) -> bool:
    """False for anything declaring itself smaller than a favicon.

    Feeds carry tracking beacons as `media:content` entries with width and
    height of 1. Missing dimensions are accepted — most feeds omit them, and
    refusing those would reject nearly every real image.
    """
    for key in ("width", "height"):
        raw = node.get(key)
        if raw in (None, ""):
            continue
        try:
            if int(str(raw).strip()) < _MIN_PIXELS:
                return False
        except ValueError:
            continue
    return True


def _looks_like_image(node: dict[str, Any]) -> bool:
    mime = str(node.get("type") or "").lower()
    if mime:
        return mime.startswith("image/")

    medium = str(node.get("medium") or "").lower()
    if medium:
        return medium in _IMAGE_MEDIUMS

    url = str(node.get("url") or node.get("href") or "").lower()
    path = urlparse(url).path
    return path.endswith(_IMAGE_SUFFIXES)


def _from_nodes(nodes: Any, base: str | None) -> str | None:
    if not isinstance(nodes, list):
        nodes = [nodes] if isinstance(nodes, dict) else []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if not _looks_like_image(node) or not _big_enough(node):
            continue
        found = _plausible(node.get("url") or node.get("href"), base=base)
        if found:
            return found
    return None


def image_from_entry(entry: dict[str, Any], *, link: str | None = None) -> str | None:
    """The lead image for one feed entry, or None.

    Ordered by how much the source is actually asserting. `media:content` is
    a publisher saying "this is the media for this item"; an `<img>` scraped
    out of the summary might be a logo, a byline portrait, or a share button.
    """
    for key in ("media_content", "media_thumbnail"):
        found = _from_nodes(entry.get(key), link)
        if found:
            return found

    found = _from_nodes(entry.get("enclosures"), link)
    if found:
        return found

    # Last resort. `feedparser` puts the entry's HTML in `content` or
    # `summary`, and the first `<img>` in it is usually the lead art —
    # usually being exactly why this is last.
    html_parts: list[str] = []
    content = entry.get("content")
    if isinstance(content, list):
        html_parts += [str(c.get("value", "")) for c in content if isinstance(c, dict)]
    for key in ("summary", "description"):
        value = entry.get(key)
        if isinstance(value, str):
            html_parts.append(value)

    for markup in html_parts:
        for match in _IMG_TAG.finditer(markup):
            node = _img_node(match.group(0))
            # The same eligibility rules the feed nodes go through. A tracking
            # beacon is far MORE likely here than in `media:content` — it is
            # the first thing in the body of a great many newsletters.
            if not _big_enough(node):
                continue
            found = _plausible(node["url"], base=link)
            if found:
                return found

    return None
