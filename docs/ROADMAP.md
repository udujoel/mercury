# Mercury Roadmap

Full detail lives in the PRD. This is the working checklist.

## Phase 0 — Foundation (weeks 1 to 2)

Prove every risky dependency in isolation. Each spike is throwaway. The goal is
knowledge, not code.

- [x] Google Cloud project: enable Gmail API, Calendar API, Cloud TTS —
      `app/ingestion/gmail.py` and `calendar.py` read a real inbox nightly,
      and Google TTS is the working fallback provider
- [x] OAuth consent screen in testing mode, your account as sole test user —
      published; Google read the live policy at joeludu.com/mercury/privacy
- [x] Spike 1 — iOS OAuth via ASWebAuthenticationSession, token to Keychain
- [x] Spike 2 — Gmail fetch, yesterday's messages, plain text extraction
- [x] Spike 3 — Calendar fetch, today plus 72 hours, all calendars
- [x] Spike 4 — Gemini 2.5 Flash, fake data in, two-host JSON script out
- [x] Spike 5 — TTS synthesis and stitching. Never written as a spike; the
      knowledge went straight into `app/synthesis/stitch.py`, which has
      shipped every briefing in R2
- [x] Spike 6 — AVAudioEngine playback with millisecond position at 20 Hz —
      `Core/Audio/PlaybackCoordinator.swift`, `CADisplayLink`-driven
- [ ] Spike 7 — BGProcessingTask on a physical device, triggered via LLDB.
      `Core/Background/BackgroundScheduler.swift` exists and is unit-tested
      against a seam with no `BGTaskScheduler` involved — which is exactly
      what this spike was for and exactly what it does not prove. Left
      unticked deliberately: nobody has watched the task fire on a device,
      and the whole point of a spike is the thing tests cannot tell you

**Gate: Spike 5 must produce audio you would actually choose to listen to.**
If it sounds robotic, change the TTS provider before writing product code.

## Phase 1 — Vertical slice MVP (weeks 3 to 7)

Gmail plus Calendar in, briefing out. No news, no clustering, no automation,
no voice. Resist every temptation to add.

Backend
- [x] CLOSED — FastAPI skeleton. The auth callback was never built and is
      not wanted: ADR-008 replaced the HTTP surface with the cron plus the
      Worker, and onboarding says in as many words that the Google account
      is connected on the server. The app survives as a local dev harness
- [x] Gmail ingestion producing `Item`
- [x] Calendar ingestion producing `CalendarEvent`
- [x] Heuristic priority scoring (no embeddings yet)
- [x] Two-pass script chain: outline then draft
- [x] Parallel TTS with pacing and loudness rules
- [x] Timing manifest generation
- [x] Object storage for audio — R2, live. `briefing.yml` sets
      `STORAGE_BACKEND: r2` on both jobs and has since ADR-008

iOS
- [x] Onboarding: welcome, Google connect, wake time, length
- [x] Home: four-card stack
- [x] Player: content stage, chapter tabs, transport
- [x] AVAudioEngine playback, background audio, lock screen
- [x] Transcript sync from manifest
- [x] SwiftData persistence and resume position

**Gate: you tap the greeting card and within ninety seconds two hosts are
discussing your actual meetings and your actual inbox.**

## Audio identity backlog — CLOSED, absorbed into "The show"

Phase 0's gate passed on Google Neural2 — good, not great. See ADR-006.

This list was staged "do before Phase 3", and Phase 3 was dropped (ADR-009).
Everything on it was done under tasks 1-3 below rather than here; it is kept
so the trail from ADR-006 to the work is readable.

- [x] ElevenLabs Flash v2.5 behind the existing `TTSProvider` protocol,
      one new file in `app/providers/`, no pipeline change — task 1
- [x] A/B the same script through Neural2 and ElevenLabs, listen to both
      cold opens, keep the winner — task 1, and settled again on 2026-09-05
      by measuring the shipped audio when both hosts came out male: the
      briefing's speakers sat at 132Hz and 94Hz against 219Hz for the same
      voice's own preview, which is how the 402 on a library voice was found
- [x] Short intro stinger before the cold open, short outro under the
      sign-off — task 3
- [x] Offset every manifest timing by the intro duration — task 3. Done by
      moving `cursor_ms` before the turn loop rather than correcting numbers
      afterwards, so there is one place that computes offsets
- [x] Duck the music under the first and last turns rather than hard-cutting.
      `stitch._duck` OVERLAYS the tail rather than crossfading or appending:
      both of those move every timing in the manifest, which is ADR-006's
      failure wearing a nicer coat. Overlay leaves `len(combined)` untouched.
      Tested on both halves, because either alone passes for the wrong
      reason — the length must not move AND the audio must actually change

## UI refinement backlog — SUPERSEDED by ADR-012

Every item below describes the dark ambient card deck. ADR-012 replaced that
register with Particle's light editorial one, and the views these refer to —
the translucent deck, the placeholder weather card — no longer exist. Left
unticked rather than deleted, because "we changed our minds" is a more useful
record than a clean list; do not treat them as work outstanding.

Observed while testing the MVP on the simulator, none of it blocking:

Verified 2026-09-07 by grep, not by memory: no `WeatherCard`, `CalendarCard`
or `CardDeck` remains in `ios/Mercury` — the only mentions left are
historical notes in `DesignSystem/Colors.swift` and a fixture's script text.
The loading item is superseded by ADR-001 rather than ADR-012:
`TodaysBriefingCard` absorbs the state and says "Getting today's briefing…",
and there is no ~70-second on-device generation to narrate any more, because
generation happens on the cron. Task 4's waiting room covers the case where
there is nothing to fetch.

- [x] CLOSED — Cards are semi-transparent, so the card behind bleeds through the
      greeting text. PRD 3.3 specifies opaque over true black
