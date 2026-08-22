# Phase 3 — the iOS Join flow

Voice interrupt implementation for Mercury Phase 3 (PRD 3.5). Scope: `ios/Mercury/**` only.

## Acceptance gate — both commands exit 0

```
$ xcodebuild -project Mercury.xcodeproj -scheme Mercury \
    -destination 'generic/platform=iOS Simulator' -quiet build
$ echo $?
0
```

Clean build produces no output under `-quiet` (no errors, no warnings) and
exits 0.

```
$ xcodebuild -project Mercury.xcodeproj -scheme Mercury \
    -destination 'platform=iOS Simulator,name=iPhone 17' test
...
Test Suite 'BackgroundSchedulerTests' passed at 2026-08-22 9:09:50.876 PM.
Test Suite 'BinarySearchTests' passed at 2026-08-22 9:09:50.902 PM.
Test Suite 'BriefingIdAndWakeTimeTests' passed at 2026-08-22 9:09:50.931 PM.
Test Suite 'JoinCoordinatorTests' passed at 2026-08-22 9:09:51.923 PM.
Test Suite 'JoinStateMachineTests' passed at 2026-08-22 9:09:51.983 PM.
Test Suite 'JoinTimingTests' passed at 2026-08-22 9:09:52.011 PM.
Test Suite 'ManifestDecodingTests' passed at 2026-08-22 9:09:52.048 PM.
Test Suite 'PlaybackCoordinatorVoiceTests' passed at 2026-08-22 9:09:52.748 PM.
Test Suite 'ResumePositionTests' passed at 2026-08-22 9:09:52.784 PM.
Test Suite 'SegmentSeekTests' passed at 2026-08-22 9:09:52.789 PM.
Test Suite 'ShowNotesTests' passed at 2026-08-22 9:09:52.820 PM.
Test Suite 'MercuryTests.xctest' passed at 2026-08-22 9:09:52.820 PM.
	 Executed 91 tests, with 0 failures (0 unexpected) in 1.750 (1.954) seconds
Test Suite 'All tests' passed at 2026-08-22 9:09:52.844 PM.
	 Executed 91 tests, with 0 failures (0 unexpected) in 1.750 (1.978) seconds
...
** TEST SUCCEEDED **
```

91 tests total: the 37 that existed before this phase, unchanged and still
green, plus 54 new (22 `JoinStateMachineTests`, 12 `PlaybackCoordinatorVoiceTests`,
14 `JoinCoordinatorTests`, 6 `JoinTimingTests`).

Two real bugs surfaced by actually running the suite rather than trusting it
compiled, both fixed before this report was written:

1. **`JoinTimingRecorder` off-by-one-millisecond flake.** `Int(elapsed *
   1000)` truncates; a floating-point elapsed time conceptually equal to
   `50ms` sometimes evaluates to `49.99999999997` and truncates down to
   `49`. Fixed by rounding before truncating
   (`Int((elapsed * 1000).rounded())`).
2. **Four `JoinCoordinatorTests` cases emitted a fake speech event before
   `JoinCoordinator`'s own `requestSpeech()` task had run** — `startListening`
   registers the fake's callback asynchronously (behind an `await
   requestAuthorization()`), so an `emit()` called immediately after `tap()`
   with no intervening `await` landed on a still-`nil` callback and was
   silently dropped, leaving the coordinator stuck in `.listening`. Fixed by
   awaiting one `settle()` between `tap()` and the first `emit()` in each of
   those tests. This is a real, load-bearing ordering constraint of the
   production code (speech authorization is genuinely asynchronous), not a
   test artifact worth papering over — noted here rather than only in the
   diff, per CLAUDE.md's "verify by running and pasting real output."

## What was built

A five-state, enum-driven state machine (`JoinStateMachine`) driven by an
orchestrator (`JoinCoordinator`) that wires together speech recognition,
volume ducking/pause on the existing `PlaybackCoordinator`, a streamed
`/voice/ask` network call, sequential playback of the streamed answer, and
per-stage latency instrumentation against the PRD 3.5 budget.

```
idle ──tap mic──► listening ──speech ends──► thinking
                      │                          │
                      │ tap again / timeout       │ answer ready
                      ▼                          ▼
                   resuming ◄──answer done── answering
                      │
                      ▼
                    idle
