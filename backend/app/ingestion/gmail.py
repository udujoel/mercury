"""
Gmail ingestion.

Ports the MIME-walking, base64url decoding, quote/signature stripping and
newsletter-detection heuristics proven in `scripts/spike_02_gmail.py` into a
proper pipeline module. See that file's docstring for the reasoning behind
each heuristic — this module lifts the logic verbatim rather than
reinventing it.

CLAUDE.md rule 4: read-only scopes only (gmail.readonly). This module never
sends, deletes, labels or modifies anything — it only lists and reads.

CLAUDE.md rule 5: raw email bodies are ephemeral. This module holds message
bodies only in memory for the duration of a single `fetch_emails` call and
returns them embedded in `Item.body` for the generation run; nothing here
writes a raw body to disk.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import sys
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.core.schemas import Item, Priority, SourceType
from app.ingestion.drops import drop_id, log_drop, report_drops

log = logging.getLogger(__name__)

# `in:inbox`, not all mail. Without it a Gmail search covers everything the
# account holds bar spam and trash — so a message already archived, or one a
# filter routed straight past the inbox into a label, was still fetched,
# still judged, and still eligible to be read out. The briefing is meant to
# be about what is sitting in front of you this morning; mail you have
# already dealt with is not that.
#
# Spam and trash were never included: `users.messages.list` takes
# `includeSpamTrash` and it defaults to false. It is passed explicitly at
# the call site anyway, so the guarantee is visible where the query runs
# rather than resting on an API default nobody can see.
GMAIL_QUERY = "in:inbox newer_than:1d -category:promotions -category:social"

# app/core/config.py has no dedicated "user email" setting (user_name is a
# display name, e.g. "Joel"), and the Gmail account address is whatever

# ──────────────────────────────────────────────────────────────
# MIME walking
# ──────────────────────────────────────────────────────────────


def decode_base64url(data: str) -> str:
    """Gmail API base64url bodies commonly arrive without padding, and can
    use either '-'/'_' or occasionally the standard alphabet. Pad and decode
    defensively; never let a bad body kill the whole message."""
    if not data:
        return ""
    data = data.replace("-", "+").replace("_", "/")
    padding = (-len(data)) % 4
    data += "=" * padding
    try:
        return base64.b64decode(data).decode("utf-8", errors="replace")
    except Exception:
        return ""


def walk_mime_parts(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Recursively descend payload/parts and return (plain_text, html_text),
    preferring the first text/plain and text/html leaves found. Skips parts
    that are attachments (have a filename) or lack inline body data."""
    plain: str | None = None
    html: str | None = None

    def visit(node: dict[str, Any]) -> None:
        nonlocal plain, html
        mime_type = node.get("mimeType", "")
        filename = node.get("filename") or ""
        body = node.get("body", {})
        data = body.get("data")

        if mime_type == "text/plain" and not filename and data and plain is None:
            plain = decode_base64url(data)
        elif mime_type == "text/html" and not filename and data and html is None:
            html = decode_base64url(data)

        for part in node.get("parts") or []:
            visit(part)

    visit(payload)
    return plain, html


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    # tracking pixels: 1x1 (or width/height <=2) images carry no content
    for img in soup.find_all("img"):
        width, height = img.get("width"), img.get("height")
        try:
            if width and height and int(width) <= 2 and int(height) <= 2:
                img.decompose()
        except (TypeError, ValueError):
            pass
    return soup.get_text(separator="\n")


def extract_body(payload: dict[str, Any]) -> str:
    """Prefer text/plain; fall back to HTML stripped with BeautifulSoup."""
    plain, html = walk_mime_parts(payload)
    if plain:
        return plain
    if html:
        return html_to_text(html)
    return ""


# ──────────────────────────────────────────────────────────────
# Cleaning
# ──────────────────────────────────────────────────────────────

QUOTE_HEADER_RE = re.compile(r"^\s*On .+ wrote:\s*$", re.MULTILINE | re.IGNORECASE)
TRACKING_PIXEL_RE = re.compile(
    r"<img[^>]+(width=[\"']?1[\"']?|height=[\"']?1[\"']?)[^>]*>", re.IGNORECASE
)
UNSUBSCRIBE_LINE_RE = re.compile(r"^.*unsubscribe.*$", re.IGNORECASE | re.MULTILINE)


def strip_quote_chain(text: str) -> str:
    """Cut everything from the first 'On ... wrote:' line onward, and drop
    any leading '>' quoted lines that remain."""
    match = QUOTE_HEADER_RE.search(text)
    if match:
        text = text[: match.start()]
    lines = [line for line in text.split("\n") if not line.lstrip().startswith(">")]
    return "\n".join(lines)


def strip_signature(text: str) -> str:
    """Cut everything from a standalone '-- ' signature delimiter onward."""
    marker = "\n-- \n"
    idx = text.find(marker)
    if idx == -1 and text.startswith("-- \n"):
        idx = 0
    if idx != -1:
        text = text[:idx]
    return text


def strip_unsubscribe_footer(text: str) -> str:
    return UNSUBSCRIBE_LINE_RE.sub("", text)


