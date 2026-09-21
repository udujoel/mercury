"""
Semantic email triage (ADR-013).

The cases that matter here are the ones the heuristic got wrong in
production, so they are written as the bug rather than as the feature: #55's
bank alert, and the marketing mail whose question mark made it look like
someone was asking Joel something.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from app.core.providers import EmailJudgment
from app.core.schemas import Item, Priority, SourceType
from app.pipeline.scoring import score_emails
from app.pipeline.triage import (
    BODY_CHARS,
    email_state,
    priority_for,
    triage_emails,
)
from app.providers.fake_judgment import FakeJudgmentProvider

NOW = datetime(2026, 9, 21, 7, 0, tzinfo=UTC)


def email(
    *, name="Sender", subject="Subject", body="body", newsletter=False,
    when=NOW, priority=Priority.MEDIUM, requires_response=False,
) -> Item:
    return Item(
        source_type=SourceType.EMAIL,
        source_name=name,
        title=subject,
        body=body,
        author="someone@example.com",
        timestamp=when,
        priority=priority,
        requires_response=requires_response,
        raw={"is_newsletter": newsletter, "to": ["udujoel@gmail.com"]},
    )


# ── rule 5: what actually leaves the machine ──────────────────────────


def test_state_truncates_the_body():
    item = email(body="x" * 5000)
    assert len(email_state(item)["body_excerpt"]) == BODY_CHARS


def test_state_is_an_allowlist_not_the_whole_item():
    """A field added to Item later must not silently start being sent."""
    item = email(body="secret")
    item.raw["oauth_refresh_token"] = "must-not-leave"
    state = email_state(item)
    assert set(state) == {
        "sender_name", "sender_address", "subject", "to",
        "looks_like_bulk_mail", "received", "body_excerpt",
    }
    assert "must-not-leave" not in str(state)


# ── thresholds ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "consequence,expected",
    [
        (3.00, Priority.HIGH),    # measured: bank account-restriction warning
        (1.99, Priority.HIGH),    # measured: colleague's dated request
        (1.50, Priority.HIGH),    # boundary
        (1.49, Priority.MEDIUM),
        (0.50, Priority.MEDIUM),
        (0.00, Priority.LOW),     # measured: "Ready to upgrade?"
    ],
)
def test_consequence_maps_to_priority(consequence, expected):
    judgment = EmailJudgment(
        consequence=consequence, confidence=1.0, awaiting_reply=0.1, bulk=0.1
    )
    assert priority_for(judgment)[0] is expected


def test_a_person_waiting_is_not_demoted_to_medium():
    """Regression: the scale is a level INDEX (0-3), not a 0-1 fraction.

    Thresholding it as a fraction put HIGH at 2.5, which demoted every
    "a specific person is waiting" email — they measure ~1.99 — to MEDIUM.
    Only a live call surfaced this; a fixture written against the wrong
    assumption would have agreed with itself.
    """
    judgment = EmailJudgment(
        consequence=1.99, confidence=0.99, awaiting_reply=0.86, bulk=0.05
    )
    priority, requires_response = priority_for(judgment)
    assert priority is Priority.HIGH
    assert requires_response is True


# ── #55: bulk AND serious ─────────────────────────────────────────────


async def test_bank_alert_is_high_although_it_is_bulk_mail():
    """The heuristic needed an is_urgent branch jumped ahead of the
    newsletter floor to get this right. Three independent judgments get it
    with no ordering between them."""
    item = email(
        name="Monzo",
        subject="Action required: your account has been restricted",
        body="We have restricted your account. Confirm your identity by 5pm.",
        newsletter=True,
        priority=Priority.LOW,
    )
    await triage_emails([item], FakeJudgmentProvider())
    assert item.priority is Priority.HIGH
    assert item.consequence == 3.0


async def test_marketing_question_mark_does_not_make_it_urgent():
    item = email(
        name="Notion",
        subject="Ready to upgrade your workspace?",
        body="Have you tried it? Save 20% today. unsubscribe",
        newsletter=True,
    )
    await triage_emails([item], FakeJudgmentProvider())
    assert item.priority is Priority.LOW


# ── fallback (rule 7) ─────────────────────────────────────────────────


async def test_no_provider_leaves_the_heuristic_verdict_untouched():
    item = email(priority=Priority.HIGH, requires_response=True)
    await triage_emails([item], None)
    assert item.priority is Priority.HIGH
    assert item.requires_response is True
    assert item.consequence is None


async def test_one_unjudged_email_keeps_its_heuristic_priority():
    judged = email(subject="Account suspended", priority=Priority.LOW)
    unjudged = email(subject="Anything", priority=Priority.HIGH)
    await triage_emails(
        [judged, unjudged], FakeJudgmentProvider(fail_indices=frozenset({1}))
    )
    assert judged.priority is Priority.HIGH      # Jev overrode the heuristic
    assert unjudged.priority is Priority.HIGH    # heuristic survived
    assert unjudged.consequence is None


async def test_a_provider_that_raises_does_not_take_the_briefing_down():
    class Exploding:
        async def triage_emails(self, states, *, timeout=20.0):
            raise RuntimeError("TypeSafe is down")

    item = email(priority=Priority.HIGH)
    result = await triage_emails([item], Exploding())
    assert result == [item]
    assert item.priority is Priority.HIGH


async def test_a_short_result_list_is_discarded_rather_than_misaligned():
    """Zipping 1 judgment onto 2 emails would hand the second email the
    first one's verdict. Wrong-in-a-known-way beats wrong-and-invisible."""
    class Truncating:
        async def triage_emails(self, states, *, timeout=20.0):
            return [
                EmailJudgment(
                    consequence=3.0, confidence=1.0, awaiting_reply=0.9, bulk=0.0
                )
            ]

    a = email(subject="first", priority=Priority.LOW)
    b = email(subject="second", priority=Priority.LOW)
    await triage_emails([a, b], Truncating())
    assert a.priority is Priority.LOW and a.consequence is None
    assert b.priority is Priority.LOW and b.consequence is None


