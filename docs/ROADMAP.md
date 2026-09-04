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

Weeks 8 and 9 were ticked here for about a fortnight while being false in the
way that is hardest to notice: the code existed, was tested, and had no
caller. `fetch_articles`, `build_stories`, `cluster_items` and `score_stories`
were reachable only from the test suite — `score_stories` not even from
there — and `script_chain.py` passed `n_stories=0` with the literal string
"(none — Phase 1 has no news clustering)" into every outline prompt. A green
suite said week 9 was done; the product had never clustered anything. Wired
in `feat/wire-news-pipeline`.

**Gate: you stop opening the app to generate, and no story appears twice.**

Second half: **met**. `backend/tests/test_news_pipeline_e2e.py` drives the
PRD's own acceptance case — a story covered by three newsletters and two news
sources — from real Gmail and RSS normalisation through embedding,
clustering, ranking, selection and prompt assembly, and asserts it reaches
the briefing exactly once, in one segment, with all five sources attributed.

Two things that test does NOT establish, and neither should be claimed until
a live run says otherwise:

- The threshold. 0.78 is tuned for Gemini embeddings and has still never run
  against real feeds; the offline test uses 0.60, the value calibrated for
  `FakeEmbeddingProvider`, and sweeps 0.50-0.70 to show the merge is not
  balanced on one number. What holds is the wiring, not the tuning.
- Whether real newsletters covering the same event actually cluster. The
  fixture is written to be a duplicate. Real ones may sit either side of the
  threshold, and finding out needs a morning of live data.

First half (you stop opening the app) is ADR-010/ADR-011 territory and is
not affected by this branch.

## Phase 3 — Voice interaction — DROPPED (see ADR-009)

Built and merged in full, then removed from the product. It was PRD
priority P2, and it was the only workload requiring a warm server —
dropping it is what makes the free architecture in ADR-008 possible.
Recoverable with `git show c701e4f`.

<details><summary>Original Phase 3 scope</summary>

- [x] SFSpeechRecognizer on-device with tuned endpointing
- [x] Audio ducking, fade not cut
- [x] Join state machine, five states, full visual feedback
- [x] Streaming LLM with full transcript context
- [x] Streaming TTS, playback on first complete sentence
- [x] Bridging line and seamless resume
- [x] Latency instrumentation against the timing budget

Gate was: perceived gap under 2.5s. Server share reached 1642-1977ms
before the feature was dropped.

</details>

## Next — The show (current)

Seven requests from 2026-09-04, reordered once the code was actually read.
This pulls forward two items from Phase 4 (story detail, summary styles) and
three from Phase 5 (briefing history, voice customisation, settings), and adds
three that were not on the roadmap at all: ElevenLabs, word timings, and music.

Three of the seven turned out to be already built or nearly so:

- **R2 cleanup is done.** ADR-011 shipped 30-day retention and
  `delete_briefing` sweeps every key under a briefing id in both storage
  backends. Nothing to build. Note that `retention_days` is already the same
  number as the "no older than a month" archive horizon — they must stay one
  setting, or the archive will offer a day the sweeper has deleted.
- **Article links are already in the data.** `DigestSource` carries `url`,
  `title`, `source_name` and `published_at`, ordered by authority. This is a
  rendering job, not an ingestion one.
- **`list_briefing_ids()` already exists** in both backends and is exposed by
  nothing — the Worker serves `/health`, `/status`, `/briefing/{id}`, `/audio`
  and `/digest`. Browsing previous mornings is one route, not a subsystem.

### Why ElevenLabs comes before the highlighting

Highlighting words as they are spoken needs to know when each word is spoken.
The manifest carries `start_ms`/`end_ms` **per turn** — a whole paragraph —
which is enough to highlight a line and nothing finer.

Google Neural2 can only produce finer timings via SSML `<mark>` plus the
v1beta1 `timepoints` API: inject a mark around every word, reassemble them,
keep them aligned through the stitcher. A pipeline built to be thrown away.
ElevenLabs returns character-level alignment natively from
`/with-timestamps`, and was already the planned upgrade under ADR-006.

