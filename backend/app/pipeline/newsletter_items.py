"""A newsletter is a bundle. This takes it apart.

**Why this exists.** Mercury treated one newsletter as one item: Morning
Brew's Tuesday edition — the Fed, a chip ruling and two earnings calls — was
a single thing with a single embedding and a single summary. That is why the
hosts could spend a minute on "Morning Brew covered several topics" and say
nothing, and it is why cross-source dedup fired eight times in 5,590
stories.

That last number was read as evidence that the premise was wrong — that
Joel's sources simply do not overlap. It is not. Two newsletters that both
lead with the Fed never cluster, because each embedding is dominated by its
own four-way mixture of unrelated stories. **The unit being compared was a
bundle, not a story.** Extract the items first and the comparison has
something it can actually match, which is what makes "three of your
newsletters led with the Fed" computable rather than invisible.

The owner's framing, 2026-09-15: "An email can mention a number of news or
info, extract these and look into it, then create a good narrative to convey
the info. Across the different newsletters found in my inbox."

**Rule 5 holds by construction.** Bodies go to the model during the run and
are never stored; what survives is a headline, a one-line gist, a subject
and which newsletter carried it. Nothing here writes a body anywhere.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.providers import ContentBlocked, LLMProvider
from app.core.schemas import Item, Research
from app.pipeline.prompts import (
    NEWSLETTER_EXTRACT_PROMPT,
    _fence_untrusted,
)

log = logging.getLogger(__name__)

# How much of a newsletter the extractor sees, and how many items it may
# return for one issue.
#
# A newsletter is mostly chrome — headers, sponsor blocks, unsubscribe
# footers — and the news is near the top. 6,000 characters is generous for
# the editorial and cheap enough that twenty newsletters still fit in one
# call. The per-issue cap stops a link-dump digest with forty one-line
# blurbs from crowding out the four newsletters that actually wrote
# something.
BODY_CHARS = 6_000
MAX_ITEMS_PER_NEWSLETTER = 6

# How many issues may go into the one extraction call.
#
# Everything else in this module caps the OUTPUT — `MAX_ITEMS_PER_NEWSLETTER`
# per issue, `MAX_SPOKEN_SUBJECTS` for the segment — and every one of those
# runs after the model has answered. The input had no bound at all except
# `max_messages=60`, which is a cap on the inbox, not on this prompt.
#
# The binding constraint is the model's OUTPUT window, not its context. Each
# item costs roughly sixty tokens of JSON, so N issues ask for up to N x 6
# items — about 360N tokens — against a default output limit near 8,192.
# Twenty issues sit inside that; forty-nine, which a real morning produced,
# ask for nearly twice it. The failure is not a smaller segment, it is no
# segment: truncated JSON reaches `_parse` and returns [], or the response
# comes back MAX_TOKENS with no parts at all.
#
# Twenty is the number the BODY_CHARS comment above already assumed, written
# when nothing enforced it.
MAX_NEWSLETTERS = 20

# Sorts an issue with no Date header to the back of the queue rather than
# crashing the comparison. `gmail._parse_date` can return None, and this is
# the same naive/aware trap `_aware` exists for one line up.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# How many merged subjects the hosts actually discuss.
#
# Found by listening, which is the only way it could have been found. A live
# run put all 60-odd subjects from 25 newsletters into the prompt, and the
# model dutifully mentioned nearly every one: twelve consecutive turns of
# "PC-ALM offers a local alternative to backpropagation. Tau is a coding
# agent. Telus warned customers." That is a list being read aloud, which is
# the "concatenated, not edited" failure ADR-003 exists to prevent — and
# `rank` ordering them was never going to stop it, because ordering a
# firehose still gives you a firehose.
#
# Six OFFERED, and the prompt asks for at most four DISCUSSED. The gap is
# deliberate editorial headroom: the ranking knows which subjects the most
# outlets carried, but not which of them will turn out to have anything to
# argue about, and the model can see that once it has the gists. The two it
# does not choose fall into the rapid-fire tail rather than being wasted.
#
# Six against a world segment that covers four stories out of 209. The tail
# is COUNTED rather than listed, the way the desk's "plus eleven others"
# already works: a cap that hides what it dropped is how the tail became a
# segment in the first place.
MAX_SPOKEN_SUBJECTS = 6


def _aware(moment: datetime) -> datetime:
    """Naive timestamps are read as UTC so they can be compared at all.

    The same idiom as `scoring._aware` and `clustering._as_utc`.
    """
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


@dataclass(frozen=True)
class NewsletterItem:
    """One piece of news, as one newsletter told it."""

    headline: str
    gist: str
    # The merge key. A short noun phrase, and the prompt asks for the
    # IDENTICAL phrase on every item about the same thing — the same
    # contract `DigestStory.subject` already uses, for the same reason.
    subject: str
    source_name: str
    sender: str | None = None
    received_at: datetime | None = None
    # The email this came out of, so the desk can link back to it.
    email_id: str = ""


@dataclass(frozen=True)
class MergedStory:
    """One subject, and every newsletter that covered it."""

    subject: str
    items: list[NewsletterItem] = field(default_factory=list)
    # What task 18 turned up about this subject, where it turned up
    # anything. None is the common case and stays cheap: most subjects are
    # below the research cap, and rule 3 discards an ungrounded answer
    # before it reaches here.
    research: Research | None = None

    @property
    def outlets(self) -> list[str]:
        """Distinct newsletters carrying this, in first-seen order.

        Distinct, because one newsletter mentioning a story twice is not two
        outlets — the whole editorial claim of the merge is "several
        independent people thought this mattered", and counting one of them
        twice makes that claim false.
        """
        seen: list[str] = []
        for item in self.items:
            if item.source_name not in seen:
                seen.append(item.source_name)
        return seen

    @property
    def headline(self) -> str:
        """The longest headline, as the most informative one available.

        Not the first: ordering here follows arrival, and the newsletter
        that happened to land earliest is not the one that explained it
        best. Length is a crude proxy for specificity, but "Fed holds rates,
        signals two cuts in 2027" beating "Fed holds" is the common case.
        """
        return max((i.headline for i in self.items), key=len, default="")

    @property
    def latest(self) -> datetime | None:
        """The most recent arrival, comparing naive and aware safely.

        Not defensive padding. `gmail._parse_date` returns a NAIVE datetime
        for a `-0000` Date header, and `max()` over a mix of naive and aware
        raises TypeError rather than returning anything. This project has
        paid for that twice already: `script_chain._owed_replies` exists to
        avoid it and says "That is not theoretical", and
        `clustering._as_utc` was written after the same mismatch silently
        disabled clustering.

        Here it would have been quieter still — `_extract_newsletter_stories`
        catches broadly for rule 7, so one `-0000` header would have made the
        entire newsletter segment vanish behind a single warning line.
        """
        stamps = [_aware(i.received_at) for i in self.items if i.received_at]
        return max(stamps) if stamps else None


# A body that contains this file's own block delimiters can forge a second
# newsletter inside its own block — "---\n\n[1] FROM: The Economist" renders
# as though The Economist said it. The injection fence does not stop that:
# fencing prevents the model OBEYING the text, not the text impersonating a
# structure. `_strip_fence_markers` makes the same argument about the fence
# tokens themselves.
#
# It matters more here than it looks, because outlet count is the ranking
# signal AND the segment's editorial claim — "three of your newsletters led
# with this" is the sentence the prompt asks for. One email forging three
# blocks would headline the segment with whatever it liked.
_BLOCK_MARKER = re.compile(r"^\s*(?:-{3,}|\[\d+\]\s*FROM:)", re.M)


def _neutralise_markers(text: str) -> str:
    """Defang anything in a body that looks like this file's own framing."""
    return _BLOCK_MARKER.sub("·", text)


