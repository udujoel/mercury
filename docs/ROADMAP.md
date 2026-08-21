# Mercury Roadmap

Full detail lives in the PRD. This is the working checklist.

## Phase 0 — Foundation (weeks 1 to 2)

Prove every risky dependency in isolation. Each spike is throwaway. The goal is
knowledge, not code.

- [ ] Google Cloud project: enable Gmail API, Calendar API, Cloud TTS
- [ ] OAuth consent screen in testing mode, your account as sole test user
- [x] Spike 1 — iOS OAuth via ASWebAuthenticationSession, token to Keychain
- [x] Spike 2 — Gmail fetch, yesterday's messages, plain text extraction
- [x] Spike 3 — Calendar fetch, today plus 72 hours, all calendars
- [x] Spike 4 — Gemini 2.5 Flash, fake data in, two-host JSON script out
- [ ] Spike 5 — TTS synthesis and stitching, `scripts/spike_05_tts.py`
- [ ] Spike 6 — AVAudioEngine playback with millisecond position at 20 Hz
- [ ] Spike 7 — BGProcessingTask on a physical device, triggered via LLDB

**Gate: Spike 5 must produce audio you would actually choose to listen to.**
If it sounds robotic, change the TTS provider before writing product code.

## Phase 1 — Vertical slice MVP (weeks 3 to 7)

Gmail plus Calendar in, briefing out. No news, no clustering, no automation,
no voice. Resist every temptation to add.

Backend
- [ ] FastAPI skeleton: health, auth callback, generate
- [ ] Gmail ingestion producing `Item`
- [ ] Calendar ingestion producing `CalendarEvent`
- [ ] Heuristic priority scoring (no embeddings yet)
- [ ] Two-pass script chain: outline then draft
- [ ] Parallel TTS with pacing and loudness rules
- [ ] Timing manifest generation
- [ ] Object storage for audio (Cloudflare R2)

iOS
- [ ] Onboarding: welcome, Google connect, wake time, length
- [ ] Home: four-card stack
- [ ] Player: content stage, chapter tabs, transport
- [ ] AVAudioEngine playback, background audio, lock screen
- [ ] Transcript sync from manifest
- [ ] SwiftData persistence and resume position

**Gate: you tap the greeting card and within ninety seconds two hosts are
discussing your actual meetings and your actual inbox.**

## Phase 2 — News, clustering, automation (weeks 8 to 11)

- [ ] Week 8 — RSS aggregation, trafilatura extraction, schema normalisation
- [ ] Week 9 — embeddings, agglomerative clustering, threshold tuning,
      cross-source dedup between newsletters and news
- [ ] Week 10 — ranking function, word budgets, critique and revision passes
- [ ] Week 11 — server cron, APNs silent push, BGProcessingTask prefetch,
      wake notification, show notes

**Gate: you stop opening the app to generate, and no story appears twice.**

## Phase 3 — Voice interaction (weeks 12 to 15)

- [ ] SFSpeechRecognizer on-device with tuned endpointing
- [ ] Audio ducking, fade not cut
- [ ] Join state machine, five states, full visual feedback
- [ ] Streaming LLM with full transcript context
- [ ] Streaming TTS, playback on first complete sentence
- [ ] Bridging line and seamless resume
- [ ] Latency instrumentation against the timing budget

**Gate: perceived gap under 2.5 seconds.**

## Phase 4 — Depth (weeks 16 to 19)

- [ ] Week 16 — DeepCast with web-grounded research and chapter navigation
- [ ] Week 17 — story detail, summary styles (ELI5, 5Ws, translate), ask
- [ ] Week 18 — Reddit, Bluesky, Slack follow-ups segment
- [ ] Week 19 — endless mode, better scrubbing, podcast and newsletter
      subscriptions. These are the six things Huxe users asked for and never got.

## Phase 5 — Polish and release (weeks 20 to 23)

- [ ] Briefing history with transcript search
- [ ] CarPlay and StandBy widgets
- [ ] Home and Lock Screen widgets
- [ ] Shortcuts and Siri intents
- [ ] Voice and host name customisation
- [ ] Reliability: token refresh, backoff, partial-failure degradation
- [ ] Cost instrumentation
- [ ] TestFlight
