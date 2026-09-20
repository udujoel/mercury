"""The hosts must not relay a security instruction. ROADMAP task 32.

The fence stops the model OBEYING an email. It does nothing about the
model faithfully RELAYING one, and relaying is the more dangerous half.

Live, 2026-09-13-1835:

    "It advises changing your password... This is a critical security
    step to protect your account."

Every safeguard worked exactly as designed. The model did not obey the
email — it reported it, which is what the prompt asks for. But audio
strips what a reader uses to judge a phishing mail (the sender address,
the headers, the link) and replaces it with two trusted voices calling
the instruction critical.

Three things make this worse than it looks:

  - the selection is attacker-controlled. `classify_priority` gives HIGH
    plus `requires_response` to anything addressed to the listener
    containing a "?".
  - task 27 widened it further, on purpose: account and security mail can
    now escalate past the newsletter floor. The path from "a stranger
    sends a well-worded email" to "the hosts read it as urgent" got
    SHORTER today, not longer.
  - `check_grounding` scores a phishing claim as MORE grounded, not less,
    because the email IS the source data.
"""

from __future__ import annotations

import pytest

from app.core.schemas import Segment, Turn
from app.pipeline.quality import check_relayed_instructions, run_all_gates
from app.providers.fake import FakeLLMProvider


def _turn(text: str, segment: Segment = Segment.INBOX) -> Turn:
    return Turn(speaker="A", segment=segment, text=text)


# The live line, and the shapes nearest to it.
THE_RELAYS = [
    "It advises changing your password. This is a critical security step "
    "to protect your account.",
    "There's a note from your bank — you should verify your account today.",
    "Make sure to click the link in that message before it expires.",
    "They want you to confirm your payment details, and it's urgent.",
    "You'll want to reset your password right away.",
]


def test_the_live_phishing_relay_is_caught():
    for text in THE_RELAYS:
        assert check_relayed_instructions([_turn(text)]), text


def test_reporting_that_an_email_asks_for_something_is_still_allowed():
    """The segment's whole job. A gate that forbade mentioning the message
    would replace a security problem with a usefulness one, and the
    listener would simply not learn the mail exists."""
    allowed = [
        "Something from paypal-security dot net is asking you to reset a "
        "password. It's sitting in your inbox.",
        "There's a message claiming your account needs verifying. It came "
        "from a domain you haven't seen before.",
        "Your bank sent a transaction notification overnight.",
        "Three of your newsletters led with the acquisition.",
    ]
    for text in allowed:
        assert not check_relayed_instructions([_turn(text)]), text


def test_the_contract_the_prompt_now_asks_for_passes_the_gate():
    """Name the sender's domain and stop — the exact sentence the inbox
    contract gives as its model."""
    text = (
        "Something from paypal-security dot net is asking you to reset a "
        "password — it is in your inbox."
    )
    assert check_relayed_instructions([_turn(text)]) == []


def test_a_segment_that_never_quotes_email_is_not_scanned():
    """The cold open and the weather do not relay anybody's message, and
    a gate that fires there is a false positive with no upside."""
    text = "You should change your password on a day like this."
    assert check_relayed_instructions([_turn(text, Segment.WEATHER)]) == []
    assert check_relayed_instructions([_turn(text, Segment.INBOX)])


def test_the_flag_names_what_fired():
    """Same debuggability contract as every other gate here: a finding
    nobody can argue with is a finding nobody can fix."""
    flagged = check_relayed_instructions([
        _turn("It advises changing your password. This is a critical step.")
    ])
    assert flagged[0]["turn_index"] == 0
    assert flagged[0]["segment"] == "inbox"
    assert "password" in flagged[0]["action"]
    assert flagged[0]["endorsement"] == "critical"


def test_grounding_rewards_the_phishing_claim_that_this_gate_catches():
    """Why the existing gates could not have caught it.

    `check_grounding` asks whether a claim appears in the source data. A
    phishing instruction quoted accurately from the email scores as
    PERFECTLY grounded, because the email is the source. The safeguard
    pointed the wrong way, and that is the argument for a separate gate
    rather than a tweak to that one.
    """
    source = (
        "From: security@paypa1-alerts.test\n"
        "Subject: Unusual sign-in\n"
        "We advise changing your password immediately. This is a critical "
        "security step to protect your account."
    )
    turns = [_turn(
        "It advises changing your password. This is a critical security "
        "step to protect your account."
    )]

    report = run_all_gates(turns, source)
    assert report["grounding"] == [], (
        "precondition: grounding is satisfied — the claim is quoted from "
        "the source, which is exactly the problem"
    )
    assert report["relayed_instructions"], "and this is what catches it"
    assert report["total_issues"] >= 1


# ──────────────────────────────────────────────────────────────
# What actually SHIPS. The gate only matters if it changes the
# script that reaches the listener.
# ──────────────────────────────────────────────────────────────


