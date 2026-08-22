# Phase 3 — streaming voice-answer backend

Implements the server side of "the Join flow": the user taps the mic
mid-briefing, asks a question, and one of the two hosts answers, grounded in
what was just being discussed, then bridges back into the briefing. Full
spec: `docs/PRD.md` §3.5, extracted timing budget in the Phase 3 brief.

## Files created / modified

- `backend/app/pipeline/voice.py` — **new**. The streaming answer pipeline:
  `answer_question()`, incremental JSON field extraction
  (`_JSONFieldStreamer`), the sentence splitter (`split_complete_sentences`),
  and context selection (`_build_context`).
- `backend/app/api/voice.py` — **new**. `POST /voice/ask`, NDJSON streaming
  response, guarded by `require_api_token`.
- `backend/app/main.py` — mounted the voice router (import + one
  `include_router` line; nothing else touched).
- `backend/app/providers/fake.py` — extended `FakeLLMProvider` so the
  voice-answer branch reads the actual question and source-data fields back
  out of the formatted prompt and produces an answer that depends on both,
  instead of a fixed canned string.
- `backend/tests/test_voice_pipeline.py` — **new**, 33 tests.
- `backend/tests/test_voice_api.py` — **new**, 9 tests.
- `docs/phase3/voice_backend.md` — this file.

`app/pipeline/prompts.py` (`VOICE_ANSWER_PROMPT`, used verbatim) and
`app/core/providers.py` (`LLMProvider.stream`, already existed) were **not**
touched, per the brief.

## The streaming design

```
LLM tokens -> incremental JSON field extraction -> sentence splitting
           -> per-sentence TTS -> audio yielded to the caller
```

`VOICE_ANSWER_PROMPT` asks the model to return
`{"answer": "...", "bridge": "..."}` as JSON — a clean shape for a single
completion, but naively it means waiting for the closing `}` before any
audio can start. Two pieces solve that:

**`_JSONFieldStreamer`** decodes the `answer` then `bridge` string fields'
*unescaped* text character-by-character as raw token chunks arrive, tracking
enough state (which field, whether inside the string, whether the previous
character was a backslash, partial `\uXXXX` escapes) to survive a chunk
boundary landing anywhere — mid-key, mid-escape, anywhere. It reports a
`completed` event the instant a field's closing quote arrives, which is
what lets the bridge line be synthesised, and the last pending answer
sentence released, *before* the LLM stream has necessarily finished
producing its last few tokens (the closing `}`, if nothing else) — this is
the mechanism the headline "first audio before LLM exhausted" test
(`test_first_audio_chunk_emitted_before_llm_stream_exhausted`) actually
proves, not just asserts by inspection.

**`split_complete_sentences`** decides where one synthesisable unit ends. A
naive split on `. ` breaks "Dr. Smith", "e.g. this", "3.14 percent" and
"Wait... really?" into nonsense fragments TTS would mangle individually. The
rule: a run of `.`/`!`/`?` followed by whitespace is a boundary *unless*:

- it's an ellipsis (two or more plain dots) — a pause, not a hard stop,
  regardless of what follows it;
- the word immediately before it is a known abbreviation (`Dr`, `e.g`,
  `i.e`, a single initial like `J`) — decimals like `3.14` are naturally
  unaffected since no whitespace ever follows that `.`;
- a character *is* already visible right after the whitespace and it's
  lower-case (a continuation, not a new sentence — "well... let's see").

A trailing `"sentence. "` with nothing after it *yet* is still treated as
complete rather than withheld pending more text — committing to it early is
what lets synthesis start the instant a sentence finishes instead of
waiting for the next one to begin. `flush=True` (passed when a JSON field
reports `completed`, or the LLM stream ends) forces any non-empty remainder
out as a final sentence regardless of trailing punctuation, since nothing
more is coming that could resolve an ambiguous case either way.

**Release ordering.** Each synthesised chunk is held as `pending` and
released (yielded to the caller) the moment the *next* unit is ready, via a
one-step lookahead (`_offer`). This is not "buffer the whole answer" — it
only ever holds back the single most-recent chunk, and exists solely so the
final chunk (whichever one turns out to be last) can carry the completed
timing dict. In practice the release almost always happens well before the
LLM stream itself is exhausted, because the bridge field's `completed` event
fires — synthesising and releasing the last answer sentence — before the
underlying `llm.stream()` generator has produced its final tokens.

