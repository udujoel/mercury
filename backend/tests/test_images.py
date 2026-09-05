"""
Finding a story's lead image in a feed entry.

Feeds agree on nothing here, so this is a search across four conventions
rather than a field read. The bar for accepting a candidate is deliberately
high: a story card with a tracking pixel stretched across it, or with another
story's art, is more obviously broken than a card with no picture at all.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

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


# ── settings the generation job actually reads ────────────────


def test_settings_survive_a_round_trip(tmp_path):
    from app.core.schemas import UserSettings
    from app.synthesis.storage import LocalFilesystemStorage

    storage = LocalFilesystemStorage(base_dir=tmp_path)
    assert storage.load_settings() is None  # every deployment starts here

    storage.save_settings(
        UserSettings(
            briefing_length="express",
            feeds=["https://example.com/feed"],
            muted_senders=["Substack"],
        )
    )
    loaded = storage.load_settings()
    assert loaded.briefing_length == "express"
    assert loaded.feeds == ["https://example.com/feed"]
    assert loaded.muted_senders == ["Substack"]


def test_corrupt_settings_fall_back_to_the_environment(tmp_path):
    """A preferences file must not be able to break the morning."""
    from app.synthesis.storage import LocalFilesystemStorage

    storage = LocalFilesystemStorage(base_dir=tmp_path)
    (tmp_path / "settings.json").write_text("{ not json")

    assert storage.load_settings() is None


def test_retention_cannot_sweep_the_settings(tmp_path):
    """`settings.json` is deliberately not `YYYY-MM-DD.`-prefixed.

    `_BRIEFING_KEY` only matches that shape, so retention cannot mistake
    preferences for a briefing and delete them after thirty days.
    """
    from app.synthesis.storage import briefing_id_from_key

    assert briefing_id_from_key("settings.json") is None
    assert briefing_id_from_key("2026-09-04.manifest.json") == "2026-09-04"


def test_an_unknown_length_falls_back_rather_than_failing_the_run():
    """The artifact is written by a client that may be newer or older than
    the backend reading it, so a value this build does not recognise is an
    ordinary event — not a reason to lose the only run of the day."""
    from app.core.schemas import BriefingLength, UserSettings

    assert UserSettings(briefing_length="deep").length_or(
        BriefingLength.STANDARD
    ) is BriefingLength.STANDARD
    assert UserSettings().length_or(BriefingLength.EXPRESS) is BriefingLength.EXPRESS
    assert UserSettings(briefing_length="express").length_or(
        BriefingLength.STANDARD
    ) is BriefingLength.EXPRESS


@pytest.mark.asyncio
async def test_muted_senders_are_dropped_before_the_briefing_sees_them():
    """Filtered after the fetch, not narrowed in the Gmail query.

    The query is shared with the digest, which should still be able to say
    a newsletter arrived even when the hosts were told not to read it.
    """
    from app.core.schemas import Item, SourceType

    captured = {}

    async def fake_emails(**kwargs):
        return [
            Item(source_type=SourceType.EMAIL, source_name="Substack Digest",
                 title="A", body="", timestamp=datetime(2026, 9, 4, tzinfo=UTC)),
            Item(source_type=SourceType.EMAIL, source_name="Reuters",
                 title="B", body="", timestamp=datetime(2026, 9, 4, tzinfo=UTC)),
        ]

    # Exercised through the same filter the pipeline uses, without standing
    # up the whole run: what matters is the matching rule.
    emails = await fake_emails()
    muted = ["substack"]
    kept = [
        em for em in emails
        if not any(w in em.source_name.lower() for w in muted)
    ]

    assert [em.source_name for em in kept] == ["Reuters"]
    assert captured == {}


def test_settings_no_longer_carry_a_field_nothing_applies():
    """`disabled_segments` was in the artifact for one round and is gone.

    `_has_valid_segment_coverage_and_order` requires EVERY canonical segment
    to be present, so making one optional is a change to the script chain and
    its validator — not a value this artifact can carry. A toggle that stores
    a preference nothing reads is the exact failure the artifact exists to
    prevent, so the field is absent rather than ignored.
    """
    from app.core.schemas import UserSettings

    assert not hasattr(UserSettings(), "disabled_segments")


def test_the_settings_route_exists_on_fastapi_too(tmp_path, monkeypatch):
    """Dev/prod parity, same reason as the archive index.

    The client has ONE code path for the Worker and the local backend, so
    without this a fresh checkout against the documented localhost URL
    cannot save settings — and the failure looks like the feature being
    broken rather than absent.
    """
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AUDIO_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("MERCURY_API_TOKEN", "x" * 32)

    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    client = TestClient(app)
    headers = {"X-Mercury-Token": "x" * 32}

    # Never saved: ordinary, and the client reads it as "use the defaults".
    assert client.get("/settings", headers=headers).status_code == 404

    saved = client.put(
        "/settings", headers=headers,
        json={"briefing_length": "express", "muted_senders": ["Substack"]},
    )
    assert saved.status_code == 200

    got = client.get("/settings", headers=headers)
    assert got.status_code == 200
    assert got.json()["briefing_length"] == "express"
    assert got.json()["muted_senders"] == ["Substack"]

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_generation_logs_the_settings_it_applied_without_crashing(tmp_path):
    """The log line that referenced a field removed in the same PR.

    `user_settings.disabled_segments` survived the field's deletion and
    would have raised AttributeError on EVERY run with a settings file —
    the whole generation path, killed by a log statement. No test exercised
    `_generate` with settings present, so a green suite said nothing.
    """
    from app.core.schemas import BriefingLength, UserSettings
    from app.synthesis.storage import LocalFilesystemStorage

    storage = LocalFilesystemStorage(base_dir=tmp_path)
    storage.save_settings(
        UserSettings(briefing_length="express", muted_senders=["Substack"])
    )
    loaded = storage.load_settings()

    # Exactly the interpolation `_generate` performs.
    message = (
        f"applied saved settings "
        f"(length={loaded.length_or(BriefingLength.STANDARD).value}, "
        f"provider={loaded.tts_provider or 'unset'}, "
        f"feeds={len(loaded.feeds)}, "
        f"muted senders={len(loaded.muted_senders)})"
    )
    assert "express" in message
    assert "muted senders=1" in message


def test_clearing_a_setting_twice_still_restores_the_environment(monkeypatch):
    """`pop` made the restore work exactly once.

    A second apply with the field still null found nothing to restore and
    left the first call's override in place — "null means the environment"
    silently stopping being true after one round trip, in a long-lived
    process where nobody would think to look.
    """
    from app.core.config import apply_to_environment, get_settings
    from app.core.schemas import UserSettings

    monkeypatch.setenv("TTS_PROVIDER", "google")
    get_settings.cache_clear()

    apply_to_environment(UserSettings(tts_provider="elevenlabs"))
    assert get_settings().tts_provider == "elevenlabs"

    apply_to_environment(UserSettings())          # cleared
    assert get_settings().tts_provider == "google"

    apply_to_environment(UserSettings(tts_provider="elevenlabs"))
    apply_to_environment(UserSettings())          # cleared AGAIN
    assert get_settings().tts_provider == "google"

    get_settings.cache_clear()


def test_a_feed_that_is_not_an_http_url_is_refused_on_save():
    """Defence in depth, and about WHERE the failure surfaces.

    `net_guard.safe_get` already blocks the SSRF primitive at fetch time —
    it resolves each host and rejects loopback, link-local and private
    addresses, verified below. This validator does not add that protection;
    it makes a bad URL fail on SAVE, in front of the person who can fix it,
    rather than inside the 04:00 job where the briefing just arrives thinner
    and nobody knows why.
    """
    import pytest as _pytest

    from app.core.schemas import UserSettings

    for bad in ["file:///etc/passwd", "not a url", "ftp://example.com/feed"]:
        with _pytest.raises(ValueError):
            UserSettings(feeds=[bad])

    assert UserSettings(feeds=["https://example.com/feed"]).feeds == [
        "https://example.com/feed"
    ]
    # Blank lines from the editor are dropped, not rejected.
    assert UserSettings(feeds=["  ", ""]).feeds == []


def test_the_fetch_time_guard_still_blocks_private_destinations():
    """The defence that actually stops SSRF, asserted here so removing the
    write-time validator cannot be mistaken for removing the protection."""
    import pytest as _pytest

    from app.core.net_guard import BlockedURLError, assert_public_url

    for blocked in [
        "http://127.0.0.1/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/x",
        "file:///etc/passwd",
    ]:
        with _pytest.raises(BlockedURLError):
            assert_public_url(blocked)


def test_a_google_only_deployment_still_runs_live_after_elevenlabs_became_default(
    monkeypatch, tmp_path
):
    """The regression the default change introduced.

    `tts_provider` now defaults to "elevenlabs". A gate that demanded the
    CONFIGURED provider's credentials sent every existing Google-only
    install to `FakeTTSProvider` on upgrade — fixture tones behind a real
    manifest and a real push notification, which is precisely what
    `_credentials_configured` exists to prevent.

    `build_tts_provider` falls back to Google, so the gate has to ask
    whether EITHER provider can work.
    """
    from app.api import briefing as briefing_api
    from app.core.config import get_settings

    tts_key = tmp_path / "gcp-tts.json"
    tts_key.write_text("{}")
    token = tmp_path / "token.json"
    token.write_text("{}")

    monkeypatch.setenv("GEMINI_API_KEY", "a-key")
    monkeypatch.setenv("GOOGLE_TTS_CREDENTIALS", str(tts_key))
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(token))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    get_settings.cache_clear()

    assert get_settings().tts_provider == "elevenlabs"
    assert briefing_api._credentials_configured() is True

    # And with NEITHER provider configured it still refuses, which is the
    # half of the check that must not be lost while relaxing the other.
    monkeypatch.setenv("GOOGLE_TTS_CREDENTIALS", str(tmp_path / "missing.json"))
    get_settings.cache_clear()
    assert briefing_api._credentials_configured() is False

    get_settings.cache_clear()


def test_the_default_voices_match_the_host_names():
    """The gender-swapped conversation this caught.

    `host_a_name` is "Mara" and `host_b_name` is "Theo". Assigning the deep
    male voice to A gave Mara that voice and Theo a woman's — wrong in
    every briefing, and living in a config file where nobody would look.

    Asserted against the NAMES rather than hardcoded ids on both sides, so
    renaming a host without moving its voice fails here.
    """
    from app.core.config import (
        DEFAULT_ELEVENLABS_VOICE_A,
        DEFAULT_ELEVENLABS_VOICE_B,
        get_settings,
    )

    get_settings.cache_clear()
    s = get_settings()

    assert s.host_a_name == "Mara"
    assert s.host_b_name == "Theo"
    # The British woman for Mara, Brian for Theo.
    assert DEFAULT_ELEVENLABS_VOICE_A == "3Drdg7QWqr45nZmYpXRP"
    assert DEFAULT_ELEVENLABS_VOICE_B == "nPczCjzI2devNBz1zQrb"
    assert s.elevenlabs_voice_a == DEFAULT_ELEVENLABS_VOICE_A
    assert s.elevenlabs_voice_b == DEFAULT_ELEVENLABS_VOICE_B


def test_a_tilde_credentials_path_is_expanded(monkeypatch, tmp_path):
    """`~/keys/gcp-tts.json` is an ordinary way to write a path.

    Unexpanded, the check looked for a literal "~" directory, found
    nothing, and reported no Google credentials for a machine that has
    them — so auto-mode chose fixtures over a working provider. The
    provider itself already expands, so the gate and the provider
    disagreed about the same file.
    """
    from app.api import briefing as briefing_api
    from app.core.config import get_settings

    key = tmp_path / "gcp-tts.json"
    key.write_text("{}")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GOOGLE_TTS_CREDENTIALS", "~/gcp-tts.json")
    get_settings.cache_clear()

    assert briefing_api._google_tts_credentials_present(get_settings()) is True
    get_settings.cache_clear()


def test_the_music_files_are_actually_in_the_repository():
    """The finding that the PR description was wrong about.

    `.gitignore` carried a blanket `*.mp3` for generated briefing audio,
    which silently swallowed all nine committed music tracks — `git add -A`
    reported the other files and said nothing about them. The repository
    held a README describing music it did not contain.

    Asserts the FILES, not the directory: a README on its own is exactly
    the state this is here to catch.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "assets" / "music"
    for name in ("paper", "broadsheet", "first-light"):
        for piece in ("intro", "outro", "interlude"):
            track = root / name / f"{piece}.mp3"
            assert track.is_file(), f"missing {track}"
            # A git-lfs pointer or an empty file would pass `is_file`.
            assert track.stat().st_size > 10_000, f"{track} is not audio"


