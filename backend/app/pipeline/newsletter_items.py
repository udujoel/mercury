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
from dataclasses import dataclass, field
from datetime import datetime

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
        stamps = [i.received_at for i in self.items if i.received_at]
        return max(stamps) if stamps else None


def _format_newsletters(newsletters: list[Item]) -> str:
    """The newsletters, numbered, for the extraction prompt."""
    blocks = []
    for index, item in enumerate(newsletters):
        blocks.append(
            f"[{index}] FROM: {item.source_name}\n"
            f"SUBJECT: {item.title}\n"
            f"{(item.body or '').strip()[:BODY_CHARS]}"
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
        parsed = parsed.get("items", [])
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
# Eight, against a live morning that produced 96 subjects from 49
# newsletters. The cap is the owner's ("every extracted item, up to a cap"),
# and the ranking is what makes the cap defensible: the tail it cuts is
# single-outlet items, and the head it keeps included one subject nine
# newsletters carried.
RESEARCH_MAX_SUBJECTS = 8


async def research(
    stories: list[MergedStory], provider, *, max_subjects: int = RESEARCH_MAX_SUBJECTS
) -> dict[str, Research]:
    """What else is true about the subjects several newsletters chose.

    Shares `digest.research_ranked` with the story path rather than
    reimplementing the budget and the rule-3 discard. The only thing that
    differs is how outlets are counted.
    """
    from app.pipeline.digest import research_ranked

    return await research_ranked(
        {s.subject: len(s.outlets) for s in stories},
        provider, max_subjects=max_subjects,
    )


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
