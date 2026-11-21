"""GitHub, CI and auto-reply mail never reaches the spoken briefing.

The owner's rule, 2026-11-21: "GitHub issues, PR and build noise ain't
important. I want mainly newsletters and emails needing my attention."

Before this, a GitHub notification carried `List-Unsubscribe`, so ingestion
called it a newsletter and pinned it LOW, and then Jev's triage overwrote
that verdict. A review request reads literally as "a specific person is
waiting for you", which is exactly what `awaiting_reply` looks for, so the
notification could come out of triage HIGH with a reply owed and be read
out in the segment the hosts introduce as needing attention.

Everything here that can go through `message_to_item` does, for the reason
`test_transactional_mail.py` records: a test that hand-builds
`raw={"is_noise": True}` stays green when ingestion stops writing the flag.
Private mail (banks, money apps, personal accounts) is filtered the same
way, at the owner's request the same afternoon. ROADMAP task 62.
"""

from __future__ import annotations

import base64
import logging

import pytest

from app.core.providers import EmailJudgment
from app.core.schemas import BriefingLength, Priority
from app.ingestion.gmail import (
    detect_automated_noise,
    detect_private,
    message_to_item,
)
from app.pipeline.generate import (
    _drop_filtered_mail,
    _newsletters_for_extraction,
    generate_briefing,
)
from app.providers.fake import FakeLLMProvider
from app.providers.fake_embeddings import FakeEmbeddingProvider
from app.providers.fake_tts import FakeTTSProvider
from app.synthesis.storage import LocalFilesystemStorage

USER = "owner@example.test"


