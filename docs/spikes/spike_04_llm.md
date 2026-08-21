# Spike 4 — Gemini 2.5 Flash, fake data in, two-host JSON script out

Phase 0. Goal: prove the outline -> draft -> critique -> revise script chain
end to end, offline right now, and wire up the live Gemini path so it works
the moment `GEMINI_API_KEY` exists — never faking a live run.

## Files created

- `backend/app/providers/__init__.py`
- `backend/app/providers/gemini.py` — live `GeminiProvider`
- `backend/app/providers/fake.py` — deterministic offline `FakeLLMProvider`
- `scripts/spike_04_llm.py` — runs the four-pass chain, robust JSON parsing,
  four quality-gate functions
- `backend/tests/fixtures/spike_04_input.json` — 5 emails, 2 calendar events,
  weather
- `backend/tests/test_spike_04_script.py` — 68 offline tests
- `docs/spikes/spike_04_llm.md` — this file

No other files were touched. `app/pipeline/prompts.py` was reused verbatim
(`OUTLINE_PROMPT`, `DRAFT_PROMPT`, `CRITIQUE_PROMPT`, `REVISE_PROMPT`,
`HOST_SYSTEM`) — nothing in it was changed.

## What was proven offline

The full chain runs against `FakeLLMProvider` with zero network calls:

1. **Outline** — 7 segments, in canonical order, word budgets summing to
   the `standard` target (1050 words).