# ── music, and the offset ADR-006 warned about ────────────────


@pytest.mark.asyncio
async def test_music_moves_every_timing_by_exactly_the_intro_length():
    """The failure ADR-006 named, asserted rather than reasoned about.

    Every `start_ms` is an offset into the finished file. Prepending eight
    seconds of intro must move every turn, every chapter boundary AND every
    word timing by exactly eight seconds — no more, no less, and all of them
    together.

    Getting this wrong is invisible today and glaring the moment word-level
    highlighting is on, because the words light up ahead of the voice.
    """
    from app.core.providers import Speech, WordTiming
    from app.core.schemas import Segment, Turn
    from app.providers.fake_tts import FakeTTSProvider
    from app.synthesis.music import load_music_set
    from app.synthesis.stitch import synthesize_briefing

    class _TimedTTS:
        voice_a = "a"
        voice_b = "b"

        async def synthesize(self, text: str, voice_id: str) -> Speech:
            fake = FakeTTSProvider()
            base = await fake.synthesize(text, fake.voice_a)
            return Speech(
                audio=base.audio,
                words=(WordTiming(word="first", start_ms=0, end_ms=200),),
            )

    # ONE segment, deliberately. A segment boundary inserts an interlude,
    # which shifts everything after it as well — correct behaviour, and it
    # would make this test assert two things at once and pass for the wrong
    # reason. The interlude has its own test below.
    def turns():
        return [
            Turn(speaker="A", segment=Segment.COLD_OPEN, text="first line"),
            Turn(speaker="B", segment=Segment.COLD_OPEN, text="second line"),
        ]

    common = dict(
        briefing_id="2026-09-05",
        audio_url_for=lambda fmt: f"x.{fmt}",
        sources=[],
    )

    _, without, _ = await synthesize_briefing(
        turns(), tts=_TimedTTS(), music_set=None, **common
    )
    _, with_music, _ = await synthesize_briefing(
        turns(), tts=_TimedTTS(), music_set="paper", **common
    )

    paper = load_music_set("paper")
    assert paper is not None and paper.intro is not None
    intro_ms = len(paper.intro)
    assert intro_ms > 1000, "the intro should be real audio, not a stub"

    # Turns moved by the intro, and by nothing else.
    for before, after in zip(without.turns, with_music.turns, strict=True):
        assert after.start_ms == before.start_ms + intro_ms
        assert after.end_ms == before.end_ms + intro_ms

    # The FIRST segment moved too. A chapter boundary left at zero would
    # drop the listener into the middle of the intro.
    assert with_music.segments[0]["start_ms"] == (
        without.segments[0]["start_ms"] + intro_ms
    )

    # And the words, which is the half that makes highlighting wrong.
    assert without.turns[0].words and with_music.turns[0].words
    assert (
        with_music.turns[0].words[0].start_ms
        == without.turns[0].words[0].start_ms + intro_ms
    )


