"""A safety block must cost a segment, not the morning.

2026-09-15, run 35024541910: the draft prompt came back with no candidates
and `block_reason: PROHIBITED_CONTENT`. The SDK's `.text` accessor raises a
bare `ValueError` in that case, nothing on the path caught it, and the whole
briefing died — the same shape as 2026-08-27, when a single malformed JSON
response also cost a morning.

Rule 7 is explicit: "Any source can fail and the briefing still ships, minus
that segment." A story the model refuses to read is a source failing.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from app.core.providers import ContentBlocked, LLMProvider
from app.core.schemas import BriefingLength, Item, SourceType, Story
from app.pipeline.script_chain import generate_script


def _story(cluster_id: str, title: str, body: str) -> Story:
    item = Item(
        source_type=SourceType.RSS,
        source_name="Reuters",
        title=title,
        body=body,
        url=f"https://example.test/{cluster_id}",
        timestamp=datetime(2026, 9, 15, 6, 0, tzinfo=UTC),
        cluster_id=cluster_id,
    )
    return Story(cluster_id=cluster_id, canonical=item, members=[item])


def _email(title: str, body: str) -> Item:
    return Item(
        source_type=SourceType.EMAIL,
        source_name="A Colleague",
        title=title,
        body=body,
        author="colleague@example.test",
        timestamp=datetime(2026, 9, 15, 5, 0, tzinfo=UTC),
        raw={"is_newsletter": False},
    )


# The beat carries a sentinel, so the fake can tell the DRAFT prompt from
# the OUTLINE prompt by the only reliable signal there is: the draft prompt
# is the one with the outline echoed back inside it.
_OUTLINE = '{"segments": [{"segment": "cold_open", "beats": ["ECHOED-OUTLINE"]}]}'
_TURNS = (
    '[{"speaker": "A", "segment": "cold_open", "text": "Good morning, Joel."},'
    ' {"speaker": "B", "segment": "cold_open", "text": "A quiet one today."}]'
)


class _BlocksOnPhrase:
    """Refuses any prompt containing `poison`, the way the filter does.

    Deterministic on CONTENT — which is the property that makes retrying the
    identical prompt pointless, and the whole reason the recovery has to
    change its input rather than ask again.

    `drafts_only` reproduces the 2026-09-15 shape specifically: the outline
    pass SUCCEEDED that morning (660 candidate tokens) and the draft was
    refused. Without it the block lands on the outline instead, because
    `_format_stories` puts article bodies into that prompt too — so a test
    that does not distinguish them never exercises the path the incident
    actually took.
    """

    def __init__(self, poison: str, *, drafts_only: bool = False) -> None:
        self.poison = poison
        self.drafts_only = drafts_only
        self.prompts: list[str] = []

    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float = 0.85,
        json_mode: bool = False,
        response_schema: dict | None = None,
    ) -> str:
        self.prompts.append(prompt)
        is_draft = "ECHOED-OUTLINE" in prompt
        if self.poison in prompt and (is_draft or not self.drafts_only):
            raise ContentBlocked("PROHIBITED_CONTENT", stage="prompt")
        return _TURNS if is_draft else _OUTLINE

    async def stream(
        self, system: str, prompt: str, *, temperature: float = 0.85
    ):  # pragma: no cover
        raise NotImplementedError


class _AlwaysBlocks:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float = 0.85,
        json_mode: bool = False,
        response_schema: dict | None = None,
    ) -> str:
        self.calls += 1
        raise ContentBlocked("PROHIBITED_CONTENT", stage="prompt")

    async def stream(
        self, system: str, prompt: str, *, temperature: float = 0.85
    ):  # pragma: no cover
        raise NotImplementedError


async def test_a_blocked_news_story_costs_the_story_not_the_briefing():
    """The 2026-09-15 failure, in one test."""
    llm = _BlocksOnPhrase("MASSACRE")
    turns, _ = await generate_script(
        llm=llm,
        emails=[_email("Lunch?", "Are you free on Thursday?")],
        events=[],
        weather=None,
        length=BriefingLength.STANDARD,
        date=datetime(2026, 9, 15, 6, 0, tzinfo=UTC),
        stories=[_story("c1", "Reports from the region", "A MASSACRE was reported.")],
    )
    assert turns, "the briefing must still ship"
    # It must have DROPPED the story, not asked again with it.
    assert not any(llm.poison in p for p in llm.prompts[-2:])


async def test_a_block_that_survives_the_retry_is_raised_not_swallowed():
    """Degrading forever would ship a briefing about nothing.

    If even the reduced prompt is refused, that is a real failure and the run
    must say so rather than emit an empty script — the `n_stories=0` shape
    this project keeps rediscovering.
    """
    llm = _AlwaysBlocks()
    with pytest.raises(ContentBlocked):
        await generate_script(
            llm=llm,
            emails=[_email("Lunch?", "Are you free?")],
            events=[],
            weather=None,
            length=BriefingLength.STANDARD,
            date=datetime(2026, 9, 15, 6, 0, tzinfo=UTC),
            stories=[_story("c1", "A headline", "A body.")],
        )


# ── The translation itself ────────────────────────────────────────────


class _Enum:
    """Stands in for a protobuf enum value, which has a `.name`."""

    def __init__(self, name: str) -> None:
        self.name = name


class _Response:
    """Shaped like the SDK's response, including the part that bites.

    `parts` is modelled rather than faked away. The SDK raises from `.text`
    whenever `content.parts` is empty — for EVERY finish reason, STOP
    included — so a stub that sets `.text` to a plain string can never
    reproduce the failure and will certify a guard that does not work. The
    first version of this file did exactly that.
    """

    def __init__(
        self,
        *,
        block_reason=None,
        finish_reason=None,
        text="ok",
        candidates=None,
        parts=None,
    ) -> None:
        self.prompt_feedback = type("F", (), {"block_reason": block_reason})()
        if candidates is None:
            parts = [text] if parts is None else parts
            content = type("Content", (), {"parts": parts})()
            candidates = [
                type("C", (), {"finish_reason": finish_reason, "content": content})()
            ]
        self.candidates = candidates
        self.text = text


def test_a_blocked_prompt_is_named_not_an_accessor_complaint():
    """The exact 2026-09-15 response shape."""
    from app.providers.gemini import _text_of

    with pytest.raises(ContentBlocked) as caught:
        _text_of(_Response(block_reason=_Enum("PROHIBITED_CONTENT")))
    assert caught.value.reason == "PROHIBITED_CONTENT"
    assert caught.value.stage == "prompt"


def test_a_candidate_that_stopped_itself_is_a_response_block():
    """Different stage, because a different recovery applies.

    Note the empty `parts`. An earlier version of this test passed a SAFETY
    finish reason WITH text and expected a refusal — encoding the bug the
    review found, where a finish-reason allowlist raised on responses that
    had perfectly usable text in them. What makes it a refusal is that there
    is nothing to return.
    """
    from app.providers.gemini import _text_of

    with pytest.raises(ContentBlocked) as caught:
        _text_of(_Response(finish_reason=_Enum("SAFETY"), parts=[]))
    assert caught.value.stage == "response"
    assert caught.value.reason == "SAFETY"


def test_an_empty_candidate_list_with_no_stated_reason_is_still_a_refusal():
    from app.providers.gemini import _text_of

    with pytest.raises(ContentBlocked):
        _text_of(_Response(candidates=[]))


def test_an_ordinary_response_is_untouched():
    """The gate must not become a filter on healthy responses.

    MAX_TOKENS in particular: a truncated draft is still usable text, and
    treating it as a refusal would throw away a briefing over a long one.
    """
    from app.providers.gemini import _text_of

    assert _text_of(_Response(finish_reason=_Enum("STOP"), text="hello")) == "hello"
    assert _text_of(_Response(finish_reason=_Enum("MAX_TOKENS"), text="cut")) == "cut"
    assert _text_of(_Response(finish_reason=None, text="plain")) == "plain"


def test_text_with_a_refusal_shaped_finish_reason_is_still_returned():
    """The narrowing the first cut introduced, asserted against.

    RECITATION and OTHER arrive WITH partial text often enough to matter. The
    old `.text` returned it, `parse_json_response` raised, and
    `_complete_json`'s repair loop usually recovered. Treating those as
    refusals turned a recoverable truncation into a lost segment.
    """
    from app.providers.gemini import _text_of

    for reason in ("RECITATION", "OTHER", "LANGUAGE"):
        assert (
            _text_of(_Response(finish_reason=_Enum(reason), text='{"partial": true}'))
            == '{"partial": true}'
        )


def test_stop_with_no_parts_is_a_refusal_not_a_crash():
    """The case the finish-reason allowlist waved straight through.

    Gemini 2.5 Flash can spend its whole output budget on thinking tokens and
    return STOP with zero parts. The SDK raises a bare ValueError from
    `.text` for that, which is the very error this function exists to
    translate — and the first cut allowlisted STOP.
    """
    from app.providers.gemini import _text_of

    for reason in ("STOP", "MAX_TOKENS", "FINISH_REASON_UNSPECIFIED"):
        with pytest.raises(ContentBlocked) as caught:
            _text_of(_Response(finish_reason=_Enum(reason), parts=[]))
        assert caught.value.reason == reason


# ── The ladder ────────────────────────────────────────────────────────


async def test_the_incident_shape_a_good_outline_then_a_refused_draft():
    """2026-09-15 exactly: the outline returned 660 tokens, the draft none.

    The first version of this file blocked on the outline instead, because
    `_format_stories` puts article bodies in that prompt too — so it passed
    without ever running the path that lost the morning.
    """
    llm = _BlocksOnPhrase("MASSACRE", drafts_only=True)
    degraded: list[str] = []
    turns, _ = await generate_script(
        llm=llm,
        emails=[_email("Lunch?", "Are you free on Thursday?")],
        events=[],
        weather=None,
        length=BriefingLength.STANDARD,
        date=datetime(2026, 9, 15, 6, 0, tzinfo=UTC),
        stories=[_story("c1", "Reports", "A MASSACRE was reported.")],
        degraded=degraded,
    )
    assert turns
    assert degraded == ["world"], "the manifest must say the news was dropped"
    # An outline succeeded before the block, so the ladder is entered from
    # the draft, not from the first call.
    assert len(llm.prompts) >= 3


async def test_the_retry_turns_the_world_segment_off_not_merely_empty():
    """`stories=[]` alone leaves the model told to write two to four
    clustered stories from zero items — `n_stories=0`, rebuilt by hand."""
    llm = _BlocksOnPhrase("MASSACRE")
    await generate_script(
        llm=llm,
        emails=[_email("Lunch?", "Are you free?")],
        events=[],
        weather=None,
        length=BriefingLength.STANDARD,
        date=datetime(2026, 9, 15, 6, 0, tzinfo=UTC),
        stories=[_story("c1", "Reports", "A MASSACRE was reported.")],
    )
    retry_prompt = llm.prompts[-1]
    assert (
        "- world" not in retry_prompt
    ), "the world segment is still being requested with nothing to write it from"


async def test_a_poisoned_EMAIL_costs_the_inbox_and_the_briefing_still_ships():
    """The rung the first cut did not have.

    Mercury's news arrives largely as email, so dropping only `stories`
    leaves the likelier culprit in the prompt — and buys a second refusal.
    """

    llm = _BlocksOnPhrase("MASSACRE")
    degraded: list[str] = []
    turns, _ = await generate_script(
        llm=llm,
        emails=[_email("Newsletter", "A MASSACRE was reported overnight.")],
        events=[],
        weather=None,
        length=BriefingLength.STANDARD,
        date=datetime(2026, 9, 15, 6, 0, tzinfo=UTC),
        stories=[_story("c1", "Something else", "A quiet story.")],
        degraded=degraded,
    )
    assert turns, "calendar, weather and the greeting still ship"
    assert degraded == ["world", "inbox", "follow_ups"]


def test_the_fakes_satisfy_the_protocol():
    """Rule 6, and the rule test_research.py states: a fake that accepts
    anything cannot fail when the real signature changes.

    `isinstance` alone would not catch it — `runtime_checkable` checks only
    that the method NAMES exist — so the signature is compared directly.
    """
    for fake in (_BlocksOnPhrase("x"), _AlwaysBlocks()):
        assert isinstance(fake, LLMProvider)
        assert inspect.signature(
            type(fake).complete, eval_str=True
        ) == inspect.signature(LLMProvider.complete, eval_str=True)


async def test_the_ladder_at_the_setting_production_actually_uses():
    """Every other test here runs `passes=2`; `generate.py` passes 4.

    Review found the production configuration of this exact path untested —
    which matters because passes=4 adds the critique and revise calls, both
    of which see `source_data` and so can be refused in their own right.
    """
    llm = _BlocksOnPhrase("MASSACRE", drafts_only=True)
    degraded: list[str] = []
    turns, _ = await generate_script(
        llm=llm,
        emails=[_email("Lunch?", "Are you free?")],
        events=[],
        weather=None,
        length=BriefingLength.STANDARD,
        date=datetime(2026, 9, 15, 6, 0, tzinfo=UTC),
        stories=[_story("c1", "Reports", "A MASSACRE was reported.")],
        passes=4,
        degraded=degraded,
    )
    assert turns
    assert degraded == ["world"]


def test_a_refused_prompt_is_final_but_an_empty_response_is_not():
    """The distinction the first cut collapsed.

    RECITATION means the model was echoing source wording — a re-draft
    routinely clears it. Treating that as final spent a whole news segment
    to avoid one cheap retry.
    """
    assert not ContentBlocked("PROHIBITED_CONTENT", stage="prompt").worth_retrying
    assert not ContentBlocked("SAFETY", stage="response").worth_retrying
    assert not ContentBlocked("BLOCKLIST", stage="response").worth_retrying
    assert ContentBlocked("RECITATION", stage="response").worth_retrying
    assert ContentBlocked("STOP", stage="response").worth_retrying


def test_max_tokens_is_not_worth_retrying_however_it_arrives():
    """An overflowing answer is a property of the question.

    This assertion used to run the other way, on the reasoning that
    MAX_TOKENS was "a bad roll rather than a verdict". Review of #53 priced
    that: the newsletter extraction sends the largest prompt in the
    pipeline, and asking for more items than the output window holds fails
    the same way every time. Retrying bought five attempts at it and
    1.5+3+6+12 seconds of backoff out of a 90-second budget — a quarter of
    the morning spent re-losing the same segment.

    Both stages, because the stage says which half was refused and neither
    half gets shorter on a second identical ask.
    """
    assert not ContentBlocked("MAX_TOKENS", stage="response").worth_retrying
    assert not ContentBlocked("MAX_TOKENS", stage="prompt").worth_retrying


def test_an_unset_block_reason_is_falsy_on_the_real_enum():
    """Pins the one assumption `if block_reason:` rests on.

    Review argued a protobuf enum of value 0 would be truthy and so an unset
    reason would raise. It is not: `BlockReason` derives from `IntEnum`, so
    BLOCK_REASON_UNSPECIFIED is `int` 0 and falsy, and the four real reasons
    are 1-4 and truthy.

    Deliberately against the REAL SDK enum rather than a stub. A stub would
    only restate the assumption; this fails if a future SDK changes the base
    class, which is the only way the argument could ever become correct.
    """
    import google.generativeai.protos as protos

    from app.providers.gemini import _text_of

    reasons = protos.GenerateContentResponse.PromptFeedback.BlockReason
    assert not reasons.BLOCK_REASON_UNSPECIFIED
    assert all(
        getattr(reasons, name)
        for name in ("SAFETY", "OTHER", "BLOCKLIST", "PROHIBITED_CONTENT")
    )
    # And the guard behaves accordingly: unset falls through to the text.
    assert (
        _text_of(_Response(block_reason=reasons.BLOCK_REASON_UNSPECIFIED, text="hello"))
        == "hello"
    )
    with pytest.raises(ContentBlocked):
        _text_of(_Response(block_reason=reasons.PROHIBITED_CONTENT))