def _format_newsletters(newsletters: list[Item]) -> str:
    """The newsletters, numbered, for the extraction prompt."""
    blocks = []
    for index, item in enumerate(newsletters):
        blocks.append(
            f"[{index}] FROM: {_neutralise_markers(item.source_name)}\n"
            f"SUBJECT: {_neutralise_markers(item.title)}\n"
            f"{_neutralise_markers((item.body or '').strip()[:BODY_CHARS])}"
        )
    return "\n\n---\n\n".join(blocks)


def _parse(raw: str, newsletters: list[Item]) -> list[NewsletterItem]:
    """Turn the model's JSON into items, dropping anything malformed.

    Per-item rather than all-or-nothing. One unusable entry in a list of
    thirty is not a reason to lose the other twenty-nine, and rule 7 is the
    same argument one level down.
    """
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("newsletter extraction returned unparseable JSON")
        return []
    if isinstance(parsed, dict):
        # A provider that wraps the array under its own key. "items" is the
        # one we ask for, but rule 6 says to expect a swap, and the first cut
        # turned {"stories": [...]} into [] without a word — indistinguishable
        # from a genuinely quiet morning.
        for key in ("items", "stories", "results", "data"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
        else:
            log.warning(
                "newsletter extraction returned an object with keys %s and no "
                "recognisable list of items",
                sorted(parsed)[:8],
            )
            return []
    if not isinstance(parsed, list):
        log.warning("newsletter extraction returned %s, not a list", type(parsed))
        return []

    per_source: dict[int, int] = {}
    out: list[NewsletterItem] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        headline = str(entry.get("headline") or "").strip()
        gist = str(entry.get("gist") or "").strip()
        subject = str(entry.get("subject") or "").strip()
        # All three or none. A headline with no subject cannot merge, and an
        # item that cannot merge is exactly what this module exists to
        # prevent — it would reappear as its own story under every
        # newsletter that carried it.
        if not (headline and gist and subject):
            continue
        try:
            index = int(entry.get("source"))
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(newsletters):
            continue
        # Enforced here rather than trusted to the prompt. The cap is a
        # budget promise to everything downstream, and a prompt is a request.
        if per_source.get(index, 0) >= MAX_ITEMS_PER_NEWSLETTER:
            continue
        per_source[index] = per_source.get(index, 0) + 1

        source = newsletters[index]
        out.append(
            NewsletterItem(
                headline=headline, gist=gist, subject=subject,
                source_name=source.source_name, sender=source.author,
                received_at=source.timestamp, email_id=source.id,
            )
        )
    return out


async def extract_items(
    llm: LLMProvider, newsletters: list[Item]
) -> list[NewsletterItem]:
    """Pull the discrete news items out of every newsletter, in one call.

    One call rather than one per newsletter, for two reasons. It is twenty
    times cheaper against a free tier with a 06:00 deadline. And it is the
    only way the model can honour "use the identical subject phrase for the
    same thing" — a per-newsletter call cannot know what the others called
    it, and the merge downstream is an exact string match.

    Never raises. Rule 7: a briefing without the newsletter segment beats no
    briefing.
    """
    if not newsletters:
        return []
    # Newest first, then capped. Newsletters are perishable in a way the
    # rest of the inbox is not — an issue from Tuesday is describing a
    # Tuesday the listener has already had — so when something has to go,
    # the oldest issue is the cheapest thing to lose.
    #
    # Said out loud rather than dropped quietly. A silent cap reads as "we
    # covered everything" in a log, and this module was written because a
    # stage that produced nothing looked exactly like a stage with nothing
    # to do.
    if len(newsletters) > MAX_NEWSLETTERS:
        newsletters = sorted(
            newsletters,
            key=lambda i: _aware(i.timestamp) if i.timestamp else _EPOCH,
            reverse=True,
        )
        log.warning(
            "newsletters: %d issue(s) is over the %d the extraction prompt "
            "budgets for; taking the %d most recent and dropping %d",
            len(newsletters), MAX_NEWSLETTERS, MAX_NEWSLETTERS,
            len(newsletters) - MAX_NEWSLETTERS,
        )
        newsletters = newsletters[:MAX_NEWSLETTERS]
    # Fenced, and the instructions are OUTSIDE the fence — the mistake
    # `script_chain` documents and that this project has made once already.
    # A rule about the data, fenced together with the data, tells the model
    # to obey it and to distrust it in the same breath.
    prompt = NEWSLETTER_EXTRACT_PROMPT.format(
        n=len(newsletters),
        newsletters=_fence_untrusted(_format_newsletters(newsletters)),
    )
    try:
        raw = await llm.complete(
            "You are a news editor preparing a morning briefing.",
            prompt, temperature=0.2, json_mode=True,
            response_schema=_EXTRACT_SCHEMA,
        )
    except ContentBlocked as blocked:
        log.warning(
            "newsletter extraction refused (%s, %s); the briefing ships "
            "without the newsletter segment",
            blocked.reason, blocked.stage,
        )
        return []
    except Exception:
        log.warning("newsletter extraction failed", exc_info=True)
        return []

    items = _parse(raw, newsletters)
    log.info(
        "newsletters: %d issue(s) yielded %d item(s) across %d subject(s)",
        len(newsletters), len(items), len({i.subject.lower() for i in items}),
    )
    return items


def merge_by_subject(items: list[NewsletterItem]) -> list[MergedStory]:
    """Group items that are about the same thing.

    Matched on the lowercased, stripped subject. Exact-match rather than
    fuzzy or embedding-based: the prompt asks for the identical phrase, one
    call sees every newsletter at once so it can comply, and a threshold
    nobody can explain is how the 0.78 similarity cutoff became a number in
    a config file that no one has ever tuned against real feeds.
    """
    order: list[str] = []
    grouped: dict[str, list[NewsletterItem]] = {}
    for item in items:
        key = item.subject.strip().lower()
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append(item)
    return [
        MergedStory(subject=grouped[key][0].subject, items=grouped[key])
        for key in order
    ]


# How many merged subjects get a grounded-search call.
#
# The same number as `MAX_SPOKEN_SUBJECTS`, and deliberately not larger.
# It was 8 against a spoken cap of 6, which review showed could never bind:
# `take_spoken` truncates before research runs, so the extra two were a dead
# constant and a comment describing "spares" that did not exist. Researching
# a subject nobody hears is the purest form of spending a free tier on
# nothing.
#
# The owner's ceiling was "up to a cap" of about eight; six is under it.
RESEARCH_MAX_SUBJECTS = MAX_SPOKEN_SUBJECTS

# Half the digest's, because both run in the same generation and the two
# budgets add up on the critical path rather than overlapping.
RESEARCH_BUDGET_SECONDS = 45.0


async def research(
    stories: list[MergedStory], provider, *, max_subjects: int = RESEARCH_MAX_SUBJECTS
) -> dict[str, Research]:
    """What else is true about the subjects several newsletters chose.

    Shares `digest.research_ranked` with the story path rather than
    reimplementing the budget and the rule-3 discard. The only thing that
    differs is how outlets are counted.

    The budget is passed EXPLICITLY and is smaller than the digest's. Both
    run in the same generation, sequentially, so inheriting the default 90s
    would put a second uncapped ninety-second window on a job with an 06:00
    deadline — 180s of research nobody chose.
    """
    from app.pipeline.digest import research_ranked

    return await research_ranked(
        {s.subject: len(s.outlets) for s in stories},
        provider, max_subjects=max_subjects,
        budget_seconds=RESEARCH_BUDGET_SECONDS,
    )


def take_spoken(
    stories: list[MergedStory], *, limit: int = MAX_SPOKEN_SUBJECTS
) -> tuple[list[MergedStory], int]:
    """The subjects worth a conversation, and how many were left out.

    Returns `(spoken, remainder)`. Callers must say the remainder out loud —
    "and about twenty other items across your newsletters" is honest and
    costs one clause; silently dropping fifty-four is how a briefing starts
    quietly omitting things.
    """
    return stories[:limit], max(0, len(stories) - limit)


def rank(stories: list[MergedStory]) -> list[MergedStory]:
    """Most-corroborated first, then most recent.

    Outlet count leads because it is the one signal here that is not the
    model's opinion: several independent editors choosing the same story is
    evidence about the day, not about the summariser. Recency breaks ties.
    """
    return sorted(
        stories,
        key=lambda s: (
            -len(s.outlets),
            -(s.latest.timestamp() if s.latest else 0.0),
        ),
    )


_EXTRACT_SCHEMA: dict = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "source": {"type": "INTEGER"},
            "headline": {"type": "STRING"},
            "gist": {"type": "STRING"},
            "subject": {"type": "STRING"},
        },
        "required": ["source", "headline", "gist", "subject"],
    },
}