**Context selection** (`_build_context`) finds the turn actually playing at
`position_ms` (falling back to the nearest turn at/before it for a gap or an
out-of-range position), then pulls in the turns within a trailing 45s
window (`CONTEXT_WINDOW_MS`, with a floor of 3 turns —
`MIN_CONTEXT_TURNS` — so a short recent turn doesn't starve the model of
context) and the `SourceRef`s attached to *those specific turns only*. Two
tests assert this by negation, not just presence: a turn from the very
start of a 160-second-spanning test manifest, and its source, must be
provably absent from the context built at a position 90 seconds later.

**Voice selection.** The answer always uses the host (`voice_a`/`voice_b`)
who was speaking at `position_ms` — never a third voice — read straight off
`Turn.speaker`.

**Security posture.** `segment_sources` (email/RSS titles pulled from
`SourceRef`s, third-party-controlled text) is fenced with the same
`INJECTION_WARNING` / `<<<UNTRUSTED_SOURCE_DATA...>>>` markers
`app.pipeline.script_chain._flatten_source_text` uses for the main script
chain, reusing the same constants. The user's own spoken `question` is
lower risk (CLAUDE.md's framing) and is **not** fenced.

## Graceful degradation (CLAUDE.md rule 7)

- **A sentence's TTS fails** → logged, that chunk is dropped (empty audio
  never gets offered as `pending`), and the rest of the answer — later
  sentences, the bridge line — still plays.
  `test_tts_failure_on_one_sentence_does_not_abort_the_answer` asserts this
  with a TTS provider that fails on exactly the first call.
- **The LLM fails before producing anything** → the pipeline yields
  **nothing** — no partial answer exists to salvage, and hanging on a
  stream that was never going to produce audio is worse than an empty one.
  `test_llm_failing_outright_yields_nothing` asserts `[] == list(...)`.
- **The LLM fails after producing a partial answer** (a narrower case the
  brief doesn't explicitly spec) — whatever was already synthesised is
  surfaced rather than discarded, since it's genuinely usable audio; see the
  comment in the `except` block of `answer_question`.
- **API layer**: `app/api/voice.py` wraps the whole stream in a `try/except`
  as a last-resort guard, and always emits an explicit final NDJSON frame
  (`{"kind": "done", ...}`) when the pipeline produced no timing-bearing
  chunk — otherwise a client can't distinguish "the hosts couldn't answer
  that" from a dropped connection.

## The NDJSON framing choice

`POST /voice/ask` returns newline-delimited JSON, one object per line, audio
base64-encoded inline (`StreamingResponse`, media type
`application/x-ndjson`). Chosen over a binary/length-prefixed framing
because:

- it's trivially parsable on both ends with stdlib tooling only — split on
  `\n`, `json.loads` each line — with no custom binary protocol to keep in
  sync between the Swift client and this backend while both are still
  moving;
- one frame carries audio *and* metadata (speaker, text, timing) without a
  second side-channel;
- the ~33% base64 overhead is a real cost on a compressed ~15-70KB sentence
  clip (see the chunk sizes in the live run below), but it is not what the
  budget grades — time-to-first-audio, not total bytes moved.

Unproven / explicitly out of scope: a binary multipart framing that skips
the base64 tax, if bandwidth turns out to matter more than latency once this
runs on a real cellular connection.

## Live verification — real Gemini 2.5 Flash + real Google Cloud TTS

Ran `answer_question` directly (not through the HTTP layer) against
`backend/out/2026-08-21.manifest.json` — a real 44-turn briefing, 524
seconds long, generated from real Gmail/Calendar/RSS in an earlier phase —
via `set -a && . ./backend/.env && set +a`, asking "What does Priya's
rollout plan actually say?" at a position 5s into the inbox segment. Three
runs:

| run | LLM first token | first token → first audio | total | answer (truncated) |
|---|---|---|---|---|
| 1 | 3224ms | 755ms | 4717ms | "Joel, looking through the information we have..." |
| 2 | 2838ms | 635ms | 4312ms | "Joel, looking at the information we have..." |
| 3 | 2536ms | 344ms | 3883ms | "That's a good question, Joel. However, looking..." |

### Budget check

| stage | budget | measured (3 runs) | verdict |
|---|---|---|---|
| transcript → LLM first token | < 1200ms | 2536 / 2838 / 3224ms | **FAILS, consistently, by ~1.3-2s** |
| first token → first audio | < 800ms | 344 / 635 / 755ms | **PASSES all three runs** |

**The budget is not met.** The failure is entirely in
`transcript -> LLM first token`: real Gemini 2.5 Flash on a free-tier AI
Studio key takes 2.5-3.2s to produce its first streamed token for this
prompt, roughly 2-2.5x the 1200ms budget. `first token -> first audio` —
the number this module's own streaming design (sentence splitting +
per-sentence TTS) is actually responsible for — meets budget on every run,
with real synthesised sentence clips (16-75KB of MP3 audio per chunk).