def _item(
    sender: str,
    subject: str,
    body: str = "Some body text.",
    extra: dict[str, str] | None = None,
    *,
    to: str = USER,
):
    headers = [
        {"name": "From", "value": sender},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": "Sat, 21 Nov 2026 04:00:00 +0000"},
    ]
    for name, value in (extra or {}).items():
        headers.append({"name": name, "value": value})
    raw = {
        "id": f"m-{abs(hash((sender, subject)))}",
        "threadId": "t1",
        "payload": {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
        "internalDate": "1795233600000",
    }
    return message_to_item(raw, USER)


_GITHUB_LIST = {
    "List-Unsubscribe": "<https://github.com/notifications/unsubscribe/x>",
    "List-ID": "udujoel/mercury <mercury.udujoel.github.com>",
}

# Shaped like the real thing: GitHub sets these headers on every
# notification, and a review request asks a direct question of the owner.
THE_NOISE = [
    pytest.param(
        "Some Reviewer <notifications@github.com>",
        "Re: [udujoel/mercury] fix: tighten the triage filter (PR #87)",
        "Could you take another look at this before Friday?",
        {**_GITHUB_LIST, "X-GitHub-Reason": "review_requested"},
        id="github-review-request",
    ),
    pytest.param(
        "Some User <notifications@github.com>",
        "[udujoel/mercury] Briefing failed to generate (Issue #90)",
        "Opened an issue, can you check?",
        {**_GITHUB_LIST, "X-GitHub-Reason": "subscribed"},
        id="github-issue",
    ),
    pytest.param(
        "GitHub <notifications@github.com>",
        "[udujoel/mercury] Run failed: Generate briefing - main (2a176bf)",
        "The workflow run failed.",
        {**_GITHUB_LIST, "X-GitHub-Reason": "ci_activity"},
        id="github-actions-run-failed",
    ),
    pytest.param(
        # The header alone, with the case Gmail sometimes hands back.
        "Someone <reply+abc123@reply.example.test>",
        "Re: a thread",
        "Looks good to me.",
        {"X-Github-Reason": "comment"},
        id="github-header-any-case",
    ),
    pytest.param(
        "GitHub <noreply@github.com>",
        "[GitHub] Dependabot alerts for udujoel/mercury",
        "3 new alerts.",
        {},
        id="github-sender-domain",
    ),
    pytest.param(
        "GitLab <gitlab@ci.example.test>",
        "Pipeline #42 has failed for main",
        "See the job log.",
        {"X-GitLab-Project": "mercury", "X-GitLab-Pipeline-Id": "42"},
        id="gitlab-pipeline",
    ),
    pytest.param(
        "Vercel <notifications@vercel.com>",
        "Deployment failed for mercury-web",
        "Build error.",
        {},
        id="vercel-deploy",
    ),
    pytest.param(
        "Jenkins <builds@ci.example.test>",
        "Build #311 failed: mercury-backend",
        "Console output attached.",
        {},
        id="self-hosted-ci-subject",
    ),
    pytest.param(
        "A Colleague <colleague@example.test>",
        "Out of office: back on Monday",
        "I am away until Monday.",
        {"Auto-Submitted": "auto-replied"},
        id="out-of-office",
    ),
    # Review of #87: explicit automation markers beat an urgency phrase
    # quoted in the subject, which `detect_urgent` reads as fresh text.
    pytest.param(
        "A Colleague <colleague@example.test>",
        "Out of office: Action required: approve Q4 budget",
        "I am away until Monday.",
        {"Auto-Submitted": "auto-replied"},
        id="out-of-office-quoting-an-urgent-subject",
    ),
    pytest.param(
        "GitLab <gitlab@ci.example.test>",
        "Action required: rotate the deploy key (issue #12)",
        "New issue.",
        {"X-GitLab-Project": "mercury"},
        id="non-bulk-repo-notification-with-urgent-words",
    ),
    # Review of #87: "action required" is not an account alert, so it no
    # longer exempts a dev-tool sender.
    pytest.param(
        "Vercel <notifications@vercel.com>",
        "Action required: Deployment failed for mercury-web",
        "Build error.",
        {"List-Unsubscribe": "<https://x.test/u>"},
        id="vercel-action-required-deploy",
    ),
]


@pytest.mark.parametrize(("sender", "subject", "body", "extra"), THE_NOISE)
def test_the_noise_is_flagged_and_pinned_low(sender, subject, body, extra):
    item = _item(sender, subject, body, extra=extra)
    assert item.raw["is_noise"], f"{sender} / {subject!r} reaches the briefing"
    assert item.raw["noise_signal"], "a silent classifier cannot be argued with"
    assert item.priority is Priority.LOW
    assert item.requires_response is False
    # Noise is never journalism, so the desk and extractor refuse it too.
    assert item.raw["is_transactional"]
    assert _newsletters_for_extraction([item]) == []


# The expensive direction. Each of these is mail the owner said they want.
THE_KEEPERS = [
    pytest.param(
        "A Colleague <colleague@example.test>",
        "Budget sign-off",
        "Can you approve the Q4 budget by Thursday?",
        {},
        id="a-person-asking",
    ),
    pytest.param(
        "Morning Brew <crew@morningbrew.com>",
        "Gaming Brew",
        "Today's stories.",
        {"List-Unsubscribe": "<https://x.test/u>"},
        id="a-newsletter",
    ),
    pytest.param(
        # A news word in a newsletter subject is not a build result: the
        # CI subject rule only counts when the sender is a machine too.
        "The Pragmatic Engineer <pragmaticengineer@substack.com>",
        "Why the CrowdStrike deploy failed",
        "A deep dive.",
        {"List-Unsubscribe": "<https://x.test/u>"},
        id="newsletter-about-a-failed-deploy",
    ),
    pytest.param(
        "Engineering Weekly <newsletter@engweekly.test>",
        "Your build passed: the week in CI tools",
        "Issue 120.",
        {"List-Unsubscribe": "<https://x.test/u>"},
        id="newsletter-with-ci-words",
    ),
    # Review of #87: `noreply@` means bulk, not CI, so a publication using
    # it keeps a headline about a failed deploy.
    pytest.param(
        "Medium Daily <noreply@medium.com>",
        "Why the CrowdStrike deploy failed",
        "A deep dive.",
        {"List-Unsubscribe": "<https://x.test/u>"},
        id="noreply-newsletter-about-a-failed-deploy",
    ),
    # Review of #87: "ci@" was a substring match and fired on marci@.
    pytest.param(
        "Marci <marci@example.test>",
        "Build failed on main",
        "Can you help me look at this?",
        {},
        id="a-person-whose-name-ends-in-ci",
    ),
    # Private mail must not swallow journalism about money or security.
    pytest.param(
        "Fintech Brainfood <hi@fintechbf.test>",
        "Why payment failed rates matter",
        "This week in payments.",
        {"List-Unsubscribe": "<https://x.test/u>"},
        id="newsletter-about-payments",
    ),
    pytest.param(
        "Risky Biz <newsletter@risky.biz>",
        "Roundup",
        "Cloudflare users saw unusual sign-in alerts this week.",
        {"List-Unsubscribe": "<https://x.test/u>"},
        id="newsletter-reporting-someone-elses-breach",
    ),
    # Review of #87: "overdue" without payment context is a person's ask.
    pytest.param(
        "A Colleague <colleague@example.test>",
        "Q4 budget",
        "Can you approve the overdue Q4 budget by Thursday?",
        {},
        id="overdue-budget-ask",
    ),
    pytest.param(
        # A look-alike host must not match a private domain.
        "Someone <hi@notgtbank.com>",
        "Coffee next week?",
        "Free on Tuesday?",
        {},
        id="look-alike-domain",
    ),
]


@pytest.mark.parametrize(("sender", "subject", "body", "extra"), THE_KEEPERS)
def test_what_the_owner_wants_survives(sender, subject, body, extra):
    item = _item(sender, subject, body, extra=extra)
    assert not item.raw[
        "is_noise"
    ], f"{sender} / {subject!r} was dropped by {item.raw['noise_signal']}"
    assert not item.raw[
        "is_private"
    ], f"{sender} / {subject!r} was dropped by {item.raw['private_signal']}"


# The owner's second rule: "also filter out private emails, like from banks
# or money apps, finance or other personal stuff." Urgency does not save
# these. That reverses task 27, which lifted a bank warning into the
# needs-attention segment, and it is deliberate.
THE_PRIVATE = [
    pytest.param(
        "GTBank <gens@gtbank.com>",
        "Your account has been restricted",
        "Call us.",
        {"List-Unsubscribe": "<https://x.test/u>", "Auto-Submitted": "auto-generated"},
        id="urgent-bank-alert",
    ),
    pytest.param(
        "Trading212 <no-reply@info.trading212.com>",
        "Deposit successful",
        "Your deposit has arrived.",
        {"List-Unsubscribe": "<https://x.test/u>"},
        id="money-app-receipt",
    ),
    pytest.param(
        "Kuda <help@kuda.com>",
        "Hello from Kuda",
        "Some body text.",
        {},
        id="bank-sender-any-subject",
    ),
    pytest.param(
        "Some Shop <orders@shop.example.test>",
        "Your receipt from Some Shop",
        "Thanks for your order.",
        {},
        id="receipt-from-anyone",
    ),
    pytest.param(
        "Google <no-reply@accounts.google.com>",
        "Security alert for owner@example.test",
        "A new sign-in.",
        {},
        id="personal-account-security",
    ),
    pytest.param(
        # Account security from a code host is about the owner, not a repo.
        "GitHub <noreply@github.com>",
        "[GitHub] Unusual sign-in activity on your account",
        "We noticed a sign-in from a new location.",
        {},
        id="code-host-account-security",
    ),
    pytest.param(
        "Some Service <security@service.example.test>",
        "Please check this",
        "We noticed unusual sign-in activity on your account.",
        {},
        id="account-alert-in-a-non-bulk-body",
    ),
    # Review of #87: a subdomain of a private domain, with a subject no
    # private signal covers.
    pytest.param(
        "Trading212 <no-reply@info.trading212.com>",
        "Portfolio update",
        "Your weekly summary.",
        {"List-Unsubscribe": "<https://x.test/u>"},
        id="private-subdomain-sender",
    ),
    # Review of #87: account verification from a bulk dev-tool sender.
    pytest.param(
        "GitHub <noreply@github.com>",
        "[GitHub] Verify your email",
        "Click to verify.",
        {"List-Unsubscribe": "<https://x.test/u>"},
        id="bulk-dev-tool-account-verification",
    ),
    pytest.param(
        "Billing <billing@service.example.test>",
        "Overdue payment on your account",
        "Please pay.",
        {},
        id="overdue-payment",
    ),
]


@pytest.mark.parametrize(("sender", "subject", "body", "extra"), THE_PRIVATE)
def test_private_mail_is_flagged(sender, subject, body, extra):
    item = _item(sender, subject, body, extra=extra)
    assert item.raw["is_private"], f"{sender} / {subject!r} reaches the briefing"
    assert item.raw["private_signal"]
    # Private mail is never journalism either, so it stays off the desk.
    assert item.raw["is_transactional"]
    assert _newsletters_for_extraction([item]) == []
    assert _drop_filtered_mail([item]) == []


def test_private_detection_directly():
    assert detect_private({"From": "x <a@monzo.com>"}, "Hi")[0]
    assert not detect_private({"From": "x <a@example.test>"}, "Lunch Friday?")[0]


def test_exemptions_are_honoured_directly():
    headers = {"From": "GitHub <noreply@github.com>", "X-GitHub-Reason": "security"}
    assert detect_automated_noise(headers, "x")[0]
    # A repository header beats urgency (review of #87); financial wins.
    assert detect_automated_noise(headers, "x", is_urgent=True)[0]
    assert not detect_automated_noise(headers, "x", is_financial=True)[0]
    # Urgency still exempts mail whose only evidence is the sender.
    sender_only = {"From": "GitHub <noreply@github.com>"}
    assert detect_automated_noise(sender_only, "x")[0]
    assert not detect_automated_noise(sender_only, "x", is_urgent=True)[0]


def test_dropping_logs_what_it_removed_without_naming_private_senders(caplog):
    noise = _item(*[p.values for p in THE_NOISE][0])
    private = _item(*[p.values for p in THE_PRIVATE][0])
    keeper = _item(*[p.values for p in THE_KEEPERS][0])
    with caplog.at_level(logging.INFO):
        kept = _drop_filtered_mail([noise, private, keeper])
    assert kept == [keeper]
    assert "filtered 2 of 3 emails out of the briefing" in caplog.text
    assert "X-GitHub-Reason" in caplog.text
    assert "private sender x1" in caplog.text
    assert "gtbank" not in caplog.text.lower()


class _RecordingJudge:
    """Records what triage was asked about and rates everything as urgent,
    the worst case: if a noise message reached it, it would come out HIGH
    with a reply owed."""

    def __init__(self) -> None:
        self.subjects: list[str] = []

    async def triage_emails(self, states, *, timeout=20.0):
        self.subjects.extend(s["subject"] for s in states)
        return [
            EmailJudgment(
                consequence=3.0, confidence=0.9, awaiting_reply=0.95, bulk=0.0
            )
            for _ in states
        ]

    async def judge_claims(self, state, claims, *, timeout=20.0):
        return {c.key: None for c in claims}


async def _async(value):
    return value


async def test_noise_never_reaches_triage_or_the_script(tmp_path):
    """The wiring test. Flags on an `Item` certify nothing unless
    `generate_briefing` acts on them, which is this project's signature
    failure (n_stories=0, music_set, the newsletter segment)."""
    review = _item(*[p.values for p in THE_NOISE][0])
    run_failed = _item(*[p.values for p in THE_NOISE][2])
    bank = _item(*[p.values for p in THE_PRIVATE][0])
    ask = _item(*[p.values for p in THE_KEEPERS][0])
    judge = _RecordingJudge()

    class _RecordingLLM(FakeLLMProvider):
        def __init__(self) -> None:
            super().__init__()
            self.prompts: list[str] = []

        def _dispatch(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return super()._dispatch(prompt)

    llm = _RecordingLLM()

    await generate_briefing(
        briefing_id="2026-11-21",
        length=BriefingLength.STANDARD,
        llm=llm,
        tts=FakeTTSProvider(),
        embeddings=FakeEmbeddingProvider(),
        storage=LocalFilesystemStorage(base_dir=tmp_path),
        fetch_emails=lambda **_: _async([review, run_failed, bank, ask]),
        fetch_events=lambda **_: _async([]),
        fetch_weather=lambda **_: _async(None),
        fetch_articles=lambda **_: _async([]),
        research_provider=None,
        judgment_provider=judge,
    )

    assert judge.subjects == ["Budget sign-off"], judge.subjects
    # Rule 5 (review of #87): a dropped message stays in `all_emails` for
    # the desk, so its body has to be scrubbed there too.
    assert review.body == run_failed.body == bank.body == ""
    prompts = "\n".join(llm.prompts)
    assert "Budget sign-off" in prompts
    assert "PR #87" not in prompts
    assert "Run failed" not in prompts
    assert "restricted" not in prompts


def test_a_lowercase_subject_header_is_still_read():
    """Review of #87: `headers.get("Subject")` missed a lowercase header and
    the subject became "(no subject)", out of reach of the exemptions."""
    raw = {
        "id": "m-lower",
        "threadId": "t1",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "from", "value": "A Colleague <colleague@example.test>"},
                {"name": "to", "value": USER},
                {"name": "subject", "value": "Budget sign-off"},
            ],
            "body": {"data": ""},
        },
        "internalDate": "1795233600000",
    }
    item = message_to_item(raw, USER)
    assert item.title == "Budget sign-off"
    assert item.author == "colleague@example.test"


