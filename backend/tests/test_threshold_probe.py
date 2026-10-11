"""
The other half of ADR-016: what the 0.78 threshold MISSED.

`verify_stories` re-reads the merges the threshold made. This probe reads
the merges it did not make — cross-cluster pairs that fell just short — and
logs the ones a semantic reading calls the same event. It is observe-only
by construction: it returns nothing, and every test here that touches
behaviour is checking that nothing moved.

The band exists so the SpaceX / Rocket Lab pair this repo records at cosine
0.6908 is inside it. That pair is the tightest non-merge measured, and it is
the whole justification for 0.78; a probe that could not see it would be
tuning against data that excluded the deciding case.
"""

from __future__ import annotations

import hashlib
import logging
import math
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.schemas import Item, SourceType, Story
from app.pipeline.cluster_verify import (
    BODY_CHARS,
    SAME_EVENT_RULE,
    probe_threshold,
    verify_stories,
)
from app.providers.fake_judgment import FakeClaimJudge

NOW = datetime(2026, 9, 22, 7, 0, tzinfo=UTC)
SAME = "__SAME_EVENT__"
DIFFERENT = "__DIFFERENT_EVENT__"
LOGGER = "app.pipeline.cluster_verify"


def _slug(title: str) -> str:
    """Deterministic: `hash()` on a str is salted per process."""
    return hashlib.sha256(title.encode()).hexdigest()[:12]


def _vec(cosine: float, sign: float = 1.0) -> list[float]:
    """A unit vector whose cosine with BASE is exactly `cosine`.

    `sign` flips it to the other side of the BASE axis, which is how two
    items equally similar to a canonical can be dissimilar to each other.
    """
    return [cosine, sign * math.sqrt(max(0.0, 1.0 - cosine * cosine)), 0.0]


BASE = [1.0, 0.0, 0.0]


def item(title, *, source="Reuters", body="body", embedding=None):
    # The url varies per item: `Item.model_post_init` seeds the id from
    # (source_name, url-or-title, timestamp), and a fixed url gives every
    # item in a fixture the same id. See the 2026-09-22 log entry — that
    # mistake silently emptied three tests on #62.
    got = Item(
        source_type=SourceType.RSS,
        source_name=source,
        title=title,
        body=body,
        url=f"https://example.test/{_slug(title)}",
        timestamp=NOW,
    )
    got.embedding = BASE if embedding is None else embedding
    return got


def story(*members, cluster_id="c0"):
    return Story(cluster_id=cluster_id, canonical=members[0], members=list(members))


def at(cosine: float, title: str, **kw) -> Story:
    """A one-item story sitting at `cosine` from a BASE story."""
    return story(
        item(title, embedding=_vec(cosine), **kw), cluster_id=f"c-{_slug(title)}"
    )


def messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


def disagreements(caplog) -> list[str]:
    return [m for m in messages(caplog) if "threshold probe: MISSED" in m]


def logged_cosines(caplog) -> list[float]:
    """The cosines as the log reports them.

    The similarity is deliberately absent from the judgment payload — it is
    the quantity under evaluation, and telling the model the pair scores
    0.77 anchors the answer toward "same event" exactly where a
    disagreement counts most. So the log is the only place it appears, and
    the only honest place to assert on it.
    """
    return [
        float(m.split("at cosine ")[1].split(" ")[0]) for m in disagreements(caplog)
    ]


# ── it never acts ─────────────────────────────────────────────────────


async def test_the_probe_returns_nothing():
    """Observe-only is structural, not a promise in a docstring.

    There is no value a caller could thread into clustering by mistake.
    """
    pair = [story(item("A")), at(0.69, "B")]
    assert await probe_threshold(pair, FakeClaimJudge()) is None


async def test_the_stories_are_not_touched():
    a, b = story(item("A")), at(0.69, f"B {SAME}")
    before = [(s.cluster_id, list(s.members), s.canonical) for s in (a, b)]
    await probe_threshold([a, b], FakeClaimJudge())
    after = [(s.cluster_id, list(s.members), s.canonical) for s in (a, b)]
    assert before == after


async def test_a_provider_that_raises_is_survivable(caplog):
    class Exploding:
        async def judge_claims(self, state, claims, *, timeout=20.0):
            raise RuntimeError("TypeSafe is down")

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert (
            await probe_threshold([story(item("A")), at(0.69, "B")], Exploding())
            is None
        )
    assert any("threshold probe failed" in m for m in messages(caplog))


