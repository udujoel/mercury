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
- [~] FastAPI skeleton: health and generate done; auth callback not yet
- [x] Gmail ingestion producing `Item`
- [x] Calendar ingestion producing `CalendarEvent`
- [x] Heuristic priority scoring (no embeddings yet)
- [x] Two-pass script chain: outline then draft
- [x] Parallel TTS with pacing and loudness rules
- [x] Timing manifest generation
- [~] Object storage for audio — local filesystem for now, R2 not yet

iOS
- [x] Onboarding: welcome, Google connect, wake time, length
- [x] Home: four-card stack
- [x] Player: content stage, chapter tabs, transport
- [x] AVAudioEngine playback, background audio, lock screen
- [x] Transcript sync from manifest
- [x] SwiftData persistence and resume position

**Gate: you tap the greeting card and within ninety seconds two hosts are
discussing your actual meetings and your actual inbox.**

## Audio identity backlog (do before Phase 3)

Phase 0's gate passed on Google Neural2 — good, not great. See ADR-006.

- [ ] ElevenLabs Flash v2.5 behind the existing `TTSProvider` protocol,
      one new file in `app/providers/`, no pipeline change
- [ ] A/B the same script through Neural2 and ElevenLabs, listen to both
      cold opens, keep the winner
- [ ] Short intro stinger before the cold open, short outro under the sign-off
- [ ] Offset every manifest timing by the intro duration — `synthesize_briefing`
      currently assumes turn zero starts at 0ms, so transcript sync silently
      drifts by the length of the intro if this is missed
- [ ] Duck the music under the first and last turns rather than hard-cutting

## UI refinement backlog

Observed while testing the MVP on the simulator, none of it blocking:

- [ ] Cards are semi-transparent, so the card behind bleeds through the
      greeting text. PRD 3.3 specifies opaque over true black
- [ ] Weather card shows a placeholder "San Francisco" and is not wired to the
      Open-Meteo data the backend already fetches correctly
- [ ] Card deck sits small and centred; PRD 3.3 wants roughly 0.62 of screen
      width with the fanned offsets
- [ ] Loading state is a bare spinner for the ~70s generation — it should say
      what is happening

## Phase 2 — News, clustering, automation (weeks 8 to 11)

- [x] Week 8 — RSS aggregation, trafilatura extraction, schema normalisation
- [x] Week 9 — embeddings, agglomerative clustering, threshold tuning,
      cross-source dedup between newsletters and news
- [x] Week 10 — critique and revision passes; quality gates
- [x] Week 11 — server cron, APNs silent push, BGProcessingTask prefetch,
      wake notification, show notes

**Gate: you stop opening the app to generate, and no story appears twice.**

## Phase 3 — Voice interaction (weeks 12 to 15)

- [x] SFSpeechRecognizer on-device with tuned endpointing
- [x] Audio ducking, fade not cut
- [x] Join state machine, five states, full visual feedback
- [x] Streaming LLM with full transcript context
- [x] Streaming TTS, playback on first complete sentence
- [x] Bridging line and seamless resume
- [x] Latency instrumentation against the timing budget

**Gate: perceived gap under 2.5 seconds.** Server share measured at
1642-1977ms to first audio. The client stages need a physical device —
`SFSpeechRecognizer` and real ducking do not run in the simulator. Free
provisioning covers this; see docs/RUNNING_ON_YOUR_IPHONE.md and ADR-007.

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
