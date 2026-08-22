# Phase 2, week 10 — four-pass script chain

Extends `backend/app/pipeline/script_chain.py` from the Phase 1 two-pass
chain (outline → draft) to the full four-pass chain specified in
`docs/ROADMAP.md` Phase 2 week 10: outline → draft → **critique → revise**.
Quality gates encoding CLAUDE.md rules 1–3 are promoted out of
`scripts/spike_04_llm.py` into a real, importable module,
`backend/app/pipeline/quality.py`.

## Files created / modified

- `backend/app/pipeline/quality.py` — **new**. `check_filler_words`,
  `check_monologues`, `check_disagreement`, `check_grounding`,
  `run_all_gates`, all operating on `list[Turn]`.
- `backend/app/pipeline/script_chain.py` — extended with
  `_run_critique_and_revise`, segment-coverage/order and word-budget
  validators (`_has_valid_segment_coverage_and_order`,
  `_is_within_word_budget`, `_is_valid_script`), and a `passes: int = 2`
  keyword-only argument on `generate_script`.
- `backend/tests/test_quality_gates.py` — **new**, 19 tests.
- `backend/tests/test_script_chain_critique.py` — **new**, 12 tests.
- `docs/phase2/script_chain.md` — this file.

`scripts/spike_04_llm.py` and `app/providers/fake.py` were **not** touched —
both were already correct for this work (the fake already plants a
monologue and an over-agreeable exchange and returns canned critique/revise
responses that fix them; the spike already proves the gate logic).

## The four-pass flow

```
outline → draft → critique → revise
```

`generate_script(..., passes=2)` (the **default**) runs outline → draft
only, unchanged from Phase 1. `generate_script(..., passes=4)` runs the
full chain. The default was kept at 2 deliberately: `app/pipeline/
generate.py` and the whole Phase 1 test suite call `generate_script`
without a `passes=` argument and expect exactly the old two-call behaviour.
Changing the default to 4 would have silently doubled the cost and latency
of every existing caller and broken `test_script_chain.py::
test_generate_script_end_to_end_with_fake_provider`, which explicitly
asserts critique/revise are *not* invoked. Flipping the switch for
production briefings is a decision for whoever wires `passes=4` into
`app/pipeline/generate.py`, not something this change makes silently.

`CRITIQUE_PROMPT` and `REVISE_PROMPT` from `app.pipeline.prompts` are used
verbatim, exactly as instructed — this file does not touch `prompts.py`.

### Degradation (CLAUDE.md rule 7)

`_run_critique_and_revise` always returns a usable script:

1. Critique response fails to parse → log, return the draft. Revise is
   never called.
2. Revise response fails to parse → log, return the draft.
3. Revised turns are structurally invalid — empty, missing a canonical
   segment, segments out of order, or outside the ±20% word budget — → log,
   return the draft. Validated with the *same* function used to accept the
   draft (`_is_valid_script`); the revision gets no special pass.
4. Revised turns are valid but score **worse** on `quality.run_all_gates`
   than the draft did → log the before/after issue counts, return the
   draft. This is not hypothetical — the live run below hit it.

A worse script is never produced by this module; at worst it returns
exactly what the draft pass already produced.

## Live verification — real Gemini 2.5 Flash, two runs

Ran the chain step by step (not through `generate_script`, so the critique
JSON itself is visible) against three emails (a rollout-plan sign-off from
"Priya", a 1:1-agenda email from "Sam" that also asks whether he should
join the 9am, and a GitHub notifications digest), two calendar events, and
weather — via `set -a && . ./backend/.env && set +a` then a standalone
driver script. `GeminiProvider` logged token usage per call.

### Run 1 — revision improved the script

Critique findings (verbatim from the model, abbreviated):

- `GENERIC` × 4 — "Setting the tone for a very focused start" (cold_open),
  a clichéd weather-to-work transition (weather), a generic "quiet news
  cycle" remark (world), and a generic closing line (sign_off).
