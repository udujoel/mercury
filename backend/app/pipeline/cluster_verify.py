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

import numpy as np

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

    **This includes EMAIL.** `generate._build_world_stories` puts
    newsletters and articles into ONE `build_stories` call on purpose —
    that is what lets an email newsletter and a news article about the same
    event deduplicate against each other — so a cluster member here can be
    an email, and BODY_CHARS of its body goes to TypeSafe.

    That is the same egress ADR-014 established for triage (which sends 500
    characters of every inbox message), not a new one. It is written down
    here because ADR-016's payload is easy to read as "news only", and the
    person who approved the triage egress should not have to rediscover
    that clustering shares it.
    """
    return {
        "source": item.source_name,
        "title": item.title,
        "excerpt": item.body[:BODY_CHARS],
    }


# The definition of "the same event", shared by both directions of ADR-016.
#
# One constant rather than two copies because the split direction and the
# threshold probe are meant to be read against each other: the verifier
# says what 0.78 wrongly merged and the probe says what it wrongly missed,
# and those two answers are only comparable if the question is identical.
# Two copies of a sentence this long drift on the first edit, and the
# symptom — a week of tuning data collected under a slightly different
# definition than the one that ships — is not something either module's
# tests would notice.
SAME_EVENT_RULE = (
    "report the SAME underlying news event. One being original reporting "
    "and the other being commentary about it still counts as the same "
    "event. Two different events at the same organisation, two different "
    "launches or releases, and two editions of the same recurring "
    "publication do NOT count as the same event."
)


def _claim(story_index: int, member_index: int) -> Claim:
    return Claim(
        key=f"cluster:{story_index}:{member_index}",
        instructions=(
            f"`clusters[{story_index}].members[{member_index}]` and "
            f"`clusters[{story_index}].canonical` {SAME_EVENT_RULE}"
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

    # `_apply` is INSIDE the try, not after it. A provider that returns the
    # wrong SHAPE rather than raising — a list, None, anything without
    # `.get` — used to take the AttributeError all the way out of a module
    # whose docstring promises it never raises. Rule 7 is about what the
    # briefing survives, and "the provider answered strangely" has to
    # degrade the same way "the provider timed out" does.
    try:
        answers = await provider.judge_claims(
            state, claims, timeout=settings.judgment_timeout_seconds
        )
        return _apply(stories, addressed, answers, settings)
    except Exception as exc:
        log.warning(
            "cluster verification failed (%s) — keeping clustering's own "
            "merges for all %d multi-source story(ies)", exc, len(candidates),
        )
        return stories


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


# ──────────────────────────────────────────────────────────────
# The other direction: what the threshold MISSED (ADR-016 follow-up)
# ──────────────────────────────────────────────────────────────


def _pair_claim(index: int) -> Claim:
    return Claim(
        key=f"probe:{index}",
        instructions=f"`pairs[{index}].a` and `pairs[{index}].b` "
                     f"{SAME_EVENT_RULE}",
    )


def _is_evidence(probability: float, settings) -> bool:
    """Is this answer worth putting in front of a human as tuning data?

    Deliberately the SAME cut the split direction uses, not a stricter one,
    and the reasoning is worth stating because the obvious argument runs
    the other way.

    That argument: a logged pair is a nudge toward LOWERING 0.78, and
    lowering it makes more merges, which is ADR-003's expensive direction —
    so the bar for evidence should be higher than the bar for ejecting.

    It does not hold, because **this log does not act.** A pair recorded
    here changes nothing until a human reads a week of mornings and edits
    a number. The cost of a marginal answer is one line Joel decides is
    marginal; the cost of hiding it is a threshold tuned on a filtered
    sample, with the filter chosen by the same guess the tuning is meant
    to replace. Under-reporting is the error that cannot be seen later.

    The 2026-09-21 probe found no marginal answers at all — 0.94-0.96
    against 0.01-0.04 across seven pairs — so on the measured evidence
    this cut and a stricter one select the same pairs anyway.
    """
    return probability >= settings.cluster_verify_threshold


def _already_ruled_on(left: Story, right: Story) -> bool:
    """Did `verify_stories` split one of these out of the other this run?

    The probe receives the story list AFTER the split direction has run, so
    an ejected member and its narrowed parent arrive here as two ordinary
    stories — and their average linkage often lands inside the band, since
    the ejection turned on a member-vs-canonical judgment that says nothing
    about where the cluster average sits once the member is gone.
    Reproduced on #65 at cosine 0.6696.

    Re-asking is not merely a wasted claim slot. The framing differs —
    canonical against canonical here, member against canonical there — so
    the two readings can disagree, and then the tuning log carries a
    MISSED merge arguing to LOWER 0.78 and re-make a merge this same run
    rejected. An entry pointing the wrong way is worse than no entry,
    because the direction it points is the expensive one (ADR-003).

    Siblings are deliberately NOT skipped. Two members ejected from one
    cluster were each judged against the canonical and never against each
    other, so whether they are the same event is a question nobody has
    answered — which is exactly what this probe is for.
    """
    return (
        right.cluster_id.startswith(f"{left.cluster_id}-split-")
        or left.cluster_id.startswith(f"{right.cluster_id}-split-")
    )


def _near_threshold_pairs(
    stories: list[Story], floor: float, ceiling: float
) -> list[tuple[Story, Story, float]]:
    """Cross-cluster story pairs whose similarity lands in [floor, ceiling).

    **Average linkage, because that is the quantity the threshold cuts
    on.** `cluster_items` runs AgglomerativeClustering with
    `linkage="average"`, so what gets compared against 0.78 is the MEAN
    pairwise cosine between two clusters — not the closest pair and not
    canonical-to-canonical. Reporting anything else would answer a
    question nobody asked: "should 0.78 move to 0.70" is only answerable
    in the units 0.78 is expressed in.

    It also follows that nothing here can come back at or above the
    ceiling. Average linkage stops merging when every remaining cluster
    pair is further apart than the threshold, so a surviving pair is
    below it by construction. The half-open band is belt and braces for
    input that did not come from `cluster_items`.

    Recomputed from `Item.embedding` rather than plumbed out of
    clustering. Clustering is the expensive, well-tested pass and this is
    a diagnostic; a diagnostic does not get to change the signature of the
    thing it observes.

    Returned nearest-the-threshold first, which is the order the cap
    truncates in. A pair at 0.77 flips if 0.78 moves by a hundredth; one
    at 0.65 needs it to move by a tenth.
    """
    vectors: list[list[float]] = []
    owner: list[int] = []
    for index, story in enumerate(stories):
        for member in story.members:
            if member.embedding:
                vectors.append(member.embedding)
                owner.append(index)

    rows: dict[int, list[int]] = {}
    for position, index in enumerate(owner):
        rows.setdefault(index, []).append(position)
    if len(rows) < 2:
        return []

    matrix = np.array(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # `dedup.build_stories` already drops zero-norm vectors, so this guard
    # is for input that reached here another way. A zero vector has no
    # direction and its cosine is undefined; dividing by 1.0 leaves it at
    # the origin, similar to nothing, which is the honest reading.
    matrix = matrix / np.where(norms == 0.0, 1.0, norms)
    similarities = matrix @ matrix.T

    present = sorted(rows)
    sizes = np.array([len(rows[index]) for index in present], dtype=np.float64)

    # Every cluster-pair average in one matmul, rather than a Python loop
    # reading one-element blocks out of `similarities`. The loop version
    # was measured at 473ms on 400 stories — 79,800 fancy-index reads, and
    # half a second of a 90-second briefing spent on a diagnostic. `assign`
    # is the one-hot item->cluster matrix, so `assignᵀ · S · assign` is the
    # block SUM and dividing by the outer product of the sizes makes it the
    # mean.
    assign = np.zeros((len(vectors), len(present)), dtype=np.float32)
    for column, index in enumerate(present):
        assign[rows[index], column] = 1.0
    # The averaging is float64 even though the similarities are float32
    # (clustering's own precision): summing float32 across a cluster and
    # then rounding leaves the result a float32, and the nearest float32 to
    # 0.69 widens to 0.6899999976158142 — a log line that reads as a
    # rounding bug to the person reading a week of them.
    block = np.asarray(assign.T @ similarities @ assign, dtype=np.float64)
    block /= np.outer(sizes, sizes)

    # Rounded BEFORE the band test, not after, so the number in the log is
    # the number that was filtered on. A pair reported at 0.7800 that was
    # admitted as 0.77996 would read as a contradiction of the half-open
    # band by whoever is tuning against these lines.
    block = np.round(block, 4)
    keep = np.triu(np.ones_like(block, dtype=bool), k=1)
    keep &= (block >= floor) & (block < ceiling)

    found: list[tuple[Story, Story, float]] = []
    for left, right in zip(*np.nonzero(keep), strict=True):
        one, two = stories[present[left]], stories[present[right]]
        if _already_ruled_on(one, two):
            continue
        found.append((one, two, float(block[left, right])))

    found.sort(key=lambda row: row[2], reverse=True)
    return found


async def probe_threshold(
    stories: list[Story], provider: JudgmentProvider | None
) -> None:
    """Log cross-cluster pairs the threshold missed. Merges NOTHING.

    The open half of ADR-016. `verify_stories` re-reads the merges 0.78
    MADE; this reads the merges it did not make, and says which of them a
    semantic reading calls the same event. CLAUDE.md's standing unknown is
    that 0.78 "is tuned for Gemini embeddings and has still never run
    against real feeds" — a week of these lines is the evidence for moving
    it, or for leaving it alone.

    **Observe-only is structural, not a promise.** It returns None. There
    is no value a caller could thread back into clustering by mistake, and
    the story objects are never mutated. Acting on this direction is where
    a mistake is expensive (ADR-003), and it stays out of scope until a
    human has read the data.

    Never raises, and holds nothing to degrade: a failed probe costs a log
    line nobody gets to read (rule 7).

    **It is last in the judgment queue on purpose.** The shared
    `judgment_briefing_budget_seconds` covers triage, cluster verification,
    personal relevance and the quality gates, all of which change what the
    listener hears. This one changes nothing, so it runs after them and
    takes what is left; if the budget is already spent every claim comes
    back None and the pass reports that it learned nothing. Diagnostics do
    not get to starve the product.
    """
    if provider is None or len(stories) < 2:
        return

    settings = get_settings()
    ceiling = settings.cluster_similarity_threshold
    floor = ceiling - settings.threshold_probe_band

    # Everything fallible is inside the try, including the state building
    # and the response handling — not just the await. A provider that
    # returns the wrong SHAPE rather than raising takes an AttributeError
    # out of `answers.get`, which a try around the network call alone does
    # not cover. That exact mistake shipped twice in one week (#62, #64).
    try:
        candidates = _near_threshold_pairs(stories, floor, ceiling)
        if not candidates:
            # Said out loud. A pass that logs nothing is indistinguishable
            # from a pass that never ran, which is this project's most
            # repeated failure — four features certified by a green suite
            # and called by nothing.
            log.info(
                "threshold probe: 0 near-threshold pair(s) in [%.2f, %.2f) "
                "across %d stories — nothing to ask",
                floor, ceiling, len(stories),
            )
            return

        cap = settings.threshold_probe_max_pairs
        examined = candidates[:cap]
        if len(candidates) > cap:
            log.warning(
                "threshold probe: %d near-threshold pair(s) found, capped at "
                "%d — the %d nearest %.2f were examined and the remaining %d "
                "were NOT. Today's line is a sample, not a census.",
                len(candidates), cap, cap, ceiling, len(candidates) - cap,
            )

        # The canonical stands for its cluster. Sending every member of
        # both sides would multiply the payload by the cluster sizes for a
        # question the representatives already answer, and the canonical is
        # what `authority_sorted` picked as the cluster's best account of
        # the event.
        # The cosine is deliberately NOT in the payload. It is the thing
        # being evaluated, and handing it to the judgment anchors the
        # answer: told that a pair scores 0.77, a model has every reason to
        # lean "same event", and the disagreements would inflate exactly at
        # the top of the band where they carry the most weight. The reading
        # has to be independent of the number it is being read against.
        state = {
            "pairs": [
                {
                    "a": _summarise(left.canonical),
                    "b": _summarise(right.canonical),
                }
                for left, right, _ in examined
            ]
        }
        claims = [_pair_claim(index) for index in range(len(examined))]
        answers = await provider.judge_claims(
            state, claims, timeout=settings.judgment_timeout_seconds
        )

        missed = 0
        unjudged = 0
        # Zipped against `claims`, so the key comes off the Claim that was
        # sent rather than being rebuilt from the index. Two copies of the
        # `probe:{i}` format coupled only by a repeated literal degrade the
        # wrong way: change one and every lookup returns None, every pair
        # counts as unjudged, and the summary reads exactly like a spent
        # budget or a provider outage. Nobody investigates that line, and
        # the probe reports nothing for as long as it takes to notice.
        # `strict=True` turns a length mismatch into an exception the
        # enclosing try converts to a visible failure instead.
        for claim, (left, right, similarity) in zip(
            claims, examined, strict=True
        ):
            probability = answers.get(claim.key)
            # `is not None`, never a truthiness test. A judged 0.0 is a
            # real "different event" and is falsy; `or` would fold it in
            # with "not judged", and the two are opposite evidence.
            if probability is None:
                unjudged += 1
                continue
            if not _is_evidence(probability, settings):
                continue
            missed += 1
            log.warning(
                "threshold probe: MISSED merge at cosine %.4f (under the "
                "%.2f threshold) — %r (%s) and %r (%s) report the same "
                "event, p=%.2f. Observed only; nothing was merged.",
                similarity, ceiling,
                left.canonical.title[:80], left.canonical.source_name,
                right.canonical.title[:80], right.canonical.source_name,
                probability,
            )

        log.info(
            "threshold probe: %d near-threshold pair(s) in [%.2f, %.2f) "
            "examined, %d judged the same event, %d unjudged — observe "
            "only, clustering unchanged",
            len(examined), floor, ceiling, missed, unjudged,
        )
    except Exception as exc:
        # Nothing to degrade: the probe holds no result the briefing wants.
        log.warning(
            "threshold probe failed (%s) — no threshold evidence from this "
            "run; clustering is untouched either way", exc,
        )


__all__ = ["verify_stories", "probe_threshold", "SAME_EVENT_RULE"]