- [x] CLOSED — Weather card shows a placeholder "San Francisco" and is not wired to the
      Open-Meteo data the backend already fetches correctly
- [x] CLOSED — Card deck sits small and centred; PRD 3.3 wants roughly 0.62 of screen
      width with the fanned offsets
- [x] CLOSED — Loading state is a bare spinner for the ~70s generation — it should say
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

## Next — The show (built, see task 20-22 for what is not reaching the phone)

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

- [x] 1. ElevenLabs behind the existing TTS protocol. A second implementation
      file swapped by config, per rule 6; `GoogleTTSProvider` is the model.
      Must return character-level timings, so `TTSProvider` grows a richer
      return type and Google returns `None` for it. Keep Google working — it
      is the fallback when quota runs out mid-run, and rule 7 says the
      briefing still ships.
- [x] 2. Word timings in the manifest. Schema version bump; the client must
      tolerate their absence, since every briefing already in R2 lacks them.
- [x] 3. Music as a track element in the stitcher. **Assets exist** —
      nine tracks in `assets/music/`, three sets of intro/outro/interlude,
      generated 2026-09-05 and committed. The set is a saved setting with a
      picker. What remains is the stitcher itself, and the offset rule is
      the whole job. Intro, outro, inter-segment
      stings, royalty-free and committed as assets. Acceptance: a manifest
      generated with music and one without differ *only* by the offset, and
      every deep-link still lands on the right sentence.
- [x] 4. The waiting room. Bed plus a short spoken holding line when today's
      briefing is not there yet. Exception path only — a test should assert it
      never appears on a normal morning.
      **Done.** Entirely on-device, which is forced rather than chosen: it
      exists for the case where the server has nothing to give, so it cannot
      depend on the server giving it something. A bundled bed under
      `AVSpeechSynthesizer` — no network, no ElevenLabs spend, works on a
      plane. The system voice is not Mara; on an exception path a real voice
      saying a true thing beats silence.
      Gated on the STATUS, not the prose: `AppState.lastFailureStatus`
      records the code, and only when the failure left no briefing at all —
      a 404 on the AUDIO of a briefing that exists is a broken object in R2,
      not a morning that never happened, and offering the waiting room for
      it would hide the real fault. `WaitingRoomTests` covers both, plus the
      roadmap's own ask (`testANormalMorningRecordsNoFailure`) and the bed
      actually being in the built bundle — the app-icon lesson as an
      assertion.
      The session uses `.ambient`, so it obeys the Ring/Silent switch. It is
      the one sound in Mercury nobody asked to hear.

The listening surface:

- [x] 5. The transcript as a script, not a dump. Speaker-attributed blocks,
      the two hosts visually distinguished, one turn at a time. Independent of
      1-4; can start immediately.
- [x] 6. Steady highlighting, driven by task 2. Falls back to whole-turn
      highlighting when a manifest has no word data.
- [x] 7. The segment marker moves below the transcript, centred. It stops
      being a row of tabs at the bottom edge and becomes a position indicator;
      navigation moves into a tap on it.

The browse surface:

- [x] 8. The digest as boxes on Home — what #17 built, rendered. Story cards
      alongside the play button, each opening to a bullet summary, an image
      and the source row. Presentation, not new data.
- [x] 9. Article links, properly. `DigestSource.url` is already there. Start
      with in-app `SFSafariViewController` — it keeps the morning ritual
      intact. Note the field is `str | None`, and email sources are
      structurally the None case: a Gmail item carries no public URL. So the
      source row needs a defined appearance for a source you cannot open,
      and for a newsletter-only story that is EVERY row.
- [x] 10. Browse previous mornings. One Worker route exposing
      `list_briefing_ids()`, plus an archive view. The horizon must be read
      from `retention_days`, never hardcoded to 30.

Control:

- [x] 11a. **The settings artifact.** The screen is the easy half. Most of
      what belongs on it — feeds, voices, thresholds, segment budgets — is a
      server-side constant read by a job the phone cannot see, so a setting
      the app writes and the cron never reads is worse than no setting at
      all. This is a small JSON artifact in R2 that the app writes and
      `generate_briefing` reads at the top of a run, with the env vars as the
      fallback when it is absent. Without it, everything in 11b is
      decoration.
- [x] 11b. Settings, built out. Wake time and length exist and are half-wired.
      The rest: voice and provider, briefing length, which feeds and
      newsletters count, timezone, appearance override, retention window, and
      a visible account state for the Google connection — including the token
      expiry that silently broke three mornings. Last on purpose: a settings
      screen is only worth building once there is something worth setting,
      and tasks 1, 3 and 10 each add one.

Added 2026-09-04, after the ElevenLabs go-ahead:

- [x] 12. **Voice chosen in the app, not in the environment.** CLOSED —
      superseded by 12b, which built the picker this said was missing.
      Original text:
      11a landed, so the provider and both voice ids are now settings the
      app writes and the job reads. What is NOT done is the picker — listing
      real voices needs `GET /v1/voices`, which needs the ElevenLabs key,
      and the key stays on the server. The screen takes voice ids as text
      and says so rather than pretending to be a picker. Finishing this
      means a Worker route that proxies the voice list.
- [x] 12b. **A real voice picker.** What remains of 12 once 11a landed: the
      screen takes voice ids as text, which means going to elevenlabs.io,
      finding a voice and copying an opaque string. A picker needs the voice
      library from `GET /v1/voices`, which needs the API key — and the key
      stays on the server, so this is a Worker route that proxies the list,
      not a client-side call.