- `UNGROUNDED` × 2 — turn 10 said Priya "flagged it as essential" when the
  source only implies importance; turn 11 called "decisions today
  critical" as if stated, when the source only says the migration window
  is *proposed* for August 27th. Both are real, specific rewrites, not
  padding — this is close to the exact failure mode CLAUDE.md rule 3
  exists to catch.

Gate deltas:

| | draft | revised |
|---|---|---|
| filler | 0 | 0 |
| monologues | 0 | 0 |
| disagreement (calendar/inbox/world) | 1 (`world`) | 1 (`world`) |
| grounding | 13 | 12 |
| **total_issues** | **14** | **13** |

Revision was accepted: it strictly improved (13 ≤ 14). `world` stayed
flagged in both because there was no news data in this fixture — an
honest, unavoidable false-ish positive of the heuristic (see Gotchas).

### Run 2 — revision made the script WORSE, and the guard matters

Second live run (fresh, same inputs), critique findings this time were
sparser — 3 `GENERIC` flags only, no `UNGROUNDED` catch:

Gate deltas:

| | draft | revised |
|---|---|---|
| filler | 0 | 0 |
| monologues | 0 | 0 |
| disagreement (calendar/inbox/world) | 3 (`calendar`, `inbox`, `world`) | 3 (`calendar`, `inbox`, `world`) |
| grounding | 16 | 17 |
| **total_issues** | **19** | **20** |