```

`thinking` also has a failure edge straight to `resuming` (backend/network
error, or a stream that produced no audio) — CLAUDE.md rule 7, graceful
degradation: a failed ask must still hand the briefing back, never strand
the UI.

## Files created

- `ios/Mercury/Core/Voice/JoinStateMachine.swift` — the pure `(state, event)
  -> state?` transition table plus the `@Observable` class wrapping it.
- `ios/Mercury/Core/Voice/JoinCoordinator.swift` — orchestrates the whole
  interaction: state transitions, duck/pause/resume, speech, the ask
  request, answer playback, timing marks.
- `ios/Mercury/Core/Voice/SpeechRecognitionService.swift` — `SFSpeechRecognizer`
  wrapper (`requiresOnDeviceRecognition = true`) behind a `SpeechRecognizing`
  protocol, plus an RMS-based silence-edge detector for real endpointing-lag
  measurement (see "Timing instrumentation" below).
- `ios/Mercury/Core/Voice/AnswerAudioPlayer.swift` — FIFO `AVAudioPlayer`
  chain that plays the streamed answer chunks (and the bridge line) back to
  back, behind an `AnswerPlaying` protocol.
- `ios/Mercury/Core/Voice/JoinTiming.swift` — `JoinTimingRecorder`: marks
  each PRD 3.5 stage against an injectable clock, computes pass/fail per
  line, logs via `os.Logger`.
- `ios/Mercury/Core/Voice/VoiceInterruptiblePlayback.swift` — the narrow
  protocol (`duck`/`restoreVolume`/`pause`/`play`/`positionMs`) that
  `JoinCoordinator` depends on instead of the concrete `PlaybackCoordinator`,
  so its orchestration is unit-testable against a fake.
- `ios/Mercury/Core/Networking/VoiceAskClient.swift` — streaming NDJSON
  client for `POST /voice/ask`, reconciled against the real backend
  implementation (`backend/app/api/voice.py`, `backend/app/pipeline/voice.py`)
  once it landed mid-session — see "The `/voice/ask` wire contract" below.
- `ios/Mercury/Features/Player/JoinOverlay.swift` — visible feedback row
  (icon + label) for `listening`/`thinking`/`answering`/`resuming`.

## Files modified

- `ios/Mercury/Core/Audio/PlaybackCoordinator.swift` — implemented the
  previously-stubbed `duck(to:over:)`/`restoreVolume(over:)` by ramping
  `engine.mainMixerNode.outputVolume`; added the pure `rampedVolume(...)`
  interpolation function and a `duckLevel`/`duckSeconds` pair of shared
  constants; conformed to `VoiceInterruptiblePlayback`; exposed
  `outputVolume` (read-only, test-only) so the ramp's landing value is
  observable from `PlaybackCoordinatorVoiceTests`.
- `ios/Mercury/Features/Player/TransportControls.swift` — the mic button is
  real now: state-driven icon/label/pulse, disabled outside
  `idle`/`listening`.
- `ios/Mercury/Features/Player/PlayerView.swift` — hosts `JoinOverlay` and
  wires the mic tap to `appState.join.tap()`.
- `ios/Mercury/App/AppState.swift` — owns a `JoinCoordinator`, built with
  defaults then wired to live `briefingId`/`positionMs` closures via
  `configure(...)` (see "Gotchas" — this two-step construction exists
  because of a real Swift initializer ordering constraint, not style).
- `ios/Mercury/Core/Networking/APIClient.swift` — `authorize(_:)` widened
  from `private` to internal so `VoiceAskClient` can attach the same
  `X-Mercury-Token` shared secret.
- `ios/Mercury/Core/Networking/Endpoints.swift` — added the `.voiceAsk` case
  (`POST /voice/ask`).
- `ios/Mercury/Info.plist` — added `NSMicrophoneUsageDescription` and
  `NSSpeechRecognitionUsageDescription`.
- `ios/Mercury/Mercury.xcodeproj/project.pbxproj` — registered all new
  source/test files (this project has no synchronized-folder groups; every
  file needs an explicit `PBXFileReference` + build-phase entry).

## Design decisions worth flagging

### Duck is a fade *followed by* a real pause, not continuous quiet playback

PRD 3.5 says "fade briefing audio to 15 percent... rather than pausing hard,"
and the phase brief separately requires "resume returning to the exact pause
position" as a testable behaviour. Read literally, "duck instead of pause"
and "resume from an exact pause position" are in tension — if the briefing
just kept playing quietly for the whole interrupt, there would be no fixed
position to resume *to*.

The implementation resolves this as: on tap, ramp volume down over 120ms
(the audible "duck" — no instant, jarring cut); once that ramp completes,
actually pause the underlying `AVAudioPlayerNode` so the briefing holds at a
fixed, sample-accurate position for the rest of the interrupt (listening +
thinking + answering can easily run several seconds — letting the briefing
keep advancing, even quietly, underneath a spoken answer from the same two
hosts would be confusing, and would mean "resume" was never resuming
anything). `PlaybackCoordinator.play()`/`pause()` never seek, so unpausing
alone reproduces the exact position — that's what makes "exact pause
position" true rather than approximate.

Edge case: if the user cancels (tap-again) inside that 120ms window, the
freeze never actually fires (`JoinCoordinator.isPausedForInterrupt` stays
`false`), so `finishResuming()` correctly does *not* call `play()` — nothing
was ever paused, so there's nothing to resume. Volume still ramps back up
either way. Covered by
`JoinCoordinatorTests.testFastCancelWithinDuckWindowNeverPausesOrResumesPlayback`
and `testNormalSpeedInterruptFreezesPlaybackThenResumesExactlyOnce`.

### The `/voice/ask` wire contract — assumed first, then reconciled against the real backend

The phase brief says a backend agent was building `POST /voice/ask` in
parallel and to "read its code to match the wire format." `backend/app/api/voice.py`
and `backend/app/pipeline/voice.py` did not exist anywhere in this checkout
when `VoiceAskClient.swift` was first written — grepping the full repo and
git history for `voice/ask`, `VoiceAsk`, `voice_ask` found nothing at that
point. Rather than block, the first version of this client spoke a contract
designed from what *was* available: `VOICE_ANSWER_PROMPT` in
`backend/app/pipeline/prompts.py` and `LLMProvider.stream` /
`TTSProvider.synthesize` in `backend/app/core/providers.py`.

The real endpoint landed mid-session (the parallel backend agent's work
appeared in the working tree). It was read in full and the client was
rewritten to match exactly — this is that final, reconciled version; nothing
here is still assumed. The differences from the first guess were real:

| | First guess | Actual (`backend/app/api/voice.py` / `pipeline/voice.py`) |
|---|---|---|
| discriminator field | `"type"` | `"kind"`, values `"answer" \| "bridge" \| "done"` |
| audio field | `"audio_base64"` | `"audio_b64"` |
| sequence field | `"seq"` | `"index"` |
| speaker | separate `{"type":"speaker",...}` frame, once | `"speaker"` field on every frame |
| bridge marker | boolean `"is_bridge"` field | its own `kind: "bridge"` |
| end of stream | a distinct `{"type":"done",...}` frame | whichever frame carries a non-null `"timing"` object — normally the last `answer` or `bridge` chunk itself, not a separate marker |
| error signalling | a `{"type":"error",...}` frame | none — a mid-stream failure still ends the HTTP response normally (`kind: "done"`, `audio_failed: true`, empty audio); only pre-stream failures (unknown briefing, empty question) are ordinary 404/400 HTTP statuses |

**Actual contract**, verbatim from `backend/app/api/voice.py`'s own docstring
and `VoiceChunk` in `backend/app/pipeline/voice.py` (also documented at the
top of `VoiceAskClient.swift`):

```
POST /voice/ask
Headers: X-Mercury-Token, Content-Type: application/json,
         Accept: application/x-ndjson