- [x] 13. **Regenerate against what has arrived since.** Today's briefing is
      made overnight, so by 9am it is describing an inbox that has moved on.
      A "regenerate" action re-runs ingestion NOW and rebuilds the briefing
      from current mail and calendar, rather than replaying the one built at
      04:00.

      Three things make this more than a button:

      - It costs a full generation — LLM, TTS, and with ElevenLabs that is
        real money per press. It needs a confirmation and a daily cap, and
        the cap wants to be visible rather than a surprise.
      - `--force` already exists and regeneration is already idempotent per
        briefing id, so the mechanism is there. What is missing is a
        client-triggered path to it: the Worker is read-only by construction
        and answers 405 to POST, which is a deliberate security property
        (ADR-008) and must not be quietly given up. Most likely shape is a
        `workflow_dispatch` from the app with a narrowly-scoped token.
      - The briefing you are LISTENING to must not be swapped underneath
        you. The same rule the prefetch now follows: publish when the
        current one has finished, not while it is playing.

- [x] 14. **An app icon, and an onboarding worth the first launch.** Mercury
      ships with the default template icon, which is the first thing anyone
      sees and currently says nothing.

      Onboarding exists — welcome, Google connect, wake time, length — but it
      was built in the dark ambient register and predates ADR-012, so it is
      the last screen in the app still wearing the old design. It also asks
      for Google access before saying what Mercury does with it, which is the
      wrong order for the one permission prompt that decides whether the app
      works at all.

      - Icon: a mark that survives being 60pt on a home screen, drawn for the
        light editorial register rather than tinted to match it. It also
        needs a dark and a tinted variant — iOS 18 renders all three.
      - Onboarding onto the design system, and reordered: what it does, then
        why it needs Gmail (read-only, bodies discarded — the privacy policy
        already says this in plain words and the app never repeats it), then
        the permission, then wake time.
      - The empty first morning. There is no briefing until the cron has run
        once, and nothing currently explains that: a new user reaches Home
        and sees a card that fetches nothing.

- [x] 15. **Segments the user can turn off.** Done the expensive way, which
      was the only way: `resolve_segments` computes the list once and every
      pass is handed it — the prompt's segment definitions, the outline and
      turn-list response schemas, the draft's repair check and the
      revision's validator. A segment left on in any one of those is a model
      told to write something the validator then rejects, which is a repair
      loop rather than a setting.
      The validator gained a second question. It asked "is everything
      present?"; it now also asks "is anything here that should not be?",
      because a model handed a narrowed prompt can still write the weather
      and accepting it would be the toggle-that-does-nothing again.
      `cold_open` and `sign_off` are `STRUCTURAL_SEGMENTS` and cannot be
      dropped — a briefing with no greeting starts mid-sentence and one with
      no close just stops — and the iOS screen does not offer them, because
      a switch the server overrides is the same lie as a switch nothing
      reads. Unknown names are ignored rather than raised: the phone writing
      the artifact may be older or newer than the job reading it, and losing
      a morning to an unrecognised segment name is the worse outcome.
      **Found while wiring it:** `music_set` was missing from the Worker's
      `SETTINGS_FIELDS` whitelist, so the picker encoded it, the pipeline
      read it, and the Worker deleted it on every save. The music setting
      has never once applied in production. There is now a test that names
      every field `UserSettings` carries and fails when one is dropped.

**Start 1, 5 and 8 in parallel** — provider, player view, new feature module.
None blocks the others, and everything else falls out of them in order.

**Gate:** the ten second test, unchanged. What changes is what the answer is
allowed to be. Right now "voice quality" is a legitimate reason to turn it off
and there is nothing to do about it. After task 1 it stops being an excuse;
after task 3, so does "it sounds like a read-out rather than a show".

## Next — The loop that actually runs (current)

Written 2026-09-12 from an audit that read production, not the roadmap. Eight
regions, sixteen adversarial verification passes. The headline is uncomfortable:
**"The show" is almost entirely built, and almost none of it is reaching the
phone.** Nothing below is new scope. It is the gap between merged and working.

The evidence, all from the live system on 2026-09-11/12:

- `GET /settings` returns `{}`. Every toggle in 11b, 12 and 15 has only ever
  run through its env-var fallback.
- Every production run since at least 2026-09-10 logs `401 quota_exceeded`
  from ElevenLabs, fails over to Google, and exits 0 green. Runs 34470039964,
  34528479152, 34562691604, 34646633440.
- Every shipped manifest has `words: null` on all 38 turns, because only
  ElevenLabs emits them.
- The four-a-day briefings work (32 in the bucket) and `/status` is green —
  but all 40 recent runs are `event=schedule`. The Cloudflare punctual
  trigger has never dispatched one.
- The inbox segment is 188s of a 490s briefing — 38% — and spends six of its
  fourteen turns discussing a report about the user's own GitHub commits,
  after reading out a EUR 3.15 taxi receipt and before the warning that his
  Binance account is about to be restricted.

### Tasks