def collapse_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_body(raw_html_or_text: str) -> str:
    text = TRACKING_PIXEL_RE.sub("", raw_html_or_text)
    text = strip_quote_chain(text)
    text = strip_signature(text)
    text = strip_unsubscribe_footer(text)
    text = collapse_whitespace(text)
    return text


# ──────────────────────────────────────────────────────────────
# Newsletter detection (PRD F-04)
# ──────────────────────────────────────────────────────────────

NEWSLETTER_SENDER_PATTERNS = [
    "noreply@",
    "no-reply@",
    "newsletter@",
    "newsletters@",
    "hello@",
    "@substack.com",
    "@mailchimp.com",
    "@sendgrid.net",
]


def detect_newsletter(headers: dict[str, str]) -> tuple[bool, str | None]:
    """Returns (is_newsletter, signal) where signal names which heuristic
    fired, so the classification is debuggable rather than a black box."""
    if _header(headers, "List-Unsubscribe"):
        return True, "List-Unsubscribe header"
    if _header(headers, "Precedence").lower() == "bulk":
        return True, "Precedence: bulk"
    if _header(headers, "List-Id"):
        return True, "List-Id header"

    sender = _header(headers, "From").lower()
    for pattern in NEWSLETTER_SENDER_PATTERNS:
        if pattern in sender:
            return True, f"sender heuristic ({pattern})"

    return False, None


# ──────────────────────────────────────────────────────────────
# Transactional mail (ROADMAP task 27)
# ──────────────────────────────────────────────────────────────

# Why this is a SECOND flag rather than a fix to the first.
#
# `detect_newsletter` is not wrong about what it measures. A GTBank debit
# alert really does carry `List-Unsubscribe`, really is bulk-sent, and really
# should be treated editorially the way a newsletter is — not read out as
# though a person wrote it to you. What that flag cannot do is answer the
# DIFFERENT question `digest_members` asks it: does this belong on a screen
# built for published journalism?
#
# Those two questions had one answer, and here is what it cost, measured on
# the live inbox on 2026-09-20. Twelve messages were flagged `is_newsletter`
# and so were eligible for the 30-day browse desk. Seven of them:
#
#     gens@gtbank.com               "Transaction Notification"
#     no-reply@info.trading212.com  "Deposit successful"
#     no-reply@accounts.google.com  "Security alert for <an account>"
#     noreply-accounts@google.com   "You shared some Google Account data..."
#     notifications@github.com x2   "Re: [udujoel/mercury] ..."
#     notice-noreply@qoder.com      "Qoder Credit Consumption Rate Update"
#
# Five real newsletters, seven machines — and the machines are eligible for
# an LLM-written summary persisted to R2 for thirty days. Two of those seven
# are financial and one is a security alert, which is the precise material
# the owner's triage rule says belongs in ACTION REQUIRED, not on a browse
# shelf as though it were an article someone wrote.
#
# So `is_newsletter` keeps deciding TREATMENT and this decides ELIGIBILITY.
# Splitting them lets a bank alert stay correctly classified as bulk mail
# while never reaching the desk, the enrichment pass or the research
# provider.

# Three lists, because review of #55 showed one was answering two questions
# again — the exact mistake this task exists to fix.
#
# `noreply@` and `no-reply@` are NOT here, though the first cut had them.
# They are already in `NEWSLETTER_SENDER_PATTERNS` above as evidence a
# message IS a newsletter, and two functions in one file cannot draw
# opposite conclusions from one substring. The reason they appear in both
# is that they mean BULK, which journalism and receipts both are. So they
# discriminate nothing, and using them gated `noreply@medium.com` off the
# desk along with the bank.
#
# What is left are shapes a publication does not use. A newsletter is sent
# by a publisher; `notifications@`, `alerts@` and `donotreply@` are sent by
# a system with no editorial voice at all.
TRANSACTIONAL_SENDER_SIGNALS: list[str] = [
    "notifications@",
    "notification@",
    "notify@",
    "alerts@",
    "alerting@",
    "donotreply@",
    "do-not-reply@",
    "mailer-daemon@",
    "postmaster@",
]

# Providers whose mail is about the reader's own money or account, never
# about the world. An explicit list, because this is the one place where
# precision is worth the maintenance: it is what lifts a bank alert into
# ACTION REQUIRED, and a wrong entry here does not merely lose a desk row,
# it wakes someone up.
#
# Matched against the whole From header, so an entry can be a domain
# ("@gtbank.com") or a subdomain suffix that covers a family of senders.
FINANCIAL_SENDER_DOMAINS: list[str] = [
    "@gtbank.com",
    "@trading212.com",
    "@wise.com",
    "@revolut.com",
    "@paypal.com",
    "@stripe.com",
    "@monzo.com",
    "@starlingbank.com",
    "@accounts.google.com",
    "@account.microsoft.com",
]

# Subjects that can only be about the reader's own account.
#
# Four entries were removed after review: "withdrawal", "your order",
# "verify your" and "security alert". Each is a news phrase before it is a
# banking one — "US Announces Troop Withdrawal From Syria" was gated off
# the desk, and the PRD's own five-source acceptance story lost three of
# its five sources to it. Worse, those four are also `URGENT_PATTERNS`, so
# the subject that marked a message transactional was the same string that
# marked it urgent: a security newsletter was removed from the desk AND
# promoted into the action-required list by one match.
#
# What remains is anchored on a possessive or a completed action, which a
# headline about the world does not have.
TRANSACTIONAL_SUBJECT_SIGNALS: list[str] = [
    "transaction notification",
    "transaction alert",
    "deposit successful",
    "withdrawal request",
    "withdrawal successful",
    "payment received",
    "your receipt",
    "your invoice",
    "invoice #",
    "order confirmation",
    "your statement",
    "statement is ready",
    "account statement",
    "password reset",
    "reset your password",
]


