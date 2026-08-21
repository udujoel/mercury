# Mercury — Claude Code working context

Read this first. It is the standing brief for anyone (human or agent) working in this repo.

## What we are building

A native iOS app that reads the user's email, calendar, news and social feeds
overnight and produces one narrated two-host audio briefing each morning.
Modelled on Huxe (shut down May 2026), with the story-clustering approach from
Particle and the audio-generation rules from NotebookLM.

Full spec: `docs/PRD.md`. Roadmap and phase gates: `docs/ROADMAP.md`.

## Core architecture decision

**The server generates. The phone plays.**

iOS background execution windows are roughly 30 seconds. Generation takes two
to four minutes. So all ingestion, clustering, LLM and TTS work happens in the
FastAPI backend on a server-side cron, and the finished audio plus a timing
manifest are pushed to the device. The iOS app is a well-engineered player and
control surface with local caching. Do not try to move generation on-device.

## The pipeline

```
ingest → normalise → embed → cluster → rank → script chain → TTS → stitch → manifest
```

Every source normalises into the same item schema before anything else happens.
That is what lets an email newsletter and a news article about the same event
deduplicate against each other. See `backend/app/core/schemas.py`.

## Non-negotiable rules

1. **Write clean dialogue in the LLM script.** No filler words, no "um", no
   "you know". Naturalness comes from the TTS model, not the transcript. This
   is the single most important lesson from the NotebookLM team. If the audio
   sounds robotic, change the TTS provider, do not add filler to the prompt.

2. **Hosts must not always agree.** Tension is the product. At least once per
   segment one host challenges or reframes the other.

3. **Ground every factual claim in the source data.** If it is not in the
   provided items, the hosts do not say it. The critique pass exists to catch
   this.

4. **Read-only scopes only.** `gmail.readonly`, `calendar.readonly`. The app
   must be structurally incapable of sending, deleting or modifying anything.

5. **Raw email bodies are ephemeral.** Held only for the duration of a
   generation run. Discarded once the script exists. Only the script, audio and
   source references persist.

6. **Every provider is behind a protocol.** LLM, TTS and embeddings each have a
   single swappable implementation file. Free tiers get deprecated.

7. **Graceful degradation.** Any source can fail and the briefing still ships,
   minus that segment. Never fail the whole run because Slack timed out.

## The ten second test

The primary quality gate. Listen to the first thirty seconds of every generated
briefing during development. If you would have turned it off, write down why in
one sentence before doing anything else. Categorise as content selection,
script quality, voice quality or pacing. Fix the most frequent category first.

## Current phase

Phase 0 — foundation and dependency spikes. See `docs/ROADMAP.md`.

Do not start Phase 1 product code until the Phase 0 gate is met: the TTS spike
must produce audio you would actually choose to listen to.

## Conventions

- Python 3.12, FastAPI, async throughout. Ruff for lint, format on save.
- Swift 5.9+, SwiftUI, iOS 17 minimum, `@Observable` view models, async/await.
  No completion handlers.
- Secrets in `.env` locally and in the host's secret store in production.
  Never in the repo, never in `UserDefaults` on device — Keychain only.
- Conventional commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`.
- Every ingestion source gets a fixture file in `backend/tests/fixtures/` so
  the pipeline can be tested without live API calls.

## Regression set

Keep five saved input snapshots for testing prompt changes:
heavy meeting day, empty calendar day, high newsletter volume day,
breaking news day, quiet weekend. Regenerate against all five after any prompt
change and listen to each cold open.