The revision **regressed by one issue**. This run is the concrete
justification for requirement 3 in the brief ("guard against the revision
making things worse"): had `generate_script` blindly accepted whatever
`revise` returned, this briefing would have shipped a strictly worse
script than the draft already validated. Because `_run_critique_and_revise`
compares `run_all_gates` on both before accepting, this run's revision
would be **rejected** and the draft kept — confirmed by
`test_revise_that_scores_worse_than_draft_falls_back_to_draft`, which
exercises exactly this path deterministically offline.

### Whether revision improved or degraded

Both, on different live runs — **1 for 2**. Small models are not
deterministic editors; the critique pass sometimes finds and fixes a real
issue (run 1: two ungrounded claims genuinely improved), and sometimes
finds only cosmetic "GENERIC" issues, "fixes" them with rewording, and
nets out flat or worse (run 2). This is exactly why requirement 3 (compare
gates before accepting) is not a defensive nicety — on real output, it
fired.

## The disagreement-scope change

`scripts/spike_04_llm.py`'s original `detect_missing_disagreement` (and the
identical logic ported into `quality.check_disagreement`) applied to every
segment with more than one turn. Live output showed this over-firing on
`cold_open`, `weather`, `follow_ups`, and `sign_off` — segments where a
manufactured "challenge" reads as bickering ("Actually, hold on — happy
Friday to you too"), not the substantive tension CLAUDE.md rule 2 is
actually about. `quality.py`'s `_DISAGREEMENT_SEGMENTS` narrows the gate to
`{"calendar", "inbox", "world"}` only, with the reasoning captured in a
comment directly above the constant. The fix is scope, not threshold — the
same challenge-marker list and the same "agreeable with zero challenge
markers" logic still fire exactly as before on those three segments; see
`test_check_disagreement_exempts_cold_open_weather_follow_ups_sign_off` and
`test_check_disagreement_applies_to_inbox_and_world`.

## Cost / latency of two extra passes

Run 1 token usage (from `GeminiProvider`'s logged usage), gemini-2.5-flash:

| pass | prompt tokens | candidate tokens |
|---|---|---|
| outline | 1,403 | 627 |
| draft | 1,364 | 2,743 |
| critique | 3,061 | 705 |
| revise | 3,259 | 2,725 |

Critique + revise adds **3,430 candidate tokens** on top of the **3,370**
that outline + draft already used — roughly a **2× increase in generated
tokens**, and prompt tokens roughly triple (critique and revise both embed
the full draft script plus the full source data again). Run 2's full
four-pass chain took **76.9s** wall clock end to end; two calls
(critique + revise) account for roughly half of that, so critique+revise
adds on the order of **35–40 seconds** and doubles the per-briefing LLM
spend. For a server-side cron generating once per user per morning (ADR-001
— the phone never generates), this is an acceptable trade for the quality
gain when it hits (run 1) and a bounded cost even when it doesn't (run 2,
where the draft ships instead).

## Gotchas

- **The model got the day of the week wrong, twice, independently.** Both
  live runs opened with "It's Thursday, August 21st" for a date that is
  actually a Friday (per the CLAUDE.md system context and the repo's own
  commit history). `quality.check_grounding` correctly flagged `"Thursday"`
  as an ungrounded token in both runs (it appears nowhere in the source
  data), but neither critique pass caught or fixed it — `CRITIQUE_PROMPT`
  asks the model to fact-check against source data, and a wrong weekday
  computed from a date the model itself was given is a blind spot the
  prompt doesn't cover. The grounding gate is the only thing that caught
  this at all, and it only *flags*, it doesn't fix. Worth a follow-up: pass
  the weekday as an explicit string in the prompt rather than trusting the
  model to compute it from an ISO date.
- **The disagreement gate still has real false positives/negatives.**
  Run 2's draft had genuine disagreement in `calendar` and `inbox`
  ("I'd argue...", "But if this upgrade truly...") that the marker list
  didn't catch because the exact phrasing didn't match a marker string.
  This is the same heuristic-not-semantic limitation `spike_04_llm.py`
  already documents; scoping it to three segments reduced false positives
  from the *scope* change but didn't touch this pre-existing precision
  limit.
- **`world` is structurally unwinnable for the disagreement gate on a
  fixture with no news.** With no clustered stories, two hosts riffing on
  "it's quiet out there" cannot produce genuine tension, so `world` gets
  flagged every time regardless of revision. This isn't a bug in the gate;
  it's the gate correctly reporting that the fixture gave it nothing to
  work with. A real day with actual `world` stories should behave
  differently — unproven here since Phase 2 clustering (a parallel
  workstream) wasn't wired into this fixture.
- **Revision is not idempotent-safe against a single opaque check.** Rather
  than trusting "critique found N issues so revision is good," the
  guard reruns all four gates on both scripts independently. That is
  what caught run 2's regression; a naive "did critique run without
  erroring" check would have shipped the worse script.

## What is unproven

- **Production wiring.** `passes=4` was never plumbed into
  `app/pipeline/generate.py` — that file was explicitly off-limits for this
  task (two other agents were working in parallel; the brief's file
  ownership list does not include it). Every real briefing generated today
  still runs the Phase 1 two-pass chain unless a caller explicitly passes
  `passes=4`.
- **`world` segment quality on a real clustered-news fixture.** Both live
  runs used a fixture with `n_stories=0` — Phase 2 clustering lands from a
  parallel workstream. The critique/revise passes have not been exercised
  against a `world` segment carrying two to four real stories with genuine
  editorial tension, which is the primary case rule 2 and the disagreement
  gate are meant to serve.
- **Statistical confidence on "does revision help."** Two live runs is not
  enough to say revision is net-positive; it's enough to say the
  regression-guard requirement in the brief is not theoretical. A larger
  sample across the five regression-set snapshots (CLAUDE.md's "heavy
  meeting day, empty calendar day, high newsletter volume day, breaking
  news day, quiet weekend") would be the next step before trusting
  `passes=4` in production.
- **Cost at scale.** The token/latency numbers above are two single-run
  data points on one short fixture, not an average over the length tiers
  (`express`/`standard`/`full` — 450/1050/2250 target words). A `full`
  length briefing's critique/revise cost will scale with script length,
  roughly linearly, since both prompts embed the whole script.