def detect_transactional(
    headers: dict[str, str], subject: str
) -> tuple[bool, str | None]:
    """Is this a receipt, an alert, or a notification from a machine?

    Returns `(is_transactional, signal)`, with the signal naming which rule
    fired — the same debuggability contract as `detect_newsletter`, and for
    the same reason: a silent classifier is one nobody can argue with when
    it is wrong.

    Deliberately NOT a judgement about importance. A login alert may matter
    enormously; this says only that it is not journalism, so it must not be
    summarised onto the browse desk. Priority is `classify_priority`'s job,
    and `detect_urgent` is what lifts the ones that matter — the two run
    independently precisely so that "keep it off the desk" and "say it out
    loud" can both be true of one bank alert.
    """
    haystack = (subject or "").lower()
    sender = _header(headers, "From").lower()

    # Financial senders first, so the signal string names the tier that
    # `detect_urgent` keys on rather than whichever rule happened to match.
    for domain in FINANCIAL_SENDER_DOMAINS:
        if domain in sender:
            return True, f"financial ({domain})"
    for signal in TRANSACTIONAL_SUBJECT_SIGNALS:
        if signal in haystack:
            return True, f"subject ({signal})"
    for signal in TRANSACTIONAL_SENDER_SIGNALS:
        if signal in sender:
            return True, f"sender ({signal})"
    return False, None


# ──────────────────────────────────────────────────────────────
# Private mail (ROADMAP task 62, owner's rule 2026-11-21)
# ──────────────────────────────────────────────────────────────

# "Also filter out private emails, like from banks or money apps, finance or
# other personal stuff." This REVERSES an earlier rule. Until now a bank's
# account warning was lifted past the newsletter floor into the segment the
# hosts introduce as needing attention (task 27). The owner now wants none
# of it in the briefing: the briefing is audio, played aloud, stored in R2,
# and its script is written by a model that sees the mail. Private mail is
# dropped before triage, so its body never reaches Jev or Gemini either.
#
# `FINANCIAL_SENDER_DOMAINS` stays as it is and is included here, so that
# list keeps meaning "holds the reader's money" for `detect_urgent`. These
# are the extra senders that are private without being banks.
PRIVATE_SENDER_DOMAINS: list[str] = [
    *FINANCIAL_SENDER_DOMAINS,
    # Banks and money apps beyond the alert list.
    "@kuda.com",
    "@opay-inc.com",
    "@moniepoint.com",
    "@accessbankplc.com",
    "@zenithbank.com",
    "@firstbanknigeria.com",
    "@ubagroup.com",
    "@millenniumbcp.pt",
    "@cgd.pt",
    "@activobank.pt",
    "@novobanco.pt",
    "@n26.com",
    "@klarna.com",
    "@venmo.com",
    "@cash.app",
    "@squareup.com",
    "@coinbase.com",
    "@binance.com",
    "@kraken.com",
    "@robinhood.com",
    "@chase.com",
    "@barclays.co.uk",
    "@hsbc.co.uk",
    # Personal accounts: identity, tax, payroll.
    "@id.apple.com",
    "@email.apple.com",
    "@portaldasfinancas.gov.pt",
    "@hmrc.gov.uk",
    "@irs.gov",
    "@deel.com",
    "@remote.com",
]

# Subjects that can only be about the reader's own money or accounts. The
# money-shaped subset of `TRANSACTIONAL_SUBJECT_SIGNALS` plus account and
# payroll phrases, anchored on a possessive or a completed action for the
# reason that list records: a headline about the world does not have one.
PRIVATE_SUBJECT_SIGNALS: list[str] = [
    "transaction notification",
    "transaction alert",
    "debit alert",
    "credit alert",
    "deposit successful",
    "withdrawal request",
    "withdrawal successful",
    "payment received",
    "payment successful",
    "money received",
    "your receipt",
    "your invoice",
    "invoice #",
    "your statement",
    "statement is ready",
    "account statement",
    "your payslip",
    "your pay slip",
    "your salary",
    "your tax return",
    "your balance",
    # "Overdue" alone is not here and not in `_ACCOUNT_ALERT_RE`: a
    # colleague asking about "the overdue Q4 budget" is exactly the mail the
    # owner wants (review of #87). Only the payment-shaped phrases.
    "overdue payment",
    "payment overdue",
    "payment is overdue",
    "payment is past due",
    "past due balance",
    "past due payment",
    "password reset",
    "reset your password",
    "verification code",
    "one-time code",
    "one-time password",
    "your otp",
]


PRIVATE_BODY_PAYMENT_PHRASES: tuple[str, ...] = (
    "overdue payment",
    "payment overdue",
    "payment is overdue",
    "payment is past due",
    "past due balance",
    "past due payment",
    "outstanding balance",
)


