"""
Finding a story's lead image in a feed entry.

Feeds agree on nothing here, so this is a search across four conventions
rather than a field read. The bar for accepting a candidate is deliberately
high: a story card with a tracking pixel stretched across it, or with another
story's art, is more obviously broken than a card with no picture at all.
"""

from __future__ import annotations

from app.ingestion.images import image_from_entry

LINK = "https://example.com/2026/09/story.html"


# ── the four places an image hides ────────────────────────────


def test_media_content_is_preferred_over_everything_else():
    """`media:content` is the publisher saying "this is the media for this
    item". An `<img>` scraped from the summary might be a logo or a byline
    portrait, so it comes last."""
    entry = {
        "media_content": [
            {"url": "https://cdn.example.com/lead.jpg", "medium": "image"}
        ],
        "media_thumbnail": [{"url": "https://cdn.example.com/thumb.jpg"}],
        "summary": '<img src="https://cdn.example.com/logo.png">',
    }
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/lead.jpg"


def test_media_thumbnail_when_there_is_no_media_content():
    entry = {"media_thumbnail": [{"url": "https://cdn.example.com/thumb.jpg"}]}
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/thumb.jpg"


def test_an_enclosure_counts_when_it_is_an_image():
    entry = {
        "enclosures": [
            {"href": "https://cdn.example.com/audio.mp3", "type": "audio/mpeg"},
            {"href": "https://cdn.example.com/pic.jpg", "type": "image/jpeg"},
        ]
    }
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/pic.jpg"


def test_the_first_img_in_the_body_is_the_last_resort():
    entry = {"content": [{"value": '<p>x</p><img src="https://cdn.example.com/a.png">'}]}
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/a.png"


def test_nothing_at_all_is_an_ordinary_answer():
    """Most newsletters, and any feed that carries no art. A story card
    without a picture is a normal card."""
    assert image_from_entry({"title": "no art here"}, link=LINK) is None


# ── refusing the things that would look broken ────────────────


def test_a_tracking_pixel_is_not_a_lead_image():
    """Feeds carry beacons as `media:content` with width and height of 1.
    Stretched across a story card it is a grey smear."""
    entry = {
        "media_content": [
            {"url": "https://t.example.com/px.gif", "medium": "image",
             "width": "1", "height": "1"},
            {"url": "https://cdn.example.com/real.jpg", "medium": "image",
             "width": "1200", "height": "630"},
        ]
    }
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/real.jpg"


def test_a_missing_size_is_accepted_because_most_feeds_omit_it():
    """Refusing undeclared sizes would reject nearly every real image."""
    entry = {
        "media_content": [{"url": "https://cdn.example.com/x.jpg", "medium": "image"}]
    }
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/x.jpg"


def test_a_non_numeric_size_does_not_crash_the_run():
    entry = {
        "media_content": [
            {"url": "https://cdn.example.com/x.jpg", "medium": "image", "width": "wide"}
        ]
    }
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/x.jpg"


def test_a_data_uri_is_refused():
    """Inline base64 is usually a placeholder or a spacer, and storing one
    puts the bytes in the digest — which is the thing this module exists to
    avoid doing."""
    entry = {"summary": '<img src="data:image/gif;base64,R0lGOD">'}
    assert image_from_entry(entry, link=LINK) is None


def test_a_non_image_enclosure_is_not_used_as_one():
    entry = {
        "enclosures": [{"href": "https://cdn.example.com/ep.mp3", "type": "audio/mpeg"}]
    }
    assert image_from_entry(entry, link=LINK) is None


def test_a_non_http_scheme_is_refused():
    entry = {"media_content": [{"url": "ftp://example.com/x.jpg", "medium": "image"}]}
    assert image_from_entry(entry, link=LINK) is None


# ── the relative-path case that would break every client ──────


