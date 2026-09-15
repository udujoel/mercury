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

from datetime import UTC, datetime

import pytest

from app.core.providers import ContentBlocked
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
    """

    def __init__(self, poison: str) -> None:
        self.poison = poison
        self.prompts: list[str] = []

    async def complete(self, system, prompt, **kwargs):
        self.prompts.append(prompt)
        if self.poison in prompt:
            raise ContentBlocked("PROHIBITED_CONTENT", stage="prompt")
        return _TURNS if "ECHOED-OUTLINE" in prompt else _OUTLINE

    async def stream(self, system, prompt, **kwargs):  # pragma: no cover
        raise NotImplementedError


class _AlwaysBlocks:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, system, prompt, **kwargs):
        self.calls += 1
        raise ContentBlocked("PROHIBITED_CONTENT", stage="prompt")

    async def stream(self, system, prompt, **kwargs):  # pragma: no cover
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
    def __init__(self, *, block_reason=None, finish_reason=None, text="ok",
                 candidates=None) -> None:
        self.prompt_feedback = type("F", (), {"block_reason": block_reason})()
        if candidates is None:
            candidates = [type("C", (), {"finish_reason": finish_reason})()]
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
    """Different stage, because a different recovery applies."""
    from app.providers.gemini import _text_of

    with pytest.raises(ContentBlocked) as caught:
        _text_of(_Response(finish_reason=_Enum("SAFETY")))
    assert caught.value.stage == "response"


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