def detect_private(
    headers: dict[str, str], subject: str, body: str = ""
) -> tuple[bool, str | None]:
    """Is this about the reader's own money or personal accounts?

    Returns `(is_private, signal)`, the signal naming the rule, the same
    contract as every other detector in this file.

    Deliberately ignores urgency. An account-restriction warning is the
    most private mail there is, and the owner's rule is that private mail
    stays out of the briefing whatever it says. Gmail is where it gets read.

    Account-security alerts from any sender count too ("Unusual sign-in to
    your account"), matched on the subject and first screen of the body
    like `detect_urgent`, but never for bulk mail, where those words are
    reporting someone else's breach.
    """
    # Matched on the parsed HOST, the domain or any subdomain of it at a dot
    # boundary. A substring match on "@trading212.com" missed
    # no-reply@info.trading212.com (review of #87), and a bare suffix match
    # would let "notgtbank.com" through.
    # parseaddr, not `parse_sender`: RFC 5322 allows a trailing comment,
    # "updates@trading212.com (Trading 212)", which `parse_sender` returns
    # whole, leaving a host that matches nothing (review of #87).
    _, address = parseaddr(_header(headers, "From"))
    host = address.strip().lower().rpartition("@")[2] if "@" in address else ""
    for domain in PRIVATE_SENDER_DOMAINS:
        bare = domain.lstrip("@")
        if host == bare or host.endswith("." + bare):
            return True, f"sender ({domain})"
    haystack = (subject or "").lower()
    for signal in PRIVATE_SUBJECT_SIGNALS:
        if signal in haystack:
            return True, f"subject ({signal})"
    is_bulk, _ = detect_newsletter(headers)
    text = subject or ""
    if not is_bulk:
        text = f"{text}\n{(body or '')[:URGENT_BODY_CHARS]}"
    match = _ACCOUNT_ALERT_RE.search(text)
    # The pattern alone is not enough, for any mail. It must also be about
    # the reader's OWN account: the text says "your account", "your card"
    # and the like, or (for bulk mail) the sender is a service the reader
    # holds an account with (a dev-tool domain). A colleague's "Payment
    # failed in the staging environment" is engineering, not their bank
    # (review of #87); "Why payment failed rates matter" is a newsletter
    # headline; a code host's "[GitHub] Verify your email" from noreply@ is
    # still the reader's own account.
    sender = _header(headers, "From").lower()
    # A real mailing list announces itself with list headers. Mail that is
    # "bulk" only because it comes from noreply@ is usually a service writing
    # to the reader about their own account ("Verify your email"), so it gets
    # the ordinary ownership rule (review of #87).
    is_list = bool(
        _header(headers, "List-Unsubscribe")
        or _header(headers, "List-Id")
        or _header(headers, "Precedence").lower() == "bulk"
    )
    if is_list:
        # Stricter for lists: "How to verify your identity online" is a
        # newsletter headline that names a "your" and is about nobody.
        # A notice LEADS with the alert ("Verify your email", "[Acme] Your
        # card was declined"); a headline wraps it ("How to verify your
        # identity online"). List-Unsubscribe is not proof of journalism, so
        # a subject that opens with the alert counts too (review of #87).
        lead = _SUBJECT_TAG_RE.sub("", subject or "", count=1)
        owned = (
            "your account" in text.lower()
            or any(d in sender for d in DEV_NOISE_SENDER_DOMAINS)
            or bool(_ACCOUNT_ALERT_RE.match(lead))
        )
    else:
        owned = bool(_OWNERSHIP_RE.search(text))
    if match and not owned:
        match = None
    if match:
        return True, f"account alert ({match.group(0).lower()})"
    # A bill can say what it is in the body under a bland subject ("Action
    # required"). Only the payment-shaped phrases, and only for mail written
    # to the reader, so a bare "overdue" in a colleague's ask still survives.
    if not is_bulk:
        lowered = (body or "")[:URGENT_BODY_CHARS].lower()
        for phrase in PRIVATE_BODY_PAYMENT_PHRASES:
            if phrase in lowered:
                return True, f"body ({phrase})"
    return False, None


def is_financial_sender(headers: dict[str, str]) -> bool:
    """Is this from a provider that holds the reader's money or account?

    The narrow gate, kept separate from `detect_transactional` because the
    two carry different consequences. Being transactional costs a message
    its place on the browse desk. Being FINANCIAL can lift it past the
    newsletter floor into the segment the hosts introduce as needing
    attention — so it is an explicit list of providers, never a keyword.
    """
    sender = _header(headers, "From").lower()
    return any(domain in sender for domain in FINANCIAL_SENDER_DOMAINS)


# ──────────────────────────────────────────────────────────────
# Automated developer noise (ROADMAP task 62)
# ──────────────────────────────────────────────────────────────