def test_the_ci_signal_does_not_carry_subject_text():
    """Review of #87: the signal is logged, so it names the rule only."""
    item = _item(*[p.values for p in THE_NOISE if p.id == "self-hosted-ci-subject"][0])
    assert item.raw["noise_signal"] == "ci subject"


def _lowercase_message(msg_id: str, headers: dict[str, str]) -> dict:
    return {
        "id": msg_id,
        "threadId": "t1",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": k, "value": v} for k, v in headers.items()],
            "body": {"data": ""},
        },
        "internalDate": "1795233600000",
    }


def test_a_lowercase_bank_header_keeps_the_financial_exemption():
    """Review of #87: `is_financial_sender` read `From` case-sensitively, so a
    bank message with a lowercase `from` lost its exemption and an
    auto-reply header was enough to call it developer noise."""
    raw = _lowercase_message(
        "m-bank-lower",
        {
            "from": "GTBank <alerts@gtbank.com>",
            "to": USER,
            "subject": "About your request",
            "auto-submitted": "auto-replied",
        },
    )
    item = message_to_item(raw, USER)
    assert item.raw["is_noise"] is False
    assert item.raw["is_private"] is True


def test_a_lowercase_newsletter_header_is_still_bulk():
    """Review of #87: `detect_newsletter` read `List-Unsubscribe`
    case-sensitively, so a newsletter with lowercase headers was not bulk and
    an account-alert phrase in its headline marked it private."""
    raw = _lowercase_message(
        "m-news-lower",
        {
            "from": "Fintech Weekly <editor@fintechweekly.example>",
            "to": USER,
            "subject": "Why payment failed rates matter",
            "list-unsubscribe": "<https://fintechweekly.example/u>",
        },
    )
    item = message_to_item(raw, USER)
    assert item.raw["is_newsletter"] is True
    assert item.raw["is_private"] is False


