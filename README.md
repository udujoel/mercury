# Mercury

Everything that matters. One briefing. Every morning.

Mercury reads your email, calendar and news overnight and gives you a single
narrated briefing in the morning: two hosts, a real conversation, and a desk
of the stories behind it.

Built to replace [Huxe](https://huxe.com), which shut down in May 2026.

## What it does

- Connects to Gmail and Google Calendar, **read-only** and structurally
  incapable of sending, deleting or modifying anything
- Pulls news from RSS feeds across your chosen topics
- Clusters everything, so a story three newsletters and two wires all covered
  reaches you once, with all five attributed
- Writes a two-host conversational script with an LLM, then critiques and
  revises it against the source material
- Synthesises it to audio with two distinct voices, over music
- Plays it on iOS with a live transcript, chapter navigation, a lock-screen
  card, and a browsable desk of the morning's stories

Raw email bodies are held only for the length of a generation run and
discarded once the script exists. Only the script, the audio and source
references persist.

## Status

**Running in production.** Phases 0–2 are complete and a briefing is generated
four times a day, every day.

- Generation runs on GitHub Actions; audio and manifests live in Cloudflare R2
  behind a Worker (ADR-008)
- 32 briefings in the bucket as of 2026-09-12, four per day
- Phase 3 (voice interaction) was built and then **dropped** — ADR-009

Current work is in [docs/ROADMAP.md](docs/ROADMAP.md); the honest gap between
what is merged and what actually reaches the phone is the phase named "The
loop that actually runs". [docs/LOG.md](docs/LOG.md) is the running record of
decisions and why they were made.

## Repo layout

```
backend/     Generation pipeline (Python 3.12) — ingest, cluster, script, TTS, stitch
ios/Mercury/ Native SwiftUI client (iOS 17+)
worker/      Cloudflare Worker: serves R2 to the phone, and triggers generation
site/        The public page and privacy policy
assets/      Music beds — three sets of intro, outro and interlude
docs/        Roadmap, ADRs, deployment, phase write-ups
scripts/     Spikes and dev helpers
```

## Architecture in one line

**The server generates. The phone plays.** iOS background windows are about
30 seconds and generation takes two to four minutes, so every LLM and TTS call
happens server-side and the finished audio plus a timing manifest are fetched
by the device (ADR-001).

There is no always-on backend. Generation is a scheduled batch job that runs
for a few minutes a day, and a Worker in front of a private bucket is the only
thing listening the rest of the time.

## Getting started

**Backend** — the generation pipeline:

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill in your keys
```

Generate a briefing the way production does:

```bash
cd backend && ./.venv/bin/python -m app.cli generate
```

The FastAPI app (`uvicorn app.main:app --reload`) is a **local development
harness only**. ADR-008 replaced it with the cron plus Worker; nothing deploys
it.

**iOS**:

```bash
open ios/Mercury/Mercury.xcodeproj
```

You will need `ios/Mercury/Secrets.xcconfig` — copy
`Secrets.example.xcconfig` and fill it in. See
[docs/RUNNING_ON_YOUR_IPHONE.md](docs/RUNNING_ON_YOUR_IPHONE.md).

**Worker**:

```bash
cp worker/.dev.vars.example worker/.dev.vars   # fill in the real values
scripts/push_worker_secrets.sh
cd worker && npx wrangler deploy
```

The Worker has no CI deploy. Merging is not shipping — a route that exists on
`main` stays 404 in production until someone runs `wrangler deploy`.

## Tests

```bash
cd backend && ./.venv/bin/python -m pytest -q && ./.venv/bin/ruff check .
cd worker && npm test
cd ios/Mercury && xcodebuild test -scheme Mercury \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

670 backend · 335 iOS · 76 Worker. Two backend tests reach the live network
and fail without it.

## The rules that shape this codebase

Read [CLAUDE.md](CLAUDE.md) before changing anything. The short version:

1. Clean dialogue in the script — naturalness comes from the TTS model, never
   from filler in the transcript
2. The hosts must not always agree
3. Every factual claim is grounded in the source data
4. Read-only scopes only
5. Raw email bodies are ephemeral
6. Every provider sits behind a protocol
7. Any source can fail and the briefing still ships

Design decisions and their rationale: [docs/DECISIONS.md](docs/DECISIONS.md),
13 ADRs.