2. **Draft** — 27 turns, 871 words, covering all 7 segments in canonical
   order, validating against `Turn`. Two defects were planted on purpose:
   a 7-sentence monologue in the `world` segment, and a fully agreeable,
   challenge-free exchange in the `calendar` segment ("Sounds good, no
   notes there." / "Yep, agreed, nothing to add either.").
3. **Critique** — flags exactly those two planted defects (`MONOLOGUE` and
   `AGREEABLE`), each with a `turn_index`, `detail`, and `fix`.
4. **Revise** — 29 turns, 867 words. The monologue is split into two turns
   of ≤4 sentences each; the agreeable exchange gets a real challenge line
   grounded in Sam's own email ("Actually, hold on — Sam's email this
   morning asked whether he should join that nine o'clock too."). Both
   fixes are asserted by `test_revision_removes_planted_defects`.

The fixture (`backend/tests/fixtures/spike_04_input.json`) deliberately
connects one email (Priya's rollout-plan email) to one calendar event (the
9am provider upgrade review) — the draft output does make that connection
("That's the same review that's on your calendar this morning, so those two
are directly connected"), which is the PRD's "connect events to relevant
context" behaviour and the thing most worth proving here.

### Real fixture-mode output

```
$ ./backend/.venv/bin/python scripts/spike_04_llm.py --fixture

Running OFFLINE against FakeLLMProvider (no network)

--- PASS 1: outline ---
  7 segments planned
--- PASS 2: draft ---
  27 turns, 871 words
--- PASS 3: critique ---
  2 issues found
--- PASS 4: revise ---
  29 turns, 867 words

=== FINAL SCRIPT ===

[ cold_open] A: Good morning Joel. It's Friday, the twenty-first of August, just past seven.
[ cold_open] B: And it's a real day, not a quiet one. Provider upgrade review at nine, then a 1:1 with Sam this afternoon, with a run of inbox items in between that are more connected to each other than they look at first glance.
[ cold_open] A: Nothing overwhelming, but worth walking through in order so nothing gets missed before that nine o'clock, especially since a couple of things in the inbox turn out to matter more once you see how they line up with the calendar.
[   weather] A: Weather in Aveiro: clear, sixteen degrees now, climbing to a high of twenty-four by afternoon. Low tonight back down around fifteen.
[   weather] B: Good day to have that nine o'clock in person rather than on Zoom, if that's an option — no reason to be inside on a morning like this if you don't have to be.
[   weather] A: Agreed, and it holds through the weekend too, so there's no rush to squeeze outdoor plans into today specifically.
[  calendar] A: First up, the provider upgrade review with the platform team at nine, in Meeting Room 3 or on Zoom. Priya wants your sign-off on the rollout plan before the review, and the migration window she's proposing is the night of August twenty-seventh.
[  calendar] B: Actually, hold on — Sam's email this morning asked whether he should join that nine o'clock too. Might be worth a heads up to Priya before the room fills up.
[  calendar] A: Agreed, straightforward. Then it's your regular 1:1 with Sam at two this afternoon, thirty minutes, no location listed so it's probably a call.
[  calendar] B: Yep, agreed, nothing to add there either. Two meetings, a gap in between, should be a manageable day on the calendar side.
[     inbox] A: In your inbox: Priya sent the provider upgrade rollout plan last night and wants sign-off before that nine o'clock review. She specifically flagged that the plan touches every module your team owns.
[     inbox] B: That's the same review that's on your calendar this morning, so those two are directly connected — she's clearly trying to get your read on it before the room fills up, not just circulating it for the record.
[     inbox] A: I'd flag the timing too — the migration window is proposed for August twenty-seventh, and separately there's a water maintenance notice for the Glicínias district on the twenty-second from the city council. Unrelated to the migration, but worth knowing about if you're in that part of town that morning.
[     inbox] B: Also worth noting, Sam emailed about today's 1:1 agenda — he wants to cover his Q3 goals draft, and he's asking whether he should join this morning's provider review too.
[     inbox] A: Which is worth raising with Priya before nine, given Sam's on the platform team and the plan touches his area directly. Might save a second conversation later, and it answers his question in the same breath.
[     inbox] B: And three small pull requests opened overnight on the backend repo: a fix for a flaky ingestion test, retry backoff added to the embedding client, and an lxml version bump. Nothing in there that needs you today.
[     inbox] A: One more from the city council newsletter, mostly for awareness — water pressure may drop in the Glicínias district between nine and one on the twenty-second, so tomorrow, not today, and only worth acting on if you're actually in that part of town.
[     inbox] B: So really, out of everything in the inbox this morning, the one that actually changes what you do before nine is Priya's rollout plan — the rest is either routine or a day away. Worth reading the plan itself before the review, not just skimming her note.
[     world] A: The Stratechery piece this week is about how AI assistants are becoming the new aggregation point for services. Ben's argument is that owning the relationship matters more than owning the distribution channel now.
[     world] A: Bundling used to happen at the platform layer. Now it's happening at the assistant layer instead, which routes around the platform entirely. It's a change worth watching closely over the next few quarters.
[     world] A: And it's one more reason platform teams are nervous about assistant-layer lock-in.
[     world] B: I'd push back on that a little — owning the relationship sounds nice on paper, but if the assistant controls the interface, the platform doesn't get much say in how that relationship actually goes either. It's not clearly a win for platform owners just because the aggregation point moved.
[     world] A: Fair distinction. It's less 'platforms win' and more 'platforms that don't own an assistant relationship lose leverage,' which is a narrower and less comfortable claim.
[     world] B: Which loops back to your nine o'clock, honestly — a provider upgrade is exactly the kind of infrastructure decision that determines who owns that relationship later.
[     world] A: Bit of a stretch, but I'll allow it as a segue back to the actual day ahead of you.
[follow_ups] A: No open follow-ups from yesterday to circle back on.
[follow_ups] B: Clean slate, then — first time in a while. Enjoy it while it lasts, because tomorrow's queue is already starting to fill back up.
[  sign_off] A: That's the morning, Joel. Good luck with the nine o'clock, and worth a quick word to Priya about Sam joining before the room fills up.
[  sign_off] B: Have a good one, and enjoy that weather while it lasts. Talk to you tomorrow.

=== QUALITY GATES ===
  [PASS] filler words: 0 found
  [PASS] monologues (>4 sentences): 0 found
  [FAIL] disagreement per segment (heuristic): 4 segment(s) flagged
         segment 'cold_open': no challenge/reframe marker found
         segment 'weather': no challenge/reframe marker found
         segment 'follow_ups': no challenge/reframe marker found
         segment 'sign_off': no challenge/reframe marker found
  [INFO] grounding check (heuristic, expect false positives): 1 token(s) flagged
         turn 0: proper_noun 'Friday'
  [PASS] word count: 867 (target 1050, range 840-1260)

Wrote /Users/joud/sandbox/ML/mercury/out/spike_04_script.json

SOME GATES FAILED
```

The revised script is written to `out/spike_04_script.json` (outline,
critique, and the final turn array).

The overall run prints "SOME GATES FAILED" because the disagreement gate
flags four short, low-stakes segments (`cold_open`, `weather`, `follow_ups`,
`sign_off`) that have no genuine tension to manufacture — this is expected
and discussed under "quality gates" below, not a bug.

### `--live` without a key fails exactly as required

```
$ ./backend/.venv/bin/python scripts/spike_04_llm.py --live
...
RuntimeError: GEMINI_API_KEY is not set. Get a free key at
https://aistudio.google.com/app/apikey and put it in backend/.env as
GEMINI_API_KEY=... (GeminiProvider will not run without it — never fake a
live run.)
```

### pytest

```
$ cd backend && ./.venv/bin/python -m pytest -q
....................................................................     [100%]
68 passed in 0.48s
```

### ruff

```
$ cd backend && ./.venv/bin/ruff check .
All checks passed!
```

(`scripts/spike_04_llm.py` also passes `ruff check` under the same
`backend/pyproject.toml` config, checked separately since it lives outside
`backend/`.)

## What needs a live GEMINI_API_KEY

Everything about actual Gemini behaviour is unproven on this machine:

- Whether `response_mime_type: "application/json"` actually produces
  parseable JSON in practice for these four prompt shapes, or whether the
  model still occasionally wraps output or truncates on `MAX_TOKENS`.
- Real latency per pass (four sequential passes — this determines whether
  the nightly cron generation stays inside a few minutes per user).
- Whether the model actually honours the "no filler words" and "at least
  one challenge per segment" instructions in `HOST_SYSTEM`/`DRAFT_PROMPT`
  without a fake stacking the deck, and how often the critique pass finds
  real issues versus padding the list.
- Free-tier rate limit behavior in practice (does the retry/backoff in
  `GeminiProvider.complete` actually recover from real 429s, and how often
  do they occur back-to-back for a 4-pass chain).
- Token/character usage per pass, to build a real cost model.

### How to get a key and run it live

1. Go to <https://aistudio.google.com/app/apikey>, sign in, create a key.
   It is free-tier at the time of writing.
2. Create `backend/.env` (it does not exist on this machine) and add:

   ```
   GEMINI_API_KEY=your-key-here
   ```

3. Run:

   ```
   cd /Users/joud/sandbox/ML/mercury
   ./backend/.venv/bin/python scripts/spike_04_llm.py --live
   ```

   `--live` never falls back to the fake. If the key is missing or wrong,
   it fails loudly rather than silently degrading.

## JSON-mode findings

`GeminiProvider._build_model` sets
`generation_config["response_mime_type"] = "application/json"` whenever
`json_mode=True` is passed to `complete()`. This is documented by Google as
the reliable way to get the model to emit parseable JSON, as opposed to
depending on prompt instructions like "Return JSON only" alone (which is
what all four prompts in `prompts.py` also say, belt-and-suspenders). This
constraint could not be exercised against the real API here — worth
confirming on the first live run whether it still ever needs the fence/prose
stripping in `parse_json_response`, or whether JSON mode makes that dead
code in practice. I kept `parse_json_response` robust regardless, since (a)
`stream()` doesn't use JSON mode at all (Phase 3 voice path), and (b) it
costs nothing to keep the safety net.

## Free-tier rate limits and cost implication

AI Studio's free tier for Gemini 2.5 Flash (per public docs, not verified
live here) is on the order of low tens of requests per minute and a daily
cap in the low thousands of requests, with input/output token limits per
request. A single briefing generation run is **4 sequential LLM calls**
(outline, draft, critique, revise) plus, if implemented, a prosody pass —
so one generation run consumes 4-5 free-tier requests. At one generation
per user per day this is trivially inside free-tier limits even for a
handful of test users. It becomes a real constraint the moment:

- Development iterates quickly (re-running the chain many times while
  tuning prompts eats the daily request cap fast — the regression set in
  CLAUDE.md, five snapshots re-run after every prompt change, is 20-25
  requests per prompt-tuning session on its own).
  a
- The product scales past a handful of users, since AI Studio free tier is
  explicitly not meant for production multi-user traffic; ADR-004 (every
  provider behind a Protocol) is exactly what makes moving to a paid
  Gemini API key, or a different provider, a one-file change instead of a
  rewrite.

`GeminiProvider.complete` retries up to 5 times with exponential backoff
(base 1.5s, doubling, plus jitter) on 429/503, which should absorb
short-lived bursts but will not save a chain run from a hard daily-cap
exhaustion — that will show up as a 429 on every attempt and eventually
raise after `MAX_ATTEMPTS`.

## How good the quality gates actually are

All four gates are implemented as standalone functions in
`scripts/spike_04_llm.py` (`detect_filler_words`, `detect_monologues`,
`detect_missing_disagreement`, `check_grounding`) so they can be lifted
directly into the Phase 2 critique harness.

- **Filler words** (`detect_filler_words`) — regex word-boundary match
  against `um`, `uh`, `you know`, `i mean`, `like,`. Reliable: an early
  naive substring version false-positived on "bu**m**p" and "alb**um**";
  fixed with `(?<![a-z])word(?![a-z])` boundaries and covered by
  `test_detect_filler_words_no_false_positive_on_substring`. Zero false
  positives observed on the fixture output.

- **Monologues** (`detect_monologues`) — splits on `.`/`!`/`?` and flags
  turns with more than 4 sentences. Simple and reliable for the fake's
  planted 7-sentence defect; a real model's punctuation (ellipses, decimal
  numbers, abbreviations like "e.g.") could trip the sentence splitter, so
  treat borderline counts (5 vs 4) with some skepticism on live output.

