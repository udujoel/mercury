# Mercury

Everything that matters. One briefing. Every morning.

Mercury reads your email, calendar, news and social feeds overnight and gives you a single narrated briefing in the morning. Two AI hosts, natural audio, and you can interrupt them with your voice.

Built to replace [Huxe](https://huxe.com), which shut down in May 2026.

## What it does

- Connects to Gmail and Google Calendar (read only)
- Pulls news across your chosen topics, plus Reddit, RSS and Slack
- Clusters everything so the same story never appears twice
- Writes a two-host conversational script with an LLM
- Synthesises it to natural audio with two distinct voices
- Plays it on iOS with live transcript, chapter navigation and voice interrupt

## Repo layout

```
backend/     FastAPI generation pipeline (Python 3.12)
ios/         Native SwiftUI client (iOS 17+)
docs/        PRD and technical blueprint
scripts/     Dev helpers
```

## Status

Phase 0: foundation and dependency spikes.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the full plan.

## Getting started

Backend:

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill in your keys
uvicorn app.main:app --reload
```

iOS:

```bash
cd ios
open Mercury.xcodeproj
```

## Architecture in one line

The server generates. The phone plays. iOS background windows are about 30 seconds and generation takes minutes, so all LLM and TTS work happens server side and the result is pushed to the device.