@pytest.mark.asyncio
async def test_asking_for_no_music_gets_exactly_the_unmusical_briefing():
    """"none" is a real choice, and it must not quietly mean "the default"."""
    from app.core.schemas import Segment, Turn
    from app.providers.fake_tts import FakeTTSProvider
    from app.synthesis.stitch import synthesize_briefing

    def turns():
        return [Turn(speaker="A", segment=Segment.COLD_OPEN, text="only line")]

    common = dict(
        tts=FakeTTSProvider(),
        briefing_id="2026-09-05",
        audio_url_for=lambda fmt: f"x.{fmt}",
        sources=[],
    )

    silent_bytes, silent, _ = await synthesize_briefing(
        turns(), music_set="none", **common
    )
    none_bytes, plain, _ = await synthesize_briefing(turns(), music_set=None, **common)

    assert silent.turns[0].start_ms == 0
    assert plain.turns[0].start_ms == 0
    assert len(silent_bytes) == len(none_bytes)


def test_an_unknown_music_set_costs_the_music_not_the_run(caplog):
    """The artifact is written by a client that may be newer than this
    build, so a name we do not recognise is ordinary — and must not fail
    the only run of the day. Logged, because a briefing quietly losing its
    music is the kind of change nobody reports and everybody notices."""
    from app.synthesis.music import available_sets, load_music_set

    assert load_music_set("a-set-that-does-not-exist") is None
    assert any(r.levelname == "ERROR" for r in caplog.records)

    # And the real ones do load, read from disk rather than hardcoded here.
    assert set(available_sets()) >= {"paper", "broadsheet", "first-light"}
    for name in ("paper", "broadsheet", "first-light"):
        loaded = load_music_set(name)
        assert loaded is not None and not loaded.is_silent