So the order is forced. Do ElevenLabs first and the karaoke view is nearly
free; do it second and you build the SSML machinery twice.

### The trap in the music

Every turn's `start_ms` is an offset into the audio file. Prepend eight
seconds of intro music and every timing in the manifest is eight seconds
wrong — every transcript line, every chapter boundary, every story deep-link.
ADR-006 flagged this when ElevenLabs was first planned and it has sat
unaddressed since.

Today the error is nearly invisible. **With word-level highlighting it becomes
glaring**, because the words light up eight seconds ahead of the voice. The
fix is not arithmetic at the call site: the stitcher must treat music as a
track element like any turn, so offsets are computed once by the thing that
already computes them.

### What "play music to buy time" means here

Huxe played music and intro lines because it generated on demand and had two
to four minutes of dead air to fill. **Mercury does not have that problem on
the happy path** — ADR-001 is the whole architecture: the server generates
overnight, and when you open the app the audio is already finished.

So the waiting-room role applies only to the exception path: opening before
the cron ran, or after a failed run. Worth building, but not the reason to do
this work. The reason is production value — a cold open over a bed and a sting
between segments are what make a briefing sound like a show rather than a
text-to-speech read-out. That is what the ten second test measures.

### Tasks

Audio and voice — a chain, each step needs the last:

- [ ] 1. ElevenLabs behind the existing TTS protocol. A second implementation
      file swapped by config, per rule 6; `GoogleTTSProvider` is the model.
      Must return character-level timings, so `TTSProvider` grows a richer
      return type and Google returns `None` for it. Keep Google working — it
      is the fallback when quota runs out mid-run, and rule 7 says the
      briefing still ships.
- [ ] 2. Word timings in the manifest. Schema version bump; the client must
      tolerate their absence, since every briefing already in R2 lacks them.
- [ ] 3. Music as a track element in the stitcher. Intro, outro, inter-segment
      stings, royalty-free and committed as assets. Acceptance: a manifest
      generated with music and one without differ *only* by the offset, and
      every deep-link still lands on the right sentence.
- [ ] 4. The waiting room. Bed plus a short spoken holding line when today's
      briefing is not there yet. Exception path only — a test should assert it
      never appears on a normal morning.

The listening surface:

- [ ] 5. The transcript as a script, not a dump. Speaker-attributed blocks,
      the two hosts visually distinguished, one turn at a time. Independent of
      1-4; can start immediately.
- [ ] 6. Steady highlighting, driven by task 2. Falls back to whole-turn
      highlighting when a manifest has no word data.
- [ ] 7. The segment marker moves below the transcript, centred. It stops
      being a row of tabs at the bottom edge and becomes a position indicator;
      navigation moves into a tap on it.

The browse surface:

- [ ] 8. The digest as boxes on Home — what #17 built, rendered. Story cards
      alongside the play button, each opening to a bullet summary, an image
      and the source row. Presentation, not new data.
- [ ] 9. Article links, properly. `DigestSource.url` is already there. Start
      with in-app `SFSafariViewController` — it keeps the morning ritual
      intact.
- [ ] 10. Browse previous mornings. One Worker route exposing
      `list_briefing_ids()`, plus an archive view. The horizon must be read
      from `retention_days`, never hardcoded to 30.

Control:

- [ ] 11. Settings, built out. Wake time and length exist and are half-wired.
      The rest: voice and provider, briefing length, which feeds and
      newsletters count, timezone, appearance override, retention window, and
      a visible account state for the Google connection — including the token
      expiry that silently broke three mornings. Last on purpose: a settings
      screen is only worth building once there is something worth setting,
      and tasks 1, 3 and 10 each add one.

**Start 1, 5 and 8 in parallel** — provider, player view, new feature module.
None blocks the others, and everything else falls out of them in order.

**Gate:** the ten second test, unchanged. What changes is what the answer is
allowed to be. Right now "voice quality" is a legitimate reason to turn it off
and there is nothing to do about it. After task 1 it stops being an excuse;
after task 3, so does "it sounds like a read-out rather than a show".

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