async def test_a_provider_that_answers_with_the_wrong_shape_is_survivable():
    """Wrap the work, not the await — the mistake made twice in one week.

    A provider that returns a list rather than a dict takes an
    AttributeError out of the response handling, which sits outside a `try`
    that guards only the network call.
    """

    class Malformed:
        async def judge_claims(self, state, claims, *, timeout=20.0):
            return ["not", "a", "dict"]

    assert await probe_threshold([story(item("A")), at(0.69, "B")], Malformed()) is None


async def test_no_provider_costs_nothing():
    assert await probe_threshold([story(item("A")), at(0.69, "B")], None) is None


# ── which pairs get examined ──────────────────────────────────────────


async def test_a_pair_inside_the_band_is_judged():
    judge = FakeClaimJudge()
    await probe_threshold([story(item("A")), at(0.69, "B")], judge)
    assert len(judge.claim_calls) == 1
    state, claims = judge.claim_calls[0]
    assert len(claims) == 1
    assert len(state["pairs"]) == 1


async def test_a_pair_below_the_band_is_not_judged():
    """0.40 is two stories with nothing to do with each other.

    Judging every cross-cluster pair is quadratic in items and would send
    a real morning's whole inbox to the vendor twice over.
    """
    judge = FakeClaimJudge()
    await probe_threshold([story(item("A")), at(0.40, "B")], judge)
    assert judge.claim_calls == []


async def test_a_pair_at_or_above_the_threshold_is_not_judged():
    """The band is half-open. Average linkage cannot reach 0.78 between two
    surviving clusters — the algorithm would have merged them — so a pair
    that does is a sign the input did not come from `cluster_items`, and
    `verify_stories` owns that direction anyway."""
    judge = FakeClaimJudge()
    await probe_threshold([story(item("A")), at(0.78, "B")], judge)
    assert judge.claim_calls == []


async def test_the_spacex_rocket_lab_pair_is_inside_the_band():
    """0.6908 — the tightest non-merge this repo has measured, and the
    reason the threshold is 0.78. The band is chosen to contain it."""
    judge = FakeClaimJudge()
    await probe_threshold(
        [
            story(item("SpaceX launches 23 Starlink satellites")),
            at(0.6908, "Rocket Lab Electron reaches orbit"),
        ],
        judge,
    )
    assert len(judge.claim_calls) == 1


async def test_items_without_an_embedding_are_skipped():
    """An unembeddable item passes through `build_stories` as a singleton
    with `embedding = None`. It has no position to be near-threshold at."""
    blank = item("no vector")
    blank.embedding = None
    judge = FakeClaimJudge()
    await probe_threshold([story(item("A")), story(blank)], judge)
    assert judge.claim_calls == []


async def test_a_story_is_never_paired_with_itself():
    judge = FakeClaimJudge()
    await probe_threshold([story(item("A"), item("A2"))], judge)
    assert judge.claim_calls == []


# ── average linkage, because that is what the threshold cuts on ───────


async def test_cluster_similarity_is_the_average_over_members(caplog):
    """`cluster_items` uses average linkage, so the quantity compared
    against 0.78 is the MEAN pairwise similarity between two clusters —
    not the closest pair and not canonical-to-canonical.

    Here neither member pair is inside the band on its own (0.60 is below
    it, 0.78 is above it) and neither is the canonical pair, but the
    average is 0.69 and belongs in the log.
    """
    other = story(
        item(f"B1 {SAME}", embedding=_vec(0.60)),
        item("B2", embedding=_vec(0.78)),
        cluster_id="c-b",
    )
    judge = FakeClaimJudge()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await probe_threshold([story(item("A")), other], judge)
    assert len(judge.claim_calls) == 1
    assert logged_cosines(caplog) == [0.69]


# ── what gets logged ──────────────────────────────────────────────────


async def test_a_disagreement_names_both_titles_both_sources_and_the_cosine(
    caplog,
):
    """The log line IS the deliverable — Joel reads a week of these and
    decides whether 0.78 moves. Anything missing from it is a morning he
    cannot reconstruct."""
    left = story(item("TechCorp acquires DataFlow", source="Reuters"))
    right = at(0.69, f"What the DataFlow buy means {SAME}", source="Stratechery")
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await probe_threshold([left, right], FakeClaimJudge())

    line = disagreements(caplog)[0]
    assert "TechCorp acquires DataFlow" in line
    assert "What the DataFlow buy means" in line
    assert "Reuters" in line
    assert "Stratechery" in line
    assert "0.69" in line


