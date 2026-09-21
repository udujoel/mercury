"""
A second opinion on what clustering merged (ADR-016).

`cluster_items` decides that two items are the same story from the cosine
similarity of their embeddings, at a threshold of 0.78. ADR-003 settled the
asymmetry that governs this file: **merging unrelated stories is far worse
than leaving a duplicate.** A duplicate costs the listener thirty seconds;
a false merge tells them two things happened when one did, and attributes a
source to a story it never covered.

CLAUDE.md is explicit that the threshold is unproven — "tuned for Gemini
embeddings and has still never run against real feeds", and "a fixture
written to contain duplicates cannot tell you whether real newsletters
cluster". This module does not tune the threshold. It checks the merges the
threshold produced, and undoes the ones that do not survive a semantic
reading.

Only in the SPLIT direction, deliberately. Pulling a member out of a
cluster can only ever cost a duplicate — the error ADR-003 calls the
cheaper one. Adding merges the threshold did not make is the direction
where a mistake is expensive, and it stays out of scope until the split
direction has run against real mornings. See ADR-016 for the follow-up.
"""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.core.providers import Claim, JudgmentProvider
from app.core.schemas import Item, Story

log = logging.getLogger(__name__)

# How much of an item the judgment sees. Titles carry nearly all the signal
# for "is this the same event" — `Item.embed_text` uses title plus 500
# characters for the same reason — and a shorter payload keeps a cluster of
# five inside one request.
BODY_CHARS = 300


def _summarise(item: Item) -> dict:
    """What the judgment sees for one item.

    An allowlist, like `triage.email_state`, and for the same reason: a
    field added to `Item` later must not silently start leaving the machine.
    """
    return {
        "source": item.source_name,
        "title": item.title,
        "excerpt": item.body[:BODY_CHARS],
    }


def _claim(story_index: int, member_index: int) -> Claim:
    return Claim(
        key=f"cluster:{story_index}:{member_index}",
        instructions=(
            f"`clusters[{story_index}].members[{member_index}]` and "
            f"`clusters[{story_index}].canonical` report the SAME underlying "
            "news event. One being original reporting and the other being "
            "commentary about it still counts as the same event. Two "
            "different events at the same organisation, two different "
            "launches or releases, and two editions of the same recurring "
            "publication do NOT count as the same event."
        ),
    )


async def verify_stories(
    stories: list[Story], provider: JudgmentProvider | None
) -> list[Story]:
    """Split cluster members that do not survive a semantic reading.

    Returns a new list: unchanged stories plus, for any cluster that lost
    members, the narrowed cluster and one singleton per ejected member.

    Never raises. A failed verification leaves clustering's own decision
    standing, which is what shipped before this module existed (rule 7).
    """
    if provider is None or not stories:
        return stories

    # Singletons have nothing to verify, and a cluster is only worth a claim
    # if clustering actually merged something into it.
    candidates = [
        (index, story) for index, story in enumerate(stories)
        if len(story.members) > 1
    ]
    if not candidates:
        return stories

    settings = get_settings()
    state, claims, addressed = _build(candidates)

    try:
        answers = await provider.judge_claims(
            state, claims, timeout=settings.judgment_timeout_seconds
        )
    except Exception as exc:
        log.warning(
            "cluster verification failed (%s) — keeping clustering's own "
            "merges for all %d multi-source story(ies)", exc, len(candidates),
        )
        return stories

    return _apply(stories, addressed, answers, settings)


def _build(
    candidates: list[tuple[int, Story]],
) -> tuple[dict, list[Claim], dict[str, Item]]:
    """One state carrying every cluster, one claim per non-canonical member."""
    clusters = []
    claims: list[Claim] = []
    addressed: dict[str, Item] = {}

    for position, (_, story) in enumerate(candidates):
        others = [m for m in story.members if m.id != story.canonical.id]
        clusters.append({
            "canonical": _summarise(story.canonical),
            "members": [_summarise(m) for m in others],
        })
        for member_position, member in enumerate(others):
            claim = _claim(position, member_position)
            claims.append(claim)
            addressed[claim.key] = member

    return {"clusters": clusters}, claims, addressed


def _apply(
    stories: list[Story],
    addressed: dict[str, Item],
    answers: dict[str, float | None],
    settings,
) -> list[Story]:
    """Rebuild the story list with rejected members pulled out."""
    # An unjudged member STAYS in its cluster. "Not judged" is not evidence
    # of a bad merge, and treating it as one would let a network blip
    # shatter a correct cluster — the failure mode this module exists to
    # prevent, arriving by the other door.
    ejected: dict[str, Item] = {}
    unjudged = 0
    for key, member in addressed.items():
        probability = answers.get(key)
        if probability is None:
            unjudged += 1
            continue
        if probability < settings.cluster_verify_threshold:
            ejected[member.id] = member
            log.info(
                "cluster verify: ejecting %r (%s) — p(same event)=%.2f",
                member.title[:60], member.source_name, probability,
            )

    if not ejected:
        log.info(
            "cluster verify: %d claim(s), %d unjudged, no merges undone",
            len(addressed), unjudged,
        )
        return stories

    out: list[Story] = []
    narrowed = 0
    for story in stories:
        kept = [m for m in story.members if m.id not in ejected]
        removed = [m for m in story.members if m.id in ejected]
        if not removed:
            out.append(story)
            continue
        narrowed += 1
        # The canonical is never a candidate, so `kept` is never empty.
        out.append(Story(
            cluster_id=story.cluster_id,
            canonical=story.canonical,
            members=kept,
            score=story.score,
        ))
        out.extend(_ejected_story(story, m) for m in removed)

    # `narrowed` counted directly, not derived from the change in list
    # length. The first version computed it as
    # len(set(out ids)) - len(stories) + 1, which reports 2 when one
    # cluster loses one member — a number that looks like a fact and is
    # not. #56 is the standing lesson: an alarm that names the wrong cause
    # costs more than no alarm.
    log.warning(
        "cluster verify: undid %d merge(s) in %d clustered story(ies) that "
        "the %.2f similarity threshold had made — %d claim(s), %d unjudged",
        len(ejected), narrowed,
        settings.cluster_similarity_threshold, len(addressed), unjudged,
    )
    return out


def _ejected_story(parent: Story, member: Item) -> Story:
    """A member pulled out of `parent`, as its own story.

    The cluster id is derived from the parent's rather than reusing it, so a
    reader of the digest can see where the item came from and nothing
    downstream joins the two on a shared id.
    """
    member.cluster_id = f"{parent.cluster_id}-split-{member.id[:8]}"
    return Story(
        cluster_id=member.cluster_id, canonical=member, members=[member]
    )


__all__ = ["verify_stories"]
