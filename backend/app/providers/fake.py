"""
Deterministic, offline LLMProvider. No network calls.

Lets the whole outline -> draft -> critique -> revise chain run and be tested
right now, with no GEMINI_API_KEY and no live spike. Structurally satisfies
`app.core.providers.LLMProvider`.

The fake distinguishes which pass is being requested by sniffing a few fixed
strings that are unique to each prompt template in app/pipeline/prompts.py
(reused verbatim by scripts/spike_04_llm.py). It does not parse the caller's
input beyond that — the fake's job is to return schema-valid, deterministic
output for each pass, not to actually reason about the source data.

The planted draft defects (see DRAFT below) exist so the critique and
revision passes, and the tests, have something real to catch:
  1. a monologue turn with more than four sentences (rule: turns are 1-4
     sentences)
  2. a too-agreeable exchange in the "calendar" segment with no challenge or
     reframe (rule: hosts must not always agree)
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

# ──────────────────────────────────────────────────────────────
# Canned draft, with two deliberate defects.
# ──────────────────────────────────────────────────────────────

_MONOLOGUE_TEXT = (
    "The Stratechery piece this week is about how AI assistants are "
    "becoming the new aggregation point for services. It argues that "
    "bundling used to happen at the platform layer, but now it is "
    "happening at the assistant layer instead. That is a real shift for "
    "anyone who owns a platform business, because the assistant sits "
    "between the platform and the user and can route around it entirely. "
    "Ben's argument is that owning the relationship matters more than "
    "owning the distribution channel now. It is a change worth watching "
    "closely over the next few quarters. And it is one more reason "
    "platform teams are nervous about assistant-layer lock-in."
)  # 7 sentences on purpose — this is the planted monologue defect.

_DRAFT_TURNS: list[dict] = [
    # cold_open (~100 words)
    {"speaker": "A", "segment": "cold_open",
     "text": "Good morning Joel. It's Friday, the twenty-first of August, "
             "just past seven."},
    {"speaker": "B", "segment": "cold_open",
     "text": "And it's a real day, not a quiet one. Provider upgrade "
             "review at nine, then a 1:1 with Sam this afternoon, with a "
             "run of inbox items in between that are more connected to "
             "each other than they look at first glance."},
    {"speaker": "A", "segment": "cold_open",
     "text": "Nothing overwhelming, but worth walking through in order "
             "so nothing gets missed before that nine o'clock, "
             "especially since a couple of things in the inbox turn "
             "out to matter more once you see how they line up with "
             "the calendar."},
    # weather (~80 words)
    {"speaker": "A", "segment": "weather",
     "text": "Weather in Aveiro: clear, sixteen degrees now, climbing to "
             "a high of twenty-four by afternoon. Low tonight back down "
             "around fifteen."},
    {"speaker": "B", "segment": "weather",
     "text": "Good day to have that nine o'clock in person rather than "
             "on Zoom, if that's an option — no reason to be inside on a "
             "morning like this if you don't have to be."},
    {"speaker": "A", "segment": "weather",
     "text": "Agreed, and it holds through the weekend too, so there's "
             "no rush to squeeze outdoor plans into today specifically."},
    # calendar — planted defect: too agreeable, no challenge (~220 words)
    {"speaker": "A", "segment": "calendar",
     "text": "First up, the provider upgrade review with the platform "
             "team at nine, in Meeting Room 3 or on Zoom. Priya wants "
             "your sign-off on the rollout plan before the review, and "
             "the migration window she's proposing is the night of "
             "August twenty-seventh."},
    {"speaker": "B", "segment": "calendar",
     "text": "Sounds good, no notes there."},
    {"speaker": "A", "segment": "calendar",
     "text": "Agreed, straightforward. Then it's your regular 1:1 with "
             "Sam at two this afternoon, thirty minutes, no location "
             "listed so it's probably a call."},
    {"speaker": "B", "segment": "calendar",
     "text": "Yep, agreed, nothing to add there either. Two meetings, "
             "a gap in between, should be a manageable day on the "
             "calendar side."},
    # inbox (~300 words)
    {"speaker": "A", "segment": "inbox",
     "text": "In your inbox: Priya sent the provider upgrade rollout "
             "plan last night and wants sign-off before that nine "
             "o'clock review. She specifically flagged that the plan "
             "touches every module your team owns."},
    {"speaker": "B", "segment": "inbox",
     "text": "That's the same review that's on your calendar this "
             "morning, so those two are directly connected — she's "
             "clearly trying to get your read on it before the room "
             "fills up, not just circulating it for the record."},
    {"speaker": "A", "segment": "inbox",
     "text": "I'd flag the timing too — the migration window is "
             "proposed for August twenty-seventh, and separately there's "
             "a water maintenance notice for the Glicínias district on "
             "the twenty-second from the city council. Unrelated to the "
             "migration, but worth knowing about if you're in that part "
             "of town that morning."},
    {"speaker": "B", "segment": "inbox",
     "text": "Also worth noting, Sam emailed about today's 1:1 agenda — "
             "he wants to cover his Q3 goals draft, and he's asking "
             "whether he should join this morning's provider review too."},
    {"speaker": "A", "segment": "inbox",
     "text": "Which is worth raising with Priya before nine, given Sam's "
             "on the platform team and the plan touches his area "
             "directly. Might save a second conversation later, and it "
             "answers his question in the same breath."},
    {"speaker": "B", "segment": "inbox",
     "text": "And three small pull requests opened overnight on the "
             "backend repo: a fix for a flaky ingestion test, retry "
             "backoff added to the embedding client, and an lxml "
             "version bump. Nothing in there that needs you today."},
    {"speaker": "A", "segment": "inbox",
     "text": "One more from the city council newsletter, mostly for "
             "awareness — water pressure may drop in the Glicínias "
             "district between nine and one on the twenty-second, so "
             "tomorrow, not today, and only worth acting on if you're "
             "actually in that part of town."},
    {"speaker": "B", "segment": "inbox",
     "text": "So really, out of everything in the inbox this morning, "
             "the one that actually changes what you do before nine is "
             "Priya's rollout plan — the rest is either routine or a day "
             "away. Worth reading the plan itself before the review, not "
             "just skimming her note."},
    # world (~250 words, includes planted monologue)
    {"speaker": "A", "segment": "world",
     "text": _MONOLOGUE_TEXT},
    {"speaker": "B", "segment": "world",
     "text": "I'd push back on that a little — owning the relationship "
             "sounds nice on paper, but if the assistant controls the "
             "interface, the platform doesn't get much say in how that "
             "relationship actually goes either. It's not clearly a win "
             "for platform owners just because the aggregation point "
             "moved."},
    {"speaker": "A", "segment": "world",
     "text": "Fair distinction. It's less 'platforms win' and more "
             "'platforms that don't own an assistant relationship lose "
             "leverage,' which is a narrower and less comfortable claim."},
    {"speaker": "B", "segment": "world",
     "text": "Which loops back to your nine o'clock, honestly — a "
             "provider upgrade is exactly the kind of infrastructure "
             "decision that determines who owns that relationship later."},
    {"speaker": "A", "segment": "world",
     "text": "Bit of a stretch, but I'll allow it as a segue back to "
             "the actual day ahead of you."},
    # follow_ups (~40 words)
    {"speaker": "A", "segment": "follow_ups",
     "text": "No open follow-ups from yesterday to circle back on."},
    {"speaker": "B", "segment": "follow_ups",
     "text": "Clean slate, then — first time in a while. Enjoy it while "
             "it lasts, because tomorrow's queue is already starting to "
             "fill back up."},
    # sign_off (~60 words)
    {"speaker": "A", "segment": "sign_off",
     "text": "That's the morning, Joel. Good luck with the nine o'clock, "
             "and worth a quick word to Priya about Sam joining before "
             "the room fills up."},
    {"speaker": "B", "segment": "sign_off",
     "text": "Have a good one, and enjoy that weather while it lasts. "
             "Talk to you tomorrow."},
]

# ──────────────────────────────────────────────────────────────
# Canned outline. Word budgets sum to the "standard" 1050 target.
# ──────────────────────────────────────────────────────────────

_OUTLINE = {
    "segments": [
        {"segment": "cold_open", "covers": "Greeting and the shape of the day",
         "angle": "A busy but not overwhelming Friday", "words": 100},
        {"segment": "weather", "covers": "Today's forecast for Aveiro",
         "angle": "Good weather, mention it briefly", "words": 80},
        {"segment": "calendar", "covers": "The provider upgrade review and "
         "the 1:1 with Sam", "angle": "Two meetings, one prep-heavy",
         "words": 220},
        {"segment": "inbox", "covers": "Priya's rollout plan, Sam's agenda "
         "note, the council notice, GitHub PRs",
         "angle": "The rollout plan connects directly to the morning "
         "meeting", "words": 300},
        {"segment": "world", "covers": "The Stratechery piece on AI "
         "assistants as the new aggregation layer",
         "angle": "What it means for platform owners", "words": 250},
        {"segment": "follow_ups", "covers": "Nothing outstanding",
         "angle": "Keep it short", "words": 40},
        {"segment": "sign_off", "covers": "Close out the briefing",
         "angle": "Warm, brief", "words": 60},
    ]
}

# ──────────────────────────────────────────────────────────────
# Canned critique: flags exactly the two planted defects.
# ──────────────────────────────────────────────────────────────

_MONOLOGUE_INDEX = next(
    i for i, t in enumerate(_DRAFT_TURNS) if t["text"] == _MONOLOGUE_TEXT
)
_AGREEABLE_INDEX = next(
    i for i, t in enumerate(_DRAFT_TURNS) if t["text"] == "Sounds good, no notes there."
)

_CRITIQUE = {
    "issues": [
        {
            "turn_index": _MONOLOGUE_INDEX,
            "type": "MONOLOGUE",
            "detail": "Host A's turn on the Stratechery piece runs six "
                      "sentences, well past the four-sentence limit.",
            "fix": "Split it: state the argument in two sentences, let "
                   "Host B respond, then let A make the platform-owner "
                   "point separately.",
        },
        {
            "turn_index": _AGREEABLE_INDEX,
            "type": "AGREEABLE",
            "detail": "The calendar segment is pure agreement — 'sounds "
                      "good, no notes' and 'agreed, nothing to add' back "
                      "to back — with no challenge or reframe.",
            "fix": "Give Host B a real reaction: question whether Sam "
                   "should also join the nine o'clock, since his email "
                   "raised exactly that.",
        },
    ]
}

# ──────────────────────────────────────────────────────────────
# Canned revision: same script with both defects fixed.
# ──────────────────────────────────────────────────────────────

_REVISED_TURNS: list[dict] = [
    t.copy() for t in _DRAFT_TURNS
]

# Fix 1 — split the monologue into two turns of <=4 sentences each.
_REVISED_TURNS[_MONOLOGUE_INDEX] = {
    "speaker": "A", "segment": "world",
    "text": "The Stratechery piece this week is about how AI assistants "
            "are becoming the new aggregation point for services. Ben's "
            "argument is that owning the relationship matters more than "
            "owning the distribution channel now.",
}
_REVISED_TURNS.insert(_MONOLOGUE_INDEX + 1, {
    "speaker": "A", "segment": "world",
    "text": "Bundling used to happen at the platform layer. Now it's "
            "happening at the assistant layer instead, which routes "
            "around the platform entirely. It's a change worth watching "
            "closely over the next few quarters.",
})
_REVISED_TURNS.insert(_MONOLOGUE_INDEX + 2, {
    "speaker": "A", "segment": "world",
    "text": "And it's one more reason platform teams are nervous about "
            "assistant-layer lock-in.",
})

# Fix 2 — replace the agreeable exchange with a real challenge, prompted by
# Sam's own email about wanting to join the morning review. The calendar
# segment is entirely before the insert above, so _AGREEABLE_INDEX (computed
# against the original _DRAFT_TURNS) is still valid here.
_REVISED_TURNS[_AGREEABLE_INDEX] = {
    "speaker": "B", "segment": "calendar",
    "text": "Actually, hold on — Sam's email this morning asked whether he "
            "should join that nine o'clock too. Might be worth a heads up "
            "to Priya before the room fills up.",
}


def _draft_json() -> str:
    return json.dumps(_DRAFT_TURNS)


def _revised_json() -> str:
    return json.dumps(_REVISED_TURNS)


def _outline_json() -> str:
    return json.dumps(_OUTLINE)


def _critique_json() -> str:
    return json.dumps(_CRITIQUE)


def _extract_between(text: str, start_marker: str, end_marker: str) -> str:
    """Pull the substring between two literal markers out of `text`, or ""
    if `start_marker` isn't found. Used to pick the QUESTION and SOURCE DATA
    fields back out of a formatted VOICE_ANSWER_PROMPT — see
    `_voice_answer_json` below."""
    start = text.find(start_marker)
    if start == -1:
        return ""
    start += len(start_marker)
    end = text.find(end_marker, start)
    return text[start:end if end != -1 else None].strip()


def _unfence(text: str) -> str:
    """Strip the injection-warning fence app.pipeline.voice._fence_untrusted
    wraps the `segment_sources` prompt field in, so the fake reads the
    actual source list rather than treating the warning banner text as the
    first "source". Mirrors the literal marker strings
    app.pipeline.script_chain.SOURCE_FENCE_OPEN/CLOSE use; duplicated here
    rather than imported so this offline provider has no dependency on the
    pipeline package.
    """
    open_marker, close_marker = "<<<UNTRUSTED_SOURCE_DATA", "UNTRUSTED_SOURCE_DATA>>>"
    open_idx = text.find(open_marker)
    close_idx = text.find(close_marker)
    if open_idx == -1 or close_idx == -1:
        return text
    content_start = text.find("\n", open_idx) + 1
    return text[content_start:close_idx].strip()


def _voice_answer_json(prompt: str) -> str:
    """Build a canned voice-answer response that actually depends on the
    QUESTION and the SOURCE DATA the caller assembled from the manifest —
    not a fixed blob regardless of input.

    This project has already been burned once by a fake that looked
    reasonable and hid a prompt-wiring bug (see the module docstring). The
    Phase 3 voice pipeline builds its context — which turns surround
    position_ms, which SourceRefs attach to them — entirely inside the
    caller (app.pipeline.voice._build_context) and hands it to the LLM only
    through the formatted prompt string; a fake that ignores that string
    can't catch a bug in that wiring. So this one actually reads it back
    out, using the same literal section headers VOICE_ANSWER_PROMPT (see
    app/pipeline/prompts.py) always produces.
    """
    question = _extract_between(
        prompt, "THE QUESTION:\n", "\n\nAnswer in two to five sentences"
    )
    sources = _unfence(_extract_between(
        prompt, "SOURCE DATA BEHIND THAT SEGMENT:\n", "\n\nEARLIER QUESTIONS"
    ))
    first_source_line = next(
        (line.strip() for line in sources.splitlines() if line.strip()), ""
    )

    q_lower = question.lower()
    if "weather" in q_lower:
        no_weather_source = (
            "There's no weather source linked to this part of the briefing."
        )
        answer = (
            "You asked about the weather. "
            + (first_source_line or no_weather_source)
        )
    elif first_source_line:
        answer = (
            f'On "{question}" — the source behind this part of the '
            f"briefing is: {first_source_line}"
        )
    else:
        answer = (
            f'On "{question}": nothing in this part of the briefing answers '
            "that directly."
        )

    return json.dumps({"answer": answer, "bridge": "Anyway, back to the briefing."})


def _digest_json(prompt: str) -> str:
    """One digest card per story id found in the prompt.

    Driven by its input rather than canned, because the thing worth testing
    offline is that cards come BACK matched to the right cluster — a fixed
    response would pass whatever ids the pipeline sent and prove nothing
    about the join. Same approach as `_voice_answer_json`.

    The summary text is deliberately generic. A fake that echoed the source
    text back would defeat the one test that matters most here: that no raw
    body reaches the stored digest.
    """
    ids = re.findall(r"^\s*- id: (\S+)\s*$", prompt, flags=re.MULTILINE)
    return json.dumps(
        [
            {
                "id": cluster_id,
                "headline": f"Fake headline for {cluster_id}",
                "summary": f"A fake two-sentence summary for {cluster_id}. "
                           "It says nothing about the source text.",
                "why_it_matters": None,
                "topics": ["fake"],
            }
            for cluster_id in ids
        ]
    )


class FakeLLMProvider:
    """Deterministic LLMProvider for offline development and tests.

    Detects which of the four prompts (outline / draft / critique / revise)
    it has been asked to complete by looking for strings that are unique to
    each template in app/pipeline/prompts.py, and returns a fixed,
    schema-valid response for that pass. The voice-answer pass is the one
    exception to "fixed" — see `_voice_answer_json`.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []  # (kind, json_mode) audit trail
        self.schemas: list[dict | None] = []  # response_schema per complete()

    def _dispatch(self, prompt: str) -> str:
        if "Produce an OUTLINE for the briefing" in prompt:
            kind, body = "outline", _outline_json()
        elif "Write the full script from this outline" in prompt:
            kind, body = "draft", _draft_json()
        elif "Review this draft script critically" in prompt:
            kind, body = "critique", _critique_json()
        elif "Apply these fixes to the script" in prompt:
            kind, body = "revise", _revised_json()
        elif "Write one card for each story below" in prompt:
            kind, body = "digest", _digest_json(prompt)
        elif "just interrupted the briefing" in prompt:
            kind, body = "voice_answer", _voice_answer_json(prompt)
        else:
            kind, body = "unknown", "{}"
        self.calls.append((kind, True))
        return body

    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float = 0.85,
        json_mode: bool = False,
        response_schema: dict | None = None,
    ) -> str:
        # response_schema is accepted and ignored: this provider returns
        # canned, already-schema-valid bodies, so there is nothing to
        # constrain. It stays in the signature because the fake has to keep
        # step with app.core.providers.LLMProvider — a fake that silently
        # rejects a keyword the real provider accepts turns a protocol
        # change into a test-only failure at the worst moment.
        self.schemas.append(response_schema)
        return self._dispatch(prompt)

    async def stream(
        self, system: str, prompt: str, *, temperature: float = 0.85
    ) -> AsyncIterator[str]:
        text = self._dispatch(prompt)
        # Yield in small chunks to exercise streaming call sites offline.
        chunk_size = 24
        for i in range(0, len(text), chunk_size):
            yield text[i : i + chunk_size]