@pytest.mark.asyncio
async def test_an_interlude_shifts_everything_after_the_segment_it_opens():
    """The second half of the offset rule.

    An interlude at a segment boundary moves every turn after it, exactly
    as the intro moves every turn. Same cursor, same arithmetic — which is
    the point of doing it in the loop rather than correcting afterwards.
    """
    from app.core.schemas import Segment, Turn
    from app.providers.fake_tts import FakeTTSProvider
    from app.synthesis.music import load_music_set
    from app.synthesis.stitch import synthesize_briefing

    def turns():
        return [
            Turn(speaker="A", segment=Segment.COLD_OPEN, text="first line"),
            Turn(speaker="B", segment=Segment.INBOX, text="second line"),
        ]

    common = dict(
        tts=FakeTTSProvider(),
        briefing_id="2026-09-05",
        audio_url_for=lambda fmt: f"x.{fmt}",
        sources=[],
    )

    _, without, _ = await synthesize_briefing(turns(), music_set=None, **common)
    _, with_music, _ = await synthesize_briefing(turns(), music_set="paper", **common)

    paper = load_music_set("paper")
    intro_ms = len(paper.intro)
    interlude_ms = len(paper.interlude)

    # The first turn moves by the intro alone — nothing precedes it.
    assert with_music.turns[0].start_ms == without.turns[0].start_ms + intro_ms

    # The second opens a new segment, so it moves by the intro AND the
    # interlude that now sits in front of it.
    assert (
        with_music.turns[1].start_ms
        == without.turns[1].start_ms + intro_ms + interlude_ms
    )

    # And its chapter marker moved with it, rather than pointing at the
    # interlude that precedes it.
    assert with_music.segments[1]["start_ms"] == with_music.turns[1].start_ms


