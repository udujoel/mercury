"""Subjects, not category tags.

`topics` was specified as "one to three lowercase single words", so the model
returned `ai` for sixteen of the live 2026-09-11 digest's stories. A section
headed "AI — 11 senders" answers nothing, and research cannot act on it:
"go and find out more about `ai`" is not a question. Task 18's entire value
is the second step, and the second step needs something to look up.

These pin the CONTRACT. Whether Gemini honours it was checked against the
real model — two separate Houthi clusters came back sharing one subject,
character for character — but a test that calls the API would fail in CI and
tell us nothing about our own code.
"""

import json

from app.core.schemas import DigestStory
from app.pipeline.digest import _ENRICHMENT_SCHEMA, StoryCard
from app.pipeline.prompts import DIGEST_PROMPT


def test_the_schema_lets_the_model_return_a_subject():
    props = _ENRICHMENT_SCHEMA["items"]["properties"]
    assert "subject" in props, (
        "an unconstrained field is one the decoder may silently drop"
    )
    assert props["subject"]["nullable"] is True, (
        "a roundup genuinely has no specific subject; forcing one invents it"
    )


def test_the_prompt_demands_a_phrase_and_refuses_a_category():
    """The instruction is the whole mechanism, so it is worth asserting."""
    assert "`subject`" in DIGEST_PROMPT
    assert "noun phrase" in DIGEST_PROMPT
    # The failure it exists to prevent, named in the prompt as a bad example.
    assert 'bad: "ai"' in DIGEST_PROMPT
    # Grouping is what makes it useful to 17, and it is stated as a rule.
    assert "identical phrase on every card about the same thing" in DIGEST_PROMPT
    # And `topics` must stay categories rather than quietly becoming a second
    # subject field.
    assert "CATEGORIES" in DIGEST_PROMPT


def test_a_card_carries_the_subject_through_to_the_wire():
    card = StoryCard(
        headline="Houthi Advance Threatens Global Trade",
        subject="Houthi forces advancing on Red Sea shipping lanes",
        topics=["geopolitics", "trade"],
    )
    assert card.subject == "Houthi forces advancing on Red Sea shipping lanes"
    assert card.topics == ["geopolitics", "trade"]


def test_an_absent_subject_is_none_not_a_crash():
    """Every digest already in R2 was written before this field existed."""
    card = StoryCard(headline="Weekly roundup")
    assert card.subject is None


def test_the_wire_model_tolerates_a_digest_written_before_subjects():
    story = DigestStory.model_validate({
        "id": "st_1", "headline": "Old story", "source_count": 1, "sources": [],
    })
    assert story.subject is None


def test_subjects_group_two_clusters_that_headlines_do_not():
    """The point of the field, as a plain assertion about strings.

    Two clusters, two different headlines, one subject — which is what lets
    "what subjects arrived this morning" answer with something other than a
    list of every headline.
    """
    a = DigestStory.model_validate({
        "id": "st_a", "headline": "Houthi Advance Threatens Global Trade",
        "subject": "Houthi forces advancing on Red Sea shipping lanes",
        "source_count": 3, "sources": [],
    })
    b = DigestStory.model_validate({
        "id": "st_b", "headline": "Houthi Advance Consolidates Red Sea Power",
        "subject": "Houthi forces advancing on Red Sea shipping lanes",
        "source_count": 2, "sources": [],
    })
    assert a.headline != b.headline
    assert a.subject == b.subject


def test_the_schema_is_valid_json():
    json.dumps(_ENRICHMENT_SCHEMA)