def test_a_sender_with_a_trailing_comment_is_still_matched():
    """Review of #87: "updates@trading212.com (Trading 212)" is valid RFC 5322
    and `parse_sender` returned it whole, so the host matched no domain."""
    item = _item("updates@trading212.com (Trading 212)", "Portfolio update")
    assert item.raw["is_private"] is True


def test_a_bill_under_a_bland_subject_is_private():
    """Review of #87: the subject said nothing and the body was a bill."""
    item = _item(
        "Billing <billing@service.example.test>",
        "Action required",
        "Your payment is overdue. Outstanding balance: EUR 420.",
    )
    assert item.raw["is_private"] is True


def test_a_colleagues_overdue_ask_in_the_body_survives():
    """Bare "overdue" in a person's message is not a bill."""
    item = _item(
        "A Colleague <colleague@example.test>",
        "Quick one",
        "Can you approve the overdue Q4 budget by Thursday?",
    )
    assert item.raw["is_private"] is False


def test_a_colleagues_staging_payment_failure_survives():
    """Review of #87: "payment failed" in a person's body is engineering
    unless it is about the reader's own account."""
    item = _item(
        "A Colleague <colleague@example.test>",
        "Can you take a look?",
        "Payment failed in the staging environment. Can you investigate?",
    )
    assert item.raw["is_private"] is False


def test_a_payment_failure_on_the_readers_own_card_is_private():
    item = _item(
        "Some Shop <orders@shop.example.test>",
        "Problem with your order",
        "Your payment failed because your card was declined.",
    )
    assert item.raw["is_private"] is True


def test_account_verification_from_a_noreply_service_is_private():
    """Review of #87: noreply@ makes mail "bulk", and the newsletter guard
    then let a non-dev-tool service's "Verify your email" through."""
    item = _item("Some Service <noreply@service.example.test>", "Verify your email")
    assert item.raw["is_private"] is True


def test_a_newsletter_headline_with_list_headers_stays_public():
    item = _item(
        "The Markup <hello@themarkup.example>",
        "How to verify your identity online",
        extra={"List-Unsubscribe": "<https://themarkup.example/u>"},
    )
    assert item.raw["is_private"] is False