async def test_empty_inbox_makes_no_call():
    provider = FakeJudgmentProvider()
    assert await triage_emails([], provider) == []
    assert provider.calls == []


# ── ordering ──────────────────────────────────────────────────────────


def test_consequence_outranks_recency_within_a_tier():
    """#55 in one assertion: the gym mailer arrived later."""
    gym = email(name="Gym", when=NOW, priority=Priority.HIGH)
    gym.consequence = 0.0
    bank = email(name="Monzo", when=NOW - timedelta(hours=6), priority=Priority.HIGH)
    bank.consequence = 3.0
    assert [i.source_name for i in score_emails([gym, bank])] == ["Monzo", "Gym"]


def test_without_judgments_ordering_is_still_pure_recency():
    """The fallback has to restore the old behaviour exactly, not approximate
    it — otherwise a TypeSafe outage silently reshuffles the briefing."""
    older = email(name="Older", when=NOW - timedelta(hours=2), priority=Priority.HIGH)
    newer = email(name="Newer", when=NOW, priority=Priority.HIGH)
    assert [i.source_name for i in score_emails([older, newer])] == ["Newer", "Older"]


def test_judged_items_sort_ahead_of_unjudged_in_the_same_tier():
    judged = email(name="Judged", when=NOW - timedelta(hours=9), priority=Priority.HIGH)
    judged.consequence = 2.0
    unjudged = email(name="Unjudged", when=NOW, priority=Priority.HIGH)
    assert [i.source_name for i in score_emails([unjudged, judged])] == [
        "Judged", "Unjudged",
    ]


# ── the key actually reaches the client ───────────────────────────────


class _RecordingClient:
    """Stands in for AsyncTypeSafeClient and records how it was built."""

    seen_api_key: str | None = None

    def __init__(self, *args, **kwargs):
        type(self).seen_api_key = kwargs.get("api_key")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def system_one(self, *, state, questions):
        raise AssertionError("not reached in this test")


async def test_the_configured_key_is_passed_to_the_client(monkeypatch):
    """Regression: `.env` is read by pydantic-settings, never exported.

    The SDK's own fallback reads os.environ, finds nothing, and every
    request fails auth — while `build_judgment_provider` still hands back a
    provider and triage degrades silently to the heuristic. A live run is
    the only thing that caught it; the suite was green throughout, so this
    test exists to make the second occurrence loud.
    """
    import typesafe_sdk

    from app.core.config import Settings
    from app.providers import typesafe_jev

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: Settings(typesafe_api_key="from-dot-env", judgment_concurrency=2),
    )
    monkeypatch.setattr(typesafe_sdk, "AsyncTypeSafeClient", _RecordingClient)
    monkeypatch.setattr(typesafe_jev, "_questions", lambda: {})

    provider = typesafe_jev.JevJudgmentProvider()
    await provider.triage_emails([{"subject": "x"}])
    assert _RecordingClient.seen_api_key == "from-dot-env"


def test_a_keyless_provider_refuses_to_construct(monkeypatch):
    from app.core.config import Settings
    from app.providers.typesafe_jev import JevJudgmentProvider

    monkeypatch.setattr(
        "app.core.config.get_settings", lambda: Settings(typesafe_api_key="")
    )
    with pytest.raises(RuntimeError, match="TYPESAFE_API_KEY"):
        JevJudgmentProvider()