async def _revise_to(monkeypatch, draft: list[Turn], revised: list[Turn],
                     segments: list[Segment] | None = None):
    """Run the critique/revise pass with a fixed revision result."""
    import json

    from app.pipeline import script_chain

    class _LLM:
        async def complete(self, system, prompt, *, temperature=0.85,
                           json_mode=False, response_schema=None):
            if "Review this draft script critically" in prompt:
                return json.dumps({"issues": [{
                    "turn_index": 0, "type": "RELAYED_INSTRUCTION",
                    "detail": "the hosts endorse a password reset",
                    "fix": "name the sender's domain and stop",
                }]})
            # A bare array, which is what the revise pass expects —
            # `_revised_json` in the fake provider returns the same shape.
            return json.dumps([
                {"speaker": t.speaker, "segment": t.segment.value,
                 "text": t.text} for t in revised
            ])

        async def stream(self, system, prompt, *, temperature=0.85):
            raise NotImplementedError

    # `_is_valid_script` rejects a revision outside 0.8-1.2x the budget
    # BEFORE the comparison runs, so the budget is sized to the scripts
    # under test. Without this the revision is discarded upstream and
    # these tests pass by accident, asserting nothing about the gate.
    budget = max(
        1, sum(len(t.text.split()) for t in revised)
    )
    # `segments=` too: the default demands every canonical segment, so a
    # one-segment fixture is rejected before the comparison and the test
    # would assert nothing.
    return await script_chain._run_critique_and_revise(
        llm=_LLM(), draft_turns=draft, source_data="source", budget=budget,
        segments=segments or [Segment.INBOX],
    )


async def test_a_revision_that_removes_the_relay_wins_on_that_alone(
    monkeypatch, caplog
):
    """`total_issues` treats every finding as one point, which is right
    for a flat turn and wrong here. A revision that drops the phishing
    endorsement and picks up clumsier phrasing scores WORSE and would
    have been discarded — so the comparison is decided on the relay
    before the score is consulted at all.
    """
    import logging

    draft = [
        _turn("It advises changing your password. This is critical."),
        _turn("Anyway, um, that was the inbox."),
    ]
    # Deliberately worse on the ordinary gates: keeps the filler, adds
    # more, and still has no challenge marker.
    revised = [
        _turn("Something from paypa1-alerts dot test is asking about a "
              "password. It's in your inbox."),
        _turn("Anyway, um, that was the inbox, um, you know."),
    ]

    with caplog.at_level(logging.WARNING):
        out = await _revise_to(monkeypatch, draft, revised)

    assert out == revised, (
        "the revision removed a relayed security instruction and must be "
        "kept even though it scores worse on the other gates"
    )
    assert "relayed security instruction" in caplog.text


async def test_a_suspected_new_relay_costs_a_point_but_does_not_veto(
    monkeypatch, caplog
):
    """One-directional, and review of #57 is why.

    The first cut returned the draft whenever the revision looked worse on
    relays, which turned a false positive into shipped damage: a revision
    that fixed four filler hits — rule 1, the non-negotiable CLAUDE.md
    calls most important — was discarded because a legitimate world story
    about a breach used "critical" and "log in to your account".

    A heuristic may cost a point. It may not hold a veto. So the draft
    still wins HERE, but by the ordinary score rather than by fiat.
    """
    import logging

    draft = [_turn("There's a message from your bank in the inbox.")]
    revised = [_turn("Your bank says you should verify your account today.")]

    with caplog.at_level(logging.WARNING):
        out = await _revise_to(monkeypatch, draft, revised)

    assert out == draft
    assert "not vetoing on a heuristic" in caplog.text


async def test_a_better_revision_is_not_discarded_over_a_false_positive(
    monkeypatch
):
    """Review's finding 4, reproduced verbatim.

    Legitimate world-segment security journalism trips the gate —
    "critical" is the standard severity word in security writing. Before
    this, that discarded a revision which fixed four filler hits and
    shipped a draft violating rule 1 instead.
    """
    draft = [
        _turn("Um, you know, the Okta thing happened. Um.", Segment.WORLD),
        _turn("Um, yeah. Agreed. Um, you know.", Segment.WORLD),
    ]
    revised = [
        _turn("Okta disclosed a breach overnight.", Segment.WORLD),
        _turn("Actually, the critical part is that attackers could log in "
              "to your account.", Segment.WORLD),
    ]

    out = await _revise_to(
        monkeypatch, draft, revised, segments=[Segment.WORLD]
    )
    assert out == revised, (
        "a revision that fixes four filler hits must not be discarded "
        "because a security story used the word 'critical'"
    )


async def test_a_relay_surviving_both_is_logged_as_an_error(monkeypatch, caplog):
    """Nothing at this layer can fix it — the contract and the critique
    category are what should have caught it upstream — but it must not go
    out unremarked."""
    import logging

    draft = [_turn("It advises changing your password. This is critical.")]
    revised = [_turn("You should change your password, it's urgent.")]

    with caplog.at_level(logging.ERROR):
        await _revise_to(monkeypatch, draft, revised)

    assert "survived revision" in caplog.text


# ──────────────────────────────────────────────────────────────
# The shapes review found the first cut missing entirely.
# ──────────────────────────────────────────────────────────────