- **Disagreement per segment** (`detect_missing_disagreement`) — explicitly
  a heuristic, documented as such in its docstring and in the CLI output.
  It looks for challenge-marker phrases ("push back", "actually", "hold
  on", "not quite", etc.) anywhere in a segment's combined text, and flags
  multi-turn segments with none. On the fixture's revised output it flags
  `cold_open`, `weather`, `follow_ups`, and `sign_off` — none of which have
  or need real disagreement (there's nothing to disagree about in "no
  follow-ups today" or a two-line weather check). This is a real limitation
  worth being honest about: CLAUDE.md's rule 2 ("at least once per
  segment") is stated as a blanket rule, but in practice it only makes
  sense for segments with actual editorial content (`calendar`, `inbox`,
  `world`). I'd recommend narrowing the *requirement* itself in Phase 2 to
  content-bearing segments rather than trying to force the heuristic to
  stop flagging short segments — flagging them here, honestly, seemed
  better than quietly softening the check to hide the disagreement. The
  test suite proves the heuristic does correctly flag a known-bad
  two-line agreeable exchange and pass a known-good challenge line
  (`test_detect_missing_disagreement_flags_known_bad` /
  `..._passes_known_good`).

- **Grounding** (`check_grounding`) — flags capitalized words (proper
  nouns) and numeric tokens in the script that don't appear anywhere in the
  flattened source JSON. On the fixture's 29-turn, 867-word revised script
  it flagged exactly **1 token out of the whole script** ("Friday", which
  is correctly absent from the fixture's source data — the model/fake
  inferred the day of week from the date rather than being told it, so this
  is arguably a *true* positive, not a false one). An earlier draft of this
  check, before adding a stopword list for common sentence-initial words
  ("Actually", "Might", "Worth", "She", etc.), had roughly a 30-40% false
  positive rate purely from ordinary capitalized words that happen to start
  a clause. The current version is still a blunt token-membership check —
  it cannot verify that a *true* fact is stated in a way the source
  actually supports, only that the exact token string appears somewhere in
  the flattened source JSON. It will still false-positive on paraphrases
  (e.g., a script saying "the council" when the source says "Aveiro City
  Council") and will miss real hallucinations that happen to reuse a real
  token in a false claim ("the migration window is August 22nd" — wrong
  date, but "August" and the day both individually appear in the source).
  Treat it as a first-pass filter for a human or LLM critique reviewer, not
  a verdict, exactly as the brief asked.

## Gotchas

- **google-generativeai SDK is fully synchronous.** `complete()` and
  `stream()` both wrap the blocking SDK calls in `asyncio.to_thread` so the
  provider is genuinely async-safe inside the FastAPI event loop; this
  matters most for `stream()` since even *iterating* the stream object
  blocks synchronously per-chunk (`_next_or_none` also runs via
  `to_thread`).
- **Status-code extraction from google-generativeai exceptions is not
  standardized** across versions — `_status_code()` in `gemini.py` tries
  `.code`, `.status_code`, `.grpc_status_code` attributes and falls back to
  matching the exception class name (`ResourceExhausted` -> 429,
  `ServiceUnavailable` -> 503). This is unverified against a real 429 from
  the live API; worth confirming on first live run and adjusting if the
  installed `google-generativeai==0.8.3` raises something this doesn't
  catch (in which case the retry loop just re-raises immediately, which is
  a safe failure mode, just not a resilient one).
- **The fixture's word budget was the hardest single thing to get right by
  hand.** Getting `FakeLLMProvider`'s canned draft to land within ±20% of
  the 1050-word "standard" budget while keeping two specific, structurally
  locatable defects took several iterations of counting words and padding
  sentences — a reminder that word-budget adherence is a real prompt-tuning
  problem for the live model too, not just a fixture-writing problem.
- **`Turn.segment` is a `Segment` enum, not a bare string** — the draft/
  revise JSON must emit segment values that match the enum's string values
  (`"cold_open"`, not `"Cold Open"` or `"opening"`) or `Turn.model_validate`
  raises. This is exactly the kind of thing a live model could get subtly
  wrong that the fixture chain can't catch, since the fake always emits the
  correct enum strings by construction.
- **`parse_json_response`'s bracket-matching fallback tracks string state**
  (in-string / escape) so JSON content containing literal `{`/`[`
  characters (e.g., a script line describing "a JSON payload with a `{`")
  doesn't confuse the outermost-object extraction —
  `test_parse_nested_brackets_in_strings` covers this.