def test_a_relative_src_is_resolved_against_the_entry_link():
    """Stored as-is it renders as a broken image everywhere, and the cause
    is invisible from the client."""
    entry = {"summary": '<img src="/img/lead.jpg">'}
    assert image_from_entry(entry, link=LINK) == "https://example.com/img/lead.jpg"


def test_a_relative_src_with_no_link_to_resolve_against_is_dropped():
    entry = {"summary": '<img src="/img/lead.jpg">'}
    assert image_from_entry(entry, link=None) is None


# ── shapes feedparser actually produces ───────────────────────


def test_a_bare_dict_is_handled_as_well_as_a_list():
    """`feedparser` gives a list, but fixtures and some parsers give one
    dict. A crash here would take down the whole feed under rule 7's
    per-entry catch, silently costing every story from that publication."""
    entry = {"media_thumbnail": {"url": "https://cdn.example.com/t.jpg"}}
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/t.jpg"


def test_an_extension_stands_in_for_a_missing_mime_type():
    entry = {"enclosures": [{"href": "https://cdn.example.com/pic.webp"}]}
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/pic.webp"


def test_an_unrecognised_extension_falls_through_rather_than_being_guessed():
    entry = {
        "enclosures": [{"href": "https://cdn.example.com/thing.bin"}],
        "summary": '<img src="https://cdn.example.com/real.png">',
    }
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/real.png"


def test_junk_entries_do_not_stop_a_good_one_being_found():
    entry = {"media_content": ["not a dict", {"url": "https://cdn.example.com/ok.jpg",
                                              "medium": "image"}]}
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/ok.jpg"


# ── the image reaching the digest ─────────────────────────────


def test_the_lead_image_prefers_the_canonical_members_art():
    """When a cluster spans publications, the canonical is the version the
    ranking judged most authoritative — and its art is the one most likely
    to be OF this story, rather than a stock photo the third aggregator to
    run it reached for."""
    from datetime import UTC, datetime

    from app.core.schemas import Item, SourceType, Story
    from app.pipeline.digest import _lead_image

    def _item(name: str, image: str | None) -> Item:
        return Item(
            source_type=SourceType.RSS, source_name=name, title=f"{name} on it",
            timestamp=datetime(2026, 9, 4, tzinfo=UTC), image_url=image,
        )

    aggregator = _item("Aggregator", "https://cdn/stock.jpg")
    reuters = _item("Reuters", "https://cdn/real.jpg")
    story = Story(cluster_id="c1", canonical=reuters, members=[aggregator, reuters])

    assert _lead_image(story, [aggregator, reuters]) == "https://cdn/real.jpg"


def test_the_lead_image_falls_back_to_any_member_that_has_one():
    from datetime import UTC, datetime

    from app.core.schemas import Item, SourceType, Story
    from app.pipeline.digest import _lead_image

    def _item(name: str, image: str | None) -> Item:
        return Item(
            source_type=SourceType.RSS, source_name=name, title=f"{name} on it",
            timestamp=datetime(2026, 9, 4, tzinfo=UTC), image_url=image,
        )

    plain = _item("Newsletter", None)
    illustrated = _item("Reuters", "https://cdn/real.jpg")
    story = Story(cluster_id="c1", canonical=plain, members=[plain, illustrated])

    assert _lead_image(story, [plain, illustrated]) == "https://cdn/real.jpg"


def test_a_newsletter_only_story_simply_has_no_image():
    """Rule 5 discards the body a newsletter's images live in, so there is
    nowhere to get one. On a quiet morning this is most of the desk."""
    from datetime import UTC, datetime

    from app.core.schemas import Item, SourceType, Story
    from app.pipeline.digest import _lead_image

    email = Item(
        source_type=SourceType.EMAIL, source_name="Stratechery", title="Monday",
        timestamp=datetime(2026, 9, 4, tzinfo=UTC),
    )
    story = Story(cluster_id="c1", canonical=email, members=[email])

    assert _lead_image(story, [email]) is None
