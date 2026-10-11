"""
Extracted newsletter items are checked before they become source data
(ADR-018).

`script_chain._flatten_source_text` puts these headlines and gists into
`source_data`, and `check_grounding` measures the finished script against
that block. So an item invented at extraction time is not merely wrong —
it is promoted to evidence, and every claim derived from it then scores as
perfectly grounded.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.core.schemas import Item, SourceType
from app.pipeline.newsletter_items import (
    WINDOW_CHARS,
    NewsletterItem,
    _best_window,
    verify_extracted,
)
from app.providers.fake_judgment import FakeClaimJudge

NOW = datetime(2026, 9, 22, 7, 0, tzinfo=UTC)
UNSUPPORTED = "__UNSUPPORTED__"


def newsletter(body, name="Morning Brew", url="https://n/1"):
    return Item(
        source_type=SourceType.EMAIL,
        source_name=name,
        title="Issue 42",
        body=body,
        url=url,
        timestamp=NOW,
    )


def extracted(headline="A headline", gist="A gist", subject="subj"):
    return NewsletterItem(
        headline=headline,
        gist=gist,
        subject=subject,
        source_name="Morning Brew",
        sender="x@y.z",
        received_at=NOW,
        email_id="e1",
    )


# ── local retrieval ───────────────────────────────────────────────────


def test_a_short_body_is_sent_whole():
    body = "short enough"
    assert _best_window(body, "anything") == body


def test_the_window_is_bounded():
    assert len(_best_window("x" * 10_000, "needle")) == WINDOW_CHARS


def test_the_window_finds_the_passage_the_gist_came_from():
    """Retrieval in code so the judgment can be narrow.

    A gist cannot be checked against the first 700 characters of a long
    newsletter — the passage it came from is usually somewhere else.
    """
    filler = "Assorted unrelated newsletter chatter. " * 60
    passage = (
        "TechCorp confirmed it has acquired DataFlow for 2.1 billion dollars, "
        "with the transaction expected to close in the first quarter."
    )
    body = filler + passage + filler
    window = _best_window(body, "TechCorp acquires DataFlow for $2.1bn")
    assert "DataFlow" in window and "2.1 billion" in window


def test_an_empty_needle_does_not_crash():
    assert len(_best_window("y" * 5000, "")) == WINDOW_CHARS


# ── fallback ──────────────────────────────────────────────────────────


async def test_no_provider_keeps_everything():
    pairs = [(extracted(), newsletter("body"))]
    assert await verify_extracted(pairs, None) is pairs


async def test_a_provider_that_raises_keeps_everything():
    class Exploding:
        async def judge_claims(self, state, claims, *, timeout=20.0):
            raise RuntimeError("down")

    pairs = [(extracted(), newsletter("body"))]
    assert await verify_extracted(pairs, Exploding()) == pairs


async def test_no_pairs_makes_no_call():
    judge = FakeClaimJudge()
    assert await verify_extracted([], judge) == []
    assert judge.claim_calls == []


# ── the gate ──────────────────────────────────────────────────────────


async def test_an_unsupported_item_is_dropped_before_it_becomes_source():
    keep = (extracted(headline="Real story"), newsletter("a real passage"))
    drop = (
        extracted(headline="Invented"),
        newsletter(f"passage {UNSUPPORTED}", url="https://n/2"),
    )
    kept = await verify_extracted([keep, drop], FakeClaimJudge())
    assert [i.headline for i, _ in kept] == ["Real story"]


async def test_a_supported_item_survives():
    pairs = [(extracted(), newsletter("an ordinary passage"))]
    assert len(await verify_extracted(pairs, FakeClaimJudge())) == 1


async def test_uncertainty_keeps_the_item():
    """`_best_window` is approximate, so "can't tell" must not drop.

    A false drop silently loses a real story — the failure this module was
    written to stop — while a false keep costs only the pre-ADR behaviour.
    """

    class _Uncertain:
        async def judge_claims(self, state, claims, *, timeout=20.0):
            return {c.key: 0.45 for c in claims}  # above the 0.2 floor

    pairs = [(extracted(), newsletter("body"))]
    assert len(await verify_extracted(pairs, _Uncertain())) == 1


async def test_an_unjudged_item_is_kept():
    judge = FakeClaimJudge(unjudged_keys=frozenset({"support:0"}))
    pairs = [(extracted(), newsletter(f"body {UNSUPPORTED}"))]
    assert len(await verify_extracted(pairs, judge)) == 1


async def test_a_drop_is_said_out_loud(caplog):
    pairs = [(extracted(headline="Invented"), newsletter(f"p {UNSUPPORTED}"))]
    with caplog.at_level(logging.WARNING, logger="app.pipeline.newsletter_items"):
        await verify_extracted(pairs, FakeClaimJudge())
    assert any("not supported by the issue" in r.getMessage() for r in caplog.records)


# ── pairing ───────────────────────────────────────────────────────────


async def test_items_are_paired_by_index_not_by_colliding_email_id():
    """`NewsletterItem.email_id` is `Item.id`, a content hash.

    Two issues from one sender at one timestamp collide on it, so a lookup
    by email_id can hand an item the wrong newsletter to be verified
    against. `_parse` keeps the index pairing instead.
    """
    a = Item(
        source_type=SourceType.EMAIL,
        source_name="Brew",
        title="Issue",
        body="ALPHA passage",
        url=None,
        timestamp=NOW,
    )
    b = Item(
        source_type=SourceType.EMAIL,
        source_name="Brew",
        title="Issue",
        body="BRAVO passage",
        url=None,
        timestamp=NOW,
    )
    assert a.id == b.id, "fixture must actually collide"

    judge = FakeClaimJudge()
    await verify_extracted(
        [(extracted(headline="one"), a), (extracted(headline="two"), b)], judge
    )
    state, _ = judge.claim_calls[0]
    passages = [row["newsletter_passage"] for row in state["extracted"]]
    assert passages == ["ALPHA passage", "BRAVO passage"]


async def test_the_payload_is_an_allowlist():
    judge = FakeClaimJudge()
    source = newsletter("body")
    source.raw = {"oauth_refresh_token": "must-not-leave"}
    await verify_extracted([(extracted(), source)], judge)
    state, _ = judge.claim_calls[0]
    assert set(state) == {"extracted"}
    assert set(state["extracted"][0]) == {"headline", "gist", "newsletter_passage"}
    assert "must-not-leave" not in str(state)


# ── review finding (PR #64) ───────────────────────────────────────────


async def test_a_malformed_response_degrades_instead_of_raising():
    """Regression: only the `await` was inside the try.

    A provider that returns the wrong SHAPE rather than raising — a list,
    None, anything without `.get` — took an AttributeError out of
    `extract_items`, whose docstring promises it never raises, costing the
    whole newsletter segment rather than just its verification.

    Same defect review found in `cluster_verify` on #62, two PRs later.
    Wrapping the await alone is not wrapping the work.
    """

    class _Malformed:
        async def judge_claims(self, state, claims, *, timeout=20.0):
            return [0.9]

    pairs = [(extracted(), newsletter("body"))]
    assert await verify_extracted(pairs, _Malformed()) == pairs


async def test_a_body_that_breaks_window_selection_degrades():
    """`_best_window` runs over untrusted bodies, inside the try."""
    from app.pipeline import newsletter_items

    def _boom(*a, **k):
        raise ValueError("window selection blew up")

    original = newsletter_items._best_window
    newsletter_items._best_window = _boom
    try:
        pairs = [(extracted(), newsletter("body"))]
        assert await verify_extracted(pairs, FakeClaimJudge()) == pairs
    finally:
        newsletter_items._best_window = original