# The owner's rule, 2026-11-21: GitHub issues, pull requests and build
# results are not briefing material. What belongs in the email segment is
# newsletters and mail that needs the owner's attention.
#
# Why a deterministic gate rather than another line in the Jev prompt. A
# GitHub review request is, read literally, "a specific person is waiting
# for the recipient", which is exactly what `awaiting_reply` is asked to
# find, and a failed deploy reads as a consequence. Both kept reaching the
# spoken inbox through triage. These senders announce themselves in their
# headers, so asking a model to rediscover that on every run spends the
# judgment budget the semantic quality gates need (task 47) to learn
# something the headers already say for free.
#
# Noise is NOT the same thing as transactional. Transactional costs a
# message its place on the browse desk; noise costs it its place in the
# briefing entirely. Every noise message is also marked transactional (it
# is certainly not journalism), but not the other way round: a bank receipt
# is transactional and still gets spoken if triage thinks it matters.

# Header names that only a code host or CI system sets. Their presence is
# the strongest signal there is, stronger than the sender, because GitHub
# also sends from addresses that look personal when a reply comes in by mail.
DEV_NOISE_HEADERS: list[str] = [
    "X-GitHub-Reason",
    "X-GitHub-Sender",
    "X-GitLab-Project",
    "X-GitLab-NotificationReason",
    "X-GitLab-Pipeline-Id",
]

# `List-Id` values a code host uses for its per-repository lists, e.g.
# "udujoel/mercury <mercury.udujoel.github.com>".
DEV_NOISE_LIST_IDS: list[str] = [
    ".github.com>",
    ".gitlab.com>",
]

# Sender domains whose mail is about repositories, builds and deploys.
# Matched against the whole From header like `FINANCIAL_SENDER_DOMAINS`, so
# "@github.com" covers notifications@, noreply@ and the per-thread reply
# addresses alike.
DEV_NOISE_SENDER_DOMAINS: list[str] = [
    "@github.com",
    "@gitlab.com",
    "@bitbucket.org",
    "@circleci.com",
    "@travis-ci.com",
    "@buildkite.com",
    "@vercel.com",
    "@netlify.com",
    "@codecov.io",
]

# A build or deploy result. Only counted when the sender is ALSO a machine
# (see `_CI_SENDER_LOCAL_PARTS`), because "Why our build failed" is a
# perfectly good newsletter headline and the guard against journalism being
# gated by a news word is a lesson #55 already paid for.
_CI_SUBJECT_RE = re.compile(
    r"\b(?:build|pipeline|workflow|deploy(?:ment)?|run|job|check)s?\b"
    r"[^\n]{0,40}?\b(?:failed|failing|passed|succeeded|successful|fixed|"
    r"broken|cancell?ed|errored|recovered)\b",
    re.I,
)

# Mailbox local parts a CI or deploy system sends from, matched EXACTLY
# against the parsed address. Two things review of #87 caught in the first
# cut: a substring match made "ci@" fire on marci@, and counting `noreply@`
# as a machine dropped "Why the CrowdStrike deploy failed" from
# noreply@medium.com. `noreply` means bulk, which publications are too (see
# the note above `TRANSACTIONAL_SENDER_SIGNALS`), so it is not evidence of a
# build system and is not here.
_CI_SENDER_LOCAL_PARTS: frozenset[str] = frozenset(
    {
        "builds",
        "build",
        "ci",
        "jenkins",
        "deploy",
        "deploys",
        *(s.rstrip("@") for s in TRANSACTIONAL_SENDER_SIGNALS),
    }
)

# The subjects that exempt a dev-tool sender's mail from the domain rule:
# the owner's own account or money, never a repository. Deliberately NOT
# the whole `URGENT_PATTERNS`: "action required" and "expires soon" are what
# a failed-deploy or token-expiry notice says too, and review of #87 found
# "Action required: Deployment failed" from Vercel slipping through on them.
_ACCOUNT_ALERT_RE = re.compile(
    "|".join(
        f"({p})"
        for p in (
            r"unusual (?:sign[- ]?in|login|activity)",
            r"suspicious (?:activity|sign[- ]?in|login)",
            r"account (?:has been |was |is |been )?"
            r"(?:restricted|suspended|locked|disabled)",
            r"(?:un)?authoriz?ed (?:access|transaction|login)",
            r"payment (?:has )?(?:failed|declined)",
            r"(?:card|transaction) (?:was )?declined",
            r"verify your (?:account|identity|email)",
            r"confirm your identity",
            r"security alert (?:for|on) your",
            r"new (?:device|sign[- ]?in) (?:on|to) your account",
        )
    ),
    re.I,
)


# What makes an account alert the reader's own. Checked with the alert
# pattern in `detect_private`, never on its own.
_OWNERSHIP_RE = re.compile(
    r"\byour (?:account|card|payment|bank|wallet|transaction|login|"
    r"sign[- ]?in|password|identity|email|subscription|balance)\b",
    re.I,
)


# A leading "[Brand]" tag or "Re:"/"Fwd:" prefix, stripped before asking
# whether a subject opens with an account alert.
_SUBJECT_TAG_RE = re.compile(r"^\s*(?:(?:\[[^\]]*\]|re:|fwd?:)\s*)*", re.I)