def test_no_key_means_no_provider_rather_than_a_broken_one(monkeypatch):
    from app.core.config import Settings
    from app.core.providers import build_judgment_provider

    monkeypatch.setattr(
        "app.core.config.get_settings", lambda: Settings(typesafe_api_key="")
    )
    assert build_judgment_provider() is None


# ── review findings (PR #60) ──────────────────────────────────────────


def test_the_fake_reads_the_body():
    """Regression: the fake read keys `email_state` has never emitted.

    It looked for "body" and "sender"; the real state carries
    "body_excerpt", "sender_name" and "sender_address". So it judged on the
    subject line alone, and nine tests passed only because their subjects
    happened to contain the keywords. A fake reading a state shape that
    does not exist certifies nothing — the same failure `docs/LOG.md` lists
    four times over.
    """
    from app.pipeline.triage import email_state
    from app.providers.fake_judgment import judge_state

    # Every signal is in the BODY. The subject is deliberately inert.
    item = email(
        subject="quick note",
        body="Can you review the deck before Friday? Let me know.",
    )
    judgment = judge_state(email_state(item))
    assert judgment.consequence == 2.0
    assert judgment.awaiting_reply > 0.6


def test_the_fake_agrees_with_email_state_about_bulk_mail():
    from app.pipeline.triage import email_state
    from app.providers.fake_judgment import judge_state

    item = email(subject="Weekly roundup", body="stories", newsletter=True)
    assert judge_state(email_state(item)).bulk > 0.5


async def test_all_unjudged_is_reported_at_warning(caplog):
    """Regression: a 401 on every request looked like a quiet morning.

    The whole-batch `except` already warned. Reaching the same outcome one
    item at a time logged at INFO, so a pass that judged nothing at all was
    indistinguishable from a normal one in the Actions log.
    """
    import logging

    items = [email(subject="a"), email(subject="b")]
    provider = FakeJudgmentProvider(fail_indices=frozenset({0, 1}))
    with caplog.at_level(logging.INFO, logger="app.pipeline.triage"):
        await triage_emails(items, provider)
    records = [r for r in caplog.records if "email triage:" in r.message]
    assert records and records[-1].levelno == logging.WARNING


async def test_partial_failure_stays_at_info(caplog):
    import logging

    items = [email(subject="Account suspended"), email(subject="b")]
    provider = FakeJudgmentProvider(fail_indices=frozenset({1}))
    with caplog.at_level(logging.INFO, logger="app.pipeline.triage"):
        await triage_emails(items, provider)
    records = [r for r in caplog.records if "email triage:" in r.message]
    assert records and records[-1].levelno == logging.INFO


class _HangingClient:
    """A provider that accepts the request and never answers."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def system_one(self, *, state, questions):
        import asyncio

        await asyncio.sleep(3600)


async def test_a_hung_provider_is_bounded_by_the_total_budget(monkeypatch):
    """Regression: per-request timeouts left the PASS unbounded.

    With 60 emails at concurrency 8 and a 20s per-request timeout, a
    provider that hangs rather than refusing takes 8 waves x 20s = 160s —
    against a 90s briefing budget already 71s spent. The briefing would not
    fail; it would miss the morning.
    """
    import time

    import typesafe_sdk

    from app.core.config import Settings
    from app.providers import typesafe_jev

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: Settings(
            typesafe_api_key="k",
            judgment_concurrency=2,
            judgment_timeout_seconds=30.0,   # per-request: far beyond the budget
            judgment_total_budget_seconds=0.3,
        ),
    )
    monkeypatch.setattr(typesafe_sdk, "AsyncTypeSafeClient", _HangingClient)
    monkeypatch.setattr(typesafe_jev, "_questions", lambda: {})

    provider = typesafe_jev.JevJudgmentProvider()
    started = time.perf_counter()
    result = await provider.triage_emails([{"subject": str(i)} for i in range(8)])
    elapsed = time.perf_counter() - started

    assert elapsed < 3.0, f"budget not enforced: took {elapsed:.1f}s"
    assert result == [None] * 8


def test_the_production_workflow_passes_the_key():
    """Regression: the feature was inert on merge.

    `build_judgment_provider` returns None on an empty key and logs at INFO,
    so a missing secret in the workflow ships a dead feature that looks
    merged and green. That is the `music_set` whitelist and the un-deployed
    Worker routes, twice already in docs/LOG.md.

    Asserted against the YAML rather than trusted to review, because the one
    caller that is a config file is the one a reader of Python does not see.
    """
    workflow = (
        pathlib.Path(__file__).resolve().parents[2]
        / ".github/workflows/briefing.yml"
    ).read_text()
    assert "TYPESAFE_API_KEY: ${{ secrets.TYPESAFE_API_KEY }}" in workflow