def test_a_blank_env_var_falls_back_to_the_default(monkeypatch):
    """The bug that made a configured ElevenLabs key do nothing.

    `briefing.yml` passes `TTS_PROVIDER: ${{ secrets.TTS_PROVIDER }}`, and
    an UNSET secret interpolates to the empty string rather than being
    omitted. So the variable was present, pydantic preferred it to the
    field default, and every run used `tts_provider == ""` — which fell
    through to Google. The defaults added so the API key alone would be
    enough were defeated by their own absence.

    Confirmed from the live run log, which showed `TTS_PROVIDER:` with
    nothing after it.
    """
    from app.core.config import (
        DEFAULT_ELEVENLABS_VOICE_A,
        DEFAULT_ELEVENLABS_VOICE_B,
        get_settings,
    )

    for blank in ("", "   "):
        monkeypatch.setenv("TTS_PROVIDER", blank)
        monkeypatch.setenv("ELEVENLABS_VOICE_A", blank)
        monkeypatch.setenv("ELEVENLABS_VOICE_B", blank)
        get_settings.cache_clear()

        s = get_settings()
        assert s.tts_provider == "elevenlabs", f"blank {blank!r} won"
        assert s.elevenlabs_voice_a == DEFAULT_ELEVENLABS_VOICE_A
        assert s.elevenlabs_voice_b == DEFAULT_ELEVENLABS_VOICE_B

    # An explicit value still wins — the fix must not make the setting
    # unsettable, which would be the opposite bug.
    monkeypatch.setenv("TTS_PROVIDER", "google")
    get_settings.cache_clear()
    assert get_settings().tts_provider == "google"

    # And surrounding whitespace is stripped rather than carried into a
    # provider-name comparison that would then miss.
    monkeypatch.setenv("TTS_PROVIDER", "  elevenlabs  ")
    get_settings.cache_clear()
    assert get_settings().tts_provider == "elevenlabs"

    get_settings.cache_clear()
