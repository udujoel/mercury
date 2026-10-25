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
twice a day: in the morning by the Cloudflare Worker (03:40 UTC, with retries),
and again from a GitHub schedule entry at 12:12 UTC, which GitHub usually runs
a few hours late.

- Generation runs on GitHub Actions; audio and manifests live in Cloudflare R2
  behind a Worker (ADR-008)
- The phone shows a desk of the morning's stories with who covered each, a
  story page in Particle's shape, a calendar strip for earlier mornings, and
  says when a briefing was made without a source or read by the backup voice
- Phase 3 (voice interaction) was built and then **dropped** — ADR-009

**Operating constraint.** The repository is private on GitHub Free, so every
briefing, every CI run and the watchdog share 2,000 Actions minutes a month.
They ran out on 2026-09-22 and no briefing was made for eight days; see
`docs/LOG.md` (2026-10-11) and ROADMAP task 45 before adding CI jobs,
especially macOS ones, which bill at 10x.

Current work is in [docs/ROADMAP.md](docs/ROADMAP.md), in the section marked
"(current)". [docs/LOG.md](docs/LOG.md) is the running record of decisions,
production findings and what turned out not to be true.

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

About 1,100 backend, 420 iOS and 83 Worker tests as of 2026-10-11. CI runs
each suite once per PR, and again on `main` after a merge (`backend.yml`,
`ios.yml`, `worker.yml`, path-filtered). Two backend tests need the live
network and a writable temp directory, so they fail inside a sandbox
(`test_net_guard.py::test_public_url_allowed` and the audio check in
`test_generate_e2e.py`); run them unsandboxed before treating either as a
real failure.

Green tests are necessary, not sufficient. Every phase so far has had a
feature the suite certified and the product never ran, so check a change on a
simulator and in a production run's logs, not just in CI.

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
18 ADRs.
