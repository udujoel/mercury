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


def test_a_url_that_cannot_even_be_parsed_costs_the_image_not_the_story():
    """`urlparse` RAISES on some malformed input — `https://[` is a
    ValueError, not a parse with empty fields. Uncaught it escapes into
    rss.py's per-entry handler, which drops the whole entry: one bad image
    URL costing an article, with the log saying only that an entry failed."""
    entry = {
        "media_content": [{"url": "https://[", "medium": "image"}],
        "summary": '<img src="https://cdn.example.com/good.jpg">',
    }
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/good.jpg"


def test_a_malformed_url_with_no_alternative_is_simply_no_image():
    entry = {"media_content": [{"url": "https://[", "medium": "image"}]}
    assert image_from_entry(entry, link=LINK) is None


# ── the bullet list, normalised rather than rejected ──────────


def test_bullet_characters_the_model_adds_anyway_are_stripped():
    """The prompt says not to include them and models do it regardless. The
    client draws its own, so a literal "- " ships as a double bullet."""
    from app.pipeline.digest import StoryCard

    card = StoryCard(headline="h", points=["- four point two billion", "• regulators"])
    assert card.points == ["four point two billion", "regulators"]


def test_blank_points_are_dropped_not_rendered_as_empty_rows():
    from app.pipeline.digest import StoryCard

    assert StoryCard(headline="h", points=["real", "   ", ""]).points == ["real"]


def test_too_many_points_are_capped_rather_than_failing_the_card():
    """Rule 7 applied to a screen. Making 2-4 a hard requirement would mean
    one over-long list costs that story its whole summary — a headline with
    nothing under it, to punish a bullet."""
    from app.pipeline.digest import StoryCard

    card = StoryCard(headline="h", points=[f"p{i}" for i in range(9)])
    assert card.points == ["p0", "p1", "p2", "p3"]


def test_a_single_point_is_left_alone():
    """A thin card, not a broken one."""
    from app.pipeline.digest import StoryCard

    assert StoryCard(headline="h", points=["only one"]).points == ["only one"]


# ── the HTML fallback held to the same standard ───────────────


def test_a_beacon_in_the_body_is_skipped_like_one_in_a_feed_node():
    """Capturing only `src` meant the fallback path skipped the size check
    every feed node goes through — and a 1x1 in the body is far MORE likely
    than one in `media:content`."""
    entry = {
        "summary": '<img src="https://t.example.com/px.gif" width="1" height="1">'
                   '<img src="https://cdn.example.com/lead.jpg">'
    }
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/lead.jpg"


def test_an_html_entity_in_the_src_is_decoded():
    """`&amp;` stored literally makes a signed CDN URL fail its own
    signature check, and the image 403s with nothing in the log."""
    entry = {"summary": '<img src="https://cdn.example.com/a.jpg?w=1&amp;s=abc">'}
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/a.jpg?w=1&s=abc"


def test_a_port_out_of_range_is_rejected_rather_than_stored():
    """`urlparse` defers port validation until the property is READ, so this
    parses happily and only raises when something looks at it."""
    entry = {
        "media_content": [
            {"url": "https://cdn.example.com:99999/x.jpg", "medium": "image"}
        ],
        "summary": '<img src="https://cdn.example.com/ok.jpg">',
    }
    assert image_from_entry(entry, link=LINK) == "https://cdn.example.com/ok.jpg"


def test_a_leading_minus_on_a_figure_is_not_a_bullet_marker():
    """The one that reverses a fact.

    `lstrip("-*•… ")` ate the sign in "-5% revenue growth" and shipped
    "5% revenue growth": the same figure with the opposite meaning, in a
    form nothing downstream can detect as wrong. Rule 3 says claims are
    grounded in the source data, and a display transform that flips a
    number breaks that more quietly than a hallucination would.

    A bullet marker is a marker only when a space follows it.
    """
    from app.pipeline.digest import StoryCard

    card = StoryCard(headline="h", points=["-5% revenue growth", "- down again"])
    assert card.points == ["-5% revenue growth", "down again"]


def test_an_em_dash_opening_a_sentence_survives_when_it_is_not_a_bullet():
    from app.pipeline.digest import StoryCard

    assert StoryCard(headline="h", points=["—and then it closed"]).points == [
        "—and then it closed"
    ]


# ── the archive index, and dev/prod parity ────────────────────


def test_the_index_route_lists_only_briefings_with_playable_audio(tmp_path):
    """Mirrors `serveBriefingList` in the Worker, including the filter.

    The route exists at all because the client has ONE code path for the
    Worker and the local backend: without it a fresh checkout against
    localhost got a 404 and the archive read as empty — a screen that looks
    built and does nothing.
    """
    from app.synthesis.storage import MIN_PLAYABLE_AUDIO_BYTES, LocalFilesystemStorage

    storage = LocalFilesystemStorage(base_dir=tmp_path)

    (tmp_path / "2026-09-04.m4a").write_bytes(b"x" * (MIN_PLAYABLE_AUDIO_BYTES + 1))
    # An upload that died mid-flight. `exists()` is true and it is not audio.
    (tmp_path / "2026-09-03.m4a").write_bytes(b"")
    # A regeneration that failed after deleting the old file.
    (tmp_path / "2026-09-02.manifest.json").write_text("{}")

    assert storage.audio_exists("2026-09-04") is True
    assert storage.audio_exists("2026-09-03") is False
    assert storage.audio_exists("2026-09-02") is False
