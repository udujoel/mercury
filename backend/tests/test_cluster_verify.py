"""
A second opinion on what clustering merged (ADR-016).

ADR-003 settled the asymmetry these tests are written around: merging
unrelated stories is far worse than leaving a duplicate. So every test here
checks that the module errs toward SPLITTING when it is sure, and toward
leaving clustering alone when it is not.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.core.schemas import Item, SourceType, Story
from app.pipeline.cluster_verify import BODY_CHARS, verify_stories
from app.providers.fake_judgment import FakeClaimJudge

NOW = datetime(2026, 9, 22, 7, 0, tzinfo=UTC)


def _slug(title: str) -> str:
    """Deterministic. `hash()` on a str is salted per process, so using it
    here would give the fixture different ids on every run."""
    return hashlib.sha256(title.encode()).hexdigest()[:12]
DIFFERENT = "__DIFFERENT_EVENT__"


def item(title, *, source="Reuters", body="body", url=None):
    # The url must vary per item: `Item.model_post_init` seeds the id from
    # (source_name, url or title, timestamp), so a fixed url across a
    # fixture gives every item the SAME id and the non-canonical filter
    # silently empties. Derived from the title rather than passed in, so a
    # test cannot forget.
    return Item(
        source_type=SourceType.RSS, source_name=source, title=title,
        body=body, url=url or f"https://example.test/{_slug(title)}",
        timestamp=NOW,
    )


def story(canonical, *members):
    all_members = [canonical, *members]
    return Story(
        cluster_id="c1", canonical=canonical, members=all_members
    )


# ── fallback ──────────────────────────────────────────────────────────


async def test_no_provider_leaves_clustering_alone():
    stories = [story(item("A"), item("B"))]
    assert await verify_stories(stories, None) is stories


async def test_a_provider_that_raises_leaves_clustering_alone():
    class Exploding:
        async def judge_claims(self, state, claims, *, timeout=20.0):
            raise RuntimeError("TypeSafe is down")

    stories = [story(item("A"), item("B"))]
    assert await verify_stories(stories, Exploding()) == stories


async def test_singletons_cost_nothing():
    """A cluster of one has no merge to second-guess."""
    judge = FakeClaimJudge()
    stories = [story(item("A")), story(item("B"))]
    assert await verify_stories(stories, judge) == stories
    assert judge.claim_calls == []


async def test_an_empty_list_makes_no_call():
    judge = FakeClaimJudge()
    assert await verify_stories([], judge) == []
    assert judge.claim_calls == []


# ── splitting ─────────────────────────────────────────────────────────


async def test_a_rejected_member_is_pulled_out_as_its_own_story():
    """The SpaceX / Rocket Lab case: one cluster, two different events."""
    canonical = item("SpaceX launches 23 Starlink satellites")
    intruder = item(f"Rocket Lab Electron reaches orbit {DIFFERENT}",
                    source="Ars Technica")
    result = await verify_stories([story(canonical, intruder)], FakeClaimJudge())

    assert len(result) == 2
    narrowed = next(s for s in result if s.canonical.id == canonical.id)
    split = next(s for s in result if s.canonical.id == intruder.id)
    assert narrowed.members == [canonical]
    assert split.members == [intruder]


async def test_an_accepted_member_stays():
    canonical = item("TechCorp acquires DataFlow")
    commentary = item("What the DataFlow buy means", source="Stratechery")
    result = await verify_stories(
        [story(canonical, commentary)], FakeClaimJudge()
    )
    assert len(result) == 1
    assert len(result[0].members) == 2


async def test_no_item_is_lost_when_a_cluster_splits():
    """Ejection must move an item, never drop it — it is still news."""
    members = [item("A"), item(f"B {DIFFERENT}"), item("C"),
               item(f"D {DIFFERENT}")]
    result = await verify_stories([story(*members)], FakeClaimJudge())
    seen = {m.id for s in result for m in s.members}
    assert seen == {m.id for m in members}


async def test_a_split_story_gets_its_own_cluster_id():
    canonical = item("A")
    intruder = item(f"B {DIFFERENT}")
    result = await verify_stories([story(canonical, intruder)], FakeClaimJudge())
    ids = {s.cluster_id for s in result}
    assert len(ids) == 2
    assert any(i.startswith("c1-split-") for i in ids)


async def test_the_canonical_is_never_a_candidate_for_ejection():
    """Ejecting the canonical would leave a cluster with no representative."""
    canonical = item(f"A {DIFFERENT}")
    member = item("B")
    judge = FakeClaimJudge()
    result = await verify_stories([story(canonical, member)], judge)
    _, claims = judge.claim_calls[0]
    assert len(claims) == 1                    # only the non-canonical member
    assert any(s.canonical.id == canonical.id for s in result)


# ── unjudged is not evidence of a bad merge ───────────────────────────


async def test_an_unjudged_member_stays_in_its_cluster():
    """A network blip must not shatter a correct cluster.

    That is the same failure this module exists to prevent, arriving by the
    other door: treating "not judged" as "different event" would let a
    timeout undo clustering entirely.
    """
    canonical = item("A")
    member = item("B")
    judge = FakeClaimJudge(unjudged_keys=frozenset({"cluster:0:0"}))
    result = await verify_stories([story(canonical, member)], judge)
    assert len(result) == 1
    assert len(result[0].members) == 2


# ── request shape ─────────────────────────────────────────────────────


async def test_every_cluster_is_judged_in_one_request():
    a = story(item("A1"), item("A2"))
    b = story(item("B1"), item("B2"), item("B3"))
    judge = FakeClaimJudge()
    await verify_stories([a, b, story(item("solo"))], judge)
    assert len(judge.claim_calls) == 1
    state, claims = judge.claim_calls[0]
    assert len(claims) == 1 + 2                # non-canonical members only
    assert len(state["clusters"]) == 2         # the singleton is not sent


async def test_the_payload_is_an_allowlist_and_truncated():
    canonical = item("A", body="x" * 5000)
    canonical.raw = {"oauth_refresh_token": "must-not-leave"}
    judge = FakeClaimJudge()
    await verify_stories([story(canonical, item("B"))], judge)
    state, _ = judge.claim_calls[0]
    summary = state["clusters"][0]["canonical"]
    assert set(summary) == {"source", "title", "excerpt"}
    assert len(summary["excerpt"]) == BODY_CHARS
    assert "must-not-leave" not in str(state)


async def test_the_log_counts_clusters_narrowed_not_list_growth(caplog):
    """Regression: the count was derived from the change in list length.

    `len(set(out ids)) - len(stories) + 1` reports 2 when ONE cluster loses
    one member — a number that reads as a fact and is not. #56 is the
    standing lesson here: an alarm that names the wrong cause costs more
    than no alarm.
    """
    import logging

    one_cluster = story(item("A"), item(f"B {DIFFERENT}"), item(f"C {DIFFERENT}"))
    with caplog.at_level(logging.WARNING, logger="app.pipeline.cluster_verify"):
        await verify_stories([one_cluster], FakeClaimJudge())
    line = next(r.getMessage() for r in caplog.records if "undid" in r.getMessage())
    assert "undid 2 merge(s) in 1 clustered story(ies)" in line


# ── Item.id is a content hash, not a key (review of PR #62) ───────────


def colliding(title="Your daily brief", body="body"):
    """Two of these share an `Item.id`.

    `model_post_init` seeds the id from (source_name, url or title,
    timestamp), and `ingestion/gmail.py` builds EVERY email Item with
    url=None — so two messages from one sender with one subject at one
    timestamp collide however different their bodies.
    `generate._build_world_stories` already keys its `absorbed` set on
    object identity for this exact reason.
    """
    return Item(
        source_type=SourceType.EMAIL, source_name="Morning Brew",
        title=title, body=body, url=None, timestamp=NOW,
    )


async def test_a_cluster_that_was_never_judged_is_not_touched():
    """Regression: ejection was global and keyed on Item.id.

    Reproduced before the fix: one claim was issued, about a member of c0,
    and c9 — never sent for judgment — came back with members == [] and a
    canonical absent from its own members list. A verifier whose purpose is
    to avoid the ADR-003 harm was causing it in a cluster nobody asked it
    about.
    """
    twin_a, twin_b = colliding(body="edition one"), colliding(body="edition two")
    assert twin_a.id == twin_b.id, "fixture must actually collide"

    canonical = item("TechCorp acquires DataFlow")
    judged = story(canonical, twin_a)
    untouched = Story(cluster_id="c9", canonical=twin_b, members=[twin_b])

    result = await verify_stories(
        [judged, untouched], _RejectEverything()
    )
    survivor = next(s for s in result if s.cluster_id == "c9")
    assert survivor.members == [twin_b]
    assert any(m is survivor.canonical for m in survivor.members)


async def test_an_approved_member_is_not_ejected_by_its_id_twin():
    twin_a, twin_b = colliding(body="edition one"), colliding(body="edition two")
    canonical = item("TechCorp acquires DataFlow")

    class _RejectFirstOnly:
        async def judge_claims(self, state, claims, *, timeout=20.0):
            return {c.key: (0.02 if i == 0 else 0.98)
                    for i, c in enumerate(claims)}

    result = await verify_stories(
        [story(canonical, twin_a, twin_b)], _RejectFirstOnly()
    )
    narrowed = next(s for s in result if s.cluster_id == "c1")
    assert any(m is twin_b for m in narrowed.members), (
        "the approved twin was ejected because its id-twin was rejected"
    )


async def test_two_ejected_id_twins_get_distinct_cluster_ids():
    twin_a, twin_b = colliding(body="edition one"), colliding(body="edition two")
    canonical = item("TechCorp acquires DataFlow")
    result = await verify_stories(
        [story(canonical, twin_a, twin_b)], _RejectEverything()
    )
    ids = [s.cluster_id for s in result]
    assert len(ids) == len(set(ids)), f"cluster_id collision: {ids}"


class _RejectEverything:
    async def judge_claims(self, state, claims, *, timeout=20.0):
        return {c.key: 0.02 for c in claims}
