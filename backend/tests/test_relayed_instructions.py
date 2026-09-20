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

from app.core.schemas import Segment, Turn
from app.pipeline.quality import check_relayed_instructions, run_all_gates


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


async def _revise_to(monkeypatch, draft: list[Turn], revised: list[Turn]):
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
        segments=[Segment.INBOX],
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


async def test_a_revision_that_introduces_a_relay_is_rejected(monkeypatch):
    draft = [_turn("There's a message from your bank in the inbox.")]
    revised = [_turn("Your bank says you should verify your account today.")]

    out = await _revise_to(monkeypatch, draft, revised)
    assert out == draft


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
