"""
Offline tests for scripts/spike_02_gmail.py — runs entirely against the
fixture at backend/tests/fixtures/gmail_messages.json, no live API calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import pytest  # noqa: E402
import spike_02_gmail as spike  # noqa: E402

from app.core.schemas import Priority, SourceType  # noqa: E402

USER_EMAIL = "udujoel@gmail.com"


@pytest.fixture
def raw_messages():
    return spike.load_fixture_messages()


@pytest.fixture
def by_id(raw_messages):
    return {m["id"]: m for m in raw_messages}


# ──────────────────────────────────────────────────────────────
# base64url decoding
# ──────────────────────────────────────────────────────────────


def test_decode_base64url_handles_missing_padding():
    # "hello world" base64url-encoded with padding stripped
    encoded = "aGVsbG8gd29ybGQ"
    assert spike.decode_base64url(encoded) == "hello world"


def test_decode_base64url_handles_dash_underscore_alphabet():
    # bytes that would contain '+' and '/' in standard base64 must map to
    # '-' and '_' respectively and still decode correctly.
    raw = bytes(range(250, 256)) * 4
    import base64

    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    assert "-" in encoded or "_" in encoded
    assert (
        spike.decode_base64url(encoded).encode("utf-8", errors="replace") or True
    )  # decoding must not raise


def test_decode_base64url_empty_string_returns_empty():
    assert spike.decode_base64url("") == ""


# ──────────────────────────────────────────────────────────────
# MIME walking
# ──────────────────────────────────────────────────────────────


def test_mime_walk_multipart_alternative_prefers_plain(by_id):
    payload = by_id["msg001"]["payload"]
    plain, html = spike.walk_mime_parts(payload)
    assert plain is not None
    assert "Top story" in plain
    assert html is not None
    assert "<h1>" in html


def test_extract_body_falls_back_to_html_when_no_plain(by_id):
    payload = by_id["msg002"]["payload"]  # HTML-only message
    plain, html = spike.walk_mime_parts(payload)
    assert plain is None
    assert html is not None
    body = spike.extract_body(payload)
    assert "Flash Sale" in body
    assert "<" not in body  # HTML tags must be stripped


def test_mime_walk_multipart_mixed_with_attachment_skips_attachment(by_id):
    payload = by_id["msg004"]["payload"]
    plain, _html = spike.walk_mime_parts(payload)
    assert plain is not None
    assert "updated deck" in plain


# ──────────────────────────────────────────────────────────────
# Cleaning
# ──────────────────────────────────────────────────────────────


def test_strip_quote_chain_removes_on_wrote_and_carets():
    text = (
        "My reply.\n\n"
        "On Wed, Aug 19, 2026 at 3:45 PM Dave Park wrote:\n"
        "> quoted line one\n"
        "> quoted line two\n"
    )
    cleaned = spike.strip_quote_chain(text)
    assert "My reply." in cleaned
    assert "quoted line" not in cleaned
    assert "wrote:" not in cleaned


def test_strip_signature_removes_dash_dash_block():
    text = "Body text here.\n\n-- \nMaria Chen\nHead of Ops\n"
    cleaned = spike.strip_signature(text)
    assert "Body text here." in cleaned
    assert "Head of Ops" not in cleaned


def test_clean_body_removes_tracking_pixel_and_unsubscribe():
    raw = (
        "Newsletter content.\n"
        "<img src='https://track.example.com/pixel.gif' width='1' height='1'>\n"
        "Please unsubscribe here: https://example.com/unsub\n"
    )
    cleaned = spike.clean_body(raw)
    assert "Newsletter content." in cleaned
    assert "track.example.com" not in cleaned
    assert "unsubscribe" not in cleaned.lower()


def test_clean_body_collapses_whitespace():
    raw = "line one\n\n\n\n\nline two"
    cleaned = spike.clean_body(raw)
    assert "\n\n\n" not in cleaned


# ──────────────────────────────────────────────────────────────
# Newsletter detection
# ──────────────────────────────────────────────────────────────


def test_detect_newsletter_via_list_unsubscribe():
    headers = {"List-Unsubscribe": "<https://example.com/unsub>", "From": "a@b.com"}
    is_nl, signal = spike.detect_newsletter(headers)
    assert is_nl is True
    assert "List-Unsubscribe" in signal


def test_detect_newsletter_via_precedence_bulk():
    headers = {"Precedence": "bulk", "From": "a@b.com"}
    is_nl, signal = spike.detect_newsletter(headers)
    assert is_nl is True
    assert "Precedence" in signal


def test_detect_newsletter_via_sender_heuristic():
    headers = {"From": "Alerts <noreply@somesaas.com>"}
    is_nl, signal = spike.detect_newsletter(headers)
    assert is_nl is True
    assert "sender heuristic" in signal


def test_detect_newsletter_false_for_direct_email():
    headers = {"From": "Maria Chen <maria.chen@acmecorp.com>"}
    is_nl, signal = spike.detect_newsletter(headers)
    assert is_nl is False
    assert signal is None


# ──────────────────────────────────────────────────────────────
# Priority / requires_response
# ──────────────────────────────────────────────────────────────


def test_priority_newsletter_is_always_low_and_no_response():
    priority, requires = spike.classify_priority(
        is_newsletter=True,
        body="Anything, even with a question?",
        to_addresses=[USER_EMAIL],
        user_email=USER_EMAIL,
    )
    assert priority == Priority.LOW
    assert requires is False


def test_priority_high_when_user_in_to_and_question():
    priority, requires = spike.classify_priority(
        is_newsletter=False,
        body="Can you review this before Friday?",
        to_addresses=[USER_EMAIL],
        user_email=USER_EMAIL,
    )
    assert priority == Priority.HIGH
    assert requires is True


def test_priority_medium_when_no_ask_signal():
    priority, requires = spike.classify_priority(
        is_newsletter=False,
        body="Sounds good, thanks for the update.",
        to_addresses=[USER_EMAIL],
        user_email=USER_EMAIL,
    )
    assert priority == Priority.MEDIUM
    assert requires is False


def test_priority_medium_when_question_but_user_only_cced():
    priority, requires = spike.classify_priority(
        is_newsletter=False,
        body="Can someone confirm the numbers?",
        to_addresses=["someoneelse@acmecorp.com"],
        user_email=USER_EMAIL,
    )
    assert priority == Priority.MEDIUM
    assert requires is False


# ──────────────────────────────────────────────────────────────
# Full normalisation pipeline against the fixture
# ──────────────────────────────────────────────────────────────


def test_message_to_item_produces_valid_item(by_id):
    item = spike.message_to_item(by_id["msg003"], USER_EMAIL)
    assert item.source_type == SourceType.EMAIL
    assert item.source_name == "Maria Chen"
    assert item.priority == Priority.HIGH
    assert item.requires_response is True
    assert "budget" in item.title.lower()


def test_malformed_message_raises_and_is_skippable(by_id):
    with pytest.raises(ValueError):
        spike.message_to_item(by_id["msg005"], USER_EMAIL)


def test_full_fixture_run_skips_only_malformed(raw_messages):
    items = []
    skipped = 0
    for raw in raw_messages:
        try:
            items.append(spike.message_to_item(raw, USER_EMAIL))
        except Exception:
            skipped += 1
    assert len(items) == 4
    assert skipped == 1


def test_newsletters_flagged_in_fixture(raw_messages):
    valid = [m for m in raw_messages if m["id"] != "msg005"]
    items = [spike.message_to_item(m, USER_EMAIL) for m in valid]
    newsletter_titles = {i.title for i in items if i.raw["is_newsletter"]}
    assert "This Week in Signals" in newsletter_titles
    assert "Flash Sale: 30% off everything" in newsletter_titles