def test_the_two_host_split_is_caught():
    """The bypass that mattered most: the gate scanned ONE turn, and the
    product is two hosts alternating. It caught the monologue form and
    missed the dialogue form — and the inbox contract added by this same
    PR ("name the domain and stop") makes the reporting turn compliant on
    its own, pushing the endorsement into the reply."""
    split = [
        _turn("There's a mail from your bank saying it advises changing "
              "your password."),
        _turn("That one's critical — I'd do it right away, before your "
              "nine o'clock."),
    ]
    assert check_relayed_instructions(split)


def test_an_endorsement_in_a_different_segment_does_not_leak():
    """The window is adjacent turns IN THE SAME SEGMENT. A world story
    calling something critical must not explain an inbox turn next to it.
    """
    mixed = [
        _turn("It advises changing your password."),
        _turn("Separately, the outage was critical for them.", Segment.WORLD),
    ]
    assert check_relayed_instructions(mixed) == []


@pytest.mark.parametrize("text", [
    "Change your password. Today.",
    "Go and verify your account.",
    "Just click the link in that email.",
])
def test_the_hosts_issuing_the_order_themselves_needs_no_endorsement(text):
    """The most dangerous output there is, and requiring a second signal
    made it unreachable: an order carries no endorsement marker."""
    assert check_relayed_instructions([_turn(text)])


def test_reporting_still_is_not_an_order():
    """Positional, so "It advises changing your password" is safe — the
    sentence opens with "It", not with the verb."""
    assert check_relayed_instructions([
        _turn("It advises changing your password.")
    ]) == []


@pytest.mark.parametrize("text", [
    "You should verify your payment method today.",
    "You should reset the password on that account right away.",
    "You should click a link in that message.",
    "You should click here to sort it out, it's urgent.",
    "They're asking you to update your credentials, and it's urgent.",
    "You'll want to re-authenticate with them right away.",
    "It's critical that you secure your account today.",
    "You should validate your account before it locks.",
    "Make sure to open the attachment they sent.",
    "You should call the number in the email right away.",
    "You should tap the button in that email, it's urgent.",
    "You should authorise the transfer this morning.",
    "Make sure to follow the steps in the email before noon.",
    "Your account will be closed unless you act today.",
    "You should change your password today.",
])
def test_the_relays_review_found_missing(text):
    """24 of 28 realistic relays missed the first cut, and the list was
    internally inconsistent — "confirm your payment" caught while "verify
    your payment" did not."""
    assert check_relayed_instructions([_turn(text)]), text


@pytest.mark.parametrize("text", [
    # The verb+object family joins with `\s+`, which already matches a
    # non-breaking space, so review's own NBSP example was caught even
    # without normalising — my first test for it proved nothing.
    "You should change\u00a0your\u00a0password today.",
    # The lure family joins with LITERAL spaces and genuinely is defeated
    # by one. This is the case that makes `_normalise_spaces` load-bearing,
    # and mutation testing is what found the difference.
    "You should click\u00a0the\u00a0link today.",
    "Make sure to follow\u00a0the\u00a0steps in that email.",
])
def test_a_non_breaking_space_does_not_hide_a_relay(text):
    """HTML email routinely carries NBSP and it flows into the script."""
    assert check_relayed_instructions([_turn(text)]), repr(text)


def test_a_calendar_invite_is_scanned_too():
    """`_format_calendar` interpolates `ev.description` verbatim, and an
    invite from a stranger auto-populates Google Calendar — so that field
    is an injection surface on a par with an email body."""
    text = ("Your ten o'clock invite says to verify your account before "
            "joining. That's critical — do that today.")
    assert check_relayed_instructions([_turn(text, Segment.CALENDAR)])


async def test_a_relay_is_logged_even_when_the_gates_never_run(caplog):
    """`run_all_gates` has one production caller, and four paths skip it.

    Review of #57 verified the worst: a draft carrying the live phishing
    line plus an over-budget revision produced "revision invalid — keeping
    draft" and not one word about the relay. None of those paths scanned
    the DRAFT, which is the thing that ships. So whatever survives to the
    end is scanned once more, unconditionally.
    """
    import json
    import logging
    from datetime import UTC, datetime

    from app.core.schemas import BriefingLength
    from app.pipeline.script_chain import generate_script

    relay = ("It advises changing your password. This is a critical "
             "security step to protect your account.")

    class _RelayingLLM(FakeLLMProvider):
        def _dispatch(self, prompt):
            body = super()._dispatch(prompt)
            if self.calls[-1][0] not in {"draft", "revise"}:
                return body
            turns = json.loads(body)
            payload = turns["turns"] if isinstance(turns, dict) else turns
            for turn in payload:
                if turn["segment"] == "inbox":
                    turn["text"] = relay
                    break
            return json.dumps(
                {"turns": payload} if isinstance(turns, dict) else payload
            )

    with caplog.at_level(logging.ERROR):
        turns, _ = await generate_script(
            llm=_RelayingLLM(), emails=[], events=[], weather=None,
            length=BriefingLength.STANDARD,
            date=datetime(2026, 9, 20, tzinfo=UTC),
        )

    assert any(relay in t.text for t in turns), "precondition: it shipped"
    assert "SHIPPING a script that relays" in caplog.text