### Where the time actually goes

The `first token -> first audio` gap (344-755ms) is the sum of: time for
the model to stream enough tokens to complete the first JSON string
fragment and cross a sentence boundary, plus one real Google Cloud TTS
`synthesize_speech` network round trip. Both are within budget, and the
variance between runs (344ms vs 755ms) tracks directly with how many tokens
the model needed before the first sentence closed — run 3's first sentence
("That's a good question, Joel.") is much shorter than run 1's, so TTS
started sooner.

The `transcript -> LLM first token` gap is Gemini's own time-to-first-token
for this prompt on the free tier — this pipeline does no work of its own
before calling `llm.stream()` beyond building the prompt string (context
selection, fencing, string formatting), which is sub-millisecond. This is
the same provider `app/pipeline/script_chain.py` already uses for the
non-streaming outline/draft/critique/revise passes elsewhere in the
codebase, so the latency itself isn't new — it's just that the voice
interrupt path is the first place in Mercury where Gemini's
time-to-first-token is graded against a hard, sub-second UX budget instead
of a multi-minute generation budget.

### Note on the grounding answer

All three live runs correctly answered "I don't have that" rather than
inventing details about Priya's rollout plan — the position chosen for this
run landed in a stretch of the real manifest whose actual turn content
didn't mention Priya specifically (unlike the synthetic test fixtures used
in the unit tests, which were built to include her). This is CLAUDE.md rule
3 (ground every claim, or say you don't know) working as intended against
real content, not a bug in the live check.

## What's unproven

- **The 1200ms budget with a paid/production Gemini tier or a different
  region.** The measured 2.5-3.2s first-token latency is specific to a free
  AI Studio key from this machine; a paid tier, a geographically closer
  endpoint, or a smaller/faster model could close some or all of the gap.
  Not tested here.
- **Streaming under real network conditions.** The live run and the NDJSON
  framing were exercised locally (direct pipeline call, and `TestClient`
  for the HTTP layer) — not over a real HTTP connection with cellular
  latency and packet loss, which is where base64 overhead and chunked
  transfer behavior would actually show up.
- **Ducking and mic-tap-to-listening-UI timing** (`<100ms` duck, `<200ms`
  listening UI) are entirely client-side (iOS) concerns outside this
  backend's scope — not measured or claimed here.
- **Speech-end detection (`<600ms`)** is also client-side, not backend.
- **Concurrent voice requests.** The pipeline and endpoint were only ever
  exercised one request at a time; no load testing was done.
- **A genuinely huge answer** (the model ignoring "two to five sentences").
  The sentence splitter and JSON streamer have no upper bound on buffer
  growth; fine for the expected answer length, unverified for a pathological
  one.

## Gotchas

- **The fake had to be taught to read its own input.** `FakeLLMProvider`'s
  voice-answer branch previously returned one fixed canned JSON blob
  regardless of the question asked — exactly the kind of fake this project
  has already been burned by once (a fake that looks reasonable and hides a
  prompt-wiring bug). It now parses the `THE QUESTION:` and
  `SOURCE DATA BEHIND THAT SEGMENT:` sections back out of the formatted
  prompt (including un-fencing the injection-warning wrapper around the
  latter) and produces an answer that actually depends on both — proven by
  `test_ask_answer_depends_on_the_question_asked` at the API layer and
  `test_fake_response_differs_for_different_questions` at the pipeline
  layer.
- **The context-window test fixture had to use realistic time gaps.** An
  early version of the test manifest used tens-of-seconds gaps between all
  turns; with `CONTEXT_WINDOW_MS = 45_000`, that meant the window
  trivially covered the *entire* short manifest regardless of whether the
  windowing logic worked at all — the negative assertion ("the cold-open
  turn from the start must NOT appear in context built 90s later") only
  means anything once the fixture actually spans minutes, the way a real
  briefing does.
- **Sentence-boundary decisions can only look at what's already in the
  buffer.** The splitter deliberately commits to a boundary the moment
  terminal punctuation + whitespace is seen with nothing following yet
  (rather than waiting for `flush`), which is what makes the "first audio
  before stream exhausted" property hold for a single-sentence answer. The
  cost is a very rare mis-split if the *very next* character streamed in
  turns out to be lower-case in a way the abbreviation table doesn't catch
  — accepted as a latency/precision tradeoff, not fixed.
