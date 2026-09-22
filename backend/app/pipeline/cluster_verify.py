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
) -> tuple[dict, list[Claim], dict[str, tuple[int, Item]]]:
    """One state carrying every cluster, one claim per non-canonical member.

    `addressed` maps each claim key to (index into the ORIGINAL stories
    list, the member object itself). Both halves matter: the index scopes
    the ejection to the cluster that was actually judged, and the object —
    not `Item.id` — is what identifies the member. See `_apply`.
    """
    clusters = []
    claims: list[Claim] = []
    addressed: dict[str, tuple[int, Item]] = {}

    for position, (story_index, story) in enumerate(candidates):
        others = [m for m in story.members if m is not story.canonical]
        clusters.append({
            "canonical": _summarise(story.canonical),
            "members": [_summarise(m) for m in others],
        })
        for member_position, member in enumerate(others):
            claim = _claim(position, member_position)
            claims.append(claim)
            addressed[claim.key] = (story_index, member)

    return {"clusters": clusters}, claims, addressed


def _apply(
    stories: list[Story],
    addressed: dict[str, tuple[int, Item]],
    answers: dict[str, float | None],
    settings,
) -> list[Story]:
    """Rebuild the story list with rejected members pulled out.

    Scoped to the cluster that was judged, and matched on OBJECT IDENTITY
    rather than `Item.id`.

    `Item.id` is a content hash — `model_post_init` seeds it from
    (source_name, url-or-title, timestamp) — and it is not unique. Every
    Gmail item is built with `url=None`, so two messages from one sender
    with one subject at one timestamp collide however different their
    bodies. `generate._build_world_stories` already keys its `absorbed` set
    on object identity for exactly this reason.

    The first version of this function collected ejections into one set
    keyed on `Item.id` and filtered EVERY story by it. Reproduced: a
    cluster that was never judged came back with `members == []` and a
    canonical absent from its own members list, while an approved member of
    the judged cluster was pulled out alongside its id-twin. A verifier
    whose whole purpose is to avoid the ADR-003 harm was causing it, in a
    cluster it had never been asked about.
    """
    # story index -> the member objects ejected from THAT story.
    ejected: dict[int, list[Item]] = {}
    unjudged = 0
    for key, (story_index, member) in addressed.items():
        probability = answers.get(key)
        if probability is None:
            # "Not judged" is not evidence of a bad merge. Treating it as
            # one would let a network blip shatter a correct cluster.
            unjudged += 1
            continue
        if probability < settings.cluster_verify_threshold:
            ejected.setdefault(story_index, []).append(member)
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
    for index, story in enumerate(stories):
        removed = ejected.get(index)
        if not removed:
            out.append(story)
            continue
        removed_ids = {id(m) for m in removed}
        kept = [m for m in story.members if id(m) not in removed_ids]
        # The canonical is never a candidate (`_build` filters it out by
        # identity), so `kept` always still contains it.
        out.append(Story(
            cluster_id=story.cluster_id,
            canonical=story.canonical,
            members=kept,
            score=story.score,
        ))
        out.extend(
            _ejected_story(story, m, position)
            for position, m in enumerate(removed)
        )

    log.warning(
        "cluster verify: undid %d merge(s) in %d clustered story(ies) that "
        "the %.2f similarity threshold had made — %d claim(s), %d unjudged",
        sum(len(v) for v in ejected.values()), len(ejected),
        settings.cluster_similarity_threshold, len(addressed), unjudged,
    )
    return out


def _ejected_story(parent: Story, member: Item, position: int) -> Story:
    """A member pulled out of `parent`, as its own story.

    The id carries `position` as well as the member's hash, because two
    ejected members of one cluster can share an `Item.id` and would
    otherwise be handed the same cluster_id — which is precisely what this
    docstring used to promise did not happen.
    """
    member.cluster_id = f"{parent.cluster_id}-split-{position}-{member.id[:8]}"
    return Story(
        cluster_id=member.cluster_id, canonical=member, members=[member]
    )


__all__ = ["verify_stories"]