async def test_an_agreement_is_not_logged_as_a_disagreement(caplog):
    """A pair the embeddings separated AND the judgment calls different is
    the threshold working. It is not evidence and it is not noise worth
    carrying for a week."""
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await probe_threshold([story(item("A")), at(0.69, "B")], FakeClaimJudge())
    assert disagreements(caplog) == []


async def test_an_unjudged_pair_is_not_a_disagreement(caplog):
    """'Not judged' is not "the threshold missed a merge". Counting a
    timeout as evidence would make a bad network look like a bad threshold,
    which is the reading that would move 0.78 for the wrong reason."""
    judge = FakeClaimJudge(unjudged_keys=frozenset({"probe:0"}))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await probe_threshold([story(item("A")), at(0.69, f"B {SAME}")], judge)
    assert disagreements(caplog) == []
    assert any("1 unjudged" in m for m in messages(caplog))


async def test_a_quiet_morning_still_says_what_it_examined(caplog):
    """Silence reads as "it did not run" — the failure mode this repo has
    hit four times. A pass that examined nothing says so."""
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await probe_threshold([story(item("A")), at(0.40, "B")], FakeClaimJudge())
    assert any(
        "threshold probe: 0 near-threshold pair(s)" in m for m in messages(caplog)
    )


# ── the cap ───────────────────────────────────────────────────────────


async def test_the_cap_truncates_and_says_so(caplog):
    """Silent truncation reads as "covered everything", which is the one
    thing a tuning log must never imply."""
    cap = get_settings().threshold_probe_max_pairs
    stories = [story(item("A"))] + [
        at(0.66 + i * 0.0001, f"near {i}") for i in range(cap + 5)
    ]
    judge = FakeClaimJudge()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await probe_threshold(stories, judge)

    _, claims = judge.claim_calls[0]
    assert len(claims) == cap
    assert any(f"capped at {cap}" in m for m in messages(caplog))


async def test_the_cap_keeps_the_pairs_closest_to_the_threshold(caplog):
    """A pair at 0.77 would merge if 0.78 moved by a hundredth; one at 0.65
    needs the threshold to move by a tenth. When the cap bites, the ones
    that would flip FIRST are the ones worth the budget."""
    cap = get_settings().threshold_probe_max_pairs
    stories = [story(item("A"))] + [
        at(0.66 + i * 0.001, f"near {i} {SAME}") for i in range(cap + 5)
    ]
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await probe_threshold(stories, FakeClaimJudge())

    seen = logged_cosines(caplog)
    assert len(seen) == cap
    assert seen == sorted(seen, reverse=True)
    # The five furthest from the threshold were dropped, not the nearest.
    assert min(seen) > 0.66 + 4 * 0.001


async def test_one_request_at_the_cap():
    """The cap is below CLAIMS_PER_REQUEST so the whole probe is a single
    request against the shared briefing budget."""
    from app.providers.typesafe_jev import CLAIMS_PER_REQUEST

    assert get_settings().threshold_probe_max_pairs <= CLAIMS_PER_REQUEST


# ── the payload ───────────────────────────────────────────────────────


async def test_the_payload_is_an_allowlist_and_truncated():
    """Same egress rule as `verify_stories`: a field added to `Item` later
    must not silently start leaving the machine."""
    left = item("A", body="x" * 5000)
    left.raw = {"oauth_refresh_token": "must-not-leave"}
    judge = FakeClaimJudge()
    await probe_threshold([story(left), at(0.69, "B")], judge)
    pair = judge.claim_calls[0][0]["pairs"][0]
    # No "similarity": the cosine is what the judgment is being used to
    # evaluate, so sending it would anchor the answer.
    assert set(pair) == {"a", "b"}
    assert set(pair["a"]) == {"source", "title", "excerpt"}
    assert len(pair["a"]["excerpt"]) == BODY_CHARS
    assert "must-not-leave" not in str(judge.claim_calls[0][0])


async def test_the_probe_asks_the_same_question_the_verifier_asks():
    """Both directions must mean the same thing by "same event", or the
    two halves of ADR-016 cannot be read against each other."""
    judge = FakeClaimJudge()
    await probe_threshold([story(item("A")), at(0.69, "B")], judge)
    _, claims = judge.claim_calls[0]
    assert SAME_EVENT_RULE in claims[0].instructions