Body:    { "briefing_id": String, "question": String,
           "position_ms": Int, "prior_questions": [String] }
           (backend also accepts an "offline" override; client omits it)

Response: Content-Type: application/x-ndjson, chunked transfer, one JSON
          object per line, every line the same shape:
  {"kind": "answer" | "bridge" | "done", "index": Int,
   "speaker": "A" | "B", "text": String,
   "audio_b64": String, "audio_failed": Bool,
   "timing": {String: Int?} | null}
```

`timing` is non-null on exactly the last chunk of the stream. The client
(`VoiceAskChunk.isFinal`) and `JoinCoordinator` don't actually need to key
off it directly — the stream simply ending is enough to know the answer is
complete — but it's decoded and available (`WireEvent.timing`) since the
backend's own instrumentation is a useful cross-check against the client's
independent `JoinTimingRecorder` numbers if the two ever disagree.

`JoinCoordinator.ask(question:)` treats any chunk carrying `audio_failed ==
false` and non-empty audio as playable, regardless of `kind` — normally that
is an `.answer` chunk followed by a `.bridge` chunk, but if answer synthesis
entirely failed and only the bridge line survived, the bridge chunk alone is
what moves `.thinking` to `.answering`. A stream that ends with nothing ever
playable (every chunk `audio_failed`, or a lone fallback `.done` marker)
correctly falls through to `failAnswer()` — same graceful-degradation path
as a transport error.

### `JoinCoordinator` depends on protocols, not concrete types

`SpeechRecognizing`, `VoiceAskClientProtocol`, `AnswerPlaying`, and
`VoiceInterruptiblePlayback` are all seams specifically so `JoinCoordinatorTests`
can drive the orchestration deterministically with fakes. This mirrors
CLAUDE.md rule 6 ("every provider behind a protocol") even though none of
these are AI providers — the same argument (swap the implementation, test
against a fake) applies just as much to "the microphone" and "the network."

### `AppState`'s two-step `JoinCoordinator` construction

`AppState.join` is built as `JoinCoordinator(playback: playback)` (defaults
only) and then wired to live `briefingId`/`positionMs` closures via a
separate `join.configure(...)` call inside `init`. This isn't stylistic —
Swift's definite-initialization checker rejects a property initializer
(default value *or* another property's constructor argument in the same
statement) that captures `self` in a closure, because `self` isn't
considered fully initialized until every stored property (including the one
currently being assigned) has a value. `JoinCoordinator.configure(...)`
exists specifically to give `AppState` a point after all its stored
properties are set where capturing `self` is legal.

## Timing instrumentation (PRD 3.5's budget, made observable)

`JoinTimingRecorder` marks eight instants per interaction and reports six
deltas, each checked against its PRD budget:

| Stage | Budget | Marked at |
|---|---|---|
| tap → audio ducked | 100ms | `beginListening()`, immediately after the synchronous `playback.duck(...)` call |
| tap → listening UI visible | 200ms | immediately after `machine.send(.tapMic)` succeeds |
| speech end → detected | 600ms | RMS silence-edge (see below) → `SFSpeechRecognizer`'s `.final` callback |
| transcript → LLM first token | 1200ms | final transcript accepted → first NDJSON line received |
| first token → audio playing | 800ms | first NDJSON line → `AnswerAudioPlayer` actually calling `AVAudioPlayer.play()` |
| **TOTAL** | **2500ms** | tap → first audio playing |

The "speech end → detected" line is the one budget line that needed real
thought: the two client-observable events (RMS dropping below a speech
threshold in the mic tap, and `SFSpeechRecognizer` deciding `.isFinal`)
otherwise fire at essentially the same client-observed instant if measured
naively. `SFSpeechRecognitionService` runs its own lightweight RMS check
inside the existing audio tap and reports a `.silenceStarted` event on the
speaking→silence edge; `JoinCoordinator` marks "speech end" there and
"detected" on the recognizer's own `.final`, so the delta is a real estimate
of `SFSpeechRecognizer`'s endpointing lag rather than always ~0ms.

**All six numbers are logged via `os.Logger` on every completed interaction**
(`JoinTimingRecorder.log()`, called from `JoinCoordinator.finishResuming()`)
— pass or fail, so a regression is visible in the log the moment it happens.

## What is test-verified vs. what needs a device

**Verified by the test suite (`xcodebuild test`, simulator, no hardware
needed):**

- `JoinStateMachineTests` — every legal transition in the PRD 3.5 diagram,
  a representative illegal transition from *every* state (including the
  acceptance-criteria case: a tap during `.answering` does not re-enter
  `.listening`), plus an exhaustive sweep (`testEveryTransitionNotInTheLegalTableIsIllegal`)
  over all 35 `(state, event)` pairs, not just the ones explicitly tested
  elsewhere.
- `JoinTimingTests` — `JoinTimingRecorder`'s math against an injected
  deterministic clock (no real elapsed time): correct deltas, correct
  budget/`withinBudget` flags, a cancelled-before-measured stage is absent
  rather than falsely reported as 0ms, `reset()` actually clears prior
  marks, and the six budget constants are pinned to PRD 3.5's numbers
  verbatim.
- `PlaybackCoordinatorVoiceTests` — the pure `rampedVolume(...)` formula
  (start/end/midpoint/clamping/zero-duration edge cases) with exact
  arithmetic; `duck`/`restoreVolume` end to end with short real durations
  (tens of ms, so the suite stays fast) confirming the ramp actually lands
  on the target `outputVolume`; starting a new ramp mid-fade correctly
  supersedes the old one. This exercises `AVAudioMixerNode.outputVolume`,
  a plain property — no audio session route or output device is involved,
  unlike real playback, so this is safe in a headless simulator.
- `JoinCoordinatorTests` — full orchestration against fakes for speech,
  the network, and playback: the happy path end to end (including that a
  second ask's `prior_questions` includes the first question's text); tap
  during `.answering` is a no-op; tap-again cancels without ever calling the
  backend; the fast-cancel-vs-normal-speed pause/resume distinction
  described above, with an explicit assertion that `pause()` happens
  strictly before `play()`; empty transcript, backend failure, a stream with
  zero audio chunks, and denied authorization all return to `.idle` rather
  than stranding in `.thinking`/`.listening` (graceful degradation,
  CLAUDE.md rule 7); no briefing loaded fails without ever starting a
  listening session.

**NOT verified by the test suite — needs a physical device:**

- `SFSpeechRecognitionService` itself. On-device speech recognition has no
  model bundled in the iOS Simulator, so `SFSpeechRecognizer.isAvailable` is
  reliably `false` there; this class only participates in the build (it
  satisfies `SpeechRecognizing`, so `JoinCoordinator` compiles against it) —
  its actual behaviour, including the RMS silence-edge detector and the
  1.1s backstop timer, has never run against real audio.
- Every *real* millisecond in the timing table above. The recorder's own
  arithmetic is proven exact (`JoinTimingTests`), but the actual measured
  values — is `SFSpeechRecognizer`'s endpointing genuinely under 600ms, is a
  real network round trip to a real `/voice/ask` under 1200ms to first
  token, does `AVAudioPlayer.play()` on real hardware start within 800ms of
  receiving the first decoded chunk — are unmeasured. The instrumentation
  is real and will report these numbers the first time this runs on a
  device against a live backend; none of the six numbers in this document
  are measured, they are the PRD's targets.
- Real microphone authorization flows (first-run prompt, Settings-based
  denial and re-grant) and the actual `NSMicrophoneUsageDescription`/
  `NSSpeechRecognitionUsageDescription` prompts.
- Whether ducking + a 120ms-later pause actually *sounds* like "the hosts
  pausing to listen" rather than a stutter — this is fundamentally a
  listening-test judgment call (CLAUDE.md's "ten second test"), not
  something a unit test can assert.
- An actual end-to-end run against the real `/voice/ask` over a real network
  connection. The wire contract itself is reconciled and code-verified
  against `backend/app/api/voice.py` (see above), and `VoiceAskClient`
  compiles and its request/response shapes match — but no fake stands in
  for genuine network latency, HTTP/1.1 chunked-transfer quirks, or the
  live LLM/TTS providers actually being slow/fast in the ways the timing
  budget cares about. `JoinCoordinatorTests` proves the *orchestration*
  around the network call is correct; it cannot prove the network call
  itself performs.

## Gotchas for whoever revisits this

1. **`WireEvent` had to move to file scope.** It was originally a
   `private struct` nested inside `VoiceAskClient`; `VoiceAskChunk.init?(wire:)`
   (a different top-level type in the same file) couldn't see it even at
   `fileprivate` access, because a nested type's access level controls
   *whether* outside code may reference it, not whether it can be named
   without qualification (that would need `VoiceAskClient.WireEvent`).
   Moved to file scope. If the backend's response shape changes again,
   `WireEvent`'s `CodingKeys` (and `VoiceAskChunk.Kind`'s three raw values)
   are the only things that should need editing — `JoinCoordinator` only
   ever sees the already-decoded `VoiceAskChunk`.
2. **The Xcode project has no synchronized file groups.** Every new file
   needed a `PBXFileReference`, a `PBXBuildFile`, group membership, and a
   `Sources` build-phase entry, added via the `xcodeproj` Ruby gem
   (`Xcodeproj::Project`) rather than hand-editing `project.pbxproj` — safer
   given how easy it is to get a path wrong (the first attempt at this
   duplicated every path segment, e.g. `Core/Voice/Core/Voice/Foo.swift`,
   because `group.new_reference(path)` resolves relative to the *group's own*
   directory, not the project root).
3. **`AVAudioMixerNode.outputVolume` has no native ramp API.** `duck`/
   `restoreVolume` step it by hand on a cancellable `Task` at a 12ms
   interval; `PlaybackCoordinator.rampedVolume(...)` is the pure
   interpolation this steps through, kept `static` specifically so it's
   testable without a running engine.
4. **Speech recognition's own `SFSpeechRecognitionTask.isFinal`, plus a
   from-scratch 1.1s silence backstop timer, both feed into the same
   `.final` event.** Whichever fires first wins (both paths call
   `stopListening()`, which is idempotent). This backstop exists because
   `SFSpeechRecognizer`'s automatic endpointing is tuned for dictation and
   is snappier than a natural conversational pause warrants — untested on
   device, tune `silenceTimeout` (currently 1.1s) after listening to real
   sessions.