- [x] 20. **The ids that predict the wrong briefing.** DONE (#35, #37) —
      and a FOURTH site turned up on the simulator afterwards
      (`BriefingDigest.title`, #45). Original text: #33 changed the id
      shape and two places that PREDICT ids were not updated — neither
      appears in that diff, which is the "newly reachable, not newly
      written" lesson in its purest form.
      - `MercuryApp.swift:163` derives a bare `YYYY-MM-DD` for the
        background prefetch, and its comment argues no round trip is needed.
        R2 has held only `-HHMM` ids since 2026-09-07, so **every nightly
        prefetch has 404ed for five days** — and ADR-001's entire promise is
        that the audio is finished before you wake.
      - `AppState.swift:419` records the pending rebuild under the bare day
        while the job lands `YYYY-MM-DD-HHMM`, so the equality at `:564`
        never holds and the spinner runs until the 20-minute expiry.
      The day-prefix concept is named on the server (`briefing_day`,
      `DAY_PREFIX_LENGTH`) and nowhere on the phone, where it is open-coded
      once in `ArchiveView.swift:168`. Name it once and the third site stops
      being invisible.

- [ ] 21. **Word timings that survive the cache.** `TurnEntity`
      (`PersistenceModels.swift:66`) has no `words` field, so highlighting
      degrades to whole-line on every manifest that comes back from
      SwiftData — offline, relaunch, and the notification tap. Task 6 works
      only on a live fetch, and nothing on screen distinguishes the two.
      `BriefingManifest` also still has no `schema_version` although the
      digest and settings artifacts both carry one; task 2's "schema version
      bump" was never done.

- [~] 22. **DECIDED 2026-09-13: ElevenLabs stays.** No paid plan, and none
      is needed — the failover is per-run instance state, so the next run
      after the quota resets uses ElevenLabs again with no action.
      WHAT REMAINS is the second half, still worth doing: the iOS client
      does not decode `degraded_sources`, so nothing on the phone can say
      the voice is not the one you configured. Original text: The code is
      right and the account is out of credits. Either buy Starter or set
      `TTS_PROVIDER=google` and stop the roadmap claiming a voice that does
      not ship. Either way the daily silent narrator-swap must stop being a
      green run: `degraded_sources` is computed and written on every run and
      the iOS client does not decode it, so the one field that could say
      "this morning is not what you configured" is dropped on the floor.
      Note `elevenlabs_tts.py:232` special-cases the 402 that cost a day in
      September and not the 401 that fires every morning now.

- [x] 26. **The briefing has to END.** DONE (#37). Original text: Asked for 2026-09-12 after listening
      to one all the way through. When the narration finishes, the podcast
      does not stop: the lock screen keeps the card with a play button and
      the app stays on the player screen. Three separate defects on one path,
      and each is a one-liner that was never written:
      - `PlaybackCoordinator.finish()` tells `NowPlayingService` rate 0.0,
        which is exactly how iOS spells PAUSED. `NowPlayingService` has no
        `clear()` at all, and nothing sets `playbackState = .stopped`, so the
        system has been told the briefing is paused mid-way forever.
      - Nothing calls `setActive(false)` on the audio session on the finish
        path — `setActive(true)` appears five times in the app and its
        opposite zero times — so Mercury keeps audio focus and the Now
        Playing slot after it has stopped making sound.
      - `HomeView:264` observes `isPlaying` and only ever navigates TO the
        player (`if isPlaying { showPlayer = true }`). There is no edge back,
        so the sign-off leaves you on a dead player screen.
      Ends with: the card disappears from the lock screen, the session is
      released, and Home is on screen with the desk. Replay must still work
      — `finish()` deliberately keeps the file, and clearing Now Playing must
      not become unloading the audio.

- [x] 23. **CI that runs the suites nobody runs.** DONE — `ios.yml` and
      `worker.yml`. Note the cost: macOS runners bill at 10x, so the iOS
      job is path-filtered to `ios/**`. Original text: There is no iOS job and no
      Worker job. `backend.yml` filters on `backend/**`, which `worker/**`
      does not match, and 18 Swift test files run only when someone
      remembers. The worker test written specifically to stop the next
      `music_set`-class bug is itself run by nothing — the guard against the
      failure class is an instance of it.

- [x] 24. **`follow_ups` is built from the literal string "(none)".** DONE.
      `script_chain.py:1347` hardcodes it, exactly as `n_stories=0` once was.
      The segment is a full citizen everywhere else — enum, label, prompt,
      budget, validator-required, and a user-facing toggle reading "Where you
      owe someone a reply". Every briefing therefore contains a follow-ups
      segment written from nothing. This is task 16's territory: "what needs
      a reply" IS this segment, and it should be built before 16 rather than
      beside it.

- [x] 25. **Render what already arrives.** DONE — the newsletters half.
      `DigestStory.topics` stays unrendered on purpose; see the revision
      note on task 17 and docs/LOG.md. Original text: The backend has shipped
      `digest.newsletters` since PR #17 and `DigestStory.topics` since the
      enrichment pass; iOS decodes both (`Digest.swift:27`, `:101`) and no
      view reads either. A large part of task 17 is rendering data already
      crossing the wire every launch. Cheapest item in the audit.

**Gate:** one morning where the phone has the audio before the alarm, the
narrator is the configured one, and the transcript highlights per word after
a cold launch. Today none of those three is true, and every one of them is
marked done.

## Next — The digest worth reading (queued)

"The show" made the audio good. This phase is about the half you LOOK at, and
it starts from a complaint: the desk lists stories, and a list is not a
picture of your morning. Three goals, in the order they matter.

**Goal 1 — the inbox, triaged rather than listed.** After reading it you
should know what arrived, what needs you, and what can wait, without opening
Gmail to check. Today's `inbox` segment reads the top few emails and
summarises the rest as "plus eleven others", which is a count, not a triage.

**Goal 2 — newsletters as research, not as excerpts.** A newsletter is a
POINTER to a subject someone thought was worth your time. Mercury currently
repeats what it said; it should take the topic, look it up elsewhere, and
come back with what it found. The value is in the second step.

**Goal 3 — a morning you can walk back through.** A one-line calendar at the
top of Home, and tapping a day takes you to that day's briefing and desk.
The archive already holds this; it is behind a screen nobody opens.

### The constraints that actually bite

Write these down before the tasks, because each one has already caught
something in this project:

- **Rule 5 vs. research.** Raw bodies are ephemeral. Newsletter TOPICS must
  be extracted during the run and the bodies discarded — so the research
  step works from an extracted topic and its own sources, never from a
  stored email. Anything that wants a body later is a design error.
- **Rule 3 vs. "dig into them".** Every claim in a findings block is
  grounded in a source the user can open. A researched paragraph with no
  citation is exactly the failure the critique pass exists to catch, and it
  is worse here than in the audio because it LOOKS authoritative on a page.
- **Rule 6 vs. Groq.** A research provider goes behind a protocol like every
  other, in one swappable file. Groq's free tier is a candidate, not an
  architecture — and a free tier that a CI IP gets blocked from is a lesson
  this project has already paid for once (see the ElevenLabs 401).
- **Rule 7.** Research is the most failure-prone thing here: it is network,
  third-party and slow. A newsletter section that cannot be researched
  degrades to the summary it has, and the briefing still ships.
- **Cost and time.** Generation takes 2-4 minutes by design (CLAUDE.md);
  the run measured on 2026-09-05 took 423 seconds with ElevenLabs and the
  full four-pass chain, which is the number to budget against. Research is N more
  LLM calls on a budget that is currently free, so the task list has to say
  how many and be able to cap them.

### Tasks

- [ ] 16. **Inbox triage that answers a question.** Replace "top emails plus
      a count" with a structured read: what needs a reply, what is waiting on
      someone else, what is informational, what is noise. The digest carries
      the buckets; the audio names the first two and the count of the rest.
      The bar: after listening you can decide whether to open Gmail before
      lunch, and be right.
- [x] 17. **Topics extracted from the whole inbox, not just newsletters.**
      DONE. The extraction was rewritten for SUBJECTS (2026-09-13) and the
      outlet section now groups by them. Original text:
      HALF DONE. The cluster-led half shipped: "More than one outlet"
      on Home names the stories several outlets independently carried,
      which is "what subjects arrived and who covered each" answered
      from data that already exists. What remains is the SUBJECT
      extraction — the half that needs a prompt and schema change, and
      the half task 18 depends on. See docs/LOG.md.
      Widened 2026-09-12: newsletters AND any other news the mail carries —
      a press release, a forwarded article, a product announcement. The desk
      gets a topic-led section rather than a sender-led one: what subjects
      arrived this morning, and which senders covered each.
      Two thirds of this is already running and invisible. The backend has
      shipped `digest.newsletters` since PR #17 (44 entries in the live
      2026-09-11 digest) and `DigestStory.topics` since the enrichment pass;
      iOS decodes both (`Digest.swift:27`, `:101`) and no view reads either.
      So this is: invert `DigestNewsletter.story_ids` through
      `DigestStory.topics` into topic → senders, widen the source filter
      past `_is_newsletter`, and render it. See task 25.
      **REVISED 2026-09-12, after checking the live digest.** The join works
      — 107 resolutions across 44 newsletters, 32 topics reached — but the
      topics are the wrong GRAIN. `prompts.py` asks for "one to three
      lowercase single words", so what comes back is `ai`, `society`,
      `history`: categories, not subjects, with `ai` alone spanning 16
      stories. A section keyed on those says "AI — 11 senders", barely
      better than the list it replaces, and gives task 18 nothing to
      research. The extraction has to change too — subjects, not tags — so
      this is a prompt and schema change rather than a rendering job. Only
      40 of 209 stories carry topics at all (`DIGEST_MAX_ENRICHED = 40`).
      See docs/LOG.md.
- [ ] 18. **Research each topic independently, with Gemini.** The point of
      the feature, in the user's words: "make sure the story is rich and
      informed, not just what's in the email." A newsletter is a POINTER to
      a subject someone thought was worth your time; Mercury currently
      repeats what it said. For the top N topics from 17, go and find out
      what else is true, and come back with findings the email did not
      contain.
      **Gemini, not Groq** (changed 2026-09-12 at the user's direction).
      That is the better call for a reason worth writing down: Gemini is
      ALREADY this project's LLM — `GEMINI_API_KEY` is in the environment,
      `GeminiProvider` is the working implementation, and its free tier has
      been carrying every generation for months without once hitting the
      datacenter-IP block that made the ElevenLabs free tier unusable from
      CI. Adding Groq would have meant a second vendor, a second key and a
      second free tier to be surprised by.
      Still behind a `ResearchProvider` protocol (rule 6) — the protocol is
      not about Groq, it is about not welding the research step to whoever
      is cheapest this quarter.
      Constraints that bite, all of them already paid for once here:
      - **Rule 5.** Bodies are ephemeral. Research works from an extracted
        TOPIC and its own sources, never from a stored email. Anything that
        wants a body later is a design error.
      - **Rule 3.** Every finding carries a citation the user can open. A
        researched paragraph with no source is worse on a page than in
        audio, because it LOOKS authoritative.
      - **Rule 7.** Research is network, third-party and slow — the most
        failure-prone thing in the pipeline. It degrades to 17's extraction
        and the briefing still ships.
      - **Budget.** The 2026-09-05 run took 423 seconds. Research is N more
        calls on top, so N and a wall-clock cap are part of the task, not a
        later optimisation.
- [x] 19. **The calendar strip.** DONE — verified on the simulator, where
      the first cut auto-played instead of browsing. Original text: One line at the top of Home, a week at a
      time, today emphasised. Tapping a day loads that day's briefing and
      desk — the archive's job, moved to where people will actually use it.
      Days with no briefing are visibly empty rather than tappable-and-dead.

**Gate:** the ten second test has an equivalent here, and it is the same
shape. Open the desk cold and look at it for ten seconds. If you would still
open Gmail to find out what actually arrived, write down why in one sentence
before doing anything else — and categorise it as triage, research, or
navigation.

## Next — What the product review found (2026-09-14)

Eight independent reviews of Mercury as a thing used at 7am rather than as a
codebase. Full evidence in `docs/REVIEW-2026-09-14.md`; these are the tasks it
produced, ranked by what they buy per hour spent.

- [x] 27. **Split the newsletter flag.** DONE (#51 audio half, #55 desk half). The AUDIO half
      landed: a bank alert is no longer pinned LOW and barred from the
      actionable segment — `detect_urgent` lifts money/account/security mail
      to HIGH with `requires_response`, and it is deliberately narrow because
      the wider rule (`requires_response`: addressed to you, contains a "?")
      is what a phishing mail satisfies by construction.
      WHAT REMAINS is the DESK half: bank balances still reach the browse
      surface and are still summarised by an LLM and kept for thirty days.
      The plumbing for it — a per-member gate covering `digest_members`, the
      newsletters shelf and `is_digest_story` — is on the branch
      `feat/transactional-mail-off-the-desk`, needing only a detector.
      Reuse `gmail.detect_urgent`'s shape rather than its list: PATTERNS not
      fixed phrases (the first cut matched "account restricted" and missed
      "your account HAS BEEN restricted"), and remember that a newsletter
      reporting someone else's breach is not your emergency. Original text: `detect_newsletter`
      (`gmail.py:184`) fires on `List-Unsubscribe`, which every bank, payment
      app and password-reset mailer sets. Five of the eight reviews traced
      their worst finding to this one predicate: bank balances rendered as
      journalism on the browse desk for 30 days, LLM write-ups spent on PR
      bots, security warnings pinned to LOW forever, all six inbox slots won
      by machines. `is_newsletter` keeps deciding editorial treatment; a new
      `is_transactional` decides desk eligibility and excludes an item from
      `digest_members`, `stories_to_enrich` and `research_subjects`. Pair it
      with an escalation list — restricted, suspended, unauthorised, verify,
      failed payment, unusual sign-in — that can lift an account notice to
      HIGH. This is the single highest-value change on the board.

      DESK HALF DONE (#55). Live inbox 2026-09-20: 13 messages flagged
      `is_newsletter` and eligible for the 30-day desk. After the gate, 6
      are gated — three GitHub notification mails, GTBank "Transaction
      Notification", a Google "Security alert" (also escalated to URGENT),
      Trading212 "Deposit successful" — and all five real publications are
      kept. The gate covers `digest_members`, the newsletters shelf and
      `_newsletters_for_extraction`.

      TWO STILL SLIP THROUGH and that is the deliberate trade: a Google
      account-data notice and a Qoder billing notice, both from `noreply-`
      addresses. The first cut caught them with `noreply@` / `no-reply@`
      sender rules, and review showed what that cost — those two strings
      are ALREADY in `NEWSLETTER_SENDER_PATTERNS` as evidence a message IS
      a newsletter, so two functions in one file drew opposite conclusions
      from one substring, and `noreply@medium.com` and a Reuters digest
      were gated along with the bank. `noreply@` means BULK, which
      journalism and receipts both are, so it discriminates nothing.
      Recall on two billing notices is the cheaper thing to lose.

      Three tiers now, because one list was again answering two questions:
      `FINANCIAL_SENDER_DOMAINS` (an explicit provider list — the only tier
      that can escalate to urgent), `TRANSACTIONAL_SUBJECT_SIGNALS`
      (anchored on a possessive or a completed action) and
      `TRANSACTIONAL_SENDER_SIGNALS` (shapes a publisher does not use).

      The half that was NOT in this description, and is the more serious
      one: `detect_urgent` refuses anything flagged `is_newsletter`, so the
      same `List-Unsubscribe` misclassification made bank alerts
      structurally incapable of being urgent. The category named above as
      the whole point of the escalation list could never reach it.

      The exemption is keyed on `is_financial`, NOT on `is_transactional`,
      and the first cut got this badly wrong. Four subject signals were
      also `URGENT_PATTERNS` — "security alert", "verify your", "unusual
      sign-in", "payment failed" — so one match both pulled a message off
      the desk and promoted it into the action-required list. Four of five
      realistic publications came out HIGH, including the PR's own
      counter-example. And because escalated newsletters sort first, they
      ate the six-email selection cap and were then dropped from OTHER
      MAIL, so the inbox segment read out NOTHING. For a newsletter only
      the SUBJECT is searched now: a bank states the consequence in the
      subject line, a digest mentions someone else's breach in paragraph
      three.

- [ ] 28. **Two briefings a day, and count what they cost.** Four a day put
      Google TTS at ~88% of its million free characters and Actions at ~82%
      heading to the 2,000 cap, quadrupled publisher blocking, and multiplied
      the repetition — with the digest carrying 209 stories and the audio
      covering four, a midday regeneration re-narrates the same items under a
      different headline. Both exhaustions are silent: TTS drops turns and
      exits 0, and when Actions minutes run out the WATCHDOG stops too,
      because it is itself an Actions workflow. Cut the crons to two, sum
      `len(turn.text)` per run and log it, guard the push trigger on
      `ios.yml` and `backend.yml` so a branch with an open PR is not tested
      twice, and correct the two cost comments that still price the old
      cadence.

- [!] 29. **WITHDRAWN — this task contradicts an explicit instruction.**
      It proposed setting `TTS_PROVIDER: google` in `briefing.yml`. The
      owner's instruction of 2026-09-13 was: "The elevenlabs is still the
      default, whenever the api is reset it should be restored. No
      adjustments was requested for this part." Pinning the workflow to
      Google would stop ElevenLabs being restored when the quota resets,
      which is precisely what was asked for. The current behaviour — try
      ElevenLabs, fail over to Google, self-heal next month — IS the
      requested behaviour.
      WHAT IS STILL WORTH DOING is the honesty half, task 22/33: record the
      failover in `degraded_sources` and decode it on the phone, so a
      briefing narrated by the fallback voice says so instead of looking
      like a normal morning. Original text: Every
      run calls ElevenLabs and gets a 401; the free tier is 10,000 credits a
      month against ~6,300 characters per briefing. What "resets with no
      action" produces in practice is one morning a month where the voice
      changes partway through and the rest of the turns lose word timings.
      Set `TTS_PROVIDER: google` in `briefing.yml`'s env block. The provider
      stays behind the protocol, exactly as rule 6 wants — the cron simply
      stops calling it. NOTE: this is a WORKFLOW-level change only. The
      default in `config.py` stays ElevenLabs, per the owner's decision of
      2026-09-13 (task 22), so a local run with credits still uses it.

- [x] 30. **Connect the ranker to the reader.** DONE (#58). `score_stories` accepts
      `topic_centroid` and `followed_entities` and no caller passes either
      (`generate.py:579`), so `interest` is the constant 0.5, its 0.20 weight
      cancels, and with 153 of 160 stories single-outlet the cluster term is
      constant too. The world segment is `0.12 x cluster + 0.08 x recency` —
      a recency sort. On 2026-09-13 the two stories that reached the audio
      were the two most recently published of 209, and "Ask HN: how can I
      browse HN in dark mode?" beat a Russian strike on a Ukrainian train by
      six minutes. `topic_centroid()` exists and is tested. Call it.

      There was NOWHERE FOR A PERSON TO SAY what they were interested in,
      which is why no caller ever passed one — not in `UserSettings`, not
      on the phone, not in an environment variable. The parameter was
      waiting on a feature that had never been built, which is a different
      failure from a caller that forgot.

      `UserSettings` gains `interests` and `followed_entities`;
      `generate_briefing` embeds the interests and hands `score_stories` a
      normalised centroid. Empty stays the default and is a deliberate
      no-op, but the run log now states every morning that the term is
      inactive — a silent no-op is how this survived two phases. THE
      SETTING IS STILL EMPTY in production: the wiring is live end to end
      (Worker whitelist, iOS model, backend), and the behaviour changes
      the day someone fills it in.

      Review of #58 caught the `music_set` bug happening again, verbatim:
      the Worker's `SETTINGS_FIELDS` whitelist had no entry for either
      field, so a save returned 200 and dropped them, and the feature
      could not have activated on the deployed path at all. Worse, the
      safety net would have MISREPORTED it — "no interest topics
      configured" is indistinguishable from the user having set none.
      `test_settings_fields_parity.py` now fails when any `UserSettings`
      field is missing from that whitelist; it catches `music_set`
      retroactively too.

- [~] 31. **A freshness floor, and a watchdog for all four slots.** HALF DONE (#59). The
      `00:12` cron generates a briefing named for the NEW day, so all five of
      the Worker's morning checkpoints see "this date has a playable
      briefing" and stand down, the 05:40 alarm included. Masked today only
      by GitHub running that cron four hours late; the 25 October DST change
      unmasks it. `dayState` should take a freshness floor and count a
      briefing as covering the morning only if its `-HHMM` is at or after
      ~03:00 local. Separately, three of the four daily slots have no retry
      and no check — the 2026-09-13 evening run died with a segfault and
      nothing reported it. Add `--max-age-hours` to `app.cli check`.

      DONE: the freshness floor, on the two morning checks. `app.cli
      check --max-age-hours 9` catches the case "a briefing exists for
      today and every run after it failed" — the 2026-09-20 shape, where
      the last good briefing was 20:05 and the two runs after it failed
      on a Gemini spend cap. That is 9.8 hours by the 05:52 check.
      Staleness gets exit code 5 rather than 1, because 1 sends the
      operator to regenerate and there is already a briefing.

      NOT DONE, and the first cut got this badly wrong: extending the
      watchdog to the other three generation slots. It added crons at
      13:42, 19:42 and 01:42 UTC, and every one of them would have stood
      down at the 03:00-12:00-local guard and exited 0 GREEN without
      running the check — a sweep of all 598 IANA zones found none where
      all three execute. "A watchdog for every slot" that was a watchdog
      for the same one slot as before; built and never called, in the
      change written to catch that.

      And they cannot be afforded anyway. `docs/REVIEW-2026-09-14.md`
      prices this repo at ~1,950-2,000 charged minutes against a 2,000
      cap, and its own finding is that when Actions minutes run out the
      watchdog stops too. Buying evening coverage by risking the alarm
      AND generation is the worst trade available; that needs task 28's
      cadence cut first.

      `test_watchdog_schedule.py` now fails if a cron is added that the
      guard would stand down, and if the floor is tightened to where a
      healthy morning trips it or loosened to where the incident does
      not.

      STILL OPEN: the `dayState` half. A 00:12 briefing is 5.7 hours old
      at the 05:52 checkpoint, so it passes a 7-hour floor while not
      covering the morning at all — that is a different question
      ("does this cover the morning?") from the one the floor answers
      ("has generation stopped?"), and it belongs in the Worker, where
      `dayState` should require an `-HHMM` at or after ~03:00 local.

- [x] 32. **The hosts must not relay a security instruction.** DONE (#57). The fence
      stops the model OBEYING an email; it does nothing about it faithfully
      RELAYING one, and relaying is the more dangerous half because the
      hosts strip the sender address, the headers and the link. Live
      2026-09-13-1835: "It advises changing your password... This is a
      critical security step to protect your account." The selection is
      attacker-controlled — `classify_priority` gives HIGH plus
      `requires_response` to anything addressed to the user containing a "?"
      — and `check_grounding` makes it WORSE, scoring a phishing claim as
      more grounded, not less. Add a `RELAYED_INSTRUCTION` critique category
      and a gate in the shape of the existing four, and change the segment
      contract so the hosts name the sender's domain and stop.

      All three landed. `check_relayed_instructions` flags a turn carrying
      BOTH a security action and an endorsement — reporting that a message
      asks for something stays allowed, because that is the segment's job;
      what is caught is the hosts adding their own weight to it.

      One thing the description did not anticipate. `total_issues` treats
      every finding as one point, so a revision that dropped the phishing
      endorsement and picked up two clumsy sentences scored WORSE and was
      discarded. The relay count is therefore decided BEFORE the score and
      overrides it, and a relay surviving both draft and revision is logged
      at ERROR. Task 27 also made this more urgent rather than less: account
      and security mail can now escalate past the newsletter floor, so the
      path from "a stranger sends a well-worded email" to "the hosts read it
      out as urgent" got shorter today.

- [ ] 33. **Decode `degraded_sources` on the phone.** The manifest has
      carried it since the schema was written and `BriefingManifest.swift`
      does not declare it, although its doc comment claims the mirror is
      exact. On 2026-09-01, 09-02 and 09-03 the OAuth refresh token was
      expired and all three briefings shipped with no inbox and no calendar
      — "which is most of the point" per the run log — and the app presented
      them as normal mornings. This is the second half of task 22.

- [ ] 34. **Continuity between briefings.** Cheaper now than when this was
      written: #51 gives every newsletter story a stable `subject`, which is
      the join key a "since we last spoke" pass needs and did not have.
      Original text: No briefing knows what the last
      one said. Mercury went from one a day to four without acquiring the
      concept of "what have I already told you", which is upstream of the
      repetition, the 86%-identical consecutive decks, the wasted characters
      and the reason a second briefing feels like a worse version of the
      first rather than an update to it. "Since we last spoke at midday, two
      things moved." The most upside of anything on this list, and the
      cheapest way to find out whether four a day is worth its cost.

DROP, with reasons, from the review:

- Task 21's word-timings half — no briefing has ever shipped word timings,
  and the only provider that produces them 401s every morning.
- `StoryCards.swift` — 158 lines nothing renders, including a second copy of
  a count bug already fixed once.
- Phase 4's new sources and Phase 5's transcript search — the problem is the
  denominator, not the numerator: 209 stories in, four out.
- Attention on cross-source dedup — it fires 8 times in 5,590 stories. The
  code is correct; the premise that sources overlap is not.
- Storage cost work — R2 is at 4% of tier. The cost table belongs on
  characters and charged minutes, which is where both real cliffs are.

- [x] 36. **A census of every stage, on every run.** DONE (#53). Four times
      a finished, tested feature ran zero times in production and nothing
      said so — `n_stories=0`, `music_set`, `topic_centroid`, and the
      newsletter segment for sixteen consecutive briefings. Every one was
      green in CI and invisible in the log, because a stage that produces
      nothing logs nothing and reads exactly like a stage with nothing to
      do. `app/pipeline/census.py` records what each stage took in and gave
      back, warns on any stage with input and no output, and writes the
      finding to the GitHub job summary as well as the log. It never fails
      a run. CLAUDE.md's "check what CALLS the new code" is advice to
      whoever remembers; this is the same check run by the machine.

      Review of #53 found the instrument had the bug it was built to catch:
      `report()` sat on the happy path, so the run that produced nothing at
      all was the one run with no census line. It is a context manager now.

      Also worth not rediscovering: a FAKE can be the coverage hole.
      `FakeLLMProvider` had no branch for the extraction prompt, so it
      returned `{}` and every offline briefing had an empty newsletter
      segment — including the acceptance test written to catch exactly the
      double coverage that review later found. Green, and proving nothing.

- [x] 35. **Newsletters are bundles, so take them apart.** DONE (#51, wired
      in #53 — it had never once run in production; see below). The
      owner's 2026-09-15 spec: extract the news inside each newsletter,
      research it, and build one narrative across them, with a rapid-fire
      tail for other mail and urgent items escalated.

      This also settles a question the 2026-09-14 review got backwards. It
      found cross-source dedup firing 8 times in 5,590 stories and concluded
      the sources do not overlap. They overlap enormously — clustering was
      embedding a WHOLE newsletter, so two editions that both lead with the
      Fed never match because each embedding is dominated by its own
      mixture. Live: 49 newsletters produced 117 items across 96 subjects,
      ten multi-outlet, the top one carried by nine. **Do not delete
      cross-source dedup.** Reconsider it once items, not bundles, are what
      cluster.

      Two things worth not rediscovering. The CAP was found by listening,
      not by reading: all ~60 subjects in the prompt produced twelve
      consecutive turns of one-sentence items, and `rank()` had ordered them
      correctly — ordering a firehose still gives you a firehose. And the
      injection fence does not stop a body IMPERSONATING A STRUCTURE: a
      newsletter whose body contained the block delimiter forged a second
      outlet, which mattered because outlet count is both the ranking signal
      and the sentence the hosts are told to say.

## Phase 4 — Depth (weeks 16 to 19)

- [ ] Week 16 — DeepCast with web-grounded research and chapter navigation
- [ ] Week 17 — story detail, summary styles (ELI5, 5Ws, translate), ask
- [ ] Week 18 — Reddit, Bluesky, Slack follow-ups segment. Genuinely
      unbuilt: `backend/app/ingestion/` holds calendar, drops, gmail,
      images, rss and weather, and nothing else. The README used to
      advertise Reddit and Slack as live sources and no longer does (#37);
      the PRD still does, and is the spec rather than a claim about today
- [ ] Week 19 — endless mode, better scrubbing, podcast and newsletter
      subscriptions. These are the six things Huxe users asked for and never got.

## Phase 5 — Polish and release (weeks 20 to 23)

- [~] Briefing history with transcript search — HISTORY is done (the
      archive, plus the calendar strip on Home). SEARCH is not: there is
      no `.searchable` anywhere in the app. Transcripts are already in
      SwiftData, so searching the CACHED briefings is small; searching
      all 30 retained days needs a Worker route
- [ ] CarPlay and StandBy widgets
- [ ] Home and Lock Screen widgets
- [ ] Shortcuts and Siri intents — unbuilt, and NOT blocked: App
      Intents need no paid membership
- [~] Voice and host name customisation — VOICE is done (12b's picker).
      HOST NAMES are not: nothing in the app writes `host_a_name`, and
      it is not a cosmetic field — `quality.py:190` builds the
      speakable-name allow-list from it, so renaming a host changes
      what the critique pass accepts
- [ ] Reliability: token refresh, backoff, partial-failure degradation
- [ ] Cost instrumentation
- [ ] TestFlight