# ── review of #65: pairs this run already ruled on ────────────────────


async def test_a_pair_the_verifier_just_split_is_not_re_examined():
    """`verify_stories` runs first and hands its output straight here.

    An ejected member becomes a singleton, and the average linkage between
    it and its narrowed parent lands inside the band often enough to
    matter — the ejection turned on a member-vs-canonical judgment, which
    says nothing about where the cluster AVERAGE sits once the member is
    gone.

    Re-asking is not merely wasteful. The framing differs (canonical
    against canonical here, member against canonical there), so the two
    readings can disagree — and then the tuning log carries a MISSED merge
    arguing to lower 0.78 and re-make a merge the verifier rejected
    minutes earlier. That is an entry pointing the wrong way, in the
    direction ADR-003 calls expensive.

    Reproduced on #65 at cosine 0.6696.
    """
    canonical = item("TechCorp acquires DataFlow")
    other = item(
        "What the DataFlow buy means", embedding=_vec(0.97), source="Stratechery"
    )
    intruder = item(
        f"TechCorp ships a new phone {DIFFERENT}",
        embedding=_vec(0.76, sign=-1.0),
        source="Ars Technica",
    )
    parent = story(canonical, other, intruder, cluster_id="c1")

    after = await verify_stories([parent], FakeClaimJudge())
    assert len(after) == 2, "the fixture must actually split, or this is vacuous"

    judge = FakeClaimJudge()
    await probe_threshold(after, judge)
    assert (
        judge.claim_calls == []
    ), "the probe re-judged the pair verify_stories split in this same run"


async def test_an_unrelated_story_is_still_examined_after_a_split():
    """The skip must be narrow. Suppressing every pair involving a split
    child would blind the probe to that item's relationship with the rest
    of the morning, which is ordinary evidence."""
    canonical = item("TechCorp acquires DataFlow")
    other = item(
        "What the DataFlow buy means", embedding=_vec(0.97), source="Stratechery"
    )
    intruder = item(
        f"TechCorp ships a new phone {DIFFERENT}",
        embedding=_vec(0.76, sign=-1.0),
        source="Ars Technica",
    )
    parent = story(canonical, other, intruder, cluster_id="c1")
    after = await verify_stories([parent], FakeClaimJudge())

    # A third story positioned to sit at 0.70 from the EJECTED item — well
    # inside the band — and at -0.05 from its former parent, well outside
    # it. So the only pair on offer is the one the skip must not swallow.
    bystander = story(
        item("Rival phone launch roundup", embedding=_vec(0.068, sign=-1.0)),
        cluster_id="c9",
    )

    judge = FakeClaimJudge()
    await probe_threshold([*after, bystander], judge)
    pairs = judge.claim_calls[0][0]["pairs"] if judge.claim_calls else []
    titles = {s["title"] for p in pairs for s in (p["a"], p["b"])}
    assert any("Rival phone" in t for t in titles), titles


async def test_two_members_ejected_from_one_cluster_are_still_compared():
    """The skip is parent-to-child, never sibling-to-sibling.

    `verify_stories` judges each member against the CANONICAL and never
    against the other members. So two items ejected from one cluster have
    been ruled different from the canonical and ruled nothing at all about
    each other — an open question, which is the only kind this probe wants.

    Skipping them would be the easy over-correction of the #65 fix, and it
    would silently narrow the band's coverage on exactly the mornings where
    a cluster went wrong.
    """
    canonical = item("TechCorp acquires DataFlow")
    left = item(
        f"TechCorp opens a Dublin office {DIFFERENT}",
        embedding=_vec(0.92),
        source="Reuters",
    )
    right = item(
        f"TechCorp's Dublin hiring plan {DIFFERENT}",
        embedding=_vec(0.92, sign=-1.0),
        source="Platformer",
    )

    after = await verify_stories(
        [story(canonical, left, right, cluster_id="c1")], FakeClaimJudge()
    )
    assert len(after) == 3, "both members must be ejected, or this is vacuous"
    assert sum(s.cluster_id.startswith("c1-split-") for s in after) == 2

    judge = FakeClaimJudge()
    await probe_threshold(after, judge)
    pairs = judge.claim_calls[0][0]["pairs"] if judge.claim_calls else []
    titles = {s["title"] for p in pairs for s in (p["a"], p["b"])}
    assert any("Dublin office" in t for t in titles), titles
    assert any("Dublin hiring" in t for t in titles), titles