def _header(headers: dict[str, str], name: str) -> str:
    """Case-insensitive header lookup. Gmail returns names as the sender
    wrote them, and "X-Github-Reason" and "X-GitHub-Reason" are both seen."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return ""


def detect_automated_noise(
    headers: dict[str, str],
    subject: str,
    *,
    is_urgent: bool = False,
    is_financial: bool = False,
) -> tuple[bool, str | None]:
    """Is this a code-host, CI or auto-reply message nobody needs to hear?

    Returns `(is_noise, signal)`, the signal naming which rule fired, the
    same debuggability contract as `detect_newsletter`.

    The checks run strongest evidence first, and the exemptions sit between
    them on purpose:

    1. **Financial mail is never noise.** An explicit provider list, the
       owner's own rule.
    2. **Explicit automation markers beat urgency.** A repository header,
       a code-host List-Id or `Auto-Submitted: auto-replied` says outright
       that software wrote the message. `detect_urgent` reads the subject
       as fresh text, so an out-of-office that quotes "Action required:
       approve Q4 budget" would otherwise be urgent and spoken. Review of
       #87.
    3. **Urgent mail survives the weaker rules.** Past this point the only
       evidence is who sent it or what the subject says, and
       `detect_urgent` has already decided the message is worth saying out
       loud.
    """
    if is_financial:
        return False, None

    for name in DEV_NOISE_HEADERS:
        if _header(headers, name):
            return True, f"header ({name})"

    list_id = _header(headers, "List-Id").lower()
    for marker in DEV_NOISE_LIST_IDS:
        if marker in list_id:
            return True, f"list-id ({marker})"

    # Out-of-office and other auto-replies (RFC 3834). Only `auto-replied`:
    # `auto-generated` is also what a bank's alert system sets.
    if _header(headers, "Auto-Submitted").lower().startswith("auto-replied"):
        return True, "auto-reply (Auto-Submitted)"

    if is_urgent:
        return False, None

    # Nothing past here says the message is about a REPOSITORY, only that a
    # dev tool sent it. So an account subject wins even when `detect_urgent`
    # refused it: "noreply@" makes a code host's "Unusual sign-in activity
    # on your account" a newsletter, and newsletters are never urgent.
    sender = _header(headers, "From").lower()
    for domain in DEV_NOISE_SENDER_DOMAINS:
        if domain in sender:
            if _ACCOUNT_ALERT_RE.search(subject or ""):
                return False, None
            return True, f"sender ({domain})"

    _, address = parse_sender(_header(headers, "From"))
    local_part = address.lower().rsplit("@", 1)[0] if "@" in address else ""
    if local_part in _CI_SENDER_LOCAL_PARTS:
        match = _CI_SUBJECT_RE.search(subject or "")
        if match:
            # A fixed category, not the matched text: this string reaches
            # the INFO log, and subjects do not belong there.
            return True, "ci subject"

    return False, None


# ──────────────────────────────────────────────────────────────
# requires_response / priority (PRD F-05)
# ──────────────────────────────────────────────────────────────

QUESTION_PHRASES = [
    "can you",
    "could you",
    "would you",
    "let me know",
    "please review",
    "please confirm",
    "any thoughts",
    "what do you think",
]


# Mail where not opening Gmail can cost money.
#
# Deliberately NARROW — money, accounts and security, nothing else. The
# obvious wider rule is `requires_response`: addressed to you, contains a
# question mark or one of eight stock phrases. The 2026-09-14 review found
# that is exactly what a phishing mail satisfies BY CONSTRUCTION, which buys
# it the top of the inbox and a guaranteed slot in the one segment the hosts
# introduce as needing immediate attention. A filter that promotes the
# attacker is worse than no filter.
#
# Patterns rather than fixed phrases, because the first cut missed "your
# account HAS BEEN restricted" while matching "account restricted" — real
# subject lines put words in the middle. Found by running it, not by reading
# it.
URGENT_PATTERNS: list[str] = [
    r"unusual (?:sign[- ]?in|login|activity)",
    r"suspicious (?:activity|sign[- ]?in|login)",
    r"account (?:has been |was |is |been )?(?:restricted|suspended|locked|disabled)",
    r"(?:un)?authoriz?ed (?:access|transaction|login)",
    r"payment (?:has )?(?:failed|declined)",
    r"(?:card|transaction) (?:was )?declined",
    r"(?:past due|overdue)",
    r"(?:expires|expiring) (?:today|tomorrow|soon|in \d+ days?)",
    r"action required",
    r"verify your (?:account|identity|email)",
    r"confirm your identity",
    # Squarely inside the owner's "money, accounts and security only" rule,
    # and the live inbox carried one on 2026-09-20 that reached nothing:
    # "Security alert for <an account>" from no-reply@accounts.google.com.
    #
    # ANCHORED, because the bare noun phrase is not rare. Review found
    # `r"security alert"` promoting a colleague's "we are rolling out a new
    # security alert system next week, no action needed" to HIGH, and every
    # GitHub notification for a PR whose title contains the words. Every
    # other entry in this list is possessive-anchored for the same reason.
    r"security alert (?:for|on) your",
    r"security alert for [\w.+-]+@",
    r"new (?:device|sign[- ]?in) (?:on|to) your account",
]

_URGENT_RE = re.compile("|".join(f"({p})" for p in URGENT_PATTERNS), re.I)

# How far into a body an urgent phrase still counts as being ABOUT the
# reader. Roughly the first screen.
URGENT_BODY_CHARS = 800


def detect_urgent(
    subject: str,
    body: str,
    *,
    is_newsletter: bool = False,
    is_financial: bool = False,
) -> tuple[bool, str | None]:
    """Does this mail carry a consequence worth naming out loud?

    Returns `(is_urgent, signal)`, the signal naming what fired, so a wrong
    answer can be argued with rather than guessed at.

    **Newsletters are never urgent, whatever they say.** The first cut
    flagged a digest whose body read "Cloudflare users saw unusual sign-in
    alerts" — someone else's incident, reported as news. That is the single
    biggest source of false positives here, and false positives are the
    expensive direction: this segment's whole value is that when it says
    something needs attention, it does.

    **Mail from a FINANCIAL sender is exempt from that guard**, and the
    omission was load-bearing. `detect_newsletter` fires on
    `List-Unsubscribe`, which every bank and payment app sets, so a GTBank
    account-restriction warning arrived flagged `is_newsletter` and this
    function refused it before reading a word — the one category the owner
    named as worth waking up for was the one category structurally
    incapable of being urgent.

    The exemption is `is_financial`, NOT `is_transactional`, and review of
    #55 is why. Four `TRANSACTIONAL_SUBJECT_SIGNALS` were also
    `URGENT_PATTERNS` — "security alert", "verify your", "unusual sign-in",
    "payment failed" — so for those strings the subject that marked a
    message transactional was the SAME MATCH that marked it urgent. A
    security newsletter was pulled off the desk and promoted into the
    action-required list in one move, and four of five realistic
    publications came out HIGH. `is_financial` is an explicit list of
    providers who hold the reader's money, which is the owner's stated
    rule — money, accounts and security — rather than a keyword that
    journalism also uses.

    **For a newsletter, only the SUBJECT is searched.** A bank states the
    consequence in the subject line; a digest mentions someone else's
    breach in its third paragraph. Searching the body of an exempted bulk
    message is how "Cloudflare users saw unusual sign-in alerts" became an
    emergency.

    Matched on the subject and the first screen of the body only. A phrase in
    a fortieth paragraph is reporting, not addressing.
    """
    if is_newsletter and not is_financial:
        return False, None
    if is_newsletter:
        haystack = subject or ""
    else:
        haystack = f"{subject or ''}\n{(body or '')[:URGENT_BODY_CHARS]}"
    match = _URGENT_RE.search(haystack)
    if match:
        return True, match.group(0).lower()
    return False, None


def classify_priority(
    *,
    is_newsletter: bool,
    is_urgent: bool = False,
    body: str,
    to_addresses: list[str],
    user_email: str,
) -> tuple[Priority, bool]:
    """Priority/requires_response heuristic. See spike_02_gmail.py for the
    full reasoning — lifted verbatim."""
    if is_urgent:
        # Ahead of the newsletter floor on purpose. The review found that a
        # bank's account-restriction warning was pinned LOW and barred from
        # the actionable segment, because `List-Unsubscribe` made it a
        # newsletter — sorted below a gym mailer by arrival time. This is the
        # one category where not opening Gmail has a price.
        return Priority.HIGH, True
    if is_newsletter:
        return Priority.LOW, False

    # Guard the empty case: "" is a substring of every address, so an
    # unset USER_EMAIL would make every message look directly addressed.
    user_in_to = bool(user_email) and any(
        user_email.lower() in addr.lower() for addr in to_addresses
    )
    has_question = "?" in body
    has_ask_phrase = any(phrase in body.lower() for phrase in QUESTION_PHRASES)

    if user_in_to and (has_question or has_ask_phrase):
        return Priority.HIGH, True

    return Priority.MEDIUM, False


# ──────────────────────────────────────────────────────────────
# Normalisation
# ──────────────────────────────────────────────────────────────


def header_dict(payload: dict[str, Any]) -> dict[str, str]:
    return {h["name"]: h["value"] for h in payload.get("headers", [])}


def parse_sender(from_header: str) -> tuple[str, str]:
    """'Signals Weekly <newsletter@signalsweekly.com>' -> (name, email)."""
    match = re.match(r"^(.*?)\s*<(.+?)>\s*$", from_header.strip())
    if match:
        name = match.group(1).strip().strip('"')
        return (name or match.group(2)), match.group(2)
    return from_header.strip(), from_header.strip()


def _parse_date(date_str: str | None, internal_date_ms: str | None) -> datetime:
    if internal_date_ms:
        try:
            return datetime.fromtimestamp(int(internal_date_ms) / 1000, tz=UTC)
        except (ValueError, TypeError):
            pass
    if date_str:
        try:
            from email.utils import parsedate_to_datetime

            return parsedate_to_datetime(date_str)
        except (TypeError, ValueError):
            pass
    return datetime.now(UTC)


def message_to_item(raw: dict[str, Any], user_email: str) -> Item:
    payload = raw.get("payload")
    if payload is None:
        raise ValueError(f"message {raw.get('id')} has no payload")

    headers = header_dict(payload)
    # Case-insensitive, like every detector below: a lowercase "subject"
    # header otherwise became "(no subject)" and hid an account alert from
    # the exemptions that read it.
    subject = _header(headers, "Subject") or "(no subject)"
    from_header = _header(headers, "From") or "unknown@unknown"
    sender_name, sender_email = parse_sender(from_header)
    to_addresses = [a.strip() for a in headers.get("To", "").split(",") if a.strip()]

    body_raw = extract_body(payload)
    body = clean_body(body_raw)

    is_newsletter, signal = detect_newsletter(headers)
    is_transactional, transactional_signal = detect_transactional(headers, subject)
    is_urgent, urgent_signal = detect_urgent(
        subject,
        body,
        is_newsletter=is_newsletter,
        is_financial=is_financial_sender(headers),
    )
    priority, requires_response = classify_priority(
        is_newsletter=is_newsletter,
        is_urgent=is_urgent,
        body=body,
        to_addresses=to_addresses,
        user_email=user_email,
    )
    is_noise, noise_signal = detect_automated_noise(
        headers,
        subject,
        is_urgent=is_urgent,
        is_financial=is_financial_sender(headers),
    )
    is_private, private_signal = detect_private(headers, subject, body)
    if is_private and not is_transactional:
        is_transactional, transactional_signal = True, f"private ({private_signal})"
    if is_noise:
        # LOW and no reply owed, so anything that reads priority before the
        # pipeline drops the message still ranks it last. And transactional,
        # so the desk and the newsletter extractor refuse it as well.
        priority, requires_response = Priority.LOW, False
        if not is_transactional:
            is_transactional, transactional_signal = True, f"noise ({noise_signal})"

    date_str = headers.get("Date")
    timestamp = _parse_date(date_str, raw.get("internalDate"))

    return Item(
        source_type=SourceType.EMAIL,
        source_name=sender_name,
        title=subject,
        body=body,
        author=sender_email,
        timestamp=timestamp,
        requires_response=requires_response,
        priority=priority,
        raw={
            "message_id": raw.get("id"),
            "thread_id": raw.get("threadId"),
            "is_newsletter": is_newsletter,
            "newsletter_signal": signal,
            "is_transactional": is_transactional,
            "transactional_signal": transactional_signal,
            "is_urgent": is_urgent,
            "urgent_signal": urgent_signal,
            "is_noise": is_noise,
            "noise_signal": noise_signal,
            "is_private": is_private,
            "private_signal": private_signal,
            "to": to_addresses,
        },
    )


# ──────────────────────────────────────────────────────────────
# Fetch paths
# ──────────────────────────────────────────────────────────────


def _load_fixture_messages(fixture: Path) -> list[dict[str, Any]]:
    data = json.loads(fixture.read_text())
    return data["messages"]


def _fetch_live_messages(max_messages: int) -> list[dict[str, Any]]:
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "google-api-python-client is not installed; cannot fetch Gmail "
            "live. Install it or pass fixture= for the offline path."
        ) from exc

    # Reuse the Phase 0 OAuth helper — same scopes, same token cache.
    scripts_dir = Path(__file__).resolve().parents[3] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from _google_auth import get_credentials

    # Deliberately NOT re-wrapped in a plain RuntimeError. MissingCredentialsError
    # already is one, so the documented contract holds, and flattening it threw
    # away the ExpiredCredentialsError subtype — the single most likely
    # production failure here, and the only one with a known permanent fix.
    # app.cli keys its loudest error off that type, and it cannot do that if
    # the type is discarded one frame after it is raised.
    creds = get_credentials()

    service = build("gmail", "v1", credentials=creds)

    resp = (
        service.users()
        .messages()
        .list(
            userId="me",
            q=GMAIL_QUERY,
            maxResults=max_messages,
            # The API default, stated. Rule 4 is a structural
            # promise and it should not rest on a default.
            includeSpamTrash=False,
        )
        .execute()
    )
    message_refs = resp.get("messages", [])

    full_messages = []
    for ref in message_refs:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=ref["id"], format="full")
            .execute()
        )
        full_messages.append(msg)
    return full_messages


async def fetch_emails(
    *, fixture: Path | None = None, max_messages: int = 60
) -> list[Item]:
    """Fetch and normalise recent Gmail messages into `Item`s.

    fixture=<path> reads that JSON file instead of hitting the network.
    fixture=None hits the live Gmail API and raises a clear, actionable
    RuntimeError naming the exact missing credential if it cannot run.
    """
    user_email = get_settings().user_email
    if not user_email:
        # Without it, "am I a direct To recipient" cannot be evaluated and
        # nothing is ever scored HIGH, so the briefing quietly loses its
        # "needs a response" calls. Warn rather than degrade silently.
        log.warning(
            "USER_EMAIL is unset; no email can be scored HIGH priority. "
            "Set USER_EMAIL in backend/.env to the Google account the "
            "OAuth token belongs to."
        )

    if fixture is not None:
        raw_messages = _load_fixture_messages(fixture)
    else:
        raw_messages = _fetch_live_messages(max_messages)

    considered = raw_messages[:max_messages]
    items: list[Item] = []
    dropped = 0
    for raw in considered:
        try:
            items.append(message_to_item(raw, user_email))
        except Exception:
            # A malformed message must never take down the whole run
            # (CLAUDE.md rule 7 — graceful degradation) — but it must be
            # SEEN. See app/ingestion/drops.py for why the silent version of
            # this was worse than no error handling at all.
            dropped += 1
            log_drop(log, "gmail", drop_id(raw))
    report_drops(log, "gmail", total=len(considered), dropped=dropped)
    return items
